"""Posted listings: lenient conversion of typed values and validation against the listing schema.

Users post listings typed by hand or exported from spreadsheets. These tests pin the rule that
makes that safe: accept the usual ways of writing a value, never guess between two readings,
and say exactly which field failed and why.
"""

import asyncio
import json
import unicodedata
import unittest
from datetime import date

import httpx

import requests

from src.services.listing_submission import coercion, vocabulary
from src.services.listing_submission.coercion import CoercionError
from src.services.listing_submission.geocoding import NominatimGeocoder
from src.services.listing_submission.places import location_directory
from src.services.listing_submission.validation import validate_listing, validate_listings

TODAY = date(2026, 11, 26)

# The example document of docs/plan.html, "Định dạng chuẩn".
PLAN_EXAMPLE = {
    "listing_id": "45633671",
    "title": "Kim Ngân Define - Bán căn hộ 4 phòng ngủ có hồ bơi riêng",
    "description": "Bán căn hộ có hồ bơi riêng, view sông, nội thất cơ bản, sổ hợp đồng mua bán.",
    "ward": "Phường Cát Lái",
    "former_district": "Quận 2",
    "property_type": "can_ho_chung_cu",
    "price_billion_vnd": 43.9,
    "area_m2": 325.0,
    "posted_date": "2026-10-02",
    "metadata": {
        "bedroom_count": 4,
        "bathroom_count": 5,
        "legal_status": "hop_dong_mua_ban",
        "furnishing": "co_ban",
        "latitude": 10.7792,
        "longitude": 106.7571,
        "street": "Đường Số 60 - TML",
        "project": "Căn hộ DEFINE",
        "nearby_amenities": [
            {"category": "metro", "name": "Rạch Chiếc", "driving_distance_km": 4.92, "driving_duration_minutes": 5.9},
        ],
    },
}


def problem_code(function, value):
    """The CoercionError code a converter raises for a value."""
    with unittest.TestCase().assertRaises(CoercionError) as raised:
        function(value)
    return raised.exception.problem.code


class NumberTests(unittest.TestCase):
    def test_a_single_mark_before_three_digits_has_two_readings(self):
        self.assertEqual(coercion.number_readings("70,5"), [70.5])
        self.assertEqual(coercion.number_readings("1.500"), [1.5, 1500.0])
        self.assertEqual(coercion.number_readings("1.234.567"), [1234567.0])
        self.assertEqual(coercion.number_readings("1.234,5"), [1234.5])
        self.assertEqual(problem_code(coercion.number_readings, "1.23.4"), "INVALID_NUMBER")

    def test_the_plausible_range_settles_an_ambiguous_number(self):
        self.assertEqual(coercion.to_area_m2("1.234"), 1234.0)  # 1,234 m² is not a home
        self.assertEqual(coercion.to_decimal_degrees("10.779", 10.3, 11.6), 10.779)


class PriceTests(unittest.TestCase):
    def test_common_ways_of_writing_a_price(self):
        cases = {
            43.9: 43.9, "4,5 tỷ": 4.5, "4 tỷ 500 triệu": 4.5, "4 tỷ 500": 4.5, "4 tỷ 5": 4.5,
            "4 tỷ rưỡi": 4.5, "850 triệu": 0.85, "850tr": 0.85, "4.500.000.000 đ": 4.5,
            4500000000: 4.5, "1,5": 1.5, "12 ty": 12.0,
        }
        for written, expected in cases.items():
            with self.subTest(written=written):
                self.assertEqual(coercion.to_price_billion(written), expected)

    def test_prices_that_are_not_sale_prices_are_refused_with_a_reason(self):
        self.assertEqual(problem_code(coercion.to_price_billion, "30 triệu/tháng"), "RENT_OR_UNIT_PRICE")
        self.assertEqual(problem_code(coercion.to_price_billion, "Thỏa thuận"), "NOT_A_PRICE")
        self.assertEqual(problem_code(coercion.to_price_billion, "0,03"), "OUT_OF_RANGE")
        self.assertEqual(problem_code(coercion.to_price_billion, True), "INVALID_PRICE")

    def test_two_plausible_readings_are_reported_not_guessed(self):
        self.assertEqual(problem_code(coercion.to_price_billion, "1.500"), "AMBIGUOUS_NUMBER")
        self.assertEqual(problem_code(coercion.to_price_billion, "4 tỷ 50"), "AMBIGUOUS_NUMBER")


