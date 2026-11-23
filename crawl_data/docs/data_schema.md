# Từ Điển Dữ Liệu Tầng Gold (`data/gold/`)

Tài liệu này giải thích chi tiết cấu trúc các bảng, tên cột và ý nghĩa dữ liệu đầu ra ở tầng **Gold** cho **SQL** và **Graph**, dành cho người mới đọc lần đầu.

---

## 1. Bộ dữ liệu SQL (`data/gold/sql/`)

Gồm **3 bảng tách biệt thực thể và quan hệ** (không lặp lại dữ liệu thừa):
- `listings`: Chứa thông tin của BĐS (khóa chính: `listing_id`).
- `amenities`: Chứa thông tin của tiện ích POI (khóa chính: `osm_id`).
- `listing_amenities`: Chứa các số đo khoảng cách/thời gian giữa một BĐS và một POI (khóa chính: cặp `(listing_id, osm_id)`).

### 1.1. Bảng `listings.csv` — Thông tin Bất động sản (**3.857 dòng × 30 cột**)
- **Khóa chính:** `listing_id`
- **Nhóm thông tin gốc của tin đăng từ trang bất động sản (24 cột từ `cleaned_master_data.csv`):**
  - Định danh & thời gian: `listing_id`, `listing_type` *(loại giao dịch)*, `posted_date` *(chuẩn `YYYY-MM-DD`)*, `expiry_date` *(chuẩn `YYYY-MM-DD`)*, `property_type` *(loại BĐS)*, `Folder` *(phân khúc crawl)*.
  - Vị trí văn bản & liên kết: `city_province`, `district`, `title`, `address`, `old_address`, `location_link`, `latitude_longitude`, `description`, `image_urls`.
  - Thông số BĐS: `price_range` *(tỷ VND)*, `area` *(m²)*, `area_price_history` *(% biến động giá)*, `bedrooms`, `bathrooms`, `legal_status`, `furnishing`, `balcony_direction`, `house_direction`.
- **Nhóm thông tin tọa độ & độ tin cậy (6 cột bổ sung):**
  - `latitude`, `longitude`: Tọa độ chuẩn tại TP.HCM sau khi kiểm chuẩn (để trống nếu `unresolved`).
  - `coordinate_status`: Trạng thái kiểm tra tọa độ:
    - `valid` *(3.724)*: Tọa độ gốc hợp lệ nằm trong khung bao TP.HCM (`9.5 ≤ lat ≤ 11.5`, `105.8 ≤ lon ≤ 107.6`).
    - `imputed` *(113)*: Tọa độ gốc sai vùng (VD: bị gán mặc định ở Hà Nội), đã được sửa bằng trung vị cụm các tin hợp lệ có cùng `address` chuẩn hóa trong bán kính ≤ 250 m.
    - `unresolved` *(20)*: Tọa độ gốc sai vùng và không đủ tin tham chiếu an toàn để sửa.
  - `n_references`: Số lượng tin hàng xóm hợp lệ dùng để suy luận tọa độ (`0` với `valid`/`unresolved`, `≥ 1` với `imputed`).
  - `geo_cluster_150m`: Mã cụm không gian cố định bán kính đường chim bay 150 m (VD: `gc_0001`) được tạo **sau** bước xác thực tọa độ (không tham gia sửa tọa độ); các bán kính khác tính trực tiếp từ `latitude`, `longitude`.
  - `listing_road_offset_m`: Khoảng cách (mét) từ ghim tọa độ BĐS ra tới con đường gần nhất mà OSRM bắt được. Giá trị lớn (> 100 m) thường là nhà trong hẻm sâu hoặc khu đô thị mới chưa lên bản đồ đường bộ OSM.

---

### 1.2. Bảng `amenities.csv` — Danh mục Tiện ích OSM (**10.775 dòng × 12 cột**)
- **Khóa chính:** `osm_id` (định dạng `<type>/<id>`, ví dụ `node/11586280302` hoặc `way/504577398`).
- **Các cột:**
  - `name`: Tên tiện ích trên OpenStreetMap.
  - `category`: Nhóm tiện ích chuẩn hóa (1 địa điểm có thể thuộc nhiều nhóm ngăn cách bởi `|`), gồm **9 nhóm**: `hospital`, `school`, `college_university`, `metro`, `mall`, `market`, `supermarket`, `park`, `bus_stop`.
  - `subtype`: Loại chi tiết theo tag gốc của OSM (VD: `hospital`, `clinic`, `kindergarten`, `bus_stop`, `station`...).
  - `latitude`, `longitude`: Tọa độ tâm của tiện ích.
  - `street`, `address`, `operator`: Tên đường, địa chỉ đầy đủ, đơn vị vận hành (nếu OSM có gắn tag).
  - `url`: Link xem trực tiếp đối tượng trên OpenStreetMap.
  - `timestamp`: Thời điểm dữ liệu trên OSM được chỉnh sửa gần nhất.
  - `coordinate_method`: Cách lấy tọa độ (`node_direct` cho điểm, `way_center` / `relation_center` cho vùng).

---

