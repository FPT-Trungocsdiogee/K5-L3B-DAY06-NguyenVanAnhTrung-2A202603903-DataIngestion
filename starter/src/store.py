"""Lưu và tìm kiếm vector bằng PostgreSQL + pgvector (tìm chính xác, khoảng cách cosine)."""
import json
import math
import re
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from .models import Chunk

TABLE = "chunks"
# Filter theo các khóa này dùng cột thật; khóa khác được tra trong cột metadata (JSONB).
COLUMN_FILTERS = {"document_id": "text", "source": "text", "page": "int"}
# Tên trường filter hợp lệ: chỉ chữ, số, gạch dưới (chặn chuỗi lạ do người dùng nhập).
_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def _vector_literal(vector: list[float]) -> str:
    """Đổi list số thực thành chuỗi '[0.1,0.2,...]' mà pgvector đọc được."""
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


def _as_text(value: Any) -> str:
    """Đổi giá trị filter thành text giống cách PostgreSQL hiển thị JSON với ->>."""
    return json.dumps(value) if isinstance(value, bool) else str(value)


class PgVectorStore:
    def __init__(self, database_url: str, dimensions: int) -> None:
        """Lưu thông tin kết nối và kiểm tra số chiều của model embedding."""
        if not database_url:
            raise ValueError("DATABASE_URL is missing; copy .env.example to .env")
        if not isinstance(dimensions, int) or not 0 < dimensions <= 16000:
            raise ValueError(f"invalid embedding dimensions: {dimensions!r}")
        self.database_url = database_url
        self.dimensions = dimensions

    def _connect(self) -> psycopg.Connection:
        """Mở kết nối mới; khối ``with`` sẽ tự commit khi xong, rollback khi lỗi."""
        return psycopg.connect(self.database_url, connect_timeout=10)

    def create_schema(self) -> None:
        """Tạo bảng ``chunks`` có cột vector và metadata nguồn/trang/filter.

        Gọi nhiều lần vẫn an toàn. Nếu bảng đã tồn tại với số chiều khác
        (do đổi model) thì báo lỗi thay vì ghi lẫn vector không tương thích.
        """
        with self._connect() as connection:
            connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
            connection.execute(f"""
                CREATE TABLE IF NOT EXISTS {TABLE} (
                    strategy    text  NOT NULL,
                    chunk_id    text  NOT NULL,
                    document_id text  NOT NULL,
                    source      text  NOT NULL,
                    page        int   NOT NULL,
                    text        text  NOT NULL,
                    metadata    jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                    embedding   vector({self.dimensions}) NOT NULL,
                    PRIMARY KEY (strategy, chunk_id)
                )""")
            connection.execute(
                f"CREATE INDEX IF NOT EXISTS {TABLE}_strategy_document "
                f"ON {TABLE} (strategy, document_id)")
            # Với pgvector, atttypmod của cột chính là số chiều đã khai báo.
            existing = connection.execute(
                "SELECT atttypmod FROM pg_attribute "
                "WHERE attrelid = %s::regclass AND attname = 'embedding'", (TABLE,)).fetchone()[0]
        if existing != self.dimensions:
            raise RuntimeError(
                f"table '{TABLE}' stores vector({existing}) but the model returns "
                f"{self.dimensions} dimensions; drop the table before changing models")

    def upsert(self, strategy: str, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        """Ghi chunk theo khóa (strategy, chunk_id) trong một transaction.

        - Chunk đã có thì cập nhật, chưa có thì thêm mới -> nạp lại không nhân đôi.
        - Chunk cũ của cùng strategy + cùng tài liệu nhưng không còn trong lần gọi
          này (do tài liệu nguồn đã đổi) sẽ bị xóa. Vì vậy hãy truyền TOÀN BỘ
          chunk của một tài liệu trong cùng một lần gọi.
        """
        if len(chunks) != len(vectors):
            raise ValueError(f"{len(chunks)} chunks but {len(vectors)} vectors")
        ids = [chunk.chunk_id for chunk in chunks]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate chunk_id in batch")
        for chunk, vector in zip(chunks, vectors):
            if len(vector) != self.dimensions:
                raise ValueError(f"{chunk.chunk_id}: vector has {len(vector)} dimensions, "
                                 f"expected {self.dimensions}")
            if not all(math.isfinite(value) for value in vector):
                raise ValueError(f"{chunk.chunk_id}: vector contains NaN or infinity")
        if not chunks:
            return

        rows = [(strategy, chunk.chunk_id, chunk.document_id, chunk.source, chunk.page,
                 chunk.text, Jsonb(chunk.metadata), _vector_literal(vector))
                for chunk, vector in zip(chunks, vectors)]
        documents = sorted({chunk.document_id for chunk in chunks})
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.executemany(f"""
                    INSERT INTO {TABLE} (strategy, chunk_id, document_id, source, page,
                                         text, metadata, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)
                    ON CONFLICT (strategy, chunk_id) DO UPDATE SET
                        document_id = EXCLUDED.document_id, source = EXCLUDED.source,
                        page = EXCLUDED.page, text = EXCLUDED.text,
                        metadata = EXCLUDED.metadata, embedding = EXCLUDED.embedding""", rows)
                # Xóa chunk lỗi thời của các tài liệu vừa nạp lại.
                cursor.execute(
                    f"DELETE FROM {TABLE} WHERE strategy = %s AND document_id = ANY(%s) "
                    f"AND NOT (chunk_id = ANY(%s))", (strategy, documents, ids))

    def delete_other_documents(self, strategy: str, keep_document_ids: list[str]) -> int:
        """Xóa chunk của những tài liệu đã bị gỡ khỏi corpus; trả về số dòng đã xóa."""
        with self._connect() as connection:
            return connection.execute(
                f"DELETE FROM {TABLE} WHERE strategy = %s AND NOT (document_id = ANY(%s))",
                (strategy, list(keep_document_ids))).rowcount

    def count(self, strategy: str) -> int:
        """Đếm số chunk đang lưu của một strategy (dùng để kiểm tra không nhân đôi)."""
        with self._connect() as connection:
            return connection.execute(
                f"SELECT count(*) FROM {TABLE} WHERE strategy = %s", (strategy,)).fetchone()[0]

    def search(self, strategy: str, query_vector: list[float], top_k: int = 3,
               filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Lọc metadata ngay trong SQL, xếp hạng theo khoảng cách cosine, trả về top-k.

        ``score`` là độ tương đồng cosine (= 1 - khoảng cách; càng gần 1 càng giống).
        Giá trị filter có thể là một giá trị hoặc một list các giá trị chấp nhận.
        Mọi giá trị do người dùng cung cấp đều truyền qua tham số (%s), không nối
        chuỗi vào SQL -> tránh SQL injection.
        """
        if len(query_vector) != self.dimensions:
            raise ValueError(f"query vector has {len(query_vector)} dimensions, "
                             f"expected {self.dimensions}")
        if top_k <= 0:
            raise ValueError("top_k must be > 0")

        where = ["strategy = %s"]
        where_params: list[Any] = [strategy]
        for key, value in (filters or {}).items():
            if not _KEY.match(key):
                raise ValueError(f"invalid filter field: {key!r}")
            values = value if isinstance(value, (list, tuple, set)) else [value]
            if key in COLUMN_FILTERS:           # lọc theo cột thật: document_id/source/page
                where.append(f"{key} = ANY(%s::{COLUMN_FILTERS[key]}[])")
                where_params.append([_as_text(item) for item in values])
            else:                               # lọc theo trường trong metadata JSONB
                where.append("metadata ->> %s = ANY(%s)")
                where_params += [key, [_as_text(item) for item in values]]

        vector = _vector_literal(query_vector)
        # Toán tử <=> của pgvector là khoảng cách cosine; thêm chunk_id để thứ tự ổn định khi hòa.
        sql = (f"SELECT chunk_id, document_id, source, page, text, metadata, "
               f"1 - (embedding <=> %s::vector) AS score FROM {TABLE} "
               f"WHERE {' AND '.join(where)} "
               f"ORDER BY embedding <=> %s::vector, chunk_id LIMIT %s")
        with self._connect() as connection:
            rows = connection.execute(sql, [vector, *where_params, vector, top_k]).fetchall()
        return [{"chunk_id": row[0], "document_id": row[1], "source": row[2], "page": row[3],
                 "text": row[4], "metadata": row[5], "score": float(row[6])} for row in rows]
