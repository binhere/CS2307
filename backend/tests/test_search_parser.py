import unittest

from src.search.query_parser import RuleBasedQueryParser
from src.search.schemas import RankingProfile


class QueryParserTests(unittest.TestCase):
    def setUp(self):
        self.parser = RuleBasedQueryParser()

    def test_hybrid_query(self):
        parsed = self.parser.parse(
            "Tìm căn hộ 2PN dưới 5 tỷ ở Thủ Đức, cách metro không quá 3 km, "
            "ưu tiên yên tĩnh và nhiều cây xanh"
        )
        self.assertEqual(parsed.preference_filters.property_types, ["Căn hộ"])
        self.assertEqual(parsed.preference_filters.bedrooms.min, 2)
        self.assertEqual(parsed.preference_filters.price.max, 5)
        self.assertIn("Thành Phố Thủ Đức", parsed.preference_filters.former_admin_areas)
        self.assertIn("Quận 9", parsed.preference_filters.former_admin_areas)
        self.assertEqual(parsed.hard_filters.model_dump(), type(parsed.hard_filters)().model_dump())
        self.assertEqual(parsed.amenity_filters[0].amenity_category, "metro")
        self.assertEqual(parsed.amenity_filters[0].max_driving_distance_km, 3)
        self.assertFalse(parsed.amenity_filters[0].required)

    def test_district_name_without_old_keyword_uses_former_area(self):
        parsed = self.parser.parse("tôi muốn mua nàh quận 2")
        self.assertEqual(parsed.preference_filters.districts, [])
        self.assertEqual(parsed.preference_filters.former_admin_areas, ["Quận 2"])

    def test_old_huyen_and_city_are_former_areas(self):
        huyen = self.parser.parse("mua nhà huyện hóc môn")
        city = self.parser.parse("căn hộ thành phố dĩ an")
        self.assertEqual(huyen.preference_filters.districts, [])
        self.assertEqual(huyen.preference_filters.former_admin_areas, ["Huyện Hóc Môn"])
        self.assertEqual(city.preference_filters.districts, [])
        self.assertEqual(city.preference_filters.former_admin_areas, ["Thành Phố Dĩ An"])

    def test_units_and_required_feature(self):
        parsed = self.parser.parse("Nhà đất dưới 5000 triệu, diện tích trên 80 m2, phải có ban công")
        self.assertEqual(parsed.preference_filters.price.max, 5)
        self.assertEqual(parsed.preference_filters.area.min, 80)
        self.assertIn("balcony", parsed.preference_filters.required_features)
        self.assertNotIn("balcony", parsed.hard_filters.required_features)

    def test_negative_property_type(self):
        parsed = self.parser.parse("Bất động sản dưới 6 tỷ, không lấy nhà đất")
        self.assertEqual(parsed.preference_filters.excluded_property_types, ["Nhà đất"])

    def test_explicit_filters_override_query(self):
        parsed = self.parser.parse(
            "Tìm nơi ở yên tĩnh",
            explicit_filters={"max_price": 4.5, "district": "Phường An Khánh", "pool": True},
        )
        self.assertEqual(parsed.hard_filters.price.max, 4.5)
        self.assertEqual(parsed.hard_filters.districts, ["Phường An Khánh"])
        self.assertIn("pool", parsed.hard_filters.required_features)

    def test_query_and_ui_filters_keep_separate_sources(self):
        parsed = self.parser.parse(
            "Căn hộ khoảng 5 tỷ ở Phường Thới An",
            explicit_filters={"max_price": 7, "district": "Phường An Khánh"},
        )
        self.assertEqual(parsed.preference_filters.price.target, 5)
        self.assertEqual(parsed.preference_filters.districts, ["Phường Thới An"])
        self.assertEqual(parsed.hard_filters.price.max, 7)
        self.assertEqual(parsed.hard_filters.districts, ["Phường An Khánh"])

    def test_family_profile(self):
        parsed = self.parser.parse("Căn hộ phù hợp gia đình trẻ gần trường học")
        self.assertEqual(parsed.ranking_profile, RankingProfile.FAMILY)

    def test_maximum_distance_is_not_a_negative_preference(self):
        parsed = self.parser.parse("Căn hộ cách metro không quá 3 km")
        self.assertEqual(parsed.negative_preferences, [])


class QueryParserRegressionTests(unittest.TestCase):
    """Queries that used to produce wrong filters (found in the 2026-11 review)."""

    def setUp(self):
        self.parser = RuleBasedQueryParser()

    def test_common_phrases_are_not_locations(self):
        important = self.parser.parse("Quan trọng nhất là gần metro, căn hộ quận 7")
        not_far = self.parser.parse("căn hộ không xa trung tâm, 2 phòng ngủ")
        self.assertEqual(important.preference_filters.former_admin_areas, ["Quận 7"])
        self.assertEqual(not_far.preference_filters.districts, [])
        self.assertEqual(not_far.preference_filters.former_admin_areas, [])

    def test_ward_name_keeps_its_accents(self):
        parsed = self.parser.parse("nhà đất Phường Tân Định dưới 2.4 tỷ, 2 phòng ngủ")
        self.assertEqual(parsed.preference_filters.districts, ["Phường Tân Định"])
        self.assertEqual(parsed.preference_filters.price.max, 2.4)

    def test_price_range_with_unit_on_both_ends(self):
        same_unit = self.parser.parse("nhà 2 tỷ - 3 tỷ")
        mixed_units = self.parser.parse("từ 500 triệu đến 1 tỷ")
        self.assertEqual((same_unit.preference_filters.price.min, same_unit.preference_filters.price.max), (2.0, 3.0))
        self.assertEqual((mixed_units.preference_filters.price.min, mixed_units.preference_filters.price.max), (0.5, 1.0))

    def test_investment_amount_is_a_budget_not_a_minimum(self):
        parsed = self.parser.parse("đầu tư 3 tỷ mua căn hộ")
        self.assertIsNone(parsed.preference_filters.price.min)
        self.assertEqual(parsed.preference_filters.price.target, 3.0)

    def test_negated_feature_is_not_a_preference(self):
        negated = self.parser.parse("căn hộ không gần chợ, yên tĩnh")
        wanted = self.parser.parse("căn hộ quận 7 dưới 3 tỷ gần chợ")
        limit = self.parser.parse("không quá 3 tỷ, gần chợ")
        self.assertNotIn("near_market", [item.value for item in negated.soft_preferences])
        self.assertIn("near_market", [item.value for item in wanted.soft_preferences])
        self.assertIn("near_market", [item.value for item in limit.soft_preferences])


if __name__ == "__main__":
    unittest.main()
