"""Chấm kết quả truy xuất so với gold evidence và lưu trace cho từng câu hỏi."""
import re
import unicodedata
from typing import Any


def squash(text: str) -> str:
    """Đưa văn bản về dạng dễ so sánh: NFC, chữ thường, mọi khoảng trắng thành một dấu cách.

    PDF thường xuống dòng giữa câu, nên khi so evidence quote với chunk phải bỏ
    qua vị trí xuống dòng.
    """
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text or "")).strip().casefold()


def gold_evidence(question: dict[str, Any]) -> list[dict[str, Any]]:
    """Gom gold chính và các gold thay thế hợp lệ (``gold_alternatives``) thành một list."""
    golds = [question] + list(question.get("gold_alternatives") or [])
    return [{"gold_source_id": gold["gold_source_id"], "gold_page": gold["gold_page"],
             "evidence_quote": gold["evidence_quote"]} for gold in golds]


def _pages(gold_page: Any) -> set[str]:
    """``gold_page`` có thể là một số hoặc một list số trang; đổi hết về tập chuỗi."""
    values = gold_page if isinstance(gold_page, (list, tuple)) else [gold_page]
    return {str(value) for value in values}


def _field(hit: dict[str, Any], key: str) -> Any:
    """Lấy giá trị một trường của hit: cột chính hoặc trường trong metadata."""
    return hit[key] if key in ("document_id", "source", "page") else (hit.get("metadata") or {}).get(key)


def _matches_filter(hit: dict[str, Any], filters: dict[str, Any]) -> bool:
    """Kiểm tra một hit có thỏa mọi điều kiện filter của câu hỏi hay không."""
    for key, expected in filters.items():
        accepted = expected if isinstance(expected, (list, tuple)) else [expected]
        if str(_field(hit, key)) not in {str(value) for value in accepted}:
            return False
    return True


def evaluate_query(question: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    """Chấm một câu hỏi với danh sách hit đã xếp hạng và trả về trace đầy đủ.

    Một hit được tính khi chunk thuộc đúng nguồn/trang gold VÀ chứa trọn evidence
    quote; gold thay thế nào đã ghi cũng được chấp nhận. Nhãn grounding và
    coherence là nhãn chấm tay nên để trống ở đây, không suy ra từ so khớp chuỗi.
    """
    golds = gold_evidence(question)
    filters = question.get("filter") or {}
    traced = []
    for rank, hit in enumerate(hits, start=1):
        text = squash(hit["text"])
        page_match = evidence_match = is_hit = False
        for gold in golds:
            on_page = (hit["document_id"] == gold["gold_source_id"]
                       and str(hit["page"]) in _pages(gold["gold_page"]))   # đúng nguồn + trang
            has_quote = squash(gold["evidence_quote"]) in text              # chứa trọn evidence
            page_match |= on_page
            evidence_match |= has_quote
            is_hit |= on_page and has_quote      # phải đúng cả hai với CÙNG một gold
        traced.append({
            "rank": rank, "chunk_id": hit["chunk_id"], "score": hit["score"],
            "document_id": hit["document_id"], "source": hit["source"], "page": hit["page"],
            "metadata": hit.get("metadata") or {}, "text": hit["text"],
            "source_page_match": page_match, "evidence_match": evidence_match, "is_hit": is_hit,
            "filter_match": _matches_filter(hit, filters) if filters else None,
        })

    top3 = traced[:3]
    first_hit = next((hit["rank"] for hit in top3 if hit["is_hit"]), None)   # hạng của hit đầu tiên
    return {
        "id": question["id"], "split": question["split"], "type": question.get("type"),
        "question": question["question"], "expected_answer": question.get("expected_answer"),
        "filter": filters or None, "gold": golds, "hits": traced,
        "hit_at_1": first_hit == 1, "hit_at_3": first_hit is not None,
        "first_hit_rank": first_hit,
        # Đúng trang trong top-3 nhưng không chunk nào chứa trọn quote: evidence có thể bị cắt đôi.
        "evidence_split_suspected": first_hit is None and any(h["source_page_match"] for h in top3),
        # Chỉ có ý nghĩa với câu có filter; câu không có filter để None (N/A).
        "filter_compliant_top3": all(h["filter_match"] for h in top3) if filters else None,
        # Hai trường dành cho người chấm điền tay.
        "manual_grounding_0_2": None, "manual_note": None,
    }


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Tổng hợp Hit@1 và Evidence Hit@3 theo strategy và tập dev/test.

    Giữ cả tử số và mẫu số (không chỉ %), kèm số câu nghi bị cắt evidence và
    số câu có filter mà top-3 tuân thủ filter.
    """
    summary: dict[str, Any] = {}
    for trace in traces:
        cell = summary.setdefault(trace.get("strategy", "unknown"), {}).setdefault(
            trace["split"], {"questions": 0, "hit_at_1": 0, "hit_at_3": 0,
                             "evidence_split_suspected": 0,
                             "filter_questions": 0, "filter_compliant_top3": 0})
        cell["questions"] += 1
        cell["hit_at_1"] += bool(trace["hit_at_1"])
        cell["hit_at_3"] += bool(trace["hit_at_3"])
        cell["evidence_split_suspected"] += bool(trace.get("evidence_split_suspected"))
        if trace.get("filter"):
            cell["filter_questions"] += 1
            cell["filter_compliant_top3"] += bool(trace.get("filter_compliant_top3"))
    for splits in summary.values():
        for cell in splits.values():             # tỷ lệ = số câu hit / tổng số câu
            cell["hit_at_1_rate"] = round(cell["hit_at_1"] / cell["questions"], 4)
            cell["hit_at_3_rate"] = round(cell["hit_at_3"] / cell["questions"], 4)
    return summary
