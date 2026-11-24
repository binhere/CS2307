#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script Tiền Xử Lý và Chuẩn Hóa Dữ Liệu Bất Động Sản (Data Preprocessing Pipeline)
================================================================================
Trích xuất 24 trường dữ liệu từ các file JSONL thô (Chung cư & Nhà đất):
- 23 TRƯỜNG CHÍNH (Core Target Schema): Thuộc tính bất động sản khớp với Final_Data.csv,
  làm sạch và ép kiểu số thực (Giá tỷ, Diện tích m², PN, WC, % biến động giá),
  loại bỏ các trường rác/HTML/tiện ích nhị phân.
- 1 TRƯỜNG PHỤ (Auxiliary Field): 'image_urls' - phục vụ tổng hợp, quản lý dữ liệu
  tập trung tại một nơi, thuận tiện cho việc lấy link tải ảnh (download listing images).
  Được làm sạch triệt để, loại bỏ ký tự escape '\\' thừa, chuẩn hóa về JSON array sạch.

Áp dụng quy tắc khử trùng lặp triệt để:
    df.drop_duplicates(subset=['listing_id'], keep='first')

Đầu ra:
    - data/silver/cleaned_can_ho.csv       (1,127 records sạch, 24 cột)
    - data/silver/cleaned_nha_dat.csv      (2,730 records sạch, 24 cột)
    - data/silver/cleaned_master_data.csv  (3,857 records sạch 100% Unique ID, 24 cột)
"""

import os
import sys
import json
import re
import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

# Thiết lập stdout UTF-8 cho môi trường console Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# =====================================================================
# ĐỊNH NGHĨA 24 CỘT DỮ LIỆU (23 TRƯỜNG CHÍNH + 1 TRƯỜNG PHỤ)
# =====================================================================

# 23 TRƯỜNG MỤC TIÊU CHÍNH (Core Target Schema - Khớp thuộc tính bài toán BĐS)
CORE_COLUMNS: List[str] = [
    "listing_id",
    "listing_type",
    "posted_date",
    "expiry_date",
    "property_type",
    "city_province",
    "district",
    "Folder",
    "title",
    "address",
    "old_address",
    "location_link",
    "latitude_longitude",
    "description",
    "area_price_history",
    "price_range",
    "area",
    "bedrooms",
    "bathrooms",
    "legal_status",
    "furnishing",
    "balcony_direction",
    "house_direction",
]

# TRƯỜNG THỨ 24: TRƯỜNG PHỤ (Auxiliary Field - Quản lý link ảnh tập trung để download)
AUXILIARY_COLUMNS: List[str] = [
    "image_urls",
]

# TỔNG HỢP 24 CỘT CHUẨN ĐẦU RA
TARGET_COLUMNS: List[str] = CORE_COLUMNS + AUXILIARY_COLUMNS


# =====================================================================
# BỘ CÔNG CỤ PHÂN TÍCH CÚ PHÁP VÀ LÀM SẠCH (PARSERS & CLEANERS)
# =====================================================================

def parse_listing_id(item: Dict[str, Any]) -> Optional[int]:
    """
    Trích xuất listing_id chuẩn dạng số nguyên từ listing_code hoặc URL.
    """
    code = str(item.get("listing_code") or "").strip()
    if code.isdigit():
        return int(code)

    # Fallback: Trích xuất regex -pr(\d+) từ URL
    url = str(item.get("url") or "").strip()
    match = re.search(r"-pr(\d+)", url)
    if match:
        return int(match.group(1))

    return None


def clean_text(value: Any) -> str:
    """
    Làm sạch chuỗi ký tự: bỏ khoảng trắng thừa, chuẩn hóa xuống dòng.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() in ("null", "none", "nan"):
        return ""
    # Chuẩn hóa ngắt dòng
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def clean_old_address(value: Any) -> str:
    """
    Làm sạch địa chỉ cũ, lọc bỏ cặp ngoặc đơn thừa:
    ví dụ: '(Quận 2, Hồ Chí Minh cũ)' -> 'Quận 2, Hồ Chí Minh cũ'
    """
    text = clean_text(value)
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    return text


