# Retrieval Evaluation (`evaluation/`)

Bộ đánh giá gọn nhẹ cho **Retrieval** (`POST /v1/search`), chỉ dùng thư viện chuẩn Python (không cần cài thêm gói ngoài, không dùng dữ liệu hành vi giả lập).

## Triết lý thiết kế (phù hợp đặc thù Bất động sản)

Trong tìm kiếm BĐS, một câu truy vấn như *"căn hộ 2 phòng ngủ dưới 5 tỷ ở Thủ Đức"* có nhiều căn nhà đều phù hợp — **Relevant** nghĩa là căn nhà khớp nhu cầu nêu trong truy vấn và xứng đáng nằm trong danh sách ứng viên (shortlist), không phải "khách chắc chắn mua cả 10 căn".

Bộ đánh giá chia làm **2 lớp rõ ràng**:

1. **Lớp 1 — Tự động & khách quan (`run_retrieval_eval.py`)**:
   - Chạy **150 câu truy vấn tiếng Việt thực tế** trong `queries.json` (đều đã kiểm chứng có **≥ 11 căn khớp thật** trên bộ 3.857 tin) qua `POST /v1/search` hoặc `/api/search`.
   - Kiểm tra trực tiếp trên thuộc tính thật của từng căn trả về:
     - **Exact Constraint Match@5 / @10**: tỷ lệ căn thỏa mãn **đồng thời tất cả** ràng buộc tường minh của query.
     - **Per-constraint Match@10**: tách riêng tỷ lệ đúng `price`, `area`, `bedrooms`, `bathrooms`, `property_type`, `location`, `features`.
     - **Zero-Result Rate**: tỷ lệ query bị trả về 0 kết quả.
     - **Latency (`p50`, `p95`, `mean`)** và tỷ lệ bật `semantic_enabled` / `graph_enabled`.

2. **Lớp 2 — Pooling & Chấm điểm liên quan thang `0 / 1 / 2` (`score_judgments.py`)**:
   - `run_retrieval_eval.py` xuất sẵn file `judgments_template.csv` để nhóm chấm (bằng tay hoặc LLM-as-a-Judge có kiểm tra chéo) theo 3 mức:
     - `0` = Không liên quan / sai ràng buộc quan trọng
     - `1` = Liên quan một phần (khớp phần lớn nhưng lệch nhẹ 1 tiêu chí)
     - `2` = Rất phù hợp (đáng đưa vào shortlist đi xem)
   - `score_judgments.py` tính bảng so sánh nhiều cấu hình (**Ablation Study**: ví dụ `fts_only` vs `hybrid` vs `hybrid_graph`) theo các độ đo chuẩn IR:
     - **`P@5`**, **`P@10`**, **`P@5 (grade=2)`**
     - **`nDCG@5`**, **`nDCG@10`** (chuẩn hóa theo tập pool của từng query)
     - **`MRR@10`**
     - **`Pool Recall@10`** (tỷ lệ căn liên quan trong tập pool mà mỗi cấu hình tìm lại được trong top-10)

---

## 7 nhóm câu truy vấn trong `queries.json` (150 câu)

| Nhóm (`category`) | Số câu | Mã câu | Mục đích kiểm tra |
|---|---|---|---|
| `budget_apartment` | 20 | Q001–Q020 | Căn hộ theo khoảng giá (2–12+ tỷ), diện tích (45–100+ m²), số PN/WC |
| `budget_house` | 20 | Q021–Q040 | Nhà riêng / nhà phố / biệt thự theo giá (3,5–25+ tỷ), diện tích, số PN/WC |
| `location_price` | 34 | Q041–Q074 | Kết hợp khu vực (16 quận/huyện cũ & 15 phường/xã mới đông tin nhất) + mức giá |
| `amenity_graph` | 24 | Q075–Q098 | Gần tiện ích xung quanh (trường học, bệnh viện, metro, chợ, TTTM, công viên, cao tốc) — kiểm tra **Graph / OSM** |
| `feature_specific` | 20 | Q099–Q118 | Tiện nghi nội khu & kiến trúc (hồ bơi, gym, BBQ, thang máy, bãi xe, view sông, ban công, sân vườn, gara, sân thượng) |
| `semantic_intent` | 16 | Q119–Q134 | Nhu cầu văn nói / ngữ nghĩa (*gia đình trẻ, hẻm yên tĩnh, dòng tiền, vào ở ngay, đầu tư, người lớn tuổi...*) — kiểm tra **Vector search** |
| `complex_multi_constraint` | 16 | Q135–Q150 | Truy vấn khó kết hợp đồng thời 4–6 điều kiện (loại nhà + diện tích + phòng + khu vực + giá + tiện ích) |

