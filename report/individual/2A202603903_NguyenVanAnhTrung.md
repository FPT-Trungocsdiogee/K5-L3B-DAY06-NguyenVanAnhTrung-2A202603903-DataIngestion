# Báo cáo cá nhân — Day 6

Học viên: Nguyễn Văn Anh Trung — MSSV 2A202603903 — Lớp L3B

## Dữ liệu và pipeline

- **Chủ đề, nguồn/quyền, số trang và quy tắc tính trang:** 
Phần 2 - Unit 1 *Collecting and Preparing Data* của giáo trình OpenStax *Principles of Data Science* (2025, bản PDF "WEB", ISBN 978-1-961584-60-0), tải từ https://openstax.org/details/books/principles-data-science ngày 2026-11-13. Giấy phép CC BY-NC-SA 4.0: chỉ dùng phi thương mại, giữ ghi công. PDF có 569 trang vật lý. Chỉ tính phần được nói đến(Introduction và các mục 2.1–2.5), tức **PDF trang 67–103 (37 trang nội dung)**. Chi tiết nằm ở [data/manifest.json](../../data/manifest.json).
- **Chuẩn hóa, lỗi và giới hạn:** Trích xuất từng trang bằng `pypdf` 6.21.4, rồi chuẩn hóa ([starter/src/ingestion.py](../../starter/src/ingestion.py)): Unicode NFC, xóa ký tự vô hình (soft hyphen, zero-width, BOM), gộp khoảng trắng nhưng giữ xuống dòng. Mỗi trang lỗi hoặc rỗng được ghi riêng. Kết quả [results/ingestion_log.json](../../results/ingestion_log.json): 37/37 trang có text, **0 trang rỗng, 0 lỗi trích xuất**, không cần OCR. 
Các giới hạn gặp phải trong quá trình thực hiện: 
  1. Bảng bị trải phẳng thành dòng văn bản (Table 2.4, 2.8); Table 2.8 còn trải trên 3 trang (95–97) nên không chunk nào chứa trọn bảng (xem t06).
  2. Công thức normalization trên trang 91 là hình nên không trích xuất được 
  3. Các header/footer như "Access for free at openstax.org" lẫn vào text; 
  4. `pypdf` không tạo dòng trống giữa các đoạn (0 ngắt đoạn trong toàn corpus), ảnh hưởng tới chiến lược B (xem phần Phân tích).
- **Embedding, vector store, metric, lệnh chạy và chi phí:**
  - Embedding: OpenRouter → `openai/text-embedding-3-small`, 1536 chiều. Tài liệu và câu hỏi dùng cùng model, không thêm tiền tố. Gọi HTTP bằng thư viện chuẩn, không có fallback sang vector giả; lỗi provider được báo ra ([starter/src/embeddings.py](../../starter/src/embeddings.py)).
  - Vector store: PostgreSQL 16.16 + pgvector 0.8.7 (Docker image `pgvector/pgvector:0.8.7-pg16`), bảng `chunks` với khóa `(strategy, chunk_id)`. Metric cosine (`<=>`), **tìm kiếm chính xác**. Filter metadata chạy trong SQL ([starter/src/store.py](../../starter/src/store.py)).
  - Phiên bản: Python 3.14.7, psycopg 3.3.8, pypdf 6.21.4.
  - Lệnh chạy (từ `starter/`): `docker compose -p day6_student_starter up -d --wait`, `python scripts/check_setup.py`, rồi `python -m src.run`.
  - Chi phí lần chạy chốt (14/11/2026 22:04 GMT+7): tổng 53.569 token, **≈ 0,0011 USD** (index A 0.000606 USD, index B 0.00046 USD, phần còn lại là embedding câu hỏi).
