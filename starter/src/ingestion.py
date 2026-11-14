"""Đọc và chuẩn hóa dữ liệu nguồn (PDF -> danh sách trang văn bản)."""
import re
import unicodedata
from pathlib import Path

from pypdf import PdfReader

from .models import DocumentPage

# Các ký tự "vô hình" (soft hyphen, zero-width space, BOM...) mà PDF hay để lại;
# nếu không xóa, phép so khớp chuỗi evidence sẽ thất bại dù mắt thường thấy giống nhau.
_INVISIBLE = dict.fromkeys(map(ord, "­​‌‍⁠﻿"))


def count_pdf_pages(path: Path) -> int:
    """Đếm số trang vật lý của PDF (không trích xuất text)."""
    return len(PdfReader(str(path)).pages)


def load_pdf(path: Path, document_id: str, source: str,
             pages: set[int] | None = None) -> list[DocumentPage]:
    """Trích xuất từng trang vật lý của PDF; giữ số trang (bắt đầu từ 1) và nguồn.

    ``pages``: nếu truyền vào, chỉ trích xuất các số trang này (dùng khi corpus
    chỉ là một phần của cuốn sách lớn); số trang vẫn là số trang của PDF gốc.

    Trả về TẤT CẢ các trang được yêu cầu, kể cả:
    - trang không có text (``metadata["empty"]`` = True, thường là trang scan cần OCR);
    - trang trích xuất lỗi (``metadata["extraction_error"]`` chứa thông báo lỗi).
    Nhờ vậy nơi gọi có thể ghi log đầy đủ. Hàm này không làm OCR.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"PDF not found: {path}")

    reader = PdfReader(str(path))
    result: list[DocumentPage] = []
    for number, pdf_page in enumerate(reader.pages, start=1):
        if pages is not None and number not in pages:
            continue                         # ngoài phạm vi corpus: bỏ qua, không trích xuất
        metadata: dict = {}
        try:
            raw = pdf_page.extract_text() or ""
        except Exception as error:  # một trang hỏng không được làm mất các trang còn lại
            raw = ""
            metadata["extraction_error"] = f"{type(error).__name__}: {error}"
        text = normalize_text(raw)
        metadata["char_count"] = len(text)   # số ký tự sau chuẩn hóa (dùng cho manifest)
        metadata["empty"] = not text         # đánh dấu trang rỗng
        result.append(DocumentPage(document_id=document_id, source=source,
                                   page=number, text=text, metadata=metadata))
    return result


def normalize_text(text: str) -> str:
    """Chuẩn hóa Unicode và khoảng trắng, nhưng vẫn giữ xuống dòng và ngắt đoạn.

    Dùng NFC để dấu tiếng Việt luôn ở dạng ký tự dựng sẵn: cùng một từ sẽ có
    cùng mã ký tự trong chunk, câu hỏi và evidence quote.
    """
    text = unicodedata.normalize("NFC", text)
    text = text.translate(_INVISIBLE)                                  # xóa ký tự vô hình
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n\n")
    text = re.sub(r"[^\S\n]+", " ", text)       # tab, NBSP, nhiều dấu cách -> một dấu cách
    text = re.sub(r" ?\n ?", "\n", text)        # bỏ dấu cách thừa quanh chỗ xuống dòng
    text = re.sub(r"\n{3,}", "\n\n", text)      # giữ tối đa một dòng trống (ranh giới đoạn)
    return text.strip()
