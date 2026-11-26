"""Save a validated, user-posted listing: its photos to the image store, its row to PostgreSQL.

The browser shrinks every photo to two sizes before sending (gallery: longest side 1280 px;
thumbnail: 480 px, shown on result cards), so the server only checks that each file is a real
image within those limits; it never trusts the browser for that. Photos go under
images/user-listings/<listing id>/ so the existing image route serves them unchanged. The row
is written with source "user", which the catalog seed never deletes, and its search vector is
computed afterwards in the background (embed_listing).
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from loguru import logger
from PIL import Image, UnidentifiedImageError

from src.search.embedding import embedding_model
from src.search.text_builder import build_listing_search_text
from src.services.listing_submission import vocabulary
from src.services.listing_submission.coercion import Category
from src.services.listing_submission.places import location_directory
from src.settings.event import minio_client, postgres_client

GALLERY_MAXIMUM_SIDE, GALLERY_MAXIMUM_BYTES = 1280, 1_500_000
THUMBNAIL_MAXIMUM_SIDE, THUMBNAIL_MAXIMUM_BYTES = 480, 200_000
MAXIMUM_PHOTOS = 20
ACCEPTED_FORMATS = {"JPEG": "image/jpeg", "WEBP": "image/webp", "PNG": "image/png"}
EXTENSIONS = {"JPEG": "jpg", "WEBP": "webp", "PNG": "png"}
USER_LISTING_TYPE = "Tin người dùng"
CITY = "Hồ Chí Minh"
LABELS = {
    "property_type": {category.code: category.label for category in vocabulary.PROPERTY_TYPES},
    "legal_status": {category.code: category.label for category in vocabulary.LEGAL_STATUSES},
    "furnishing": {category.code: category.label for category in vocabulary.FURNISHINGS},
}


class PhotoError(ValueError):
    """A posted photo is not acceptable; the message is Vietnamese, for the user."""


@dataclass(frozen=True)
class CheckedPhoto:
    """A posted photo that passed the checks.

    Attributes:
        content: The image bytes as sent.
        image_format: Pillow format name: "JPEG", "WEBP" or "PNG".
    """

    content: bytes
    image_format: str


def check_photo(content: bytes, maximum_side: int, maximum_bytes: int, label: str) -> CheckedPhoto:
    """Check that bytes are a real image within size limits.

    Args:
        content: The uploaded file.
        maximum_side: Largest allowed width or height in pixels.
        maximum_bytes: Largest allowed file size.
        label: Vietnamese name of the photo for messages, e.g. "Ảnh 3".

    Returns:
        CheckedPhoto: The bytes and their format.

    Raises:
        PhotoError: Empty, too large, not a JPEG/WebP/PNG image, or larger than maximum_side.
    """
    if not content:
        raise PhotoError(f"{label} trống.")
    if len(content) > maximum_bytes:
        raise PhotoError(f"{label} nặng {len(content) // 1000} KB, tối đa {maximum_bytes // 1000} KB.")
    try:
        with Image.open(io.BytesIO(content)) as image:
            image_format, width, height = image.format, image.width, image.height
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise PhotoError(f"{label} không phải ảnh hợp lệ.") from None
    if image_format not in ACCEPTED_FORMATS:
        raise PhotoError(f"{label} phải là JPEG, WebP hoặc PNG.")
    if max(width, height) > maximum_side:
        raise PhotoError(f"{label} có cạnh dài nhất {max(width, height)} px, tối đa {maximum_side} px.")
    return CheckedPhoto(content, image_format)


def check_photos(galleries: Sequence[bytes], thumbnails: Sequence[bytes]) -> List[Tuple[CheckedPhoto, CheckedPhoto]]:
    """Check a listing's photos: 1 to 20, each with its gallery and thumbnail size.

    Args:
        galleries: Gallery-size files, in display order.
        thumbnails: Thumbnail files, in the same order.

    Returns:
        list: (gallery, thumbnail) pairs.

    Raises:
        PhotoError: Wrong count, mismatched pairs or an unacceptable file.
    """
    if not galleries:
        raise PhotoError("Cần ít nhất 1 ảnh.")
    if len(galleries) > MAXIMUM_PHOTOS:
        raise PhotoError(f"Tối đa {MAXIMUM_PHOTOS} ảnh, hiện có {len(galleries)}.")
    if len(thumbnails) != len(galleries):
        raise PhotoError("Số ảnh thu nhỏ không khớp với số ảnh.")
    return [
        (check_photo(gallery, GALLERY_MAXIMUM_SIDE, GALLERY_MAXIMUM_BYTES, f"Ảnh {position}"),
         check_photo(thumbnail, THUMBNAIL_MAXIMUM_SIDE, THUMBNAIL_MAXIMUM_BYTES, f"Ảnh thu nhỏ {position}"))
        for position, (gallery, thumbnail) in enumerate(zip(galleries, thumbnails), start=1)
    ]


def photo_key(listing_id: int, size: str, position: int, image_format: str) -> str:
    """Image store key of a posted photo, e.g. images/user-listings/9000000001/gallery/001.jpg.

    Args:
        listing_id: The listing's id.
        size: "gallery" or "thumbnails"; the frontend swaps one for the other on result cards.
        position: 1-based position in the listing's photos.
        image_format: Pillow format name.
    """
    return f"images/user-listings/{listing_id}/{size}/{position:03d}.{EXTENSIONS[image_format]}"


def listing_row(listing: Dict[str, Any], listing_id: int, owner_user_id: int, gallery_keys: List[str]) -> Dict[str, Any]:
    """The properties row of a validated listing, in the catalog's own conventions.

    Args:
        listing: Canonical listing from validate_listing (codes, not labels).
        listing_id: Id from user_listing_id_seq, used as both id and listing_id.
        owner_user_id: The posting user.
        gallery_keys: Image store keys of the gallery photos, in order.

    Returns:
        dict: Column -> value. Labels replace codes because the catalog stores labels; the
        listing as validated is kept in "submission".
    """
    metadata = listing.get("metadata", {})
    coordinates = None
    if "latitude" in metadata and "longitude" in metadata:
        coordinates = f"{metadata['latitude']}, {metadata['longitude']}"
    address_parts = [metadata.get("project"), metadata.get("street"), listing["ward"], CITY]
    row = {
        "id": listing_id,
        "listing_id": listing_id,
        "listing_type": USER_LISTING_TYPE,
        "posted_date": listing["posted_date"],
        "property_type": LABELS["property_type"][listing["property_type"]],
        "city_province": CITY,
        "district": listing["ward"],
        "former_admin_area": listing["former_district"],
        "old_address": f"{listing['former_district']}, {CITY} cũ",
        "title": listing["title"],
        "description": listing["description"],
        "address": ", ".join(part for part in address_parts if part),
        "latitude_longitude": coordinates,
        "price_range": listing["price_billion_vnd"],
        "area": listing["area_m2"],
        "bedrooms": metadata.get("bedroom_count"),
        "bathrooms": metadata.get("bathroom_count"),
        "legal_status": LABELS["legal_status"].get(metadata.get("legal_status", ""), None),
        "furnishing": LABELS["furnishing"].get(metadata.get("furnishing", ""), None),
        "images": json.dumps(gallery_keys),
        "source": "user",
        "owner_user_id": owner_user_id,
        "submission": json.dumps(listing, ensure_ascii=False),
    }
    row["search_text"] = build_listing_search_text(row)
    return row


def save_listing(listing: Dict[str, Any], photos: List[Tuple[CheckedPhoto, CheckedPhoto]], owner_user_id: int) -> Dict[str, int]:
    """Store a validated listing and its photos.

    Photos are uploaded first so a saved row never points at missing images; if the row then
    cannot be written, the uploaded photos are removed again.

    Args:
        listing: Canonical, valid listing from validate_listing.
        photos: Checked (gallery, thumbnail) pairs.
        owner_user_id: The posting user.

    Returns:
        dict: {"id", "listing_id"} of the new row (the same number).

    Raises:
        ImageStorageUnavailableError: No image store is configured.
        sqlalchemy.exc.SQLAlchemyError, minio.S3Error: Storage failures.
    """
    listing_id = int(postgres_client.fetch_write("SELECT nextval('user_listing_id_seq') AS value")[0]["value"])
    uploaded: List[str] = []
    gallery_keys: List[str] = []
    try:
        for position, (gallery, thumbnail) in enumerate(photos, start=1):
            for size, photo in (("gallery", gallery), ("thumbnails", thumbnail)):
                key = photo_key(listing_id, size, position, photo.image_format)
                minio_client.put_image(key, photo.content, ACCEPTED_FORMATS[photo.image_format])
                uploaded.append(key)
                if size == "gallery":
                    gallery_keys.append(key)
        row = listing_row(listing, listing_id, owner_user_id, gallery_keys)
        columns = ", ".join(row)
        values = ", ".join(f"CAST(:{column} AS jsonb)" if column in ("images", "submission") else f":{column}" for column in row)
        postgres_client.execute_write(f"INSERT INTO properties ({columns}, search_text_version) VALUES ({values}, 'v1')", **row)
    except Exception:
        minio_client.remove_images(uploaded)
        raise
    return {"id": listing_id, "listing_id": listing_id}


def embed_listing(listing_id: int) -> bool:
    """Compute and store a posted listing's search vector (run in the background after saving).

    The listing is already searchable by text and filters; the vector adds it to semantic search.
    Failures are logged, not raised: the embedding service is optional.

    Args:
        listing_id: The row's id.

    Returns:
        bool: Whether a vector was stored.
    """
    try:
        rows = postgres_client.fetch_mappings("SELECT search_text FROM properties WHERE id = :id", id=listing_id)
        if not rows or not rows[0]["search_text"]:
            return False
        vectors = embedding_model.encode([rows[0]["search_text"]], kind="passage")
        if vectors is None:
            logger.warning("No embedding for posted listing {}: the embedding service is unavailable", listing_id)
            return False
        vector = "[" + ",".join(f"{float(value):.8f}" for value in vectors[0]) + "]"
        postgres_client.execute_write(
            "UPDATE properties SET embedding = CAST(:embedding AS vector), embedding_model = :model WHERE id = :id",
            embedding=vector, model=embedding_model.model_name, id=listing_id)
        return True
    except Exception:
        logger.exception("Embedding posted listing {} failed", listing_id)
        return False


def category_options(categories: Sequence[Category]) -> List[Dict[str, str]]:
    """Categories as form options: [{"code", "label"}]."""
    return [{"code": category.code, "label": category.label} for category in categories]


def listing_reference() -> Dict[str, Any]:
    """Choices the posting form offers: wards, former districts and the categorical codes.

    Returns:
        dict: {"wards": [names], "former_districts": [names], "ward_former_districts": {ward: [former
        districts its area belonged to]}, "former_district_groups": {former district: the unit it
        counts as, e.g. "Quận 2" -> "Thành phố Thủ Đức"}, "property_types", "legal_statuses",
        "furnishings": [{"code", "label"}], "limits": photo limits}. The form uses the two maps to
        narrow each place list to the other's choice.
    """
    reference = location_directory().reference
    return {
        "wards": [ward.name for ward in sorted(reference.wards, key=lambda ward: ward.name)],
        "former_districts": sorted(reference.former_districts),
        "ward_former_districts": {ward.name: list(ward.former_districts) for ward in reference.wards},
        "former_district_groups": {name: reference.group_of(name) for name in reference.former_districts},
        "property_types": category_options(vocabulary.PROPERTY_TYPES),
        "legal_statuses": category_options(vocabulary.LEGAL_STATUSES),
        "furnishings": category_options(vocabulary.FURNISHINGS),
        "limits": {"photos": MAXIMUM_PHOTOS, "gallery_side": GALLERY_MAXIMUM_SIDE, "thumbnail_side": THUMBNAIL_MAXIMUM_SIDE,
                   "gallery_bytes": GALLERY_MAXIMUM_BYTES, "thumbnail_bytes": THUMBNAIL_MAXIMUM_BYTES},
    }
