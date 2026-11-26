"""Allowed values and names of the listing schema (docs/plan.html, "Định dạng chuẩn").

Categorical fields store a code ("can_ho_chung_cu"); the catalog stores the Vietnamese label, so
each code keeps its label. Field names accept common Vietnamese and English spellings so that
spreadsheet exports work without renaming columns.
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

from src.services.listing_submission.coercion import Category, plain

PROPERTY_TYPES = (
    Category("can_ho_chung_cu", "Căn hộ chung cư", ("can ho", "chung cu", "apartment", "condo", "condominium")),
    Category("nha_rieng", "Nhà riêng", ("nha hem", "nha trong hem", "nha ngo", "house")),
    Category("nha_mat_pho", "Nhà mặt phố", ("mat pho", "nha mat tien", "mat tien")),
    Category("nha_biet_thu_lien_ke", "Nhà biệt thự, liền kề",
             ("biet thu", "lien ke", "biet thu lien ke", "nha biet thu", "nha lien ke", "villa", "townhouse")),
    Category("shophouse_nha_pho_thuong_mai", "Shophouse, nhà phố thương mại",
             ("shophouse", "shop house", "nha pho thuong mai")),
)

LEGAL_STATUSES = (
    Category("so_hong_rieng", "Sổ hồng riêng", ("so hong", "so do", "so do/ so hong", "so do so hong", "shr", "co so")),
    Category("so_chung", "Sổ chung", ("so hong chung", "sdc")),
    Category("hop_dong_mua_ban", "Hợp đồng mua bán", ("hdmb", "hop dong")),
    Category("dang_cho_so", "Đang chờ sổ", ("cho so", "dang lam so")),
    Category("khong_ro", "Không rõ", ()),
)
# Free text, same order as normalize_legal_status in the price model: "chờ" before "chung"
# before "hợp đồng" before any mention of a title deed.
LEGAL_STATUS_RULES = (
    (r"\bcho\b|lam so|nop ho so", "dang_cho_so"),
    (r"\bchung\b|\bsdc\b", "so_chung"),
    (r"hop dong|hdmb", "hop_dong_mua_ban"),
    (r"\bso\b|\bhong\b|\bdo\b|\bshr\b", "so_hong_rieng"),
)

FURNISHINGS = (
    Category("day_du", "Đầy đủ", ("full", "full noi that", "day du noi that", "noi that day du")),
    Category("co_ban", "Cơ bản", ("noi that co ban",)),
    Category("cao_cap", "Cao cấp", ("noi that cao cap", "full noi that cao cap", "full cao cap")),
    Category("khong_noi_that", "Không nội thất", ("tho", "nha tho", "ban giao tho", "khong co noi that")),
)
# Free text: the most specific wording wins ("Full nội thất cao cấp" is cao_cap, not day_du).
FURNISHING_RULES = (
    (r"cao cap", "cao_cap"),
    (r"khong noi that|ban giao tho|\btho\b", "khong_noi_that"),
    (r"co ban", "co_ban"),
    (r"day du|\bfull\b", "day_du"),
)

AMENITY_CATEGORIES = (
    Category("metro", "Ga metro", ("ga metro", "tau dien", "metro station")),
    Category("bus_stop", "Trạm xe buýt", ("tram xe buyt", "xe buyt", "tram bus", "bus")),
    Category("school", "Trường học", ("truong", "truong hoc")),
    Category("college_university", "Đại học, cao đẳng", ("dai hoc", "cao dang", "university", "college")),
    Category("hospital", "Bệnh viện", ("benh vien",)),
    Category("mall", "Trung tâm thương mại", ("tttm", "trung tam thuong mai", "shopping mall")),
    Category("supermarket", "Siêu thị", ("sieu thi",)),
    Category("market", "Chợ", ("cho",)),
    Category("park", "Công viên", ("cong vien",)),
)
AMENITIES_PER_CATEGORY = 5

REQUIRED_FIELDS = (
    "title", "description", "ward", "former_district", "property_type",
    "price_billion_vnd", "area_m2", "posted_date",
)
OPTIONAL_FIELDS = (
    "bedroom_count", "bathroom_count", "legal_status", "furnishing",
    "latitude", "longitude", "street", "project", "nearby_amenities",
)
# Accepted but not stored as typed: the server assigns listing ids, and a combined coordinate
# text is split into latitude and longitude.
REFERENCE_FIELD = "listing_id"
COMBINED_COORDINATES_FIELD = "coordinates"

FIELD_LABELS = {
    "listing_id": "Mã tin", "title": "Tiêu đề", "description": "Mô tả", "ward": "Phường/xã",
    "former_district": "Quận/huyện cũ", "property_type": "Loại BĐS", "price_billion_vnd": "Giá",
    "area_m2": "Diện tích", "posted_date": "Ngày đăng", "bedroom_count": "Phòng ngủ",
    "bathroom_count": "Phòng tắm", "legal_status": "Pháp lý", "furnishing": "Nội thất",
    "latitude": "Vĩ độ", "longitude": "Kinh độ", "coordinates": "Tọa độ", "street": "Đường",
    "project": "Dự án", "nearby_amenities": "Tiện ích lân cận", "metadata": "metadata",
}

# Other spellings of each field name, compared after plain() with spaces, "-" and "." as "_".
FIELD_ALIASES = {
    "listing_id": ("id", "ma_tin", "listingid", "ma"),
    "title": ("tieu_de", "ten_tin", "name"),
    "description": ("mo_ta", "noi_dung", "chi_tiet", "desc"),
    "ward": ("phuong", "phuong_xa", "xa", "new_ward"),
    "former_district": ("quan_cu", "quan_huyen_cu", "quan_huyen", "quan", "huyen", "old_district", "former_area",
                        "former_admin_area"),
    "property_type": ("loai", "loai_bds", "loai_nha", "loai_hinh", "type", "propertytype"),
    "price_billion_vnd": ("price", "gia", "gia_ban", "price_billion", "gia_ty", "price_range", "gia_ty_vnd"),
    "area_m2": ("area", "dien_tich", "dien_tich_m2", "areas"),
    "posted_date": ("ngay_dang", "date", "posted", "ngay"),
    "bedroom_count": ("bedrooms", "bedroom", "phong_ngu", "so_phong_ngu", "pn"),
    "bathroom_count": ("bathrooms", "bathroom", "phong_tam", "so_phong_tam", "wc", "toilet", "so_wc", "ve_sinh"),
    "legal_status": ("phap_ly", "giay_to", "legal"),
    "furnishing": ("noi_that", "furniture"),
    "latitude": ("lat", "vi_do"),
    "longitude": ("lng", "lon", "long", "kinh_do"),
    "coordinates": ("toa_do", "latitude_longitude", "lat_lng", "latlng", "location", "vi_tri"),
    "street": ("duong", "ten_duong", "street_name", "dia_chi_duong"),
    "project": ("du_an", "ten_du_an", "project_name"),
    "nearby_amenities": ("amenities", "tien_ich", "tien_ich_gan", "nearby"),
    "metadata": ("meta", "thong_tin_them", "optional"),
}

AMENITY_FIELD_ALIASES = {
    "category": ("loai", "nhom", "type"),
    "name": ("ten",),
    "driving_distance_km": ("distance_km", "distance", "khoang_cach", "quang_duong", "km"),
    "driving_duration_minutes": ("duration_minutes", "duration", "thoi_gian", "phut", "minutes"),
}


def key_form(name: str) -> str:
    """Comparable form of a field name: plain text with spaces, "-" and "." turned into "_"."""
    return re.sub(r"[\s.\-]+", "_", plain(name)).strip("_")


def _alias_index(aliases: Dict[str, Tuple[str, ...]]) -> Dict[str, str]:
    """Map every accepted spelling (the field name included) to its field."""
    index: Dict[str, str] = {}
    for field, spellings in aliases.items():
        for spelling in (field, *spellings):
            index[key_form(spelling)] = field
    return index


_FIELD_INDEX = _alias_index(FIELD_ALIASES)
_AMENITY_FIELD_INDEX = _alias_index(AMENITY_FIELD_ALIASES)


def resolve_field(name: str) -> Optional[str]:
    """The schema field a typed key names ("Giá" -> "price_billion_vnd"), or None if unknown."""
    return _FIELD_INDEX.get(key_form(name))


def resolve_amenity_field(name: str) -> Optional[str]:
    """The amenity field a typed key names ("khoang_cach" -> "driving_distance_km"), or None."""
    return _AMENITY_FIELD_INDEX.get(key_form(name))
