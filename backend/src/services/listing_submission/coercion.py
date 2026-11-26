"""Lenient conversion of user-typed listing values into the listing schema's types.

Each converter accepts the usual ways of writing a value and returns it in its canonical type,
or raises CoercionError with a stable code and a Vietnamese message. The rule throughout: be
lenient about format, never guess between readings. A text with two plausible readings
("1.500" is 1500 in Vietnamese and 1.5 in English) is reported as ambiguous unless only one
reading lies in the field's plausible range.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, List, Optional, Sequence, Tuple

from src.search.normalizer import normalize_text

# A written number: digits with optional "." or "," groups ("4", "4,5", "1.234.567", "1.234,5").
NUMBER_PATTERN = r"\d+(?:[.,]\d+)*"
_NUMBER = re.compile(NUMBER_PATTERN)
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_EARLIEST_POSTED_DATE = date(2000, 1, 1)


@dataclass(frozen=True)
class Problem:
    """Why a value cannot be used.

    Attributes:
        code: Stable machine code, e.g. "AMBIGUOUS_NUMBER".
        message: Vietnamese explanation for the person who typed the value.
        suggestion: Optional Vietnamese hint on how to write the value instead.
    """

    code: str
    message: str
    suggestion: Optional[str] = None


class CoercionError(ValueError):
    """Raised by a converter that cannot turn a value into its type; carries a Problem."""

    def __init__(self, code: str, message: str, suggestion: Optional[str] = None):
        super().__init__(message)
        self.problem = Problem(code, message, suggestion)


def plain(value: str) -> str:
    """Lowercase text without accents or extra spaces, for comparing words ("Quận 2" -> "quan 2")."""
    return normalize_text(value)


def format_number(value: float) -> str:
    """Write a number the Vietnamese way without thousands marks (1234.5 -> "1234,5")."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def is_number(value: Any) -> bool:
    """Whether a JSON value is a real number (bool is excluded, NaN and infinity too)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def number_readings(token: str) -> List[float]:
    """Every reading of one written number.

    Args:
        token: Digits with optional "." or "," marks, e.g. "70,5", "1.234", "1.234.567,5".

    Returns:
        list[float]: One reading, or two when a single mark followed by exactly three digits
        can be a decimal mark or a thousands mark ("1.500" -> [1.5, 1500.0]).

    Raises:
        CoercionError: INVALID_NUMBER when thousands groups are not three digits long.
    """
    marks = [character for character in token if character in ".,"]
    if not marks:
        return [float(token)]
    invalid = CoercionError("INVALID_NUMBER", f"Không đọc được số «{token}».",
                            "Vui lòng dùng dấu phẩy cho phần thập phân, ví dụ 70,5.")
    if len(set(marks)) == 2:
        # Both marks: the last one is the decimal mark, the other separates thousands.
        decimal_mark = token[max(token.rfind("."), token.rfind(","))]
        thousands_mark = "," if decimal_mark == "." else "."
        whole, fraction = token.rsplit(decimal_mark, 1)
        groups = whole.split(thousands_mark)
        if any(len(group) != 3 for group in groups[1:]) or thousands_mark in fraction:
            raise invalid
        return [float("".join(groups) + "." + fraction)]
    groups = token.split(marks[0])
    if len(groups) > 2:
        if any(len(group) != 3 for group in groups[1:]):
            raise invalid
        return [float("".join(groups))]
    whole, fraction = groups
    decimal_reading = float(f"{whole}.{fraction}")
    return [decimal_reading, float(whole + fraction)] if len(fraction) == 3 else [decimal_reading]


def pick_reading(readings: Iterable[float], minimum: float, maximum: float, unit: str, written: str) -> float:
    """The only reading inside a plausible range.

    Args:
        readings: Candidate values of one written number.
        minimum: Smallest plausible value.
        maximum: Largest plausible value.
        unit: Unit named in messages, e.g. "m²".
        written: The text as typed, quoted in messages.

    Returns:
        float: The reading inside [minimum, maximum].

    Raises:
        CoercionError: OUT_OF_RANGE when no reading fits, AMBIGUOUS_NUMBER when several do.
    """
    plausible = sorted({reading for reading in readings if minimum <= reading <= maximum})
    if len(plausible) == 1:
        return plausible[0]
    if not plausible:
        raise CoercionError(
            "OUT_OF_RANGE",
            f"«{written}» nằm ngoài khoảng hợp lý {format_number(minimum)}–{format_number(maximum)} {unit}.",
        )
    readings_text = " hay ".join(f"{format_number(reading)} {unit}".strip() for reading in plausible)
    raise CoercionError("AMBIGUOUS_NUMBER", f"«{written}» có thể hiểu là {readings_text}.",
                        "Vui lòng dùng dấu phẩy cho phần thập phân và bỏ dấu ngăn cách hàng nghìn, ví dụ 1,5 hoặc 1500.")


def single_number(text: str, written: str) -> List[float]:
    """Readings of a text that must hold exactly one number and nothing else.

    Raises:
        CoercionError: NOT_A_NUMBER when the text is not one number.
    """
    if not re.fullmatch(NUMBER_PATTERN, text):
        raise CoercionError("NOT_A_NUMBER", f"«{written}» không phải là một con số.")
    return number_readings(text)


def describe(value: Any) -> Any:
    """A JSON-safe echo of a typed value for reports, long texts shortened to 120 characters."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= 120 else text[:119] + "…"


