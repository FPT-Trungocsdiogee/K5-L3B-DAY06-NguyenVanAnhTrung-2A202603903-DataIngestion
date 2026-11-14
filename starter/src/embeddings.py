"""Embedding thật qua OpenRouter (API tương thích OpenAI).

Chỉ dùng thư viện chuẩn của Python (không cần cài thêm SDK). Cần OPENROUTER_API_KEY.
Mọi lỗi đều được báo ra; KHÔNG âm thầm thay bằng vector giả/ngẫu nhiên.
"""
import json
import os
import time
import urllib.error
import urllib.request
from typing import Protocol

OPENROUTER_URL = "https://openrouter.ai/api/v1/embeddings"


class Embedder(Protocol):
    """Giao kèo chung cho mọi embedder (để có thể đổi provider mà không sửa pipeline)."""

    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Trả về một vector cho mỗi văn bản, đúng thứ tự đầu vào."""
        ...

    def embed_query(self, question: str) -> list[float]:
        """Trả về vector câu hỏi, tương thích với vector tài liệu đã lưu."""
        ...


class EmbeddingError(RuntimeError):
    """Provider không trả về được embedding thật."""


class MyEmbedder:
    """Adapter cho OpenRouter. Tài liệu và câu hỏi dùng cùng model, không thêm tiền tố."""

    provider = "openrouter"

    def __init__(self, model_name: str, batch_size: int = 64, max_retries: int = 4,
                 timeout: float = 60.0) -> None:
        """Lưu cấu hình, đọc API key từ biến môi trường và dò số chiều của model."""
        if not model_name:
            raise ValueError("model_name is required, e.g. 'openai/text-embedding-3-small'")
        self.model_name = model_name
        self.batch_size = batch_size       # số văn bản gửi trong một request
        self.max_retries = max_retries     # số lần thử lại khi lỗi tạm thời
        self.timeout = timeout
        self._api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not self._api_key:
            raise EmbeddingError("OPENROUTER_API_KEY is missing; set it in starter/.env")
        # Lượng dùng do provider báo về, cộng dồn để ghi phần chi phí trong báo cáo.
        self.total_tokens = 0
        self.total_cost = 0.0
        self.requests = 0
        # Gọi thật một lần để biết số chiều vector, thay vì ghi cứng theo từng model.
        self.dimensions = len(self._request(["dimension probe"])[0])

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed danh sách chunk theo từng lô ``batch_size``; giữ nguyên thứ tự."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            vectors.extend(self._request(texts[start:start + self.batch_size]))
        return vectors

    def embed_query(self, question: str) -> list[float]:
        """Embed một câu hỏi bằng cùng model với tài liệu."""
        return self._request([question])[0]

    def _request(self, texts: list[str]) -> list[list[float]]:
        """Gửi một request tới OpenRouter và trả về các vector theo đúng thứ tự.

        Lỗi tạm thời (408, 429, 5xx, mất mạng) được thử lại với thời gian chờ
        tăng dần; lỗi khác (sai key, sai tên model...) báo ngay.
        """
        if any(not text or not text.strip() for text in texts):
            raise ValueError("cannot embed empty text")
        body = json.dumps({"model": self.model_name, "input": texts}).encode("utf-8")
        request = urllib.request.Request(OPENROUTER_URL, data=body, method="POST", headers={
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        })

        last_error = "unknown error"
        for attempt in range(self.max_retries + 1):
            if attempt:
                time.sleep(min(2 ** attempt, 20))   # chờ 2s, 4s, 8s... tối đa 20s
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", "replace")[:300]
                last_error = f"HTTP {error.code}: {detail}"
                if error.code in (408, 429) or error.code >= 500:
                    continue                    # lỗi tạm thời: thử lại
                raise EmbeddingError(f"{self.model_name}: {last_error}") from error
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
                last_error = f"{type(error).__name__}: {error}"
                continue

            data = payload.get("data")
            if not data:                        # OpenRouter có thể trả HTTP 200 kèm nội dung lỗi
                last_error = f"no embeddings in response: {json.dumps(payload)[:300]}"
                continue
            # Sắp theo "index" để chắc chắn vector thứ i ứng với văn bản thứ i.
            vectors = [item["embedding"] for item in sorted(data, key=lambda item: item["index"])]
            if len(vectors) != len(texts):
                raise EmbeddingError(f"expected {len(texts)} vectors, got {len(vectors)}")
            dimensions = getattr(self, "dimensions", len(vectors[0]))
            if any(len(vector) != dimensions for vector in vectors):
                raise EmbeddingError("provider returned vectors with inconsistent dimensions")
            usage = payload.get("usage") or {}
            self.total_tokens += usage.get("total_tokens") or usage.get("prompt_tokens") or 0
            self.total_cost += usage.get("cost") or 0.0
            self.requests += 1
            return vectors

        raise EmbeddingError(
            f"{self.model_name}: failed after {self.max_retries + 1} attempts ({last_error})")