def format_lat_lon(item: Dict[str, Any]) -> str:
    """
    Định dạng chuỗi tọa độ chuẩn q={latitude},{longitude}.
    Ưu tiên lấy từ latitude, longitude; fallback sang trích xuất từ link_location.
    """
    lat = str(item.get("latitude") or "").strip()
    lon = str(item.get("longitude") or "").strip()
    if lat and lon and lat != "None" and lon != "None":
        return f"q={lat},{lon}"

    link = str(item.get("link_location") or "").strip()
    match = re.search(r"[?&]q=([^&]+)", link)
    if match:
        return f"q={match.group(1)}"

    return ""


def parse_price_to_billion(item: Dict[str, Any]) -> Optional[float]:
    """
    Chuẩn hóa khoảng giá về số thực tính theo đơn vị Tỷ VNĐ.
    - '43,9 tỷ' -> 43.9
    - '850 triệu' -> 0.85
    - '1 tỷ 200 triệu' -> 1.2
    - 'Thỏa thuận' / rỗng -> None
    """
    raw_candidates = [
        item.get("price"),
        item.get("dac_diem_khoang_gia"),
        item.get("price_range"),
    ]
    price_str = ""
    for candidate in raw_candidates:
        if candidate and str(candidate).strip():
            price_str = str(candidate).strip()
            break

    if not price_str or "thỏa thuận" in price_str.lower():
        return None

    # Trường hợp đơn giá /m2 bị nhầm vào khoảng giá
    if "/m" in price_str.lower() or "/tháng" in price_str.lower():
        return None

    text = price_str.lower()
    total_billion = 0.0
    matched = False

    # Tìm phần tỷ (ví dụ: '43,9 tỷ' hoặc '1 tỷ')
    ty_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:tỷ|ty)", text)
    if ty_match:
        val_str = ty_match.group(1).replace(",", ".")
        try:
            total_billion += float(val_str)
            matched = True
        except ValueError:
            pass

    # Tìm phần triệu (ví dụ: '850 triệu' hoặc '200 triệu')
    tr_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:triệu|trieu|tr)(?!\s*/)", text)
    if tr_match:
        val_str = tr_match.group(1).replace(",", ".")
        try:
            total_billion += float(val_str) / 1000.0
            matched = True
        except ValueError:
            pass

    # Nếu không ghi rõ tỷ/triệu mà chỉ là con số thuần túy
    if not matched:
        num_match = re.search(r"^(\d+(?:[.,]\d+)?)$", text)
        if num_match:
            try:
                return float(num_match.group(1).replace(",", "."))
            except ValueError:
                return None
        return None

    return round(total_billion, 4) if matched else None


def parse_float_number(text: Any) -> Optional[float]:
    """
    Trích xuất số thực sạch từ chuỗi:
    - '325 m²' -> 325.0
    - '79,9 m2' -> 79.9
    - '4 PN' -> 4.0
    - '5 phòng' -> 5.0
    """
    if text is None:
        return None
    val_str = str(text).strip()
    if not val_str or val_str.lower() in ("null", "none", "nan"):
        return None

    # Tìm số thực có dấu phẩy hoặc chấm thập phân
    match = re.search(r"(\d+(?:[.,]\d+)?)", val_str)
    if match:
        num_str = match.group(1).replace(",", ".")
        try:
            return float(num_str)
        except ValueError:
            return None
    return None


def parse_price_history(trend_val: Any) -> Optional[float]:
    """
    Trích xuất tỷ lệ % biến động giá 1 năm từ trường price_trend:
    ví dụ: '13,2% Giá tại dự án này đã tăng...' -> 13.2
           '-5,4% ...' -> -5.4
    """
    if trend_val is None:
        return None
    text = str(trend_val).strip()
    if not text:
        return None

    match = re.search(r"([+-]?\d+(?:[.,]\d+)?)\s*%", text)
    if match:
        num_str = match.group(1).replace(",", ".")
        try:
            return float(num_str)
        except ValueError:
            return None
    return None


