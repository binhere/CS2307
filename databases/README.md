# Hướng Dẫn Dựng & Truy Vấn Databases (`databases/`)

Thư mục này chứa script Docker và cấu hình để nạp dữ liệu tầng **Gold** từ `../crawl_data/data/gold/` vào **Neo4j (Graph)** và **PostgreSQL (SQL)** phục vụ truy vấn và kiểm thử tại local.

- **Schema chi tiết các bảng, node và edge:** Xem tại [crawl_data/docs/data_schema.md](../crawl_data/docs/data_schema.md).
- **Yêu cầu duy nhất:** Máy đã cài và bật **Docker Desktop**.

---

## 1. Graph Database — Neo4j (`databases/graph/`)

### Khởi tạo & Nạp dữ liệu
Chạy từ thư mục gốc project:
```powershell
powershell -ExecutionPolicy Bypass -File databases/graph/start_neo4j.ps1
```
Script sẽ tự động:
1. Nạp **17.242 nodes** (6 loại) và **165.752 relationships** (6 loại) từ `crawl_data/data/gold/graph/`.
2. Khởi động container `cs2307-neo4j` (`neo4j:5-community`).
3. Chạy `create_indexes.cypher` tạo 6 unique constraints và 6 indexes.

### Thông tin kết nối
| Mục | Giá trị |
|---|---|
| Neo4j Browser (Web UI) | `http://localhost:7474` |
| Bolt URL | `neo4j://localhost:7687` |
| Database | `neo4j` |
| Xác thực | Tắt (`NEO4J_AUTH=none` — để trống user/password) |

### Vai trò từng file
- `start_neo4j.ps1`: Script điều phối chính — dọn container cũ, chạy `neo4j-admin import`, bật Neo4j và gọi `create_indexes.cypher`.
- `create_indexes.cypher`: Tạo 6 unique constraints (khóa chính cho 6 loại node) và 6 indexes tối ưu truy vấn *(được `start_neo4j.ps1` tự động chạy)*.
- `sample_queries.cypher`: Tập hợp các câu Cypher mẫu để bạn copy-paste chạy thử trên Neo4j Browser *(không tự động chạy)*.
- `data/`: Thư mục chứa dữ liệu database local do Neo4j sinh ra *(đã ignore trong `.gitignore`)*.

### Truy vấn thử
Mở `http://localhost:7474` và copy từng câu Cypher mẫu trong [graph/sample_queries.cypher](graph/sample_queries.cypher) để kiểm tra.

---

## 2. SQL Database — PostgreSQL (`databases/sql/`)

### Khởi tạo & Nạp dữ liệu
Chạy từ thư mục gốc project:
```powershell
powershell -ExecutionPolicy Bypass -File databases/sql/start_postgres.ps1
```
Script sẽ tự động:
1. Khởi động container `cs2307-postgres` (`postgres:16-alpine`).
2. Chạy `create_tables.sql` tạo 3 bảng `listings` (**3.857**), `amenities` (**10.775**), `listing_amenities` (**150.495**).
3. Import dữ liệu từ `crawl_data/data/gold/sql/` bằng `\copy`.
4. Chạy `create_indexes.sql` tạo 11 indexes.

### Thông tin kết nối
| Mục | Giá trị |
|---|---|
| Host / Port | `localhost:5432` |
| Database | `postgres` |
| User | `postgres` |
| Xác thực | Tắt ở local (`POSTGRES_HOST_AUTH_METHOD=trust`) |

### Vai trò từng file
- `start_postgres.ps1`: Script điều phối chính — bật container PostgreSQL, chạy `create_tables.sql`, nạp 3 file CSV bằng `\copy` và gọi `create_indexes.sql`.
- `create_tables.sql`: Định nghĩa schema 3 bảng (`listings`, `amenities`, `listing_amenities`) kèm khóa chính (PK) và khóa ngoại (FK) *(được `start_postgres.ps1` tự động chạy)*.
- `create_indexes.sql`: Tạo 11 indexes tối ưu các trường hay lọc và `JOIN` *(được `start_postgres.ps1` tự động chạy)*.
- `sample_queries.sql`: Bộ 4 câu SQL mẫu để chạy thử kiểm tra dữ liệu *(không tự động chạy)*.
- `data/`: Thư mục chứa dữ liệu database local do PostgreSQL sinh ra *(đã ignore trong `.gitignore`)*.

### Truy vấn thử
Mở terminal `psql` tương tác:
```powershell
docker exec -it cs2307-postgres psql -U postgres -d postgres
```
Hoặc chạy toàn bộ bộ query kiểm thử trong [sql/sample_queries.sql](sql/sample_queries.sql):
```powershell
Get-Content databases/sql/sample_queries.sql -Raw | docker exec -i cs2307-postgres psql -v ON_ERROR_STOP=1 -U postgres -d postgres
```

---

## Cấu trúc thư mục `databases/`

```text
databases/
├── README.md                  # Tài liệu hướng dẫn (file này)
├── graph/
│   ├── start_neo4j.ps1        # Dựng container + bulk import + tạo index Neo4j
│   ├── create_indexes.cypher  # 6 unique constraints + 6 property indexes
│   ├── sample_queries.cypher  # Bộ query Cypher mẫu
│   └── data/                  # Dữ liệu DB local
└── sql/
    ├── start_postgres.ps1     # Dựng container + tạo bảng + import + tạo index
    ├── create_tables.sql      # Schema 3 bảng listings, amenities, listing_amenities
    ├── create_indexes.sql     # 11 indexes tối ưu lọc & JOIN
    ├── sample_queries.sql     # Bộ query SQL mẫu
    └── data/                  # Dữ liệu DB local
```