- **Metadata bắt buộc, trường lọc bổ sung, nguồn giá trị và lý do chọn:**
  - Mỗi chunk có `document_id` (`openstax-pds-ch02`), `source` (tên file PDF), `page` (số trang PDF vật lý, int), `char_start`/`char_end` (vị trí trong trang), `chunking` và `chunk_index`.
  - Metadata của tài liệu (lấy từ trang bản quyền và mục lục): `title`, `book`, `publisher`, `year` (int, 2025), `language` (`en`), `document_type` (`textbook`), `unit` (int), `chapter` (int).
  - **Trường lọc: `section`** (str: `introduction`, `2.1`–`2.5`) cùng `section_title`, lấy từ mục lục/bookmark của PDF và gán theo khoảng trang trong manifest. Lý do chọn: một chương có 5 mục với chủ đề tách biệt (thu thập dữ liệu, khảo sát, web scraping, làm sạch, dữ liệu lớn), nên người dùng có thể giới hạn phạm vi theo mục đang học.
  - Các trường `year`, `language`, `chapter` có cùng một giá trị cho mọi chunk nên chỉ dùng để truy vết, không có ích cho lọc trong corpus này.

## Cấu hình và benchmark

- Chiến lược **A / B / C:**
  - **A:** `fixed_size(size=400, overlap=100)`. Cắt cửa sổ ký tự trong từng trang. 381 chunk.
  - **B:** `by_paragraph(max_size=600)`. Gom các đơn vị liền nhau (đoạn; đoạn quá dài thì tách theo câu) thành chunk ≤ 600 ký tự, ranh giới luôn ở cuối câu, không overlap. 235 chunk.
  - **C:** dùng **chính index B** (không embed lại) và thêm `WHERE metadata->>'section' = …`, chỉ áp cho câu có `filter`. **Khác biệt duy nhất B–C là filter**; A–B khác chunking.
  - Cả ba dùng cùng corpus, model, store và metric cosine. Mỗi câu lấy **top-k = 4** chunk (`TOP_K` trong [run.py](../../starter/src/run.py)) để ghi trace và chấm tay, nhưng **Hit@1/Hit@3 vẫn chỉ tính trên 3 hạng đầu** ([evaluation.py](../../starter/src/evaluation.py)).
- **Câu hỏi:** 21 câu hỏi trong [benchmark/benchmark.json](../../benchmark/benchmark.json), gồm **15 câu dev (q01–q15) / 6 câu test (t01–t06)**.
  - Loại câu: trực tiếp (q01–q05, t01, t02), diễn đạt lại (q06–q09, t03), điều kiện/ngoại lệ (q10–q12, t04), metadata filter (q13 section 2.3, q14 2.5, q15 2.4, **t05 2.5 và t06 2.4 thuộc test**).
  - **Câu mới t06** ("How many common text-processing mentioned in the table?", đáp án 10, filter `section=2.4`): câu đếm/tổng hợp, gold là danh sách trang 95–97 vì Table 2.8 trải trên ba trang. Tôi đã đếm lại trên PDF: p95 có 4 kỹ thuật (Tokenization, Lowercasing, Stopwords removal, Lemmatization and stemming), p96 có 3, p97 có 3, nên đáp án 10 là đúng. `evidence_quote` chỉ là câu dẫn "Some common techniques used in text preprocessing are reviewed in Table 2.8.", nên hit tự động chỉ chứng minh retrieval tìm được chỗ bắt đầu bảng, không chứng minh đủ bằng chứng để trả lời.
  - Kiểm chứng: `load_benchmark` trong [run.py](../../starter/src/run.py) kiểm tra mọi `evidence_quote` thực sự nằm trên (một trong các) trang gold sau chuẩn hóa (so sánh không phân biệt hoa/thường và khoảng trắng), và mọi giá trị filter có tồn tại trong corpus. Lần chạy chốt báo **0 vấn đề**.
  - Câu có filter tránh đặt evidence ở đầu các trang 68, 71, 76, 98 (đoạn cuối mục trước mang nhãn mục sau).
  - Nguy cơ rò rỉ: corpus chỉ có một tài liệu, nên dev và test cùng chủ đề và có câu gần nhau (t01 sampling error và t04 volunteer sampling ở trang 74, cùng vùng trang 72–74 với q05/q10; t06 là câu đếm giống dạng q05).
- **Tiêu chí chọn chiến lược:** *Không được ghi thành văn bản trước khi xem test.* Tiêu chí dùng trong báo cáo này: ưu tiên (1) Evidence Hit@3 trên dev, (2) coherence, (3) grounding, (4) Hit@1, (5) tài nguyên (số chunk, token, thời gian).

## Kết quả

Lần chạy chốt: A = 400/100, B/C = 600, top-k = 4. Thời gian là thời gian chunk + embed + ghi vào DB của mỗi index; C không ingest riêng mà dùng index B.