def to_text(value: Any, minimum_length: int, maximum_length: int, is_multiline: bool = False) -> str:
    """Clean free text.

    Args:
        value: A string, or a number that is meant as text (a project called 123).
        minimum_length: Fewest characters after cleaning.
        maximum_length: Most characters after cleaning.
        is_multiline: Keep line breaks (descriptions); otherwise lines are joined.

    Returns:
        str: NFC text without control characters, runs of spaces collapsed, at most one blank
        line in a row; NFC because macOS file names and pasted text are often NFD.

    Raises:
        CoercionError: NOT_TEXT, TOO_SHORT or TOO_LONG.
    """
    if is_number(value):
        value = format_number(float(value)) if isinstance(value, float) else str(value)
    if not isinstance(value, str):
        raise CoercionError("NOT_TEXT", "Giá trị phải là chữ.")
    text = _CONTROL_CHARACTERS.sub("", unicodedata.normalize("NFC", value)).replace("\r\n", "\n").replace("\r", "\n")
    if is_multiline:
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    else:
        text = re.sub(r"\s+", " ", text).strip()
    if len(text) < minimum_length:
        raise CoercionError("TOO_SHORT", f"Cần ít nhất {minimum_length} ký tự, hiện có {len(text)}.")
    if len(text) > maximum_length:
        raise CoercionError("TOO_LONG", f"Tối đa {maximum_length} ký tự, hiện có {len(text)}.")
    return text


PRICE_MINIMUM, PRICE_MAXIMUM = 0.1, 2000.0
_PRICE_EXAMPLES = "Ví dụ: 4,5 tỷ · 4 tỷ 500 triệu · 850 triệu · 4500000000."


def to_price_billion(value: Any) -> float:
    """Sale price in billion VND.

    Accepts a number in billions (43.9), a number in VND (4500000000), and texts such as
    "4,5 tỷ", "4 tỷ 500 triệu", "4 tỷ 500", "4 tỷ 5", "4 tỷ rưỡi", "850 triệu", "850tr",
    "4.500.000.000 đ".

    Returns:
        float: The price, rounded to 4 decimals.

    Raises:
        CoercionError: NOT_A_PRICE for "thỏa thuận", RENT_OR_UNIT_PRICE for prices per month or
        per m², AMBIGUOUS_NUMBER, OUT_OF_RANGE or INVALID_PRICE.
    """
    if is_number(value):
        readings = [float(value)]
        written = format_number(float(value))
    elif isinstance(value, str):
        written = value.strip()
        readings = _price_text_readings(plain(value), written)
    else:
        raise CoercionError("INVALID_PRICE", "Giá phải là số hoặc chữ như «4,5 tỷ».", _PRICE_EXAMPLES)
    # Amounts of a million or more can only be VND: no listing costs a million billion.
    readings = [reading / 1e9 if reading >= 1e6 else reading for reading in readings]
    if all(reading < PRICE_MINIMUM for reading in readings) and any(reading > 0 for reading in readings):
        raise CoercionError("OUT_OF_RANGE", f"«{written}» dưới 100 triệu: có thể là giá thuê, không phải giá bán.",
                            _PRICE_EXAMPLES)
    return round(pick_reading(readings, PRICE_MINIMUM, PRICE_MAXIMUM, "tỷ", written), 4)