class OtherValueTests(unittest.TestCase):
    def test_areas_with_units_and_sizes(self):
        for written, expected in {325: 325.0, "79,9 m2": 79.9, "70 m²": 70.0, "4x15": 60.0, "4m x 15m": 60.0}.items():
            with self.subTest(written=written):
                self.assertEqual(coercion.to_area_m2(written), expected)
        self.assertEqual(problem_code(coercion.to_area_m2, "1.5"), "OUT_OF_RANGE")
        self.assertEqual(problem_code(coercion.to_area_m2, "rộng"), "NOT_A_NUMBER")

    def test_room_counts(self):
        for written, expected in {4: 4, 4.0: 4, "4 PN": 4, "4pn": 4, "3 WC": 3, "2 phòng ngủ": 2, "studio": 0}.items():
            with self.subTest(written=written):
                self.assertEqual(coercion.to_room_count(written), expected)
        self.assertEqual(problem_code(coercion.to_room_count, "2,5"), "NOT_A_WHOLE_NUMBER")
        self.assertEqual(problem_code(coercion.to_room_count, "60"), "OUT_OF_RANGE")

    def test_dates_put_the_day_first_and_cannot_be_in_the_future(self):
        for written in ("2026-10-02", "2/10/2026", "02.10.2026", "02-10-2026", "2026-10-02T08:30:00+07:00"):
            with self.subTest(written=written):
                self.assertEqual(coercion.to_posted_date(written, TODAY), date(2026, 10, 2))
        self.assertEqual(problem_code(lambda value: coercion.to_posted_date(value, TODAY), "31/02/2026"), "INVALID_DATE")
        self.assertEqual(problem_code(lambda value: coercion.to_posted_date(value, TODAY), "2026-12-01"), "OUT_OF_RANGE")

    def test_text_is_cleaned_and_normalized_to_nfc(self):
        decomposed = unicodedata.normalize("NFD", "Căn hộ  đẹp\t quận 2")  # as macOS and some pastes deliver it
        self.assertEqual(coercion.to_text(decomposed, 1, 100), unicodedata.normalize("NFC", "Căn hộ đẹp quận 2"))
        self.assertEqual(coercion.to_text("  Bán   nhà\n\n\n\nđẹp  ", 1, 100, is_multiline=True), "Bán nhà\n\nđẹp")
        self.assertEqual(problem_code(lambda value: coercion.to_text(value, 10, 100), "Ngắn"), "TOO_SHORT")


