"""Chia chunk theo kích thước cố định và theo cấu trúc đoạn văn.

ID của chunk được tạo từ tài liệu, trang, tham số và vị trí, nên cùng một đầu vào
luôn cho cùng ID (nạp lại dữ liệu không sinh bản ghi trùng).
"""
import re

from .models import Chunk, DocumentPage

# Một đoạn văn: chuỗi ký tự kéo dài đến dòng trống kế tiếp hoặc hết văn bản.
_PARAGRAPH = re.compile(r"\S(?:.*?\S)?(?=\s*\n\s*\n|\s*\Z)", re.S)
# Một câu: kết thúc bằng . ! ? … (theo sau là khoảng trắng) hoặc hết đoạn.
_SENTENCE = re.compile(r"\S(?:.*?\S)??(?:[.!?…]+(?=\s)|(?=\s*\Z))", re.S)


def _make_chunk(page: DocumentPage, label: str, index: int, start: int, end: int) -> Chunk:
    """Tạo một Chunk từ khoảng ký tự [start, end) của trang.

    Sao chép metadata của trang (section, year...) và thêm vị trí trong trang
    (char_start/char_end) để truy vết ngược về nguồn.
    """
    metadata = dict(page.metadata)
    metadata.update(chunking=label, chunk_index=index, char_start=start, char_end=end)
    return Chunk(
        chunk_id=f"{page.document_id}:p{page.page}:{label}:{index}",   # ID ổn định
        document_id=page.document_id,
        source=page.source,
        page=page.page,
        text=page.text[start:end],
        metadata=metadata,
    )


def fixed_size(page: DocumentPage, size: int, overlap: int) -> list[Chunk]:
    """Chiến lược A: cắt văn bản thành các cửa sổ ``size`` ký tự, chồng lấn ``overlap`` ký tự.

    Ví dụ size=500, overlap=50: các chunk bắt đầu ở 0, 450, 900...
    Cách này đơn giản nhưng có thể cắt giữa câu/giữa ý.
    """
    if size <= 0:
        raise ValueError("size must be > 0")
    if not 0 <= overlap < size:
        raise ValueError("overlap must satisfy 0 <= overlap < size")

    text = page.text
    label = f"fixed{size}-{overlap}"
    chunks: list[Chunk] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if text[start:end].strip():              # bỏ cửa sổ chỉ toàn khoảng trắng
            chunks.append(_make_chunk(page, label, len(chunks), start, end))
        if end == len(text):                     # đã tới cuối trang
            break
        start += size - overlap                  # bước nhảy = size - overlap
    return chunks


def _units(text: str, max_size: int) -> list[tuple[int, int]]:
    """Trả về vị trí (start, end) của các đơn vị nhỏ nhất dùng để ghép chunk.

    Mặc định mỗi đơn vị là một đoạn văn. Đoạn dài hơn ``max_size`` được tách
    thành câu; câu vẫn quá dài thì mới cắt cứng theo số ký tự.
    """
    spans: list[tuple[int, int]] = []
    for paragraph in _PARAGRAPH.finditer(text):
        if paragraph.end() - paragraph.start() <= max_size:
            spans.append(paragraph.span())
            continue
        for sentence in _SENTENCE.finditer(text, paragraph.start(), paragraph.end()):
            start, end = sentence.span()
            while end - start > max_size:      # một câu dài hơn giới hạn -> cắt cứng
                spans.append((start, start + max_size))
                start += max_size
            if text[start:end].strip():
                spans.append((start, end))
    return spans


def by_paragraph(page: DocumentPage, max_size: int) -> list[Chunk]:
    """Chiến lược B: gom các đoạn văn liền nhau thành chunk không quá ``max_size`` ký tự.

    Các đoạn được phân cách bằng dòng trống. Ranh giới chunk luôn rơi vào cuối
    đoạn hoặc cuối câu nên chunk giữ trọn ý tốt hơn fixed_size. Không có overlap.
    """
    if max_size <= 0:
        raise ValueError("max_size must be > 0")

    label = f"para{max_size}"
    chunks: list[Chunk] = []
    group_start = group_end = None               # nhóm đơn vị đang gom dở
    for start, end in _units(page.text, max_size):
        # Thêm đơn vị này sẽ vượt giới hạn -> đóng nhóm hiện tại thành một chunk.
        if group_start is not None and end - group_start > max_size:
            chunks.append(_make_chunk(page, label, len(chunks), group_start, group_end))
            group_start = None
        if group_start is None:
            group_start = start
        group_end = end
    if group_start is not None:                  # nhóm cuối cùng còn lại
        chunks.append(_make_chunk(page, label, len(chunks), group_start, group_end))
    return chunks
