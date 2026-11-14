# Báo cáo cá nhân — Day 6

Học viên: Nguyễn Văn Anh Trung — MSSV 2A202603903 — Lớp L3B

## Dữ liệu và pipeline

- **Chủ đề, nguồn/quyền, số trang và quy tắc tính trang:** 
Phần 2 - Unit 1 *Collecting and Preparing Data* của giáo trình OpenStax *Principles of Data Science* (2025, bản PDF "WEB", ISBN 978-1-961584-60-0), tải từ https://openstax.org/details/books/principles-data-science ngày 2026-11-13. Giấy phép CC BY-NC-SA 4.0: chỉ dùng phi thương mại, giữ ghi công. PDF có 569 trang vật lý. Chỉ tính phần được nói đến(Introduction và các mục 2.1–2.5), tức **PDF trang 67–103 (37 trang nội dung)**. Chi tiết nằm ở [data/manifest.json](../../data/manifest.json).
- **Chuẩn hóa, lỗi và giới hạn:** Trích xuất từng trang bằng `pypdf` 6.21.4, rồi chuẩn hóa ([starter/src/ingestion.py](../../starter/src/ingestion.py)): Unicode NFC, xóa ký tự vô hình (soft hyphen, zero-width, BOM), gộp khoảng trắng nhưng giữ xuống dòng. Mỗi trang lỗi hoặc rỗng được ghi riêng. Kết quả [results/ingestion_log.json](../../results/ingestion_log.json): 37/37 trang có text, **0 trang rỗng, 0 lỗi trích xuất**, không cần OCR. 
Các giới hạn gặp phải trong quá trình thực hiện: 
  1. Bảng bị trải phẳng thành dòng văn bản (Table 2.4, 2.8)  
  2. Công thức normalization trên trang 91 là hình nên không trích xuất được 
  3. Các header/footer như "Access for free at openstax.org" lẫn vào text; 
  4. `pypdf` không tạo dòng trống giữa các đoạn (0 ngắt đoạn trong toàn corpus), ảnh hưởng tới chiến lược B (xem phần Phân tích).
- **Embedding, vector store, metric, lệnh chạy và chi phí:**
  - Embedding: OpenRouter → `openai/text-embedding-3-small`, 1536 chiều. Tài liệu và câu hỏi dùng cùng model, không thêm tiền tố. Gọi HTTP bằng thư viện chuẩn, không có fallback sang vector giả; lỗi provider được báo ra ([starter/src/embeddings.py](../../starter/src/embeddings.py)).
  - Vector store: PostgreSQL 16.16 + pgvector 0.8.7 (Docker image `pgvector/pgvector:0.8.7-pg16`), bảng `chunks` với khóa `(strategy, chunk_id)`. Metric cosine (`<=>`), **tìm kiếm chính xác**. Filter metadata chạy trong SQL ([starter/src/store.py](../../starter/src/store.py)).
  - Phiên bản: Python 3.14.7, psycopg 3.3.8, pypdf 6.21.4.
  - Lệnh chạy (từ `starter/`): `docker compose -p day6_student_starter up -d --wait`, `python scripts/check_setup.py`, rồi `python -m src.run`.
  - Chi phí lần chạy chốt(14/11/2026 12:23 GMT+7): tổng 51.627 token, **≈ 0,001 USD** (index A 0.000567 USD, index B 0.00046 USD, phần còn lại là embedding câu hỏi).
- **Metadata bắt buộc, trường lọc bổ sung, nguồn giá trị và lý do chọn:**
  - Mỗi chunk có `document_id` (`openstax-pds-ch02`), `source` (tên file PDF), `page` (số trang PDF vật lý, int), `char_start`/`char_end` (vị trí trong trang), `chunking` và `chunk_index`.
  - Metadata của tài liệu (lấy từ trang bản quyền và mục lục): `title`, `book`, `publisher`, `year` (int, 2025), `language` (`en`), `document_type` (`textbook`), `unit` (int), `chapter` (int).
  - **Trường lọc: `section`** (str: `introduction`, `2.1`–`2.5`) cùng `section_title`, lấy từ mục lục/bookmark của PDF và gán theo khoảng trang trong manifest. Lý do chọn: một chương có 5 mục với chủ đề tách biệt (thu thập dữ liệu, khảo sát, web scraping, làm sạch, dữ liệu lớn), nên người dùng có thể giới hạn phạm vi theo mục đang học.
  - Các trường `year`, `language`, `chapter` có cùng một giá trị cho mọi chunk nên chỉ dùng để truy vết, không có ích cho lọc trong corpus này.