class CategoryAndPlaceTests(unittest.TestCase):
    def test_codes_labels_aliases_and_free_text_reach_the_same_code(self):
        legal = lambda value: coercion.to_category(value, vocabulary.LEGAL_STATUSES, "Pháp lý", vocabulary.LEGAL_STATUS_RULES)
        for written, expected in {"hop_dong_mua_ban": "hop_dong_mua_ban", "Hợp đồng mua bán.": "hop_dong_mua_ban",
                                  "HĐMB": "hop_dong_mua_ban", "Sổ đỏ/ Sổ hồng": "so_hong_rieng",
                                  "Sổ hồng chung": "so_chung", "Đang chờ sổ": "dang_cho_so"}.items():
            with self.subTest(written=written):
                self.assertEqual(legal(written), expected)
        furnishing = lambda value: coercion.to_category(value, vocabulary.FURNISHINGS, "Nội thất", vocabulary.FURNISHING_RULES)
        self.assertEqual(furnishing("Full nội thất cao cấp"), "cao_cap")
        self.assertEqual(furnishing("Bàn giao thô"), "khong_noi_that")
        property_type = lambda value: coercion.to_category(value, vocabulary.PROPERTY_TYPES, "Loại BĐS")
        self.assertEqual(property_type("Chung cư"), "can_ho_chung_cu")
        self.assertEqual(property_type("Nhà biệt thự, liền kề"), "nha_biet_thu_lien_ke")
        with self.assertRaises(CoercionError) as raised:
            property_type("nhà phố")  # mặt phố or shophouse: not guessed
        self.assertIn("nha_mat_pho", raised.exception.problem.suggestion)

    def test_places_match_with_or_without_their_administrative_word(self):
        directory = location_directory()
        for written in ("Phường Cát Lái", "phuong cat lai", "P. Cát Lái", "Cát Lái", "Phường Cát Lái, Hồ Chí Minh"):
            with self.subTest(written=written):
                self.assertEqual(directory.match_ward(written).ward.name, "Phường Cát Lái")
        for written in ("Quận 2", "Q2", "q.2", "quan 02", "Quận 2 cũ", "District 2"):
            with self.subTest(written=written):
                self.assertEqual(directory.match_former_district(written), "Quận 2")
        self.assertEqual(directory.match_former_district("TP Thủ Đức"), "Thành phố Thủ Đức")
        # "Quận Thủ Đức" (before 2021) and "Thành phố Thủ Đức" are one unit, so a bare name is not ambiguous.
        self.assertEqual(directory.match_former_district("Thủ Đức"), "Thành phố Thủ Đức")

    def test_unknown_and_ambiguous_places_say_why(self):
        directory = location_directory()
        with self.assertRaises(CoercionError) as raised:
            directory.match_ward("Cát Lay")
        self.assertEqual(raised.exception.problem.code, "UNKNOWN_PLACE")
        self.assertIn("Phường Cát Lái", raised.exception.problem.suggestion)
        # Two communes differ only in accents: typed accents settle it, none leaves it ambiguous.
        self.assertEqual(problem_code(directory.match_ward, "Thanh An"), "AMBIGUOUS_PLACE")
        self.assertEqual(directory.match_ward("Xã Thạnh An").ward.name, "Xã Thạnh An")
        self.assertEqual(directory.match_ward("Xã Thanh An").ward.name, "Xã Thanh An")