| Strategy | Split | Hit@1 (n/N) | Evidence Hit@3 (n/N) | Chunk count | Thời gian | Chi phí |
|---|---|---|---|---|---|---|
| A | dev | 7/15 | 10/15 | 381 | 21,47 s | $0.000606 (30.290 token) |
| A | test | 5/6 | 6/6 | 381 | (chung) | (chung) |
| B | dev | 10/15 | 13/15 | 235 | 13,13 s | $0.00046 (22.994 token) |
| B | test | 4/6 | 6/6 | 235 | (chung) | (chung) |
| C | dev | 10/15 | 13/15 | 235 (dùng index B) | 0 s ingest thêm | $0 thêm |
| C | test | 4/6 | 6/6 | 235 (dùng index B) | 0 s ingest thêm | $0 thêm |

| Strategy | Coherence trung bình /2 (n) | Metadata đủ và đúng (n/N) | Filter tuân thủ top-3 (n/N hoặc N/A) | Grounding 0/1/2 (số câu) và trung bình /2 |
|---|---|---|---|---|
| A | 1,0 (n=10) | 24/24 | Không áp filter. Đo tham khảo: test 2/2, dev 3/3 | 0/2/4, trung bình 1,67 |
| B | 1,7 (n=10) | 23/24 | Không áp filter. Đo tham khảo: test 2/2, dev 3/3 | 0/1/5, trung bình 1,83 |
| C | 1,7 (n=10, cùng chunk với B) | 24/24 | **test 2/2, dev 3/3** | 0/1/5, trung bình 1,83 |

Metadata được chấm trên 6 câu test × top-4 = 24 bản ghi mỗi chiến lược. Grounding chấm theo câu, xét gộp top-3 như GUIDE yêu cầu (hạng 4 chỉ ghi để tham khảo).

**Đường dẫn trace, nhãn thủ công và cách chọn mẫu:**
- Trace top-4 (câu hỏi, chunk_id, text, score, metadata, gold, is_hit) của cả ba chiến lược: [results/traces.jsonl](../../results/traces.jsonl). Tổng hợp: [results/summary.json](../../results/summary.json).
- Nhãn metadata và grounding cho 6 câu test × top-4 × 3 chiến lược (72 dòng): [results/manual_review.csv](../../results/manual_review.csv).
- Nhãn coherence: [results/coherence_samples.csv](../../results/coherence_samples.csv). Mẫu được chọn bằng code trước khi chấm: **điểm giữa của 10 trang đầu (PDF 67–76)**; với mỗi index lấy chunk chứa vị trí đó.
- **Người chấm và thiên lệch:** nhãn thủ công do trợ lý AI (Claude) đề xuất theo đúng thang 0–2 của GUIDE, dựa trên đọc text từng chunk; mỗi nhãn kèm lý do và được đánh dấu "[AI đề xuất]". Tác giả (người viết benchmark) rà soát lại. Vì người đặt câu hỏi cũng là người chấm, nhãn có thể thiên lệch về phía dễ. Nhãn được chấm lại từ đầu cho lần chạy này vì chunk đã thay đổi.

## Phân tích và kết luận

- **So sánh A–B (chỉ đổi chunking):**
  - B tốt hơn A ở gần như mọi chỉ số dev: Evidence Hit@3 13/15 so với 10/15, Hit@1 10/15 so với 7/15. B cũng tốt hơn ở coherence (1,7 so với 1,0), grounding (1,83 so với 1,67) và tài nguyên (235 so với 381 chunk, ít token hơn khoảng 24%, ingest nhanh hơn khoảng 1,6 lần).
  - A chỉ hơn ở Hit@1 trên test (5/6 so với 4/6). Với N = 6, chênh lệch đó là một câu: A trượt hạng 1 ở t03, B trượt ở t02 và t04. Ở hai câu này B đưa một chunk liền kề cùng chủ đề lên hạng 1 (nguyên nhân measurement error; định nghĩa volunteer sampling), còn chunk gold đứng hạng 2.
  - Cả 5 câu dev A trượt đều bị cờ `evidence_split_suspected` (đúng trang, sai chunk). Với cửa sổ 400/100, mọi evidence dài hơn 100 ký tự đều có thể bị cắt đôi.
  - Coherence của A là 1 ở cả 10 mẫu, vì mọi chunk bắt đầu hoặc kết thúc giữa từ/câu.
  - So với lần chạy trước (A 500/100, B 1500), giảm B xuống 600 đã đảo ngược Hit@1 dev (8 → 10/15, giờ cao hơn A), đúng như giả thuyết "chunk lớn bị pha loãng": q08 (lỗi ví dụ của lần trước) giờ hit ở hạng 1.