def _price_text_readings(text: str, written: str) -> List[float]:
    """Candidate prices (billions, or VND when large) of a plain price text; see to_price_billion."""
    if not text:
        raise CoercionError("MISSING", "Chưa có giá.")
    if re.search(r"thoa thuan|lien he|deal", text):
        raise CoercionError("NOT_A_PRICE", "Giá phải là con số cụ thể, không ghi «thỏa thuận» hay «liên hệ».", _PRICE_EXAMPLES)
    if re.search(r"/\s*(?:thang|th|nam|m2|m)\b|\b(?:moi thang|1 thang|thang)\b", text):
        raise CoercionError("RENT_OR_UNIT_PRICE", f"«{written}» là giá thuê hoặc đơn giá theo m², không phải giá bán.",
                            _PRICE_EXAMPLES)
    text = re.sub(r"\b(?:vnd|dong)\b|(?<=\d)\s*d\b", "", text).strip()
    billions = re.fullmatch(
        rf"({NUMBER_PATTERN})\s*(?:ty|ti)(?:\s*(ruoi)|\s*({NUMBER_PATTERN})\s*(trieu|tr)?)?", text)
    if billions:
        whole_readings = number_readings(billions.group(1))
        if billions.group(2):
            return [reading + 0.5 for reading in whole_readings]
        rest = billions.group(3)
        if rest is None:
            return whole_readings
        if billions.group(4):
            rest_billions = [reading / 1000 for reading in number_readings(rest)]
        elif re.fullmatch(r"\d{3}", rest):
            rest_billions = [int(rest) / 1000]
        elif re.fullmatch(r"\d", rest):
            rest_billions = [int(rest) / 10]
        else:
            raise CoercionError("AMBIGUOUS_NUMBER", f"Không rõ phần lẻ trong «{written}» là bao nhiêu triệu.",
                                "Vui lòng ghi rõ đơn vị, ví dụ «4 tỷ 50 triệu» hoặc «4,5 tỷ».")
        return [whole + extra for whole in whole_readings for extra in rest_billions]
    millions = re.fullmatch(rf"({NUMBER_PATTERN})\s*(?:trieu|tr)", text)
    if millions:
        return [reading / 1000 for reading in number_readings(millions.group(1))]
    if re.fullmatch(NUMBER_PATTERN, text):
        return number_readings(text)
    raise CoercionError("INVALID_PRICE", f"Không đọc được giá «{written}».", _PRICE_EXAMPLES)


AREA_MINIMUM, AREA_MAXIMUM = 5.0, 50000.0


def to_area_m2(value: Any) -> float:
    """Floor or land area in m².

    Accepts 325, "325", "79,9 m2", "70 m²", "70 mét vuông" and sizes "4x15", "4 m x 15 m"
    (multiplied).

    Returns:
        float: The area, rounded to 2 decimals.

    Raises:
        CoercionError: NOT_A_NUMBER, AMBIGUOUS_NUMBER or OUT_OF_RANGE.
    """
    if is_number(value):
        return round(pick_reading([float(value)], AREA_MINIMUM, AREA_MAXIMUM, "m²", format_number(float(value))), 2)
    if not isinstance(value, str):
        raise CoercionError("NOT_A_NUMBER", "Diện tích phải là số, ví dụ 70 hoặc «70 m2».")
    written = value.strip()
    # plain() turns "²" into "2", so "m²" arrives as "m2".
    text = re.sub(r"\s*(?:m2|met vuong|m)\b", "", plain(value).replace("m 2", "m2")).strip()
    size = re.fullmatch(rf"({NUMBER_PATTERN})\s*[x*×]\s*({NUMBER_PATTERN})", text)
    if size:
        readings = [width * length for width in number_readings(size.group(1)) for length in number_readings(size.group(2))]
    else:
        readings = single_number(text, written)
    return round(pick_reading(readings, AREA_MINIMUM, AREA_MAXIMUM, "m²", written), 2)


COUNT_MAXIMUM = 50