### 1.3. Bảng `listing_amenities.csv` — Khoảng cách BĐS ↔ Tiện ích (**150.495 dòng × 10 cột**)
Lưu tối đa **Top-5 tiện ích gần nhất** (theo đường chim bay trong bán kính tìm kiếm của từng nhóm `category`) cho từng BĐS, kèm khoảng cách chim bay, khoảng cách lái xe và thời gian lái xe. Chỉ chứa quan hệ **BĐS ↔ POI** (không chứa khoảng cách lái xe giữa BĐS với BĐS).
- **Khóa ngoại:** `listing_id` → `listings(listing_id)`, `osm_id` → `amenities(osm_id)`.
- **Các cột:**
  - `category`: Nhóm tiện ích đang xét.
  - `rank`: Thứ tự `1..5` theo **đường chim bay** (`straight_line_km`).
  - `driving_rank`: Thứ tự `1..5` theo **đường lái xe thực tế** (`driving_distance_km`) — chỉ xếp hạng khi `route_status = ok`.
  - `straight_line_km`: Khoảng cách đường chim bay (km).
  - `driving_distance_km`: Quãng đường lái xe thực tế theo mạng lưới đường bộ OSRM (km).
  - `driving_duration_min`: Thời gian lái xe ước tính qua OSRM (phút).
  - `poi_road_offset_m`: Khoảng cách (mét) từ tọa độ POI ra tới con đường gần nhất trên OSRM.
  - `route_status`: Trạng thái định tuyến (`ok` | `no_route` | `pending`).

---

## 2. Bộ dữ liệu Graph (`data/gold/graph/`)

Định dạng CSV chuẩn cho `neo4j-admin import`, dùng các cột đặc biệt `:ID(...)`, `:LABEL`, `:START_ID(...)`, `:END_ID(...)`, `:TYPE`.

### 2.1. Các file Nodes (6 loại thực thể)
1. **`graph_nodes_listing.csv`** (**3.857** node, `:LABEL = Listing`):
   - Khóa `listing_id:ID(Listing)` và toàn bộ 29 thuộc tính sạch của BĐS (tương ứng bảng `listings.csv`, có ép kiểu `:int`, `:float`, `:date`, `:string`).
2. **`graph_nodes_amenity.csv`** (**10.775** node, `:LABEL = Amenity;<SubLabel>`):
   - Khóa `osm_id:ID(Amenity)`, kèm `name`, `category`, `subtype`, `latitude`, `longitude`, `street`, `address`, `operator`, `url`.
   - Sub-label tự động theo nhóm: `Hospital`, `School`, `CollegeUniversity`, `MetroStation`, `Mall`, `Market`, `Supermarket`, `Park`, `BusStop`.
3. **`graph_nodes_geo_cluster.csv`** (**1.254** node, `:LABEL = GeoCluster`):
   - Khóa `cluster_id:ID(GeoCluster)`, kèm `centroid_latitude:float`, `centroid_longitude:float`, `listing_count:int`.
4. **`graph_nodes_ward.csv`** (**121** node, `:LABEL = Ward`):
   - Khóa `ward_id:ID(Ward)`, kèm `name:string`, `normalized_name:string` *(không dấu)*, `city_province:string`.
5. **`graph_nodes_former_admin_area.csv`** (**40** node, `:LABEL = FormerAdminArea`):
   - Khóa `former_area_id:ID(FormerAdminArea)`, trích từ `old_address`, kèm `name:string`, `normalized_name:string`, `area_type:string` (`Quận`, `Huyện`...), `former_city_province:string`.
6. **`graph_nodes_street.csv`** (**1.195** node, `:LABEL = Street`):
   - Khóa `street_id:ID(Street)` *(hash theo `(ward_id, normalized_name)` để tránh trùng tên đường giữa các phường)*, kèm `name:string`, `normalized_name:string`, `ward_id:string`, `city_province:string`.

### 2.2. Các file Edges (6 loại quan hệ)
1. **`graph_edges_listing_near_amenity.csv`** (**150.495** cạnh, `:TYPE = NEAR_AMENITY`):
   - Nối `:START_ID(Listing)` → `:END_ID(Amenity)`.
   - Thuộc tính trên cạnh: `category`, `rank`, `driving_rank`, `is_nearest:boolean` (`true` khi `driving_rank == 1`), `straight_line_km`, `driving_distance_km`, `driving_duration_min`, `listing_road_offset_m`, `poi_road_offset_m`, `route_status`.
2. **`graph_edges_listing_in_cluster.csv`** (**3.837** cạnh, `:TYPE = IN_CLUSTER`):
   - Nối `:START_ID(Listing)` → `:END_ID(GeoCluster)`.
3. **`graph_edges_listing_in_ward.csv`** (**3.857** cạnh, `:TYPE = IN_WARD`):
   - Nối `:START_ID(Listing)` → `:END_ID(Ward)`.
4. **`graph_edges_listing_in_former_area.csv`** (**3.857** cạnh, `:TYPE = IN_FORMER_AREA`):
   - Nối `:START_ID(Listing)` → `:END_ID(FormerAdminArea)`.
5. **`graph_edges_ward_mapped_from_former_area.csv`** (**130** cạnh, `:TYPE = MAPPED_FROM`):
   - Nối `:START_ID(Ward)` → `:END_ID(FormerAdminArea)`, kèm `supporting_listing_count:int`.
6. **`graph_edges_listing_on_street.csv`** (**3.576** cạnh, `:TYPE = ON_STREET`):
   - Nối `:START_ID(Listing)` → `:END_ID(Street)`.