- **So sánh B–C (chỉ thêm filter):** các chỉ số top-3 **giống hệt** nhau. Với cả 5 câu có filter, B không lọc đã trả về top-3 nằm trọn trong đúng mục, nên filter không đổi kết quả chấm điểm. Khác biệt đầu tiên quan sát được nằm ở **hạng 4 của t06**: B trả về `p98:para600:0` (section=2.5), C loại chunk này và thay bằng `p97:para600:5` (2.4). Tuy vậy chunk bị loại thực ra có nội dung chính thuộc 2.4 (đoạn chuẩn hóa text nối tiếp từ p97), chỉ bị gắn 2.5 vì metadata gán theo trang. Nghĩa là filter đúng theo nhãn nhưng chỉ tốt bằng chính nhãn. Các câu metadata của benchmark có từ khóa đặc trưng (read_html, chunking, MCAR, hash, Table 2.8), dễ tách bằng ngữ nghĩa. Muốn thấy tác dụng của filter cần câu có nội dung lặp ở nhiều mục, ví dụ "storage" có ở 2.3 và 2.5, "normalization" có ở 2.4.
- **Ví dụ lỗi:**
  1. **A, q04 (dev, gold PDF trang 72) — evidence bị cắt thật.** Evidence về closed-ended questions dài 212 ký tự, lớn hơn overlap 100. Chunk hạng 1 `p72:fixed400-100:0` chứa 185 ký tự đầu của evidence rồi bị cắt. B tìm được ở hạng 1.
  2. **A, q14 (dev, gold trang 100, có filter 2.5) — evidence bị chia đôi giữa hai chunk đều nằm trong top-3.** Hạng 1 (`p100:fixed400-100:5`) chứa 105 ký tự đầu, hạng 3 (`p100:fixed400-100:6`) chứa 125 ký tự cuối của câu 133 ký tự về fault tolerance. Người đọc gộp hai chunk vẫn trả lời được, nhưng Hit@3 tự động tính là trượt. B hit ở hạng 1.
  3. **A và B, q05 (dev) và t06 (test) — câu đếm.** Cả hai hỏi "có bao nhiêu kỹ thuật". q05 trượt ở cả A và B: top-3 là các đoạn nói chung về sampling hoặc phần tóm tắt ở trang 98, không có câu dẫn. t06 thì "hit" (câu dẫn tới Table 2.8 đứng hạng 1), nhưng bảng 10 dòng trải trên p95–97 và bị chia thành nhiều chunk, nên không chiến lược nào đưa đủ bằng chứng để đếm ra 10 (grounding = 1 ở cả ba). Dense retrieval top-k nhỏ không hợp với câu cần tổng hợp toàn bộ bảng.
  4. **Lỗi metadata, B, t06 hạng 4:** chunk `p98:para600:0` gắn `section=2.5` nhưng phần nội dung chính là đoạn "Normalizing text data…" của mục 2.4; chỉ phần sau là tiêu đề 2.5. Nguyên nhân: metadata gắn theo trang, và trang 98 chứa ranh giới giữa hai mục.
  - Ngoài ra, q15 (MCAR) trượt ở cả A và B: chunk chứa định nghĩa MCAR bị xếp dưới các chunk nói chung về missing data, dù filter 2.4 đã giới hạn đúng mục.
- **Metadata hữu ích ở đâu, hạn chế gì:**
  - `page` và `char_start`/`char_end` giúp truy vết chính xác: 71/72 bản ghi test kiểm tra đều đúng trang và mục, và việc kiểm chứng evidence tự động (kể cả gold nhiều trang của t06) cũng dựa vào chúng.
  - `section` giúp giới hạn phạm vi khi người dùng biết mình đang học mục nào.
  - Hạn chế: gán theo trang nên sai ở 4 trang ranh giới (68, 71, 76, 98), và chunk A có thể vắt qua hai mục (ví dụ `p76:fixed400-100:5`). Trường hợp t06 cho thấy nhãn sai có thể khiến filter loại cả chunk liên quan. Cải tiến: gán section theo vị trí ký tự của tiêu đề mục thay vì theo trang.
