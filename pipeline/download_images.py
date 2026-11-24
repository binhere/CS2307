#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script Tải và Lưu Trữ Ảnh Bất Động Sản Theo Cấu Trúc Thư Mục Phân Cấp:
    data/bronze/images/<property_type>/<district>/<listing_id>/
Tên file ảnh chuẩn 3 chữ số:
    {mãloạibđs}_{mãphường}_{listing_id}_{số}.jpg
    (Ví dụ: 001_127_46058769_001.jpg)
Hỗ trợ cờ --ratio để scale ảnh (giữ nguyên tỷ lệ gốc origin_w:origin_h).
"""

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
from PIL import Image
from tqdm import tqdm

# Thiết lập stdout UTF-8 an toàn trên Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_CSV = DATA_DIR / "silver" / "cleaned_master_data.csv"
DEFAULT_MAPPING = DATA_DIR / "silver" / "mapping_dictionaries.json"
DEFAULT_OUTPUT_DIR = DATA_DIR / "bronze" / "images"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://batdongsan.com.vn/",
}


def load_mappings(mapping_path: Path) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Tải từ điển ánh xạ property_type và district."""
    if not mapping_path.exists():
        raise FileNotFoundError(f"Không tìm thấy file từ điển: {mapping_path}")

    with open(mapping_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    pt_to_id = data.get("property_type_to_id", {})
    dist_to_id = data.get("district_to_id", {})
    return pt_to_id, dist_to_id


def get_district_code(district_name: str, dist_to_id: Dict[str, str]) -> str:
    """Tra cứu mã phường 3 chữ số, tự động loại bỏ tiền tố Phường/Xã/Huyện nếu cần."""
    if not district_name:
        return "000"

    district_str = str(district_name).strip()
    if district_str in dist_to_id:
        return dist_to_id[district_str]

    # Loại bỏ tiền tố hành chính để tra cứu
    clean_name = re.sub(
        r"^(Phường|Xã|Huyện|Quận|Thành phố|Thị trấn|Thị xã)\s+", "", district_str
    ).strip()

    if clean_name in dist_to_id:
        return dist_to_id[clean_name]

    return "000"


def download_single_image(
    session: requests.Session,
    url: str,
    dest_path: Path,
    timeout: int = 15,
    retries: int = 3,
    ratio: int = 100,
) -> Tuple[bool, str]:
    """
    Tải 1 ảnh về dest_path, tự động skip nếu đã tồn tại.
    Nếu ratio == 100%: giữ nguyên ảnh gốc.
    Nếu ratio < 100%: scale ảnh theo tỷ lệ ratio*origin_w : ratio*origin_h.
    """
    # Cơ chế Resume: Nếu file đã tồn tại và có dung lượng hợp lệ (> 1KB) thì bỏ qua
    if dest_path.exists() and dest_path.stat().st_size > 1024:
        return True, "skipped"

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest_path.with_suffix(".tmp")

    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, headers=HEADERS, timeout=timeout, stream=True)
            if resp.status_code == 200:
                with open(temp_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=32768):
                        if chunk:
                            f.write(chunk)

                if temp_path.stat().st_size > 512:
                    # Kiểm tra tỷ lệ scale
                    if ratio < 100:
                        try:
                            with Image.open(temp_path) as img:
                                origin_w, origin_h = img.size
                                new_w = max(1, int(round(origin_w * ratio / 100.0)))
                                new_h = max(1, int(round(origin_h * ratio / 100.0)))

                                # Đảm bảo hệ màu RGB khi lưu JPEG
                                if img.mode in ("RGBA", "P"):
                                    img = img.convert("RGB")

                                resample_filter = getattr(Image, "Resampling", Image).LANCZOS
                                resized_img = img.resize((new_w, new_h), resample=resample_filter)
                                resized_img.save(dest_path, "JPEG", quality=85, optimize=True)

                            if temp_path.exists():
                                temp_path.unlink()
                            return True, "downloaded"
                        except Exception:
                            # Fallback nếu lỗi đọc ảnh thì lưu file gốc
                            temp_path.replace(dest_path)
                            return True, "downloaded"
                    else:
                        # ratio == 100%: Giữ nguyên gốc không scale
                        temp_path.replace(dest_path)
                        return True, "downloaded"
                else:
                    if temp_path.exists():
                        temp_path.unlink()
            elif resp.status_code == 404:
                return False, "404_not_found"

        except Exception:
            if attempt == retries:
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except Exception:
                        pass
                return False, "error"
            time.sleep(0.5 * attempt)

    return False, "failed"


def build_download_tasks(
    df: pd.DataFrame,
    pt_to_id: Dict[str, str],
    dist_to_id: Dict[str, str],
    output_dir: Path,
    limit: int = 0,
) -> List[dict]:
    """Duyệt DataFrame và sinh danh sách tác vụ tải ảnh."""
    tasks = []
    rows = df.iloc[:limit] if limit > 0 else df

    for _, row in rows.iterrows():
        listing_id = row.get("listing_id")
        property_type = str(row.get("property_type") or "").strip()
        district = str(row.get("district") or "").strip()
        raw_urls = row.get("image_urls")

        if not listing_id or pd.isna(listing_id):
            continue

        # Parse danh sách URL ảnh
        urls = []
        if isinstance(raw_urls, list):
            urls = raw_urls
        elif isinstance(raw_urls, str) and raw_urls.strip():
            try:
                urls = json.loads(raw_urls)
            except Exception:
                urls = []

        if not urls:
            continue

        # Lấy mã loại BĐS và mã phường chuẩn 3 chữ số
        pt_code = pt_to_id.get(property_type, "000")
        dist_code = get_district_code(district, dist_to_id)

        # Thư mục đích: output_dir / <property_type> / <district> / <listing_id>
        listing_dir = output_dir / property_type / district / str(listing_id)

        for idx, img_url in enumerate(urls, start=1):
            # Tên file: {mãloạibđs}_{mãphường}_{listing_id}_{số}.jpg
            filename = f"{pt_code}_{dist_code}_{listing_id}_{idx:03d}.jpg"
            dest_path = listing_dir / filename

            tasks.append({
                "listing_id": listing_id,
                "property_type": property_type,
                "district": district,
                "pt_code": pt_code,
                "dist_code": dist_code,
                "img_index": idx,
                "url": img_url,
                "dest_path": dest_path,
            })

    return tasks


