# Pipeline Dữ Liệu Bất Động Sản TP.HCM (`crawl_data`)

Thư mục này chứa toàn bộ quy trình **End-to-End** từ thu thập dữ liệu tin đăng bất động sản, làm sạch, làm giàu thông tin địa lý (OpenStreetMap + OSRM), đến đóng gói dữ liệu tầng **Gold** cho **SQL** và **Graph**.

---

## Kiến trúc Pipeline (3 bước)

```text
Bước 1: Crawl & Tiền xử lý
  - crawler.py
  - data_preprocessing.py
  - generate_mapping_dicts.py

Bước 2: Làm giàu không gian (OSM + OSRM)
  - download_osm_poi.py
  - verify_listing_coordinates.py
  - find_nearest_pois.py
  - compute_driving_routes.py

Bước 3: Xuất tầng Gold
  - build_gold_data.py
```

---

## Tài liệu chi tiết
- **[docs/crawler.md](docs/crawler.md)** — Hướng dẫn chi tiết cách cào dữ liệu, checkpoint/resume và xuất file cho `crawler.py`.
- **[docs/data_schema.md](docs/data_schema.md)** — Từ điển dữ liệu chi tiết các bảng SQL, Graph (Nodes/Edges) và ý nghĩa từng trường dữ liệu tầng Gold.

---

## Cài đặt môi trường

```powershell
cd crawl_data
pip install -r requirements.txt
```

---

## Quickstart (Chạy từng bước)

### Bước 1: Crawl & Tiền xử lý
1. Cào dữ liệu căn hộ & nhà đất TP.HCM theo phân khúc (xem thêm tại [docs/crawler.md](docs/crawler.md)):
   ```powershell
   python crawler.py --category ban-can-ho-chung-cu-tp-ho-chi-minh --pages 10 --delay 2.0
   ```
2. Làm sạch, ép kiểu và khử trùng lặp thành bảng master (**3.857** BĐS):
   ```powershell
   python data_preprocessing.py
   ```
   → Đầu ra: `data/cleaned_master_data.csv`
3. Sinh từ điển ánh xạ mã ID (`001`, `002`...) cho `property_type` và 169 `district` từ `cleaned_master_data.csv` và `district_list_html.txt` *(chạy từ thư mục gốc project)*:
   ```powershell
   python crawl_data/generate_mapping_dicts.py
   ```
   → Đầu ra: `data/mapping_dictionaries.json`

---

### Bước 2: Làm giàu không gian (OSM + OSRM)
1. **Tải tiện ích công cộng (POI) toàn TP.HCM** từ OpenStreetMap (**10.775** POI thuộc 9 nhóm: `hospital`, `school`, `college_university`, `metro`, `mall`, `market`, `supermarket`, `park`, `bus_stop`):
   ```powershell
   python download_osm_poi.py
   ```
   → Đầu ra: `data/osm_poi_nodes.csv`

2. **Kiểm chuẩn tọa độ BĐS & gom cụm 150 m:**
   - Phát hiện và sửa các tin bị sai tọa độ (do người đăng không ghim vị trí cụ thể nên trang BĐS gắn nhầm tọa độ mặc định ngoài TP.HCM, ví dụ trỏ ra Hà Nội) bằng cách đối chiếu cụm tin hợp lệ có cùng địa chỉ.
   - Gom các BĐS nằm gần nhau trong bán kính **150 m** (cùng tòa chung cư, dự án hoặc khu vực) vào một cụm vị trí chung.
   ```powershell
   python verify_listing_coordinates.py
   ```
   → Đầu ra: `data/listings_verified_coords.csv` (**3.857** dòng).

3. **Tìm tối đa Top-5 POI gần nhất theo đường chim bay** cho mỗi BĐS hợp lệ (**3.837** BĐS):
   ```powershell
   python find_nearest_pois.py
   ```
   → Đầu ra: `data/listing_poi_top5_candidates.csv` (**150.495** cạnh ứng viên).

4. **Tính khoảng cách & thời gian lái xe thực tế qua OSRM** (có cache & resume tại `cache/osrm/`):
   ```powershell
   # Kiểm tra trước số batch cần gọi
   python compute_driving_routes.py --plan

   # Chạy thật qua OSRM
   python compute_driving_routes.py --osrm-url http://router.project-osrm.org --delay 2.0
   ```
   → Đầu ra: `data/listing_poi_routes.csv` (**150.495** cạnh).

---

### Bước 3: Xuất dữ liệu tầng Gold (`data/gold/`)
Đóng gói dữ liệu sạch cho **SQL** (3 bảng: `listings.csv`, `amenities.csv`, `listing_amenities.csv`) và **Graph** (6 file nodes + 6 file edges), đồng thời tự động kiểm tra toàn vẹn khóa chính/khóa ngoại:

```powershell
# Xuất cả 2 bộ SQL & Graph (mặc định)
python build_gold_data.py

# Hoặc xuất riêng từng bộ
python build_gold_data.py --target sql
python build_gold_data.py --target graph
```

---

## Cấu trúc thư mục `crawl_data/`

```text
crawl_data/
├── README.md                        # Tổng quan pipeline & hướng dẫn chạy nhanh
├── docs/
│   ├── crawler.md                   # Hướng dẫn chi tiết crawler.py
│   └── data_schema.md               # Từ điển dữ liệu tầng Gold (SQL & Graph)
├── requirements.txt
├── crawler.py                       # Bước 1: Cào tin BĐS
├── data_preprocessing.py            # Bước 1: Làm sạch dữ liệu gốc
├── generate_mapping_dicts.py        # Bước 1: Tạo từ điển ánh xạ mã ID
├── district_list_html.txt           # Danh sách 169 phường/xã chuẩn
├── download_osm_poi.py              # Bước 2: Tải POI OSM
├── verify_listing_coordinates.py    # Bước 2: Kiểm chuẩn & sửa tọa độ
├── find_nearest_pois.py             # Bước 2: Tìm Top-5 POI chim bay
├── compute_driving_routes.py        # Bước 2: Tính đường đi OSRM
├── build_gold_data.py               # Bước 3: Xuất tầng Gold (SQL & Graph)
├── cache/osrm/                      # Cache JSON từ OSRM
└── data/
    ├── cleaned_master_data.csv
    ├── mapping_dictionaries.json
    ├── osm_poi_nodes.csv
    ├── listings_verified_coords.csv
    ├── listing_poi_top5_candidates.csv
    ├── listing_poi_routes.csv
    └── gold/
        ├── sql/                     # 3 bảng SQL (listings, amenities, listing_amenities)
        └── graph/                   # 6 file nodes + 6 file edges (graph_nodes_*, graph_edges_*)
```