def to_room_count(value: Any) -> int:
    """Number of bedrooms or bathrooms.

    Accepts 4, 4.0, "4", "4 PN", "4pn", "4 phòng ngủ", "3 WC", "2 toilet" and "studio" (0).

    Returns:
        int: A whole number from 0 to 50.

    Raises:
        CoercionError: NOT_A_NUMBER, NOT_A_WHOLE_NUMBER or OUT_OF_RANGE.
    """
    if is_number(value):
        readings = [float(value)]
        written = format_number(float(value))
    elif isinstance(value, str):
        written = value.strip()
        text = plain(value)
        if text == "studio":
            return 0
        text = re.sub(r"\b(?:phong ngu|phong tam|phong ve sinh|ve sinh|phong|pn|wc|toilet|ngu|tam)\b", "", text)
        text = re.sub(r"(?<=\d)(?:pn|wc|p)\b", "", text).strip()
        readings = single_number(text, written)
    else:
        raise CoercionError("NOT_A_NUMBER", "Số phòng phải là số nguyên, ví dụ 3.")
    whole_readings = [reading for reading in readings if float(reading).is_integer()]
    if not whole_readings:
        raise CoercionError("NOT_A_WHOLE_NUMBER", f"«{written}» không phải số nguyên.")
    return int(pick_reading(whole_readings, 0, COUNT_MAXIMUM, "phòng", written))


def to_posted_date(value: Any, today: date) -> date:
    """Posting date.

    Accepts ISO dates ("2026-10-02", also with a time) and day-first dates ("2/10/2026",
    "02-10-2026", "02.10.2026"); Vietnamese dates put the day first, so "02/10" is 2 October.

    Args:
        value: The typed date.
        today: Today's date in Vietnam; later dates are refused.

    Returns:
        date: The posting date.

    Raises:
        CoercionError: INVALID_DATE or OUT_OF_RANGE.
    """
    if not isinstance(value, str):
        raise CoercionError("INVALID_DATE", "Ngày đăng phải ghi theo dạng «2026-10-02» hoặc «02/10/2026».")
    written = value.strip()
    iso = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ][\d:.]+(?:Z|[+-]\d{2}:?\d{2})?)?", written)
    day_first = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", written)
    try:
        if iso:
            parsed = date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        elif day_first:
            parsed = date(int(day_first.group(3)), int(day_first.group(2)), int(day_first.group(1)))
        else:
            raise ValueError(written)
    except ValueError:
        raise CoercionError("INVALID_DATE", f"Không đọc được ngày «{written}».",
                            "Ví dụ: 2026-10-02 hoặc 02/10/2026 (ngày đứng trước tháng).") from None
    if parsed > today:
        raise CoercionError("OUT_OF_RANGE", f"Ngày đăng {parsed.strftime('%d/%m/%Y')} là ngày trong tương lai.")
    if parsed < _EARLIEST_POSTED_DATE:
        raise CoercionError("OUT_OF_RANGE", f"Ngày đăng {parsed.strftime('%d/%m/%Y')} sớm hơn 01/01/2000.")
    return parsed


def to_decimal_degrees(value: Any, minimum: float, maximum: float, written: Optional[str] = None) -> float:
    """One coordinate in decimal degrees, its reading chosen by the city's range.

    Args:
        value: A number or a text such as "10,7792".
        minimum: Smallest value inside Ho Chi Minh City.
        maximum: Largest value inside Ho Chi Minh City.
        written: Text quoted in messages; defaults to the value.

    Returns:
        float: The coordinate, rounded to 7 decimals.

    Raises:
        CoercionError: NOT_A_NUMBER, AMBIGUOUS_NUMBER or OUT_OF_RANGE.
    """
    if is_number(value):
        readings = [float(value)]
        written = written or format_number(float(value))
    elif isinstance(value, str):
        written = written or value.strip()
        readings = single_number(value.strip().lstrip("+"), written)
    else:
        raise CoercionError("NOT_A_NUMBER", "Tọa độ phải là số, ví dụ 10,7792.")
    return round(pick_reading(readings, minimum, maximum, "độ", written), 7)


def coordinate_pair_tokens(value: Any) -> Tuple[str, str]:
    """The two numbers of a combined coordinate text ("10.77, 106.75", "q=10.77,106.75", a map link).

    Raises:
        CoercionError: INVALID_COORDINATES when the text does not hold exactly two numbers.
    """
    if not isinstance(value, str):
        raise CoercionError("INVALID_COORDINATES", "Tọa độ gộp phải ghi theo dạng «10.7792, 106.7571».")
    tokens = re.findall(r"\d+(?:[.,]\d+)?", value)
    if len(tokens) != 2:
        raise CoercionError("INVALID_COORDINATES", f"«{describe(value)}» phải có đúng hai số: vĩ độ và kinh độ.",
                            "Ví dụ: 10.7792, 106.7571.")
    return tokens[0], tokens[1]


