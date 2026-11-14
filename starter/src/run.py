"""Pipeline Day 06: đọc tài liệu -> chia chunk -> embedding -> lưu -> truy xuất -> đánh giá.

Chạy từ thư mục ``starter/``:  python -m src.run [--no-strict]

Dữ liệu đầu vào bạn tự chuẩn bị trong ``starter/data/``:

manifest.json
    {"documents": [{
        "source_id": "handbook-2025",            # trở thành document_id
        "title": "...", "path": "handbook.pdf",  # đường dẫn tương đối so với data/
        "source_path_or_url": "https://...", "license_or_permission": "...",
        "accessed_at": "2026-11-14",
        "metadata": {"year": 2025},              # trường filter cho cả tài liệu
        "content_page_ranges": [{"from": 3, "to": 40}],   # tùy chọn: chỉ dùng các trang này
        "exclude_pages": [1, 2],                 # bìa, mục lục, tài liệu tham khảo
        "page_ranges": [{"from": 3, "to": 10, "metadata": {"section": "tuyen_sinh"}}]
    }]}

benchmark.json
    {"questions": [{
        "id": "q01", "split": "dev", "type": "truc_tiep", "question": "...",
        "expected_answer": "...", "gold_source_id": "handbook-2025", "gold_page": 3,
        "evidence_quote": "...",
        "filter": {"section": "tuyen_sinh"},     # tùy chọn, có trước khi truy xuất
        "gold_alternatives": [{"gold_source_id": "...", "gold_page": 7, "evidence_quote": "..."}]
    }]}

Kết quả ghi vào ``starter/results/``: summary.json, traces.jsonl, ingestion_log.json,
manual_review.csv và coherence_samples.csv (hai file cuối có sẵn cột nhãn để trống
cho phần chấm thủ công).
"""
import argparse
import csv
import json
import os
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from dotenv import load_dotenv

from .chunking import by_paragraph, fixed_size
from .embeddings import MyEmbedder
from .evaluation import evaluate_query, gold_evidence, squash, summarize
from .ingestion import count_pdf_pages, load_pdf
from .models import Chunk, DocumentPage
from .store import PgVectorStore

ROOT = Path(__file__).resolve().parents[1]          # starter/ (chứa .env)
REPO = ROOT.parent                                  # thư mục gốc repo bài nộp
DATA = REPO / "data"                                # manifest.json
BENCHMARK = REPO / "benchmark"                      # benchmark.json
RESULTS = REPO / "results"

DEFAULT_MODEL = "openai/text-embedding-3-small"   # dùng khi EMBEDDING_MODEL trong .env để trống
TOP_K = 3
MIN_CONTENT_PAGES = 30                            # yêu cầu tối thiểu của lab
FIXED_SIZE, FIXED_OVERLAP = 500, 100               # tham số chiến lược A
PARAGRAPH_MAX_SIZE = 1500                          # tham số chiến lược B và C

# Một "index" là một bộ chunk đã lưu. C tìm trên chính các chunk của B và chỉ thêm
# filter, nên khác biệt B-C chỉ do filter (không embed lại lần hai).
INDEXES = {
    "A": lambda page: fixed_size(page, FIXED_SIZE, FIXED_OVERLAP),
    "B": lambda page: by_paragraph(page, PARAGRAPH_MAX_SIZE),
}
STRATEGIES = {
    "A": {"index": "A", "use_filter": False,
          "chunking": f"fixed_size(size={FIXED_SIZE}, overlap={FIXED_OVERLAP})"},
    "B": {"index": "B", "use_filter": False,
          "chunking": f"by_paragraph(max_size={PARAGRAPH_MAX_SIZE})"},
    "C": {"index": "B", "use_filter": True,
          "chunking": f"by_paragraph(max_size={PARAGRAPH_MAX_SIZE})"},
}