class ListingTests(unittest.TestCase):
    def test_the_plan_example_is_valid_as_written(self):
        report = validate_listing(json.loads(json.dumps(PLAN_EXAMPLE)), 0, TODAY)
        self.assertTrue(report.is_valid, report.errors)
        self.assertEqual(report.conversions, [])
        self.assertEqual(report.warnings, [])
        self.assertEqual(report.reference, "45633671")
        expected = {key: value for key, value in PLAN_EXAMPLE.items() if key != "listing_id"}
        self.assertEqual(report.listing, expected)

    def test_a_spreadsheet_style_listing_is_converted_and_every_change_is_reported(self):
        typed = {
            "Tiêu đề": "Bán căn hộ 2PN view sông quận 2",
            "Mô tả": "Căn hộ 2 phòng ngủ, nội thất đầy đủ, sổ hồng riêng, gần metro.",
            "phuong": "p. cat lai", "Quận": "Q2", "loai": "chung cư", "Giá": "4 tỷ 500",
            "dien tich": "70m2", "phong ngu": "2 PN", "toa do": "10,7792, 106,7571", "Nội thất": "Full",
        }
        report = validate_listing(typed, 3, TODAY)
        self.assertTrue(report.is_valid, report.errors)
        listing = report.listing
        self.assertEqual((listing["ward"], listing["former_district"], listing["property_type"]),
                         ("Phường Cát Lái", "Quận 2", "can_ho_chung_cu"))
        self.assertEqual((listing["price_billion_vnd"], listing["area_m2"], listing["posted_date"]), (4.5, 70.0, "2026-11-26"))
        self.assertEqual(listing["metadata"], {"bedroom_count": 2, "furnishing": "day_du", "latitude": 10.7792, "longitude": 106.7571})
        renamed = {item["from"] for item in report.conversions if item["kind"] == "field"}
        self.assertTrue({"Giá", "phuong", "toa do"} <= renamed)
        converted = {(item["field"], item["from"], item["to"]) for item in report.conversions if item["kind"] == "value"}
        self.assertIn(("price_billion_vnd", "4 tỷ 500", 4.5), converted)
        derived = {(item["field"], item["to"]) for item in report.conversions if item["kind"] == "derived"}
        self.assertIn(("posted_date", "2026-11-26"), derived)

    def test_missing_and_wrong_required_fields_make_the_listing_invalid(self):
        typed = {"title": "Bán nhà riêng hẻm xe hơi", "price_billion_vnd": "1.500", "property_type": "biệt thự"}
        report = validate_listing(typed, 0, TODAY)
        self.assertFalse(report.is_valid)
        codes = {item["field"]: item["code"] for item in report.errors}
        self.assertEqual(codes, {"description": "MISSING_REQUIRED", "ward": "MISSING_REQUIRED",
                                 "former_district": "MISSING_REQUIRED", "area_m2": "MISSING_REQUIRED",
                                 "price_billion_vnd": "AMBIGUOUS_NUMBER"})
        price_error = next(item for item in report.errors if item["field"] == "price_billion_vnd")
        self.assertEqual((price_error["label"], price_error["value"]), ("Giá", "1.500"))
        self.assertIn("suggestion", price_error)

    def test_a_broken_optional_field_is_dropped_with_a_warning(self):
        typed = json.loads(json.dumps(PLAN_EXAMPLE))
        typed["metadata"].update({"bedroom_count": "nhiều", "latitude": 106.7571, "longitude": 10.7792, "colour": "xanh"})
        report = validate_listing(typed, 0, TODAY)
        self.assertTrue(report.is_valid, report.errors)
        self.assertNotIn("bedroom_count", report.listing["metadata"])
        self.assertEqual((report.listing["metadata"]["latitude"], report.listing["metadata"]["longitude"]), (10.7792, 106.7571))
        codes = {item["code"] for item in report.warnings}
        self.assertEqual(codes, {"NOT_A_NUMBER", "SWAPPED_COORDINATES", "UNKNOWN_FIELD"})

    def test_metadata_pasted_as_text_and_amenity_limits(self):
        typed = json.loads(json.dumps(PLAN_EXAMPLE))
        amenities = [{"category": "Công viên", "name": f"Công viên {index}", "khoang_cach": "800 m", "thoi_gian": "3 phút"}
                     for index in range(6)]
        amenities.append({"category": "sân bay", "name": "Tân Sơn Nhất", "driving_distance_km": 9, "driving_duration_minutes": 25})
        typed["metadata"] = json.dumps({"bedroom_count": 4, "nearby_amenities": amenities}, ensure_ascii=False)
        report = validate_listing(typed, 0, TODAY)
        self.assertTrue(report.is_valid, report.errors)
        kept = report.listing["metadata"]["nearby_amenities"]
        self.assertEqual(len(kept), vocabulary.AMENITIES_PER_CATEGORY)
        self.assertEqual(kept[0], {"category": "park", "name": "Công viên 0", "driving_distance_km": 0.8,
                                   "driving_duration_minutes": 3.0})
        self.assertEqual(sorted(item["code"] for item in report.warnings), ["TOO_MANY_AMENITIES", "UNKNOWN_CATEGORY"])
        unknown = next(item for item in report.warnings if item["code"] == "UNKNOWN_CATEGORY")
        self.assertEqual(unknown["field"], "metadata.nearby_amenities[6].category")

    def test_an_amenity_missing_a_field_names_that_field(self):
        typed = json.loads(json.dumps(PLAN_EXAMPLE))
        typed["metadata"]["nearby_amenities"] = [{"category": "metro", "driving_distance_km": 4.9, "driving_duration_minutes": 6}]
        report = validate_listing(typed, 0, TODAY)
        self.assertTrue(report.is_valid)
        self.assertEqual([(item["field"], item["code"]) for item in report.warnings],
                         [("metadata.nearby_amenities[0].name", "MISSING_REQUIRED")])
        self.assertIn("Tên tiện ích", report.warnings[0]["message"])
        self.assertNotIn("nearby_amenities", report.listing["metadata"])

    def test_a_batch_reports_each_listing_and_counts(self):
        batch = validate_listings([PLAN_EXAMPLE, "không phải object", {}], today=TODAY)
        self.assertEqual((batch["valid_count"], batch["invalid_count"]), (1, 2))
        self.assertEqual([result["index"] for result in batch["results"]], [0, 1, 2])
        self.assertEqual(batch["results"][1]["errors"][0]["code"], "NOT_AN_OBJECT")
        json.dumps(batch)