---

## Cấu trúc thư mục

```text
evaluation/
├── README.md                 # Tài liệu này
├── queries.json              # 150 câu truy vấn đã kiểm chứng khả thi kèm expected_constraints
├── metrics.py                # Thuần Python: P@K, nDCG@K, MRR@K, Pool Recall@K, Constraint Match@K, Latency
├── run_retrieval_eval.py     # Chạy đánh giá live qua POST /v1/search + xuất judgments_template.csv
├── score_judgments.py        # Chấm điểm & so sánh nhiều run từ file judgments CSV đã điền 0/1/2
├── tests/
│   └── test_metrics.py       # Unit test cho toàn bộ độ đo và hàm kiểm tra ràng buộc
└── results/                  # Output mỗi lần chạy (git-ignored)
```

---

## Cách chạy

### 1. Chạy unit test (không cần backend)
```powershell
python -m unittest discover -s evaluation/tests -v
```

### 2. Chạy đánh giá tự động (Lớp 1) — 4 chế độ Ablation Study

Backend hỗ trợ 4 giá trị `retrieval_mode` qua cờ `--mode`:
- `fts`: **PostgreSQL FTS (`ts_rank_cd`) + Structured SQL** (tắt Vector & Graph)
- `vector`: **pgvector Cosine Similarity** thuần (tắt FTS/criteria & Graph)
- `hybrid`: **PostgreSQL FTS + Structured + pgvector** (tắt Neo4j Graph)
- `hybrid_graph`: **Hệ thống đầy đủ** — PostgreSQL + pgvector + Neo4j Graph *(mặc định)*

```powershell
# Chạy đủ 4 phương pháp trực tiếp lên web đã deploy (không cần Docker local)
python evaluation/run_retrieval_eval.py --endpoint https://cs2307.reais.codes/api/search --mode fts --run-name fts
python evaluation/run_retrieval_eval.py --endpoint https://cs2307.reais.codes/api/search --mode vector --run-name vector
python evaluation/run_retrieval_eval.py --endpoint https://cs2307.reais.codes/api/search --mode hybrid --run-name hybrid
python evaluation/run_retrieval_eval.py --endpoint https://cs2307.reais.codes/api/search --mode hybrid_graph --run-name hybrid_graph

# Test nhanh 5 câu đầu
python evaluation/run_retrieval_eval.py --endpoint https://cs2307.reais.codes/api/search --mode hybrid_graph --limit 5 --run-name smoke
```

Kết quả được lưu tại `evaluation/results/<run_name>/`:
- `summary.json`: bảng tổng hợp Constraint Match@5/@10, từng loại ràng buộc, Zero-Result Rate, Latency p50/p95.
- `per_query.json`: chi tiết từng query và top-K kết quả trả về.
- `judgments_template.csv`: bảng mẫu mở bằng Excel/Google Sheets để điền cột `relevance_0_1_2`.

### 3. Chấm điểm & so sánh nhiều cấu hình (Lớp 2 — Ablation Study)
Sau khi điền cột `relevance_0_1_2` (`0`, `1`, hoặc `2`) vào file CSV (ví dụ `evaluation/results/judgments_filled.csv`):

```powershell
python evaluation/score_judgments.py `
  --judgments evaluation/results/judgments_filled.csv `
  --runs evaluation/results/fts_only evaluation/results/hybrid_full
```

Lệnh sẽ in ra bảng Markdown (`P@5`, `P@10`, `nDCG@5`, `nDCG@10`, `MRR@10`, `Pool Recall@10`) sẵn sàng dán thẳng vào báo cáo hoặc slide.