def to_distance_km(value: Any) -> float:
    """Driving distance in km: 4.92, "4,92 km", "800 m" (converted to 0.8).

    Raises:
        CoercionError: NOT_A_NUMBER, AMBIGUOUS_NUMBER or OUT_OF_RANGE (0–100 km).
    """
    if is_number(value):
        return round(pick_reading([float(value)], 0, 100, "km", format_number(float(value))), 3)
    if not isinstance(value, str):
        raise CoercionError("NOT_A_NUMBER", "Quãng đường phải là số km, ví dụ 4,92.")
    written = value.strip()
    text = plain(value)
    in_metres = re.fullmatch(rf"({NUMBER_PATTERN})\s*m", text)
    if in_metres:
        readings = [reading / 1000 for reading in number_readings(in_metres.group(1))]
    else:
        readings = single_number(re.sub(r"\s*km\b", "", text).strip(), written)
    return round(pick_reading(readings, 0, 100, "km", written), 3)


def to_duration_minutes(value: Any) -> float:
    """Driving time in minutes: 5.9, "6 phút", "6 min".

    Raises:
        CoercionError: NOT_A_NUMBER, AMBIGUOUS_NUMBER or OUT_OF_RANGE (0–600 minutes).
    """
    if is_number(value):
        return round(pick_reading([float(value)], 0, 600, "phút", format_number(float(value))), 2)
    if not isinstance(value, str):
        raise CoercionError("NOT_A_NUMBER", "Thời gian phải là số phút, ví dụ 6.")
    written = value.strip()
    text = re.sub(r"\s*(?:phut|minutes|minute|mins|min|p)\b", "", plain(value)).strip()
    return round(pick_reading(single_number(text, written), 0, 600, "phút", written), 2)


@dataclass(frozen=True)
class Category:
    """One allowed value of a categorical field.

    Attributes:
        code: Value stored in the listing document, e.g. "can_ho_chung_cu".
        label: Vietnamese label, the value the catalog stores, e.g. "Căn hộ chung cư".
        aliases: Other plain spellings accepted as the same value.
    """

    code: str
    label: str
    aliases: Tuple[str, ...] = ()


def to_category(value: Any, categories: Sequence[Category], field_label: str,
                contains_rules: Sequence[Tuple[str, str]] = ()) -> str:
    """Match a typed value to one category.

    Args:
        value: The typed value: a code, a label or an alias, with or without accents.
        categories: The allowed values.
        field_label: Vietnamese field name for messages.
        contains_rules: (pattern, code) pairs tried in order on longer free text, for fields
            such as legal status where people write sentences ("Sổ hồng riêng, công chứng ngay").

    Returns:
        str: The category code.

    Raises:
        CoercionError: UNKNOWN_CATEGORY, with the allowed codes as suggestion.
    """
    if not isinstance(value, str) or not value.strip():
        raise CoercionError("UNKNOWN_CATEGORY", f"{field_label} phải là chữ.")
    text = plain(value.replace("_", " "))
    text = re.sub(r"[.;]+$", "", text).strip()
    for category in categories:
        spellings = {plain(category.code.replace("_", " ")), plain(category.label), *category.aliases}
        if text in spellings:
            return category.code
    for pattern, code in contains_rules:
        if re.search(pattern, text):
            return code
    allowed = ", ".join(category.code for category in categories)
    raise CoercionError("UNKNOWN_CATEGORY", f"Không nhận ra {field_label.lower()} «{describe(value)}».",
                        f"Vui lòng dùng một trong các giá trị: {allowed}.")


def parse_embedded_json(value: Any, expected_type: type) -> Any:
    """A value that may arrive as JSON text (a metadata object pasted as a string).

    Returns:
        Any: The value itself when it already has the expected type, else the parsed text.

    Raises:
        CoercionError: INVALID_JSON when the text is not JSON of the expected type.
    """
    if isinstance(value, expected_type):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = None
        if isinstance(parsed, expected_type):
            return parsed
    kind = "một JSON object {…}" if expected_type is dict else "một mảng JSON […]"
    raise CoercionError("INVALID_JSON", f"Cần {kind}.")


def today_in_vietnam() -> date:
    """Today's date in Vietnam (UTC+7, no daylight saving), whatever the server's time zone."""
    return datetime.now(timezone(timedelta(hours=7))).date()