# Points checked against the reference boundaries: well inside one ward each.
CAT_LAI = (10.7792, 106.7571)
BEN_THANH = (10.7725, 106.698)
DIEN_HONG = (10.7585, 106.6615)
BIEN_HOA = (10.957, 106.843)  # Đồng Nai, outside the city


def plan_listing(**changes):
    """A copy of the plan example with top-level changes; metadata changes go in "metadata"."""
    document = json.loads(json.dumps(PLAN_EXAMPLE))
    metadata = changes.pop("metadata", None)
    document.update(changes)
    if metadata is not None:
        document["metadata"] = metadata
    return document


class ReferenceTests(unittest.TestCase):
    def test_the_reference_holds_every_official_ward_with_its_history_and_boundary(self):
        reference = location_directory().reference
        self.assertEqual(len(reference.wards), 168)
        self.assertTrue(all(ward.former_districts for ward in reference.wards))
        self.assertEqual(reference.ward_by_name["Phường Cát Lái"].former_districts, ("Thành phố Thủ Đức",))
        self.assertEqual(reference.ward_at(*CAT_LAI).name, "Phường Cát Lái")
        self.assertEqual(reference.ward_at(*BEN_THANH).name, "Phường Bến Thành")
        self.assertTrue(reference.is_in_city(*CAT_LAI))
        self.assertFalse(reference.is_in_city(*BIEN_HOA))
        self.assertIsNone(reference.ward_at(*BIEN_HOA))


