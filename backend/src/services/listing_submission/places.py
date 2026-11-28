"""Match typed ward and former district names to Ho Chi Minh City's official ones.

People write places many ways: "phuong cat lai", "P. Cát Lái", "Cát Lái", "Q2", "Quận 02",
and often an old ward name from before 1 July 2025 ("Phường Thạnh Mỹ Lợi"). A name is matched
on its accent-free form with the administrative word ("Phường", "Quận"…) optional; accents typed
by the user settle names that differ only in accents ("Xã Thanh An" and "Xã Thạnh An"). An old
ward name becomes the new ward it was merged into, using the former district or the coordinates
when the old name existed in several districts. Nothing is guessed: what stays ambiguous is
reported with the candidates.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.services.listing_submission.coercion import CoercionError, describe, plain
from src.services.listing_submission.geography import AddressReference, FormerWard, Ward, address_reference

# Administrative words that can open a place name, each mapped to its accent-free keyword.
# Keywords of former districts: typed in a ward field, they name a district, not an old ward.
DISTRICT_KEYWORDS = {"quan", "huyen", "thi xa", "thanh pho"}
_PLACE_KEYWORDS = (
    (r"dac khu|dk", "dac khu"),
    (r"thanh pho|tp", "thanh pho"),
    (r"thi xa|tx", "thi xa"),
    (r"thi tran|tt", "thi tran"),
    (r"quan|q|district", "quan"),
    (r"huyen|h", "huyen"),
    (r"phuong|p|ward", "phuong"),
    (r"xa|commune", "xa"),
)
_PLACE_PREFIX = re.compile(r"^(" + "|".join(pattern for pattern, _ in _PLACE_KEYWORDS) + r")(?:\.\s*|\s+|(?=\d))")
_KEYWORD_WORDS = {"dac khu": 2, "thanh pho": 2, "thi xa": 2, "thi tran": 2}


@dataclass(frozen=True)
class TypedPlace:
    """A typed place name taken apart.

    Attributes:
        keyword: Accent-free administrative word if one was typed ("phuong"), else None.
        bare_name: Accent-free name without that word ("cat lai").
        accented_text: Lowercase NFC text as typed (for settling names that differ in accents).
        has_accents: Whether the user typed any Vietnamese accent.
    """

    keyword: Optional[str]
    bare_name: str
    accented_text: str
    has_accents: bool


def parse_place(value: str) -> TypedPlace:
    """Take a typed place apart: "P. Cát Lái, TP.HCM" -> ("phuong", "cat lai", ...)."""
    first_part = unicodedata.normalize("NFC", value).split(",")[0].strip()
    text = re.sub(r"\s+cu$", "", plain(first_part)).strip()  # "Quận 2 cũ"
    keyword = None
    prefix = _PLACE_PREFIX.match(text)
    if prefix:
        keyword = next(canonical for pattern, canonical in _PLACE_KEYWORDS if re.fullmatch(pattern, prefix.group(1)))
        text = text[prefix.end():].strip()
    bare_name = re.sub(r"^0+(?=\d)", "", text)  # "Quận 02" -> "2"
    accented_text = re.sub(r"\s+", " ", first_part.lower())
    return TypedPlace(keyword, bare_name, accented_text, accented_text != plain(first_part))


def accented_bare_name(name: str) -> str:
    """An official name without its administrative word, accents kept: "Xã Thạnh An" -> "thạnh an"."""
    words = unicodedata.normalize("NFC", name).lower().split()
    keyword = plain(" ".join(words[:2]))
    return " ".join(words[_KEYWORD_WORDS.get(keyword, 1):]) if len(words) > 1 else words[0]


@dataclass(frozen=True)
class Place:
    """A place a directory can return.

    Attributes:
        name: Official name, e.g. "Quận Thủ Đức".
        keyword: Accent-free administrative word, e.g. "quan".
        bare_name: Accent-free name without the keyword, e.g. "thu duc".
        group: Name of the unit the place counts as; places of one group are the same answer
            ("Quận Thủ Đức" and "Thành phố Thủ Đức" are both "Thành phố Thủ Đức").
    """

    name: str
    keyword: str
    bare_name: str
    group: str


def place_from_name(name: str, group: Optional[str] = None) -> Place:
    """A Place built from an official name."""
    typed = parse_place(name)
    return Place(name, typed.keyword or "", typed.bare_name, group or name)


def settle_by_accents(typed: TypedPlace, candidates: Sequence[Place]) -> List[Place]:
    """Keep the candidates whose accented name matches what was typed, when accents were typed."""
    if not typed.has_accents or len(candidates) < 2:
        return list(candidates)
    matching = [place for place in candidates if typed.accented_text.endswith(accented_bare_name(place.name))]
    return matching or list(candidates)


class PlaceDirectory:
    """Matches typed names to one kind of place (wards, or former districts)."""

    def __init__(self, places: Sequence[Place], field_label: str):
        """Index places by their accent-free bare name.

        Args:
            places: The places of one kind.
            field_label: Vietnamese field name for messages, e.g. "Phường/xã".
        """
        self.field_label = field_label
        self.places = list(places)
        self.by_bare_name: Dict[str, List[Place]] = {}
        for place in self.places:
            self.by_bare_name.setdefault(place.bare_name, []).append(place)

    def candidates(self, typed: TypedPlace) -> List[Place]:
        """Places with the typed bare name, narrowed to the typed keyword when that leaves any."""
        candidates = self.by_bare_name.get(typed.bare_name, [])
        if typed.keyword:
            candidates = [place for place in candidates if place.keyword == typed.keyword] or candidates
        return settle_by_accents(typed, candidates)

    def match(self, value: Any, is_preferred: Optional[Callable[[Place], bool]] = None) -> Place:
        """The official place a typed name means.

        Args:
            value: The typed name.
            is_preferred: Optional tie-breaker from other fields (coordinates, former district),
                used only when several places have the typed name.

        Returns:
            Place: The matched place; for several places of one group, the group's own place.

        Raises:
            CoercionError: UNKNOWN_PLACE (with close names as suggestion) or AMBIGUOUS_PLACE.
        """
        if not isinstance(value, str) or not value.strip():
            raise CoercionError("UNKNOWN_PLACE", f"{self.field_label} phải là chữ.")
        candidates = self.candidates(parse_place(value))
        if len({place.group for place in candidates}) == 1 and len(candidates) > 1:
            return next((place for place in candidates if place.name == place.group), candidates[0])
        if len(candidates) > 1 and is_preferred:
            candidates = [place for place in candidates if is_preferred(place)] or candidates
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            names = ", ".join(place.name for place in candidates)
            raise CoercionError("AMBIGUOUS_PLACE", f"«{describe(value)}» trùng tên với nhiều nơi: {names}.",
                                "Vui lòng ghi đúng dấu và kèm chữ Phường, Xã, Quận hoặc Huyện.")
        raise self.unknown(value)

    def unknown(self, value: Any) -> CoercionError:
        """The UNKNOWN_PLACE error for a typed name, suggesting close official names."""
        bare_name = parse_place(value).bare_name if isinstance(value, str) else ""
        close = difflib.get_close_matches(bare_name, list(self.by_bare_name), n=3, cutoff=0.75)
        suggestion = ("Tên gần giống: " + ", ".join(self.by_bare_name[name][0].name for name in close) + ".") if close else None
        return CoercionError("UNKNOWN_PLACE", f"Không có {self.field_label.lower()} «{describe(value)}» ở TP.HCM.",
                             suggestion)


@dataclass(frozen=True)
class WardMatch:
    """A typed ward resolved to an official ward.

    Attributes:
        ward: The official ward.
        former_ward: The old ward the typed name meant, when an old name was typed.
        former_district: The former district typed in the ward field ("Quận 3"), when the ward was
            then taken from the coordinates; it is also the listing's former district.
    """

    ward: Ward
    former_ward: Optional[FormerWard] = None
    former_district: Optional[str] = None


class LocationDirectory:
    """Ward and former district matching over the official reference."""

    def __init__(self, reference: AddressReference):
        """Build the directories.

        Args:
            reference: Official wards, former districts and boundaries.
        """
        self.reference = reference
        self.wards = PlaceDirectory([place_from_name(ward.name) for ward in reference.wards], "Phường/xã")
        self.former_districts = PlaceDirectory(
            [place_from_name(name, reference.group_of(name)) for name in reference.former_districts], "Quận/huyện cũ")
        self.old_wards: Dict[str, List[Tuple[Place, FormerWard, Ward]]] = {}
        for ward in reference.wards:
            for former in ward.former_wards:
                if former.ward:
                    place = place_from_name(former.ward)
                    self.old_wards.setdefault(place.bare_name, []).append((place, former, ward))

    def match_former_district(self, value: Any) -> str:
        """Official name of a typed former district ("Q2" -> "Quận 2", "Thủ Đức" -> "Thành phố Thủ Đức").

        Raises:
            CoercionError: UNKNOWN_PLACE or AMBIGUOUS_PLACE.
        """
        return self.former_districts.match(value).name

    def was_in(self, ward: Ward, former_district: str) -> bool:
        """Whether a ward's area belonged to a former district (groups count as one)."""
        group = self.reference.group_of(former_district)
        return any(self.reference.group_of(district) == group for district in ward.former_districts)

    def match_ward(self, value: Any, former_district: Optional[str] = None, point_ward: Optional[Ward] = None) -> WardMatch:
        """The official ward a typed name means, current or old.

        Args:
            value: The typed ward.
            former_district: The listing's former district if known; settles old names that
                existed in several districts ("Phường 14").
            point_ward: The ward containing the listing's coordinates, if any; settles the rest.

        Returns:
            WardMatch: The ward, and the old ward when an old name was typed.

        Raises:
            CoercionError: UNKNOWN_PLACE or AMBIGUOUS_PLACE.
        """
        def is_preferred(place: Place) -> bool:
            ward = self.reference.ward_by_name[place.name]
            if point_ward is not None:
                return ward.code == point_ward.code
            return former_district is not None and self.was_in(ward, former_district)

        try:
            return WardMatch(self.reference.ward_by_name[self.wards.match(value, is_preferred).name])
        except CoercionError as error:
            if error.problem.code != "UNKNOWN_PLACE":
                raise
            unknown = error
        typed = parse_place(value)
        if typed.keyword in DISTRICT_KEYWORDS:
            return self._match_district_as_ward(value, point_ward, unknown)
        entries = self.old_wards.get(typed.bare_name, [])
        if typed.keyword:
            entries = [entry for entry in entries if entry[0].keyword == typed.keyword] or entries
        if typed.has_accents and len({entry[0].name for entry in entries}) > 1:
            entries = [entry for entry in entries if typed.accented_text.endswith(accented_bare_name(entry[0].name))] or entries
        if former_district is not None:
            group = self.reference.group_of(former_district)
            entries = [entry for entry in entries if self.reference.group_of(entry[1].district) == group] or entries
        if point_ward is not None and len({entry[2].code for entry in entries}) > 1:
            entries = [entry for entry in entries if entry[2].code == point_ward.code] or entries
        new_wards = {entry[2].code: entry[2] for entry in entries}
        if len(new_wards) == 1:
            place, former, ward = entries[0]
            return WardMatch(ward, former)
        if new_wards:
            names = ", ".join(sorted(ward.name for ward in new_wards.values())[:6])
            # An ellipsis already ends the sentence: no full stop after it.
            ending = "…" if len(new_wards) > 6 else "."
            raise CoercionError("AMBIGUOUS_PLACE",
                                f"«{describe(value)}» là tên cũ, nay thuộc một trong các phường/xã: {names}{ending}",
                                "Vui lòng ghi phường/xã mới, hoặc kèm quận/huyện cũ hay tọa độ.")
        raise unknown

    def _match_district_as_ward(self, value: Any, point_ward: Optional[Ward], unknown: CoercionError) -> WardMatch:
        """Resolve a former district typed where a ward belongs ("Quận 3" in the ward field).

        Its bare name ("3") must not be read as an old numbered ward: "Phường 3" existed in many
        districts, and even in Vũng Tàu. The ward is the coordinates' ward when it lies in that
        district; otherwise the wards made from the district are listed.

        Args:
            value: The typed ward field.
            point_ward: The ward containing the listing's coordinates, if any.
            unknown: The error to raise when the value is no former district either.

        Returns:
            WardMatch: The coordinates' ward, with the typed district as former_district.

        Raises:
            CoercionError: AMBIGUOUS_PLACE without usable coordinates; the given unknown error
                when the value names no former district.
        """
        try:
            district = self.match_former_district(value)
        except CoercionError:
            raise unknown from None
        if point_ward is not None and self.was_in(point_ward, district):
            return WardMatch(point_ward, former_district=district)
        candidates = sorted(ward.name for ward in self.reference.wards if self.was_in(ward, district))
        if len(candidates) == 1:
            return WardMatch(self.reference.ward_by_name[candidates[0]], former_district=district)
        # The list ends the sentence: an ellipsis when cut, a full stop otherwise.
        names = ", ".join(candidates[:6]) + ("…" if len(candidates) > 6 else ".")
        raise CoercionError("AMBIGUOUS_PLACE",
                            f"«{describe(value)}» là quận/huyện cũ, nay gồm các phường/xã: {names}",
                            "Vui lòng ghi phường/xã mới, hoặc kèm tọa độ.")


@lru_cache(maxsize=1)
def location_directory() -> LocationDirectory:
    """The location directory over the official reference, built once."""
    return LocationDirectory(address_reference())