- **Chiến lược chọn, đánh đổi, điều kiện áp dụng:** chọn **C** (chunk theo câu/đoạn ≤ 600 + filter `section` khi người dùng cung cấp mục).
  - Lý do: theo tiêu chí ưu tiên, B/C đứng đầu Hit@3 dev, coherence, grounding và cả Hit@1 dev, đồng thời tốn ít tài nguyên hơn A. Filter không làm xấu top-3 và đảm bảo phạm vi; ở hạng 4 nó loại được chunk khác mục theo nhãn.
  - Đánh đổi: Hit@1 test thấp hơn A (4/6 so với 5/6). Filter phụ thuộc chất lượng nhãn `section` (xem t06 hạng 4). Chunk 600 vẫn không đủ cho câu cần cả bảng.
  - Kết luận chỉ áp dụng cho corpus một chương sách giáo khoa tiếng Anh này.
- **Hạn chế của dữ liệu, nhãn, test nhỏ, đo thời gian/chi phí:**
  - **Tham số đã đổi nhiều lần sau khi thấy kết quả, gồm cả test**: lần 1 A 500/50, B 800; lần 2 A 500/100, B 1500; lần 3 (chốt) A 400/100, B 600, top-k 3 → 4. Câu t06 cũng được thêm vào test sau các lần chạy trước. Vì vậy **test không còn là holdout độc lập**.
  - Test chỉ có 6 câu: một câu đổi kết quả là thay đổi khoảng 17%. Grounding gần mức trần (chỉ t03 ở A và t06 ở cả ba dưới 2).
  - Câu t06 diễn đạt chưa chuẩn ("text-processing" thiếu "techniques", "the table" không nêu tên bảng), và evidence quote chỉ là câu dẫn, nên Hit@3 tự động đánh giá quá cao khả năng trả lời.
  - Corpus chỉ có một tài liệu và một chương, nên các trường `year`/`chapter` không thể dùng để lọc.
  - Chiến lược B thực chất là "gom câu" vì PDF không có dòng trống giữa các đoạn.
  - Nhãn thủ công chỉ do một người chấm (có AI đề xuất), không có người chấm thứ hai để đo độ đồng thuận.
  - Thời gian ingest chỉ đo một lần trên máy cá nhân và gồm cả độ trễ mạng của API. Chi phí lấy từ trường `usage.cost` mà OpenRouter trả về.
- **Nếu làm lại hoặc mở rộng:**
  - Chốt tiêu chí chọn, tham số và bộ test bằng văn bản trước lần chạy đầu, rồi chỉ tinh chỉnh trên dev.
  - Tách đoạn thật bằng tọa độ dòng (khoảng cách dọc giữa các dòng) hoặc theo tiêu đề con (Sampling Error, Data Indexing…) thay cho dòng trống.
  - Chunk riêng cho bảng: gộp cả bảng (kể cả khi trải nhiều trang) thành một chunk, hoặc mỗi dòng một chunk kèm tên bảng, để phục vụ câu đếm như q05/t06.
  - Gán `section` theo vị trí ký tự.
  - Thêm câu hỏi nhập nhằng giữa các mục để kiểm tra filter, và câu test khó hơn (cần hợp nhiều chunk, có điều kiện/ngoại lệ).
  - Thử hybrid BM25 + dense cho các câu nhiều từ khóa.
  - Mở rộng corpus sang nhiều chương để `chapter` trở thành trường lọc có ý nghĩa.

**Công cụ AI:** dùng Claude (Claude Code) để hỗ trợ viết/sửa pipeline, đề xuất câu hỏi benchmark (evidence đã kiểm chứng tự động trên PDF), đề xuất nhãn thủ công kèm lý do, soạn báo cáo. Ngoài ra, nó còn hỗ trợ việc push và pull dữ liệu lên Github. Mọi số liệu trong báo cáo lấy trực tiếp từ `results/summary.json` và `results/traces.jsonl` của lần chạy thật.