class LocationTests(unittest.TestCase):
    def test_an_old_ward_name_becomes_its_new_ward_and_fills_the_former_district(self):
        report = validate_listing(plan_listing(ward="Phường Thạnh Mỹ Lợi", former_district=None, metadata={}), 0, TODAY)
        self.assertTrue(report.is_valid, report.errors)
        self.assertEqual((report.listing["ward"], report.listing["former_district"]), ("Phường Cát Lái", "Thành phố Thủ Đức"))
        ward_change = next(item for item in report.conversions if item["field"] == "ward")
        self.assertIn("Phường Thạnh Mỹ Lợi", ward_change["note"])

    def test_an_old_name_split_between_wards_is_settled_by_coordinates(self):
        typed = plan_listing(ward="Phường 14", former_district="Quận 10", metadata={})
        report = validate_listing(typed, 0, TODAY)
        self.assertEqual([item["code"] for item in report.errors], ["AMBIGUOUS_PLACE"])  # Diên Hồng or Hòa Hưng
        typed["metadata"] = {"latitude": DIEN_HONG[0], "longitude": DIEN_HONG[1]}
        report = validate_listing(typed, 0, TODAY)
        self.assertTrue(report.is_valid, report.errors)
        self.assertEqual(report.listing["ward"], "Phường Diên Hồng")

    def test_a_former_district_typed_as_the_ward_is_settled_by_coordinates(self):
        # A crawl wrote "Quận 3" in the ward column; its number must not match every old "Phường 3".
        typed = plan_listing(ward="Quận 3", former_district=None, metadata={})
        report = validate_listing(typed, 0, TODAY)
        self.assertEqual([item["code"] for item in report.errors], ["AMBIGUOUS_PLACE", "MISSING_REQUIRED"])
        suggested = report.errors[0]["message"]
        self.assertIn("quận/huyện cũ", suggested)
        self.assertNotIn("Vũng Tàu", suggested)
        suggested_wards = suggested.split("gồm các phường/xã: ")[1].rstrip(".…").split(", ")
        directory = location_directory()
        self.assertTrue(all(directory.was_in(directory.reference.ward_by_name[name], "Quận 3") for name in suggested_wards))
        typed["metadata"] = {"latitude": 10.7842582, "longitude": 106.6775973}  # in old District 3
        report = validate_listing(typed, 0, TODAY)
        self.assertTrue(report.is_valid, report.errors)
        self.assertEqual(report.listing["former_district"], "Quận 3")
        self.assertTrue(directory.was_in(directory.reference.ward_by_name[report.listing["ward"]], "Quận 3"))
        note = next(item for item in report.conversions if item["field"] == "ward")["note"]
        self.assertIn("lấy từ tọa độ", note)

    def test_the_former_district_must_agree_with_the_ward(self):
        report = validate_listing(plan_listing(former_district="Quận 7"), 0, TODAY)
        self.assertEqual([item["code"] for item in report.errors], ["MISMATCHED_FORMER_DISTRICT"])
        self.assertIn("Thành phố Thủ Đức", report.errors[0]["suggestion"])
        # Quận 9 was merged into Thủ Đức city with Quận 2, so it counts as the same unit.
        self.assertTrue(validate_listing(plan_listing(former_district="Q9"), 0, TODAY).is_valid)

    def test_a_ward_made_from_several_districts_needs_the_former_district(self):
        report = validate_listing(plan_listing(ward="Phường Bình Dương", former_district=None, metadata={}), 0, TODAY)
        self.assertEqual([(item["field"], item["code"]) for item in report.errors], [("former_district", "MISSING_REQUIRED")])
        self.assertIn("Thành phố Tân Uyên", report.errors[0]["suggestion"])

    def test_the_ward_is_taken_from_the_coordinates_when_missing(self):
        report = validate_listing(plan_listing(ward=None, former_district=None), 0, TODAY)
        self.assertTrue(report.is_valid, report.errors)
        self.assertEqual((report.listing["ward"], report.listing["former_district"]), ("Phường Cát Lái", "Thành phố Thủ Đức"))
        self.assertIn(("ward", "derived"), {(item["field"], item["kind"]) for item in report.conversions})

    def test_coordinates_far_from_the_ward_warn_and_outside_the_city_are_dropped(self):
        elsewhere = plan_listing(metadata={"latitude": BEN_THANH[0], "longitude": BEN_THANH[1]})
        report = validate_listing(elsewhere, 0, TODAY)
        self.assertTrue(report.is_valid)
        self.assertEqual([item["code"] for item in report.warnings], ["COORDINATES_OUTSIDE_WARD"])
        self.assertIn("Phường Bến Thành", report.warnings[0]["message"])
        outside = validate_listing(plan_listing(metadata={"latitude": BIEN_HOA[0], "longitude": BIEN_HOA[1]}), 0, TODAY)
        self.assertEqual([item["code"] for item in outside.warnings], ["OUTSIDE_CITY"])
        self.assertNotIn("latitude", outside.listing["metadata"])


class FakeGeocoder:
    """Answers every query with one result (or raises), recording the queries."""

    def __init__(self, result=None, error=None):
        self.result, self.error, self.queries = result, error, []

    def search(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.result


class GeocodingTests(unittest.TestCase):
    def listing_without_coordinates(self):
        return plan_listing(metadata={"street": "Đường Số 60"})

    def test_a_found_address_inside_the_ward_fills_the_coordinates(self):
        geocoder = FakeGeocoder(result=CAT_LAI)
        report = validate_listing(self.listing_without_coordinates(), 0, TODAY, geocoder)
        self.assertEqual(geocoder.queries, ["Đường Số 60, Phường Cát Lái, Thành phố Hồ Chí Minh"])
        self.assertEqual((report.listing["metadata"]["latitude"], report.listing["metadata"]["longitude"]), CAT_LAI)
        self.assertEqual({item["kind"] for item in report.conversions}, {"geocoded"})

    def test_a_result_in_another_ward_or_a_failure_only_warns(self):
        for geocoder, code in ((FakeGeocoder(result=BEN_THANH), "ADDRESS_OUTSIDE_WARD"),
                               (FakeGeocoder(result=None), "ADDRESS_NOT_FOUND"),
                               (FakeGeocoder(error=requests.ConnectionError("down")), "GEOCODER_UNAVAILABLE")):
            with self.subTest(code=code):
                report = validate_listing(self.listing_without_coordinates(), 0, TODAY, geocoder)
                self.assertTrue(report.is_valid)
                self.assertEqual([item["code"] for item in report.warnings], [code])
                self.assertNotIn("latitude", report.listing["metadata"])

    def test_the_geocoder_is_not_asked_when_coordinates_exist_or_there_is_no_street(self):
        geocoder = FakeGeocoder(result=CAT_LAI)
        validate_listing(plan_listing(), 0, TODAY, geocoder)
        validate_listing(plan_listing(metadata={}), 0, TODAY, geocoder)
        self.assertEqual(geocoder.queries, [])


class FakeResponse:
    def __init__(self, body, status_code=200):
        self.body, self.status_code = body, status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self.body


class FakeSession:
    """Records GET calls and answers from a queue."""

    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params, headers, timeout):
        self.calls.append({"url": url, "params": params, "headers": headers})
        return self.responses.pop(0)


