#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script tải và làm sạch dữ liệu tiện ích (POI - Points of Interest) từ OpenStreetMap (OSM)
cho toàn bộ khu vực TP.HCM qua Overpass API.

Đầu ra:
    data/bronze/osm_poi_nodes.csv
"""

import sys
import json
import csv
import ssl
import urllib.request
import urllib.parse
from pathlib import Path

# Thiết lập stdout UTF-8 cho môi trường console Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Danh sách endpoint công khai của Overpass để dự phòng
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

# Query mới dùng ranh giới hành chính theo tên TP.HCM (area search)
QUERY = """
[out:json][timeout:180];
area["name"="Thành phố Hồ Chí Minh"]["admin_level"="4"]->.hcm;
(
  nwr["amenity"~"^(hospital|clinic)$"](area.hcm);
  nwr["amenity"~"^(school|kindergarten|college|university)$"](area.hcm);
  nwr["shop"~"^(mall|supermarket)$"](area.hcm);
  nwr["amenity"="marketplace"](area.hcm);
  nwr["leisure"="park"](area.hcm);
  nwr["highway"="bus_stop"](area.hcm);
  nwr["railway"="station"]["station"="subway"](area.hcm);
  nwr["public_transport"="station"]["subway"="yes"](area.hcm);
  nwr["public_transport"="platform"]["bus"="yes"](area.hcm);
);
out center tags;
"""


def fetch_from_endpoints():
    # Bỏ qua lỗi SSL xác thực chứng chỉ môi trường cục bộ Windows nếu có
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    last_error = None
    for ep in ENDPOINTS:
        print(f"Đang gửi yêu cầu truy vấn tới: {ep}")
        req = urllib.request.Request(
            ep,
            data=urllib.parse.urlencode({"data": QUERY}).encode("utf-8"),
            headers={
                "User-Agent": "MyRealEstateStudy/1.0 (academic research; POI enrichment)",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=200) as response:
                raw_data = response.read().decode("utf-8")
                data = json.loads(raw_data)
                if "elements" in data:
                    return data
        except Exception as e:
            print(f"  -> Thử endpoint này gặp lỗi: {e}")
            last_error = e
            continue

    raise RuntimeError(f"Tất cả endpoint Overpass đều không phản hồi: {last_error}")


def main():
    print("=" * 60)
    print("BẮT ĐẦU TẢI DỮ LIỆU POI TỪ OPENSTREETMAP (OVERPASS API)")
    print("=" * 60)
    print("Đang tải dữ liệu (có thể mất 30 - 90 giây tùy tốc độ mạng)...")

    try:
        data = fetch_from_endpoints()
    except Exception as e:
        print(f"\n[LỖI MẠNG] {e}")
        sys.exit(1)

    elements = data.get("elements", [])
    timestamp = data.get("osm3s", {}).get("timestamp_osm_base", "")
    print(f"-> Đã nhận về {len(elements):,} đối tượng thô từ OSM.")
    print("Đang bóc tách, chuẩn hóa và lọc bỏ địa điểm không khả dụng...")

    pois = []
    excluded_count = 0

    for e in elements:
        tags = e.get("tags", {})
        center = e.get("center", e)
        lat = center.get("lat")
        lon = center.get("lon")

        if lat is None or lon is None:
            continue

        # Lọc bỏ các địa điểm bỏ hoang, ngừng hoạt động hoặc đang xây dựng
        if any(
            tags.get(k) in ("yes", "construction", "proposed")
            for k in ["disused", "abandoned", "construction", "proposed"]
        ):
            excluded_count += 1
            continue

        cats = []
        am = tags.get("amenity", "")
        sh = tags.get("shop", "")
        rw = tags.get("railway", "")

        # 1. Bệnh viện / Phòng khám
        if am in ("hospital", "clinic"):
            cats.append("hospital")

        # 2. Trường học các cấp
        if am in ("school", "kindergarten"):
            cats.append("school")

        # 3. Đại học / Cao đẳng
        if am in ("college", "university"):
            cats.append("college_university")

        # 4. Trung tâm thương mại
        if sh == "mall":
            cats.append("mall")

        # 5. Siêu thị
        if sh == "supermarket":
            cats.append("supermarket")

        # 6. Chợ truyền thống
        if am == "marketplace":
            cats.append("market")

        # 7. Công viên cây xanh
        if tags.get("leisure") == "park":
            cats.append("park")

        # 8. Trạm xe buýt
        if tags.get("highway") == "bus_stop" or tags.get("bus") == "yes":
            cats.append("bus_stop")

        # 9. Ga tàu điện Metro
        if rw == "station" or tags.get("subway") == "yes":
            cats.append("metro")

        if not cats:
            continue

        name = tags.get("name:vi") or tags.get("name") or tags.get("name:en") or ""
        oid = f"{e['type']}/{e['id']}"

        # Lấy địa chỉ đường phố nếu có
        street = tags.get("addr:street", "")
        address = tags.get("addr:full") or ", ".join(
            tags[k]
            for k in ["addr:housenumber", "addr:street", "addr:suburb", "addr:city"]
            if tags.get(k)
        )
        operator = tags.get("operator", "")

        pois.append({
            "osm_id": oid,
            "name": name,
            "category": "|".join(cats),
            "subtype": am or sh or rw or tags.get("leisure", "") or tags.get("public_transport", "") or tags.get("highway", ""),
            "latitude": lat,
            "longitude": lon,
            "street": street,
            "address": address,
            "operator": operator,
            "timestamp": timestamp,
            "coordinate_method": "osm_node" if e.get("type") == "node" else "osm_bbox_center",
            "url": f"https://www.openstreetmap.org/{oid}",
            "tags_json": json.dumps(tags, ensure_ascii=False, separators=(",", ":")),
        })

    # Sắp xếp theo osm_id đảm bảo tính nhất quán
    pois.sort(key=lambda x: x["osm_id"])

    # Xác định đường dẫn lưu file
    base_dir = Path(__file__).resolve().parent
    out_dir = base_dir.parent / "data" / "bronze"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "osm_poi_nodes.csv"

    fieldnames = [
        "osm_id",
        "name",
        "category",
        "subtype",
        "latitude",
        "longitude",
        "street",
        "address",
        "operator",
        "timestamp",
        "coordinate_method",
        "url",
        "tags_json",
    ]

    with open(out_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(pois)

    print("=" * 60)
    print(f"XUẤT FILE THÀNH CÔNG: {out_file}")
    print(f"- Tổng số POI hợp lệ: {len(pois):,}")
    print(f"- Số POI không khả dụng đã loại bỏ: {excluded_count:,}")
    print("=" * 60)


if __name__ == "__main__":
    main()