## Cấu hình và benchmark

- Chiến lược **A / B / C:**
  - **A:** `fixed_size(size=500, overlap=100)`. Cắt cửa sổ ký tự trong từng trang. 291 chunk.
  - **B:** `by_paragraph(max_size=1500)`. Gom các đơn vị liền nhau (đoạn; đoạn quá dài thì tách theo câu) thành chunk ≤ 1500 ký tự, ranh giới luôn ở cuối câu, không overlap. 99 chunk.
  - **C:** dùng **chính index B** (không embed lại) và thêm `WHERE metadata->>'section' = …`, chỉ áp cho câu có `filter`. **Khác biệt duy nhất B–C là filter**; A–B khác chunking.
  - Cả ba dùng cùng corpus, model, store, metric cosine và top-k = 3.
- **Câu hỏi:** 20 câu hỏi trong [benchmark/benchmark.json](../../benchmark/benchmark.json), gồm **15 câu dev (q01–q15) / 5 câu test (t01–t05)**.
  - Loại câu: trực tiếp (q01–q05, t01, t02), diễn đạt lại (q06–q09, t03), điều kiện/ngoại lệ (q10–q12, t04), metadata filter (q13 section 2.3, q14 2.5, q15 2.4, **t05 2.5 thuộc test**).
  - Kiểm chứng: `load_benchmark` trong [run.py](../../starter/src/run.py) kiểm tra mọi `evidence_quote` thực sự nằm trên trang gold sau chuẩn hóa (so sánh không phân biệt hoa/thường và khoảng trắng), và mọi giá trị filter có tồn tại trong corpus. Lần chạy chốt báo **0 vấn đề**.
  - Câu có filter tránh đặt evidence ở đầu các trang 68, 71, 76, 98 (đoạn cuối mục trước mang nhãn mục sau).
  - Nguy cơ rò rỉ: corpus chỉ có một tài liệu, nên dev và test cùng chủ đề và có câu gần nhau (t01 sampling error và t04 volunteer sampling nằm cùng trang 74 với q05/q10).
- **Tiêu chí chọn chiến lược:** *Không được ghi thành văn bản trước khi xem test.* Tiêu chí dùng trong báo cáo này: ưu tiên (1) Evidence Hit@3 trên dev, (2) coherence, (3) grounding, (4) Hit@1, (5) tài nguyên (số chunk, token, thời gian).

## Kết quả

Lần chạy chốt: A = 500/100, B/C = 1500. Thời gian là thời gian chunk + embed + ghi vào DB của mỗi index; C không ingest riêng mà dùng index B.

| Strategy | Split | Hit@1 (n/N) | Evidence Hit@3 (n/N) | Chunk count | Thời gian | Chi phí |
|---|---|---|---|---|---|---|
| A | dev | 10/15 | 13/15 | 291 | 16,31 s | $0.000567 (28.373 token) |
| A | test | 4/5 | 5/5 | 291 | (chung) | (chung) |
| B | dev | 8/15 | 14/15 | 99 | 6,63 s | $0.00046 (22.979 token) |
| B | test | 4/5 | 5/5 | 99 | (chung) | (chung) |
| C | dev | 8/15 | 14/15 | 99 (dùng index B) | 0 s ingest thêm | $0 thêm |
| C | test | 4/5 | 5/5 | 99 (dùng index B) | 0 s ingest thêm | $0 thêm |

| Strategy | Coherence trung bình /2 (n) | Metadata đủ và đúng (n/N) | Filter tuân thủ (n/N hoặc N/A) | Grounding 0/1/2 (số câu) và trung bình /2 |
|---|---|---|---|---|
| A | 0,9 (n=10) | 15/15 | Không áp filter. Đo tham khảo: test 1/1, dev 3/3 | 0/0/5, trung bình 2,0 |
| B | 1,6 (n=10) | 14/15 | Không áp filter. Đo tham khảo: test 1/1, dev 3/3 | 0/0/5, trung bình 2,0 |
| C | 1,6 (n=10, cùng chunk với B) | 14/15 | **test 1/1, dev 3/3** | 0/0/5, trung bình 2,0 |