class NominatimGeocoderTests(unittest.TestCase):
    def geocoder(self, responses):
        self.now = 100.0
        self.slept = []

        def sleep(seconds):
            self.slept.append(round(seconds, 3))
            self.now += seconds

        session = FakeSession(responses)
        geocoder = NominatimGeocoder(base_url="https://nominatim.example", user_agent="test-agent/1.0",
                                     session=session, clock=lambda: self.now, sleep=sleep)
        return geocoder, session

    def test_requests_are_a_second_apart_identified_and_bounded_to_the_city(self):
        geocoder, session = self.geocoder([FakeResponse([{"lat": "10.7792", "lon": "106.7571"}]), FakeResponse([])])
        self.assertEqual(geocoder.search("Đường Số 60, Phường Cát Lái"), (10.7792, 106.7571))
        self.now += 0.25
        self.assertIsNone(geocoder.search("Đường Không Có"))
        self.assertEqual(self.slept, [0.75])
        first = session.calls[0]
        self.assertEqual(first["url"], "https://nominatim.example/search")
        self.assertEqual(first["headers"], {"User-Agent": "test-agent/1.0"})
        self.assertEqual((first["params"]["countrycodes"], first["params"]["bounded"]), ("vn", 1))

    def test_repeated_addresses_come_from_the_cache_but_failures_are_retried(self):
        geocoder, session = self.geocoder([FakeResponse([], status_code=503), FakeResponse([{"lat": "10.7", "lon": "106.7"}])])
        with self.assertRaises(requests.HTTPError):
            geocoder.search("Đường Số 60")
        self.assertEqual(geocoder.search("Đường  số 60"), (10.7, 106.7))
        self.assertEqual(geocoder.search("đường số 60"), (10.7, 106.7))
        self.assertEqual(len(session.calls), 2)


class ApiTests(unittest.TestCase):
    def request(self, body, user=None):
        """POST /v1/listings/validate through the ASGI app; user overrides the signed-in user."""
        from src.main import app
        from src.services.auth.dependencies import get_current_user

        async def send():
            if user is not None:
                app.dependency_overrides[get_current_user] = lambda: user
            try:
                # Starlette 0.32's TestClient does not work with httpx 0.28, so drive the ASGI app directly.
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    return await client.post("/v1/listings/validate", json=body)
            finally:
                app.dependency_overrides.pop(get_current_user, None)

        return asyncio.run(send())

    def test_anonymous_callers_are_refused(self):
        response = self.request({"listings": [PLAN_EXAMPLE]})
        self.assertEqual(response.status_code, 401)

    def test_signed_in_users_get_a_report_in_the_envelope(self):
        response = self.request({"listings": [PLAN_EXAMPLE, {}]}, user={"id": 7, "username": "lan"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "success")
        self.assertEqual([result["is_valid"] for result in body["data"]["results"]], [True, False])

    def test_batches_are_limited_to_one_hundred_listings(self):
        response = self.request({"listings": [PLAN_EXAMPLE] * 101}, user={"id": 7, "username": "lan"})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
