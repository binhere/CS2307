#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import sys
from pathlib import Path
import pandas as pd
from bs4 import BeautifulSoup

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CSV_PATH = DATA_DIR / "silver" / "cleaned_master_data.csv"
HTML_PATH = DATA_DIR / "bronze" / "district_list_html.txt"
OUTPUT_PATH = DATA_DIR / "silver" / "mapping_dictionaries.json"


def main():
    # 1. Property Type: Đọc từ CSV, sắp xếp A-Z
    df = pd.read_csv(CSV_PATH)
    property_types = sorted(df["property_type"].dropna().unique())
    property_type_to_id = {
        pt: f"{i:03d}" for i, pt in enumerate(property_types, start=1)
    }
    id_to_property_type = {v: k for k, v in property_type_to_id.items()}

    # 2. District: Đọc chính xác 169 district chính thức từ HTML
    with open(HTML_PATH, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")
    
    districts = [li.get_text(strip=True) for li in soup.find_all("li") if li.get_text(strip=True)]
    district_to_id = {
        d: f"{i:03d}" for i, d in enumerate(districts, start=1)
    }
    id_to_district = {v: k for k, v in district_to_id.items()}

    # 3. Xuất file JSON với đúng 4 trường được yêu cầu
    result = {
        "property_type_to_id": property_type_to_id,
        "id_to_property_type": id_to_property_type,
        "district_to_id": district_to_id,
        "id_to_district": id_to_district,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[OK] Đã tạo thành công {OUTPUT_PATH}:")
    print(f" - property_type_to_id: {len(property_type_to_id)} mục (001 -> {len(property_type_to_id):03d})")
    print(f" - district_to_id:      {len(district_to_id)} mục (001 -> {len(district_to_id):03d})")


if __name__ == "__main__":
    main()