def parse_features_dict(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Giải nén thuộc tính dự phòng từ property_features (nếu có).
    """
    feat_raw = item.get("property_features")
    if isinstance(feat_raw, dict):
        return feat_raw
    if isinstance(feat_raw, str) and feat_raw.strip():
        try:
            parsed = json.loads(feat_raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return {}


def clean_image_urls(raw_value: Any) -> str:
    """
    Làm sạch danh sách link ảnh (Trường phụ image_urls):
    - Giải mã chuỗi double-escaped từ data.jsonl (loại bỏ '\\' và '\\\"' thừa).
    - Trích xuất danh sách URL hợp lệ (bắt đầu bằng http:// hoặc https://).
    - Loại bỏ triệt để mọi ký tự backslash '\\' còn sót lại trong từng URL.
    - Khử trùng lặp link trong cùng bài đăng và giữ thứ tự xuất hiện.
    - Trả về chuỗi JSON array chuẩn sạch: '["https://...", "https://..."]'.
      (Sau này khi đọc bằng pandas/python, chỉ cần json.loads(row['image_urls'])
       là có ngay List[str] sạch 100% để truyền vào script download ảnh).
    """
    if raw_value is None:
        return "[]"

    urls: List[str] = []

    # Trường hợp 1: Dữ liệu đã là list Python
    if isinstance(raw_value, list):
        for item in raw_value:
            u = str(item).replace("\\", "").strip().strip("\"'")
            if u.startswith("http"):
                urls.append(u)

    # Trường hợp 2: Dữ liệu dạng chuỗi (thường là double-escaped JSON string từ data.jsonl)
    elif isinstance(raw_value, str):
        text = raw_value.strip()
        if not text or text in ("[]", "{}", "null", "None"):
            return "[]"

        # Thử parse JSON
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                for item in parsed:
                    u = str(item).replace("\\", "").strip().strip("\"'")
                    if u.startswith("http"):
                        urls.append(u)
            elif isinstance(parsed, str) and parsed.startswith("http"):
                urls.append(parsed.replace("\\", "").strip().strip("\"'"))
        except Exception:
            # Fallback regex tìm các link http/https nếu chuỗi JSON bị lỗi format
            found = re.findall(r'https?://[^\s"\'\\\]]+', text)
            for u in found:
                clean_u = u.replace("\\", "").strip().strip("\"'")
                if clean_u.startswith("http"):
                    urls.append(clean_u)

    # Khử trùng lặp URL trong bài đăng nhưng vẫn bảo toàn thứ tự
    seen = set()
    unique_urls: List[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique_urls.append(u)

    return json.dumps(unique_urls, ensure_ascii=False) if unique_urls else "[]"


# =====================================================================
# ÁNH XẠ VÀ BIẾN ĐỔI BẢN GHI (TRANSFORMATION ENGINE)
# =====================================================================

def transform_record(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Chuyển đổi 1 bản ghi thô từ data.jsonl thành từ điển chuẩn:
    - 23 trường mục tiêu chính (Core Target Schema)
    - 1 trường phụ 'image_urls' (Auxiliary Field)
    """
    listing_id = parse_listing_id(item)
    if listing_id is None:
        # Bỏ qua bản ghi không có mã tin hợp lệ
        return None

    features = parse_features_dict(item)

    # 1. listing_id (Core Target)
    rec_id = listing_id

    # 2. listing_type (Core Target)
    listing_type = clean_text(item.get("listing_type")) or "Tin thường"

    # 3. posted_date & 4. expiry_date (Core Target)
    posted_date = clean_text(item.get("posted_date"))
    expiry_date = clean_text(item.get("expiry_date"))

    # 5. property_type: Lấy chính xác 100% giá trị gốc từ real_estate_type, không chuẩn hóa
    property_type = clean_text(item.get("real_estate_type"))

    # 6. city_province & 7. district (Core Target)
    city_province = clean_text(item.get("province")) or "Hồ Chí Minh"
    district = clean_text(item.get("district"))

    # 8. Folder (Core Target)
    folder = clean_text(item.get("breadcrumb_folder")) or clean_text(item.get("folder"))

    # 9. title (Core Target)
    title = clean_text(item.get("title"))

    # 10. address & 11. old_address (Core Target)
    address = clean_text(item.get("address"))
    old_address = clean_old_address(item.get("old_address"))

    # 12. location_link & 13. latitude_longitude (Core Target)
    location_link = clean_text(item.get("link_location"))
    latitude_longitude = format_lat_lon(item)

    # 14. description (Core Target)
    description = clean_text(item.get("description_text"))

    # 15. area_price_history (Core Target - % biến động giá 1 năm)
    area_price_history = parse_price_history(item.get("price_trend"))

    # 16. price_range (Core Target - tính theo Tỷ VNĐ)
    price_range = parse_price_to_billion(item)

    # 17. area (Core Target - m2)
    raw_area = item.get("area_m2") or item.get("dac_diem_dien_tich") or features.get("Diện tích")
    area = parse_float_number(raw_area)

    # 18. bedrooms (Core Target)
    raw_bed = (
        item.get("dac_diem_so_phong_ngu")
        or item.get("bedrooms")
        or features.get("Số phòng ngủ")
    )
    bedrooms = parse_float_number(raw_bed)

    # 19. bathrooms (Core Target)
    raw_bath = (
        item.get("dac_diem_so_phong_tam_ve_sinh")
        or features.get("Số phòng tắm, vệ sinh")
    )
    bathrooms = parse_float_number(raw_bath)

    # 20. legal_status (Core Target)
    legal_status = clean_text(
        item.get("dac_diem_phap_ly") or features.get("Pháp lý")
    )

    # 21. furnishing (Core Target)
    raw_furnishing = clean_text(
        item.get("dac_diem_noi_that") or features.get("Nội thất")
    )
    # Loại bỏ dấu chấm cuối câu nếu có
    furnishing = raw_furnishing.rstrip(".")

    # 22. balcony_direction (Core Target)
    balcony_direction = clean_text(
        item.get("dac_diem_huong_ban_cong") or features.get("Hướng ban công")
    )

    # 23. house_direction (Core Target)
    house_direction = clean_text(
        item.get("dac_diem_huong_nha") or features.get("Hướng nhà")
    )

    # 24. image_urls (Auxiliary Field: Quản lý link ảnh tập trung để download sau này)
    image_urls = clean_image_urls(item.get("image_urls"))

    return {
        # 23 Trường Chính (Core Target Schema)
        "listing_id": rec_id,
        "listing_type": listing_type,
        "posted_date": posted_date,
        "expiry_date": expiry_date,
        "property_type": property_type,
        "city_province": city_province,
        "district": district,
        "Folder": folder,
        "title": title,
        "address": address,
        "old_address": old_address,
        "location_link": location_link,
        "latitude_longitude": latitude_longitude,
        "description": description,
        "area_price_history": area_price_history,
        "price_range": price_range,
        "area": area,
        "bedrooms": bedrooms,
        "bathrooms": bathrooms,
        "legal_status": legal_status,
        "furnishing": furnishing,
        "balcony_direction": balcony_direction,
        "house_direction": house_direction,
        # 1 Trường Phụ (Auxiliary Field)
        "image_urls": image_urls,
    }


# =====================================================================
# BỘ ĐỌC DỮ LIỆU & ÁP DỤNG KHỬ TRÙNG LẶP (DEDUPLICATION ENGINE)
# =====================================================================

def load_and_transform_jsonl(file_path: Path) -> pd.DataFrame:
    """
    Đọc streaming từng dòng của file JSONL và chuyển đổi thành DataFrame 24 cột.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"Không tìm thấy file nguồn: {file_path}")

    records: List[Dict[str, Any]] = []
    line_count = 0
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue
            line_count += 1
            try:
                raw_item = json.loads(line_str)
                transformed = transform_record(raw_item)
                if transformed:
                    records.append(transformed)
            except Exception as e:
                print(f"⚠️ Cảnh báo lỗi JSON dòng {line_count} ({file_path.name}): {e}")

    df = pd.DataFrame(records, columns=TARGET_COLUMNS)
    return df


def deduplicate_dataframe(df: pd.DataFrame, dataset_name: str) -> pd.DataFrame:
    """
    Áp dụng quy tắc khử trùng lặp dữ liệu:
        drop_duplicates(subset=['listing_id'], keep='first')
    và in thống kê so sánh trước/sau khi khử trùng.
    """
    initial_count = len(df)
    unique_ids_count = df["listing_id"].nunique()
    duplicated_count = initial_count - unique_ids_count

    # LỆNH CỐT LÕI THEO PLAN:
    df_cleaned = df.drop_duplicates(subset=["listing_id"], keep="first").copy()
    final_count = len(df_cleaned)

    print(f"🔹 Khử trùng lặp [{dataset_name}]:")
    print(f"   - Số bản ghi ban đầu      : {initial_count:,}")
    print(f"   - Số bản ghi trùng lặp bị xóa: {duplicated_count:,}")
    print(f"   - Số bản ghi độc nhất còn lại : {final_count:,} (100% Unique ID)")

    return df_cleaned


def print_field_stats(df: pd.DataFrame, title: str) -> None:
    """
    In bảng thống kê chi tiết tỷ lệ giá trị có mặt của 24 cột (23 chính + 1 phụ).
    """
    total = len(df)
    print("\n" + "=" * 85)
    print(f" BÁO CÁO THỐNG KÊ 24 TRƯỜNG DỮ LIỆU (23 CHÍNH + 1 PHỤ): {title}")
    print(f" Tổng số bản ghi sạch: {total:,}")
    print("=" * 85)
    print(f"{'STT':<4} | {'Phân loại':<10} | {'Tên trường (Field Name)':<23} | {'Có giá trị (Non-null)':<20} | {'Tỷ lệ':<8}")
    print("-" * 85)

    for idx, col in enumerate(TARGET_COLUMNS, start=1):
        group_type = "CHÍNH" if col in CORE_COLUMNS else "PHỤ"
        series = df[col]
        if series.dtype == "object":
            # Đối với image_urls, chuỗi "[]" coi như rỗng
            if col == "image_urls":
                valid_mask = series.notna() & (series.astype(str).str.strip() != "[]") & (series.astype(str).str.strip() != "")
            else:
                valid_mask = series.notna() & (series.astype(str).str.strip() != "")
        else:
            valid_mask = series.notna()

        valid_count = valid_mask.sum()
        pct = (valid_count / total * 100) if total > 0 else 0.0
        print(f"{idx:<4} | {group_type:<10} | {col:<23} | {valid_count:>6,} / {total:>6,} | {pct:>6.2f}%")
    print("=" * 85 + "\n")


# =====================================================================
# HÀM CHÍNH (MAIN PIPELINE COORDINATOR)
# =====================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Data Preprocessing Pipeline: Trích xuất 24 trường (23 chính + 1 phụ) và khử trùng lặp listing_id."
    )
    # Xác định đường dẫn mặc định linh hoạt từ vị trí đặt script
    data_dir = Path(__file__).resolve().parent.parent / "data"

    default_can_ho = (
        data_dir / "bronze" / "ban-can-ho-chung-cu-tp-ho-chi-minh" / "data.jsonl"
    )
    default_nha_dat = (
        data_dir / "bronze" / "ban-nha-dat-tp-ho-chi-minh" / "data.jsonl"
    )
    default_output_dir = data_dir / "silver"

    parser.add_argument(
        "--input-can-ho",
        type=str,
        default=str(default_can_ho),
        help="Đường dẫn file data.jsonl Căn hộ chung cư",
    )
    parser.add_argument(
        "--input-nha-dat",
        type=str,
        default=str(default_nha_dat),
        help="Đường dẫn file data.jsonl Nhà đất",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(default_output_dir),
        help="Thư mục xuất các file CSV kết quả làm sạch",
    )
    args = parser.parse_args()

    input_can_ho_path = Path(args.input_can_ho).resolve()
    input_nha_dat_path = Path(args.input_nha_dat).resolve()
    output_dir_path = Path(args.output_dir).resolve()
    output_dir_path.mkdir(parents=True, exist_ok=True)

    print("=" * 85)
    print("     BẮT ĐẦU PIPELINE TIỀN XỬ LÝ & CHUẨN HÓA DỮ LIỆU")
    print("     (23 TRƯỜNG CHÍNH BĐS + 1 TRƯỜNG PHỤ IMAGE_URLS)")
    print("=" * 85)
    print(f"📁 Tệp Căn hộ nguồn : {input_can_ho_path}")
    print(f"📁 Tệp Nhà đất nguồn : {input_nha_dat_path}")
    print(f"📁 Thư mục xuất file : {output_dir_path}")
    print("-" * 85)

    # 1. Đọc và chuyển đổi Căn hộ
    print("⏳ Đang xử lý tập dữ liệu Căn hộ chung cư...")
    df_can_ho_raw = load_and_transform_jsonl(input_can_ho_path)
    df_can_ho_clean = deduplicate_dataframe(df_can_ho_raw, "Căn Hộ Chung Cư")

    # 2. Đọc và chuyển đổi Nhà đất
    print("⏳ Đang xử lý tập dữ liệu Nhà đất...")
    df_nha_dat_raw = load_and_transform_jsonl(input_nha_dat_path)
    df_nha_dat_clean = deduplicate_dataframe(df_nha_dat_raw, "Nhà Đất")

    # 3. Gộp thành Master DataFrame và khử trùng lặp toàn diện
    print("⏳ Đang gộp cả 2 bộ dữ liệu thành Master Dataset...")
    df_master_combined = pd.concat([df_can_ho_clean, df_nha_dat_clean], ignore_index=True)
    df_master_clean = deduplicate_dataframe(df_master_combined, "Master Combined (Căn hộ + Nhà đất)")

    # 4. Xuất các file CSV kết quả
    out_can_ho = output_dir_path / "cleaned_can_ho.csv"
    out_nha_dat = output_dir_path / "cleaned_nha_dat.csv"
    out_master = output_dir_path / "cleaned_master_data.csv"

    print("-" * 85)
    print("💾 Đang ghi các tệp CSV kết quả...")
    df_can_ho_clean.to_csv(out_can_ho, index=False, encoding="utf-8")
    print(f"   ✅ Đã ghi {len(df_can_ho_clean):,} dòng vào: {out_can_ho}")

    df_nha_dat_clean.to_csv(out_nha_dat, index=False, encoding="utf-8")
    print(f"   ✅ Đã ghi {len(df_nha_dat_clean):,} dòng vào: {out_nha_dat}")

    df_master_clean.to_csv(out_master, index=False, encoding="utf-8")
    print(f"   ✅ Đã ghi {len(df_master_clean):,} dòng vào: {out_master}")

    # 5. In thống kê chất lượng dữ liệu Master
    print_field_stats(df_master_clean, "TẬP DỮ LIỆU TỔNG HỢP MASTER CLEAN")

    # 6. Kiểm tra tính toàn vẹn (Verification assertions)
    assert len(df_master_clean.columns) == 24, f"Lỗi: Số lượng cột không bằng 24! Hiện có {len(df_master_clean.columns)} cột"
    assert df_master_clean["listing_id"].is_unique, "Lỗi: ID vẫn còn bị trùng lặp!"
    print("🎉 HOÀN TẤT THÀNH CÔNG! Dữ liệu đạt chuẩn 100% Unique ID và đúng 24 trường (23 chính + 1 phụ).")


if __name__ == "__main__":
    main()