**Đường dẫn trace, nhãn thủ công và cách chọn mẫu:**
- Trace top-3 (câu hỏi, chunk_id, text, score, metadata, gold, is_hit) của cả ba chiến lược: [results/traces.jsonl](../../results/traces.jsonl). Tổng hợp: [results/summary.json](../../results/summary.json).
- Nhãn metadata và grounding cho 5 câu test × top-3 × 3 chiến lược: [results/manual_review.csv](../../results/manual_review.csv). Grounding được chấm theo câu, xét gộp cả top-3.
- Nhãn coherence: [results/coherence_samples.csv](../../results/coherence_samples.csv). Mẫu được chọn bằng code trước khi chấm: **điểm giữa của 10 trang đầu (PDF 67–76)**; với mỗi index lấy chunk chứa vị trí đó.
- **Người chấm và thiên lệch:** nhãn thủ công do trợ lý AI (Claude) đề xuất theo đúng thang 0–2 của GUIDE, dựa trên đọc text từng chunk; mỗi nhãn kèm lý do và được đánh dấu "[AI đề xuất]". Tác giả (người viết benchmark) rà soát lại. Vì người đặt câu hỏi cũng là người chấm, nhãn có thể thiên lệch về phía dễ.

## Phân tích và kết luận

- **So sánh A–B (chỉ đổi chunking):**
  - B tốt hơn A ở Evidence Hit@3 trên dev (14/15 so với 13/15; lần chạy đầu 15/15 so với 11/15), ở coherence (1,6 so với 0,9) và ở tài nguyên (99 so với 291 chunk, ít token hơn khoảng 19%, ingest nhanh hơn khoảng 2,5 lần).
  - A tốt hơn ở Hit@1 trên dev (10/15 so với 8/15). Chunk 500 ký tự tập trung một ý nên đoạn đúng dễ đứng hạng 1; chunk ~1400 ký tự của B trộn nhiều ý nên điểm cosine bị pha loãng.
  - Trên test hai chiến lược hòa nhau (4/5, 5/5): N = 5 quá nhỏ để phân biệt.
  - Coherence của A thấp vì gần như mọi chunk bắt đầu hoặc kết thúc giữa từ.
- **So sánh B–C (chỉ thêm filter):** kết quả **giống hệt** nhau. Với cả 4 câu có filter, B không lọc đã trả về top-3 nằm trọn trong đúng mục, nên filter không loại được chunk nào. Đây không phải bằng chứng filter vô dụng: các câu metadata của benchmark có từ khóa đặc trưng (read_html, chunking, MCAR, hash), dễ tách bằng ngữ nghĩa. Muốn thấy tác dụng của filter cần câu có nội dung lặp ở nhiều mục, ví dụ "storage" có ở 2.3 và 2.5, "normalization" có ở 2.4.
- **Ví dụ lỗi:**
  1. **A, q02 (dev, gold PDF trang 68) — evidence bị cắt thật.** Evidence dài 189 ký tự, lớn hơn overlap 100, nên không chunk 500/100 nào chứa trọn. Chunk `openstax-pds-ch02:p68:fixed500-100:6` (hạng 3) dừng ở "…between individuals, b". Top-1 và top-2 là trang 69 (cách transactional data được thu thập), trả lời được một phần nhưng không chứa định nghĩa gold. B tìm được ở hạng 1.
  2. **B/C, q08 (dev, gold trang 93) — chunk bị pha loãng, không phải bị cắt.** Evidence ("By calculating a seven-day average…") nằm trọn trong chunk `p93:para1500:0` (1422 ký tự). Chunk này gộp phần mở đầu về noisy data, bảng chiến lược, data cleaning, smoothing và imputation, nên không vào top-3. Top-1 là `p93:para1500:1` (binning/transformation), score chỉ 0,349. Cờ tự động `evidence_split_suspected` báo đây là "cắt evidence", nhưng thực tế là dương tính giả. Ở lần chạy đầu (B = 800), chunk nhỏ hơn nên câu này hit được.
  3. **Lỗi metadata, B/C, t01 hạng 3:** chunk `p76:para1500:0` gắn `section=2.3` nhưng nội dung là phần cuối mục 2.2 (lời giải Example 2.3 về stratified sampling). Nguyên nhân: metadata gắn theo trang, và trang 76 chứa ranh giới giữa hai mục.
  - Tương tự q08, A/q15 có evidence MCAR nằm trọn trong `p87:fixed500-100:1` nhưng chunk này bị xếp dưới các chunk nói chung về missing data.
