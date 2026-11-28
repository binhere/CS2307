#!/usr/bin/env python3
"""Turn the crawled catalog into zip files for the posting page's zip upload ("Nhiều tin (zip)").

Listings now come only from users, through the posting form and zip uploads, so the catalog goes
in the same way: validation, photo shrinking, coordinates, amenities and the graph all run on it
as on any posted listing. This script writes the upload format: one folder per listing holding
listing.json (the posting fields) and its photos, shown in file-name order.

Input:
    data/gold/sql/listings.csv     the catalog's 3,857 listings
    images.zip                     the photo archive, images/<type>/<ward>/<listing id>/<file>.jpg
Output:
    data/upload/catalog-part-NN-of-MM.zip, about --listings-per-part listings each, photos stored
    without recompression (they are JPEG). A listing keeps at most 20 photos, the posting limit;
    listings with more lose the last ones.

Not copied: the Google Maps link column (it embeds an API key) and crawl bookkeeping. The server
computes amenities itself, so none are sent.

Usage:
    python pipeline/build_upload_archives.py --archive images.zip [--listings-per-part 500]
Standard library only.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPOSITORY = Path(__file__).resolve().parents[1]
LISTINGS_FILE = REPOSITORY / "data" / "gold" / "sql" / "listings.csv"
OUTPUT_DIRECTORY = REPOSITORY / "data" / "upload"
MAXIMUM_PHOTOS = 20


def number(value: str) -> Optional[float]:
    """A float from a CSV cell, or None for blanks and text."""
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


# Compared in lower case: the crawl writes both "Thành phố" and "Thành Phố".
FORMER_DISTRICT_KINDS = ("quận ", "huyện ", "thành phố ", "thị xã ")


def former_district(old_address: str) -> Optional[str]:
    """The district before July 2025 from "Quận 2, Hồ Chí Minh cũ": "Quận 2".

    Some old addresses start with a ward instead ("Phường Chánh Hưng, Quận 8, ..."); the district is
    then the first part that is one. None when there is none: the server derives it from the ward.
    """
    parts = [part.strip() for part in (old_address or "").split(",")]
    return next((part for part in parts if part.lower().startswith(FORMER_DISTRICT_KINDS)), None)


def street_and_project(address: str, ward: str) -> Tuple[Optional[str], Optional[str]]:
    """Street and project from "Căn hộ DEFINE , Đường Số 60 - TML, Phường Cát Lái, Hồ Chí Minh".

    The address ends with the ward and the city; before them comes the street, and before that,
    when present, the project.

    Returns:
        tuple: (street, project), each None when absent.
    """
    parts = [part.strip() for part in (address or "").split(",") if part.strip()]
    if parts and parts[-1].startswith("Hồ Chí Minh"):
        parts = parts[:-1]
    if parts and parts[-1] == ward:
        parts = parts[:-1]
    street = parts[-1] if parts else None
    project = parts[-2] if len(parts) >= 2 else None
    return street, project


def listing_document(row: Dict[str, str]) -> Dict[str, object]:
    """The listing.json of one catalog row, in the posting schema (docs/plan.html).

    Args:
        row: A row of data/gold/sql/listings.csv.

    Returns:
        dict: Required fields at the top, optional ones under "metadata"; blanks left out so the
        server reports or fills them.
    """
    street, project = street_and_project(row["address"], row["district"])
    metadata: Dict[str, object] = {
        "bedroom_count": number(row["bedrooms"]), "bathroom_count": number(row["bathrooms"]),
        "legal_status": row["legal_status"] or None, "furnishing": row["furnishing"] or None,
        "street": street, "project": project,
    }
    if row.get("coordinate_status") == "valid" and number(row["latitude"]) is not None and number(row["longitude"]) is not None:
        metadata["latitude"], metadata["longitude"] = number(row["latitude"]), number(row["longitude"])
    for key in ("bedroom_count", "bathroom_count"):
        if metadata[key] is not None:
            metadata[key] = int(metadata[key])
    document: Dict[str, object] = {
        "listing_id": row["listing_id"], "title": row["title"], "description": row["description"],
        "property_type": row["property_type"], "price_billion_vnd": number(row["price_range"]),
        "area_m2": number(row["area"]), "ward": row["district"], "former_district": former_district(row["old_address"]),
        "posted_date": row["posted_date"] or None,
        "metadata": {key: value for key, value in metadata.items() if value is not None},
    }
    return {key: value for key, value in document.items() if value is not None}


def photos_by_listing(archive: zipfile.ZipFile) -> Dict[str, List[zipfile.ZipInfo]]:
    """The archive's photos per listing id, in file-name order.

    The archive was made without the UTF-8 name flag, so its Vietnamese folder names decode
    wrongly; the listing id (4th path part) and the file names are ASCII, which is all this needs.
    """
    photos: Dict[str, List[zipfile.ZipInfo]] = {}
    for info in archive.infolist():
        parts = info.filename.split("/")
        if len(parts) == 5 and parts[4].lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            photos.setdefault(parts[3], []).append(info)
    for infos in photos.values():
        infos.sort(key=lambda info: info.filename.split("/")[4])
    return photos


def main() -> None:
    """Write the upload parts and print what was left out."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--archive", type=Path, required=True, help="the photo archive (images.zip)")
    parser.add_argument("--listings-per-part", type=int, default=500)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIRECTORY)
    arguments = parser.parse_args()

    with LISTINGS_FILE.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    arguments.output.mkdir(parents=True, exist_ok=True)
    part_count = math.ceil(len(rows) / arguments.listings_per_part)
    trimmed, without_photos = [], []
    with zipfile.ZipFile(arguments.archive) as archive:
        photos = photos_by_listing(archive)
        for part in range(part_count):
            chunk = rows[part * arguments.listings_per_part:(part + 1) * arguments.listings_per_part]
            target = arguments.output / f"catalog-part-{part + 1:02d}-of-{part_count:02d}.zip"
            with zipfile.ZipFile(target, "w") as output:
                for row in chunk:
                    listing_id = row["listing_id"]
                    output.writestr(f"{listing_id}/listing.json", json.dumps(listing_document(row), ensure_ascii=False, indent=2),
                                    compress_type=zipfile.ZIP_DEFLATED)
                    infos = photos.get(listing_id, [])
                    if not infos:
                        without_photos.append(listing_id)
                    if len(infos) > MAXIMUM_PHOTOS:
                        trimmed.append((listing_id, len(infos)))
                    for info in infos[:MAXIMUM_PHOTOS]:
                        output.writestr(f"{listing_id}/{info.filename.split('/')[4]}", archive.read(info),
                                        compress_type=zipfile.ZIP_STORED)
            print(f"{target.name}: {len(chunk)} listings, {target.stat().st_size / 1e9:.2f} GB")
    print(f"Kept the first {MAXIMUM_PHOTOS} photos of {len(trimmed)} listings; {len(without_photos)} listings have no photo.")


if __name__ == "__main__":
    main()