def main():
    parser = argparse.ArgumentParser(description="Tải ảnh BĐS theo cấu trúc phân cấp.")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="Đường dẫn cleaned_master_data.csv")
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING, help="Đường dẫn mapping_dictionaries.json")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Thư mục gốc lưu ảnh")
    parser.add_argument("--limit", type=int, default=0, help="Giới hạn số tin tải (0 = tất cả)")
    parser.add_argument("--workers", type=int, default=12, help="Số luồng tải đồng thời")
    parser.add_argument("--timeout", type=int, default=15, help="Timeout request mỗi ảnh (giây)")
    parser.add_argument(
        "--ratio",
        type=int,
        default=100,
        help="Tỷ lệ scale ảnh theo %% (số nguyên, mặc định: 100). Ví dụ: --ratio 50 để giảm 50%% kích thước.",
    )
    args = parser.parse_args()

    if args.ratio <= 0 or args.ratio > 100:
        print(f"Lỗi: --ratio phải là số nguyên từ 1 đến 100 (bạn đã nhập: {args.ratio})")
        return

    print("=" * 65)
    print("BẮT ĐẦU CHƯƠNG TRÌNH TẢI ẢNH BẤT ĐỘNG SẢN")
    print("=" * 65)

    # 1. Đọc từ điển ánh xạ
    pt_to_id, dist_to_id = load_mappings(args.mapping)
    print(f"1. Đã nạp từ điển: {len(pt_to_id)} loại BĐS, {len(dist_to_id)} district.")

    # 2. Đọc file CSV
    if not args.csv.exists():
        print(f"Lỗi: Không tìm thấy file {args.csv}")
        return

    df = pd.read_csv(args.csv)
    total_listings = len(df)
    active_listings = len(df.iloc[:args.limit]) if args.limit > 0 else total_listings
    print(f"2. Đã đọc CSV: Xử lý {active_listings} / {total_listings} tin đăng.")

    # 3. Chuẩn bị danh sách tác vụ tải ảnh
    tasks = build_download_tasks(
        df=df,
        pt_to_id=pt_to_id,
        dist_to_id=dist_to_id,
        output_dir=args.output_dir,
        limit=args.limit,
    )
    print(f"3. Tổng số ảnh cần xử lý: {len(tasks)} ảnh.")

    if args.ratio < 100:
        print(f"   -> Chế độ Scale: BẬT ({args.ratio}% kích thước gốc ratio*origin_w : ratio*origin_h).")
    else:
        print("   -> Chế độ Scale: TẮT (100% kích thước gốc).")

    if not tasks:
        print("Không có ảnh nào cần tải.")
        return

    # In mẫu 3 task đầu tiên
    print("\nVí dụ 3 file đầu tiên:")
    for t in tasks[:3]:
        print(f"  -> {t['dest_path']}")

    # 4. Tải ảnh đa luồng
    session = requests.Session()
    stats = {"downloaded": 0, "skipped": 0, "failed": 0}

    print(f"\n4. Đang tiến hành tải với {args.workers} luồng đồng thời...")
    start_time = time.time()

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_to_task = {
                executor.submit(
                    download_single_image,
                    session,
                    t["url"],
                    t["dest_path"],
                    args.timeout,
                    3,
                    args.ratio,
                ): t
                for t in tasks
            }

            with tqdm(total=len(tasks), desc="Tiến độ tải ảnh", unit="ảnh") as pbar:
                for future in as_completed(future_to_task):
                    success, status = future.result()
                    if status == "downloaded":
                        stats["downloaded"] += 1
                    elif status == "skipped":
                        stats["skipped"] += 1
                    else:
                        stats["failed"] += 1
                    pbar.update(1)
    except KeyboardInterrupt:
        print("\n\n" + "=" * 65)
        print("[ĐÃ TẠM DỪNG] Bạn đã bấm Ctrl+C dừng tiến trình.")
        print("=" * 65)
        print(f"- Các ảnh đã tải về máy: {stats['downloaded']} ảnh mới ({stats['skipped']} ảnh có sẵn).")
        print("- Dữ liệu đã lưu an toàn tuyệt đối, không có file hỏng.")
        print("- Để tiếp tục tải các ảnh còn thiếu, bạn chỉ cần CHẠY LẠI ĐÚNG LỆNH CŨ:")
        print(f"  python pipeline/download_images.py --workers {args.workers} --ratio {args.ratio}")
        return

    elapsed = time.time() - start_time
    print("\n" + "=" * 65)
    print("HOÀN TẤT QUÁ TRÌNH TẢI ẢNH")
    print("=" * 65)
    print(f"- Thời gian thực hiện: {elapsed:.2f} giây")
    print(f"- Đã tải mới:         {stats['downloaded']} ảnh")
    print(f"- Đã có sẵn (Skip):   {stats['skipped']} ảnh")
    print(f"- Thất bại/Lỗi:        {stats['failed']} ảnh")
    print(f"- Tỷ lệ scale:        {args.ratio}%" if args.ratio < 100 else "- Tỷ lệ scale:        100% (gốc)")
    print(f"- Thư mục lưu trữ:    {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