- **Metadata hữu ích ở đâu, hạn chế gì:**
  - `page` và `char_start`/`char_end` giúp truy vết chính xác: cả 45 bản ghi test kiểm tra đều đúng trang, và việc kiểm chứng evidence tự động cũng dựa vào chúng.
  - `section` giúp giới hạn phạm vi khi người dùng biết mình đang học mục nào.
  - Hạn chế: gán theo trang nên sai ở 4 trang ranh giới (68, 71, 76, 98), và chunk A có thể vắt qua hai mục (ví dụ `p71:fixed500-100:4`). Cải tiến: gán section theo vị trí ký tự của tiêu đề mục thay vì theo trang.
- **Chiến lược chọn, đánh đổi, điều kiện áp dụng:** chọn **C** (chunk theo câu/đoạn ≤ 1500 + filter `section` khi người dùng cung cấp mục).
  - Lý do: theo tiêu chí ưu tiên, B/C đứng đầu Hit@3 dev và coherence, tốn ít tài nguyên nhất, và filter không làm xấu kết quả trong khi đảm bảo phạm vi.
  - Đánh đổi: Hit@1 thấp hơn A (8 so với 10/15), và chunk lớn đưa nhiều text thừa vào ngữ cảnh.
  - Nếu ứng dụng chỉ lấy top-1, A (hoặc B với max_size nhỏ hơn) có thể phù hợp hơn.
  - Kết luận chỉ áp dụng cho corpus một chương sách giáo khoa tiếng Anh này.
- **Hạn chế của dữ liệu, nhãn, test nhỏ, đo thời gian/chi phí:**
  - **Tham số đã đổi sau khi thấy kết quả lần chạy đầu, gồm cả test** (A overlap 50 → 100; B max_size 800 → 1500), nên **test không còn là holdout độc lập**. Cả hai lần chạy đều được báo cáo ở trên.
  - Test chỉ có 5 câu: một câu đổi kết quả là thay đổi 20%. Grounding đạt mức trần (5/5 điểm 2 ở cả ba chiến lược), nên không phân biệt được các chiến lược; bộ test quá dễ.
  - Corpus chỉ có một tài liệu và một chương, nên các trường `year`/`chapter` không thể dùng để lọc.
  - Chiến lược B thực chất là "gom câu" vì PDF không có dòng trống giữa các đoạn.
  - Nhãn thủ công chỉ do một người chấm (có AI đề xuất), không có người chấm thứ hai để đo độ đồng thuận.
  - Thời gian ingest chỉ đo một lần trên máy cá nhân và gồm cả độ trễ mạng của API. Chi phí lấy từ trường `usage.cost` mà OpenRouter trả về.
- **Nếu làm lại hoặc mở rộng:**
  - Chốt tiêu chí chọn và tham số bằng văn bản trước lần chạy đầu, rồi chỉ tinh chỉnh trên dev.
  - Tách đoạn thật bằng tọa độ dòng (khoảng cách dọc giữa các dòng) hoặc theo tiêu đề con (Sampling Error, Data Indexing…) thay cho dòng trống.
  - Gán `section` theo vị trí ký tự.
  - Thêm câu hỏi nhập nhằng giữa các mục để kiểm tra filter, và câu test khó hơn (cần hợp nhiều chunk, có điều kiện/ngoại lệ).
  - Thử hybrid BM25 + dense cho các câu nhiều từ khóa.
  - Mở rộng corpus sang nhiều chương để `chapter` trở thành trường lọc có ý nghĩa.

**Công cụ AI:** dùng Claude (Claude Code) để hỗ trợ viết/sửa pipeline, đề xuất câu hỏi benchmark (evidence đã kiểm chứng tự động trên PDF), đề xuất nhãn thủ công kèm lý do và soạn báo cáo. Mọi số liệu trong báo cáo lấy trực tiếp từ `results/summary.json` và `results/traces.jsonl` của lần chạy thật.