def load_corpus(manifest_path: Path) -> tuple[list[DocumentPage], list[dict]]:
    """Đọc manifest và các PDF; trả về các trang nội dung và log nạp dữ liệu từng tài liệu.

    Trang bị loại (exclude_pages) và trang rỗng không được tính là trang nội dung
    nhưng vẫn được ghi trong log. Metadata của tài liệu và của từng khoảng trang
    (page_ranges) được gắn vào mỗi trang để dùng làm filter.
    """
    if not manifest_path.is_file():
        raise SystemExit(f"Missing {manifest_path}. See the format at the top of src/run.py.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pages: list[DocumentPage] = []
    log: list[dict] = []
    seen: set[str] = set()
    for document in manifest["documents"]:
        document_id = document["source_id"]
        if document_id in seen:
            raise SystemExit(f"Duplicate source_id in manifest: {document_id}")
        seen.add(document_id)
        excluded = set(document.get("exclude_pages") or [])
        # content_page_ranges: chỉ lấy một phần của PDF (vd một chương); không có thì lấy hết.
        ranges = document.get("content_page_ranges")
        wanted = ({number for r in ranges for number in range(r["from"], r["to"] + 1)}
                  if ranges else None)
        pdf_path = DATA / document["path"]
        source = Path(document["path"]).name       # chỉ lưu tên file, không lưu đường dẫn máy
        entry = {"source_id": document_id, "path": document["path"],
                 "physical_pages": count_pdf_pages(pdf_path), "content_page_ranges": ranges,
                 "content_pages": [], "excluded_pages": sorted(excluded), "empty_pages": [],
                 "errors": [], "characters": 0, "page_characters": {}}
        for page in load_pdf(pdf_path, document_id, source, wanted):
            if "extraction_error" in page.metadata:
                entry["errors"].append({"page": page.page,
                                        "error": page.metadata["extraction_error"]})
            if page.page in excluded:
                continue
            if page.metadata["empty"]:
                entry["empty_pages"].append(page.page)     # có thể là trang scan: cần OCR
                continue
            page.metadata = {"title": document.get("title"), **(document.get("metadata") or {})}
            for page_range in document.get("page_ranges") or []:
                if page_range["from"] <= page.page <= page_range["to"]:
                    page.metadata.update(page_range["metadata"])
            entry["content_pages"].append(page.page)
            entry["characters"] += len(page.text)
            entry["page_characters"][page.page] = len(page.text)
            pages.append(page)
        log.append(entry)
    return pages, log


def load_benchmark(path: Path, pages: list[DocumentPage]) -> tuple[list[dict], list[str]]:
    """Đọc bộ câu hỏi và trả về kèm danh sách mọi vấn đề phát hiện khi kiểm tra.

    Kiểm tra: đủ trường bắt buộc, split hợp lệ, evidence quote thực sự nằm ở
    trang gold sau chuẩn hóa, giá trị filter có tồn tại trong corpus, và đủ số
    lượng câu (>= 20 câu, >= 15 dev, >= 5 test, >= 3 câu có filter với >= 1 câu test).
    """
    if not path.is_file():
        raise SystemExit(f"Missing {path}. See the format at the top of src/run.py.")
    questions = json.loads(path.read_text(encoding="utf-8"))["questions"]
    page_text = {(page.document_id, str(page.page)): squash(page.text) for page in pages}
    filter_values: dict[str, set[str]] = {}
    for page in pages:
        for key, value in {**page.metadata, "document_id": page.document_id,
                           "source": page.source, "page": page.page}.items():
            filter_values.setdefault(key, set()).add(str(value))

    problems: list[str] = []
    ids = [question.get("id") for question in questions]
    if len(set(ids)) != len(ids):
        problems.append("question ids are not unique")
    for question in questions:
        label = question.get("id", "?")
        missing = [key for key in ("id", "split", "question", "expected_answer",
                                   "gold_source_id", "gold_page", "evidence_quote")
                   if question.get(key) in (None, "")]
        if missing:
            problems.append(f"{label}: missing {', '.join(missing)}")
            continue
        if question["split"] not in ("dev", "test"):
            problems.append(f"{label}: split must be 'dev' or 'test'")
        for gold in gold_evidence(question):
            gold_pages = gold["gold_page"] if isinstance(gold["gold_page"], list) else [gold["gold_page"]]
            if not any(squash(gold["evidence_quote"]) in page_text.get((gold["gold_source_id"], str(p)), "")
                       for p in gold_pages):
                problems.append(f"{label}: evidence_quote not found on "
                                f"{gold['gold_source_id']} page {gold['gold_page']} after normalization")
        for key, value in (question.get("filter") or {}).items():
            for item in value if isinstance(value, list) else [value]:
                if str(item) not in filter_values.get(key, set()):
                    problems.append(f"{label}: filter {key}={item!r} matches no page in the corpus")

    dev = sum(question.get("split") == "dev" for question in questions)
    test = sum(question.get("split") == "test" for question in questions)
    with_filter = [question for question in questions if question.get("filter")]
    if len(questions) < 20 or dev < 15 or test < 5:
        problems.append(f"need >= 20 questions with >= 15 dev / >= 5 test; got {dev} dev, {test} test")
    if len(with_filter) < 3 or not any(q.get("split") == "test" for q in with_filter):
        problems.append("need >= 3 questions with a filter, at least 1 of them in test")
    return questions, problems


def length_stats(chunks: list[Chunk]) -> dict:
    """Thống kê số chunk và phân bố độ dài (ký tự): min, trung vị, trung bình, max."""
    lengths = sorted(len(chunk.text) for chunk in chunks)
    return {"chunks": len(lengths), "total_characters": sum(lengths),
            "min": lengths[0], "median": statistics.median(lengths),
            "mean": round(statistics.fmean(lengths), 1), "max": lengths[-1]}


def write_csv(path: Path, rows: list[dict]) -> None:
    """Ghi list dict ra CSV (utf-8-sig để Excel hiển thị đúng tiếng Việt)."""
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def coherence_samples(pages: list[DocumentPage], chunks: dict[str, list[Chunk]]) -> list[dict]:
    """Chọn mẫu chấm coherence: cùng 10 vị trí nguồn cho mọi index.

    Vị trí = điểm giữa của 10 trang đầu tiên (sắp theo document_id, page), được
    xác định trước khi xem điểm. Với mỗi index lấy chunk chứa vị trí đó; cột
    coherence_0_2 và reason để trống cho người chấm.
    """
    rows = []
    for page in sorted(pages, key=lambda page: (page.document_id, page.page))[:10]:
        position = len(page.text) // 2
        for name, index_chunks in chunks.items():
            on_page = [chunk for chunk in index_chunks
                       if (chunk.document_id, chunk.page) == (page.document_id, page.page)]
            chunk = next((chunk for chunk in on_page
                          if chunk.metadata["char_start"] <= position < chunk.metadata["char_end"]),
                         min(on_page, key=lambda chunk: abs(chunk.metadata["char_start"] - position)))
            rows.append({"document_id": page.document_id, "page": page.page,
                         "char_position": position, "index": name, "chunk_id": chunk.chunk_id,
                         "text": chunk.text, "coherence_0_2": "", "reason": ""})
    return rows


def main() -> None:
    """Chạy toàn bộ pipeline cho ba chiến lược A, B, C và ghi kết quả ra results/."""
    # Console Windows mặc định không in được tiếng Việt -> ép dùng UTF-8.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-strict", action="store_true",
                        help="report corpus/benchmark problems as warnings instead of stopping")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    RESULTS.mkdir(exist_ok=True)
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # 1. Nạp corpus và benchmark; dừng nếu chưa đạt yêu cầu (trừ khi --no-strict).
    pages, ingestion_log = load_corpus(DATA / "manifest.json")
    (RESULTS / "ingestion_log.json").write_text(
        json.dumps(ingestion_log, ensure_ascii=False, indent=2), encoding="utf-8")
    questions, problems = load_benchmark(BENCHMARK / "benchmark.json", pages)
    if len(pages) < MIN_CONTENT_PAGES:
        problems.insert(0, f"need >= {MIN_CONTENT_PAGES} content pages; got {len(pages)}")
    for problem in problems:
        print(f"[problem] {problem}")
    if problems and not args.no_strict:
        raise SystemExit("Fix the problems above, or rerun with --no-strict while developing.")
    print(f"Corpus: {len(pages)} content pages | Benchmark: {len(questions)} questions")

    # 2-3. Chia chunk, embed bằng CÙNG một model, lưu theo từng index; đo thời gian và chi phí.
    embedder = MyEmbedder(os.getenv("EMBEDDING_MODEL", "").strip() or DEFAULT_MODEL)
    store = PgVectorStore(os.getenv("DATABASE_URL", ""), embedder.dimensions)
    store.create_schema()
    print(f"Embedding: {embedder.provider} / {embedder.model_name} ({embedder.dimensions} dims)")

    document_ids = sorted({page.document_id for page in pages})
    chunks: dict[str, list[Chunk]] = {}
    index_stats: dict[str, dict] = {}
    for name, chunker in INDEXES.items():
        tokens, cost, start = embedder.total_tokens, embedder.total_cost, time.perf_counter()
        chunks[name] = [chunk for page in pages for chunk in chunker(page)]
        vectors = embedder.embed_documents([chunk.text for chunk in chunks[name]])
        store.upsert(name, chunks[name], vectors)
        removed = store.delete_other_documents(name, document_ids)
        stored = store.count(name)
        # Số dòng trong DB phải đúng bằng số chunk: chứng minh nạp lại không nhân đôi.
        if stored != len(chunks[name]):
            raise RuntimeError(f"index {name}: {stored} rows stored, expected {len(chunks[name])}")
        index_stats[name] = {**length_stats(chunks[name]), "rows_in_store": stored,
                             "rows_removed_for_dropped_documents": removed,
                             "ingest_seconds": round(time.perf_counter() - start, 2),
                             "embedding_tokens": embedder.total_tokens - tokens,
                             "embedding_cost_usd": round(embedder.total_cost - cost, 6)}
        print(f"Index {name}: {index_stats[name]}")

    # 4. Cùng bộ câu hỏi cho mọi chiến lược; mỗi câu chỉ embed một lần.
    #    Chỉ chiến lược C áp filter, và chỉ với câu hỏi có filter.
    query_vectors = {question["id"]: embedder.embed_query(question["question"])
                     for question in questions}
    traces = []
    for name, config in STRATEGIES.items():
        for question in questions:
            filters = (question.get("filter") or None) if config["use_filter"] else None
            hits = store.search(config["index"], query_vectors[question["id"]], TOP_K, filters)
            trace = evaluate_query(question, hits)
            trace.update(strategy=name, index=config["index"], filter_applied=filters)
            traces.append(trace)
    with (RESULTS / "traces.jsonl").open("w", encoding="utf-8") as handle:
        for trace in traces:
            handle.write(json.dumps(trace, ensure_ascii=False) + "\n")

    # 5. Tính chỉ số và xuất dữ liệu cho phần chấm thủ công (grounding, metadata, coherence).
    metrics = summarize(traces)
    summary = {
        "started_at_utc": started_at,
        "embedding": {"provider": embedder.provider, "model": embedder.model_name,
                      "dimensions": embedder.dimensions, "total_tokens": embedder.total_tokens,
                      "total_cost_usd": round(embedder.total_cost, 6),
                      "query_document_handling": "same model, no prefix"},
        "vector_store": {"backend": "PostgreSQL + pgvector", "table": "chunks",
                         "metric": "cosine", "search": "exact (no ANN index)"},
        "versions": {"python": platform.python_version(), "psycopg": version("psycopg"),
                     "pypdf": version("pypdf")},
        "top_k": TOP_K, "strategies": STRATEGIES, "indexes": index_stats,
        "corpus": {"content_pages": len(pages), "documents": document_ids},
        "benchmark_problems": problems, "metrics": metrics,
    }
    (RESULTS / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    review = [{"strategy": trace["strategy"], "question_id": trace["id"],
               "question": trace["question"], "expected_answer": trace["expected_answer"],
               "filter_applied": json.dumps(trace["filter_applied"], ensure_ascii=False),
               "rank": hit["rank"], "chunk_id": hit["chunk_id"], "score": round(hit["score"], 4),
               "source": hit["source"], "page": hit["page"],
               "metadata": json.dumps(hit["metadata"], ensure_ascii=False), "text": hit["text"],
               "auto_is_hit": hit["is_hit"], "metadata_correct_0_1": "",
               "grounding_0_2": "", "note": ""}
              for trace in traces if trace["split"] == "test" for hit in trace["hits"]]
    if review:
        write_csv(RESULTS / "manual_review.csv", review)
    write_csv(RESULTS / "coherence_samples.csv", coherence_samples(pages, chunks))

    print("\nstrategy split  Hit@1   Evidence Hit@3")
    for name, splits in metrics.items():
        for split, cell in sorted(splits.items()):
            print(f"{name:<8} {split:<5}  {cell['hit_at_1']}/{cell['questions']:<5} "
                  f"{cell['hit_at_3']}/{cell['questions']}")
    print(f"\nResults written to {RESULTS}")


if __name__ == "__main__":
    main()
