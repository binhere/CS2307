import json
import os
import unittest
from unittest.mock import patch

from src.search.query_parser import LLMQueryParser, RuleBasedQueryParser
from src.search.schemas import HardFilters, ParsedSearchQuery, SoftPreference


class FakeLLM:
    def __init__(self, parsed):
        self.parsed = parsed

    async def ask_llm(self, **_kwargs):
        return True, json.dumps(self.parsed.model_dump(mode="json"), ensure_ascii=False), None


class LLMQueryParserTests(unittest.IsolatedAsyncioTestCase):
    async def test_llm_location_replaces_regex_location_that_swallowed_preferences(self):
        query = "Phường Thới An giá rẻ nhiều tiện ích"
        fallback = RuleBasedQueryParser().parse(query)
        llm_result = ParsedSearchQuery(
            preference_filters=HardFilters(districts=["Phường Thới An"]),
            soft_preferences=[SoftPreference(type="amenities", value="nhiều tiện ích")],
            semantic_query=query,
        )

        with patch.dict(os.environ, {"SEARCH_USE_LLM_PARSER": "true"}):
            parsed = await LLMQueryParser(FakeLLM(llm_result)).parse(query, fallback)

        self.assertEqual(parsed.hard_filters.districts, [])
        self.assertEqual(parsed.preference_filters.districts, ["Phường Thới An"])
        self.assertEqual(parsed.soft_preferences[0].value, "nhiều tiện ích")

    async def test_rule_parser_fills_a_preference_omitted_by_llm(self):
        query = "Phường Thới An dưới 5 tỷ"
        fallback = RuleBasedQueryParser().parse(query)
        llm_result = ParsedSearchQuery(
            hard_filters=HardFilters(districts=["Phường Thới An"]),
            semantic_query=query,
        )

        with patch.dict(os.environ, {"SEARCH_USE_LLM_PARSER": "true"}):
            parsed = await LLMQueryParser(FakeLLM(llm_result)).parse(query, fallback)

        self.assertEqual(parsed.hard_filters.districts, [])
        self.assertEqual(parsed.preference_filters.districts, ["Phường Thới An"])
        self.assertEqual(parsed.preference_filters.price.max, 5)

    async def test_legacy_llm_hard_field_is_demoted_to_preference(self):
        query = "căn hộ khoảng năm tỷ"
        fallback = RuleBasedQueryParser().parse(query)
        llm_result = ParsedSearchQuery(
            hard_filters=HardFilters(
                price={"target": 5},
                property_types=["Căn hộ"],
            ),
            semantic_query=query,
        )

        with patch.dict(os.environ, {"SEARCH_USE_LLM_PARSER": "true"}):
            parsed = await LLMQueryParser(FakeLLM(llm_result)).parse(query, fallback)

        # The rule parser cannot read the written number "năm", so this value must
        # come from the LLM rather than deterministic parsing.
        self.assertEqual(parsed.hard_filters.price.target, None)
        self.assertEqual(parsed.preference_filters.price.target, 5)
        self.assertEqual(parsed.preference_filters.property_types, ["Căn hộ"])

    async def test_parser_requests_native_json_at_temperature_zero(self):
        query = "căn hộ quận 7"
        fallback = RuleBasedQueryParser().parse(query)
        llm = FakeLLM(ParsedSearchQuery(semantic_query=query))
        captured = {}

        async def recording_ask_llm(**kwargs):
            captured.update(kwargs)
            return await FakeLLM.ask_llm(llm, **kwargs)

        llm.ask_llm = recording_ask_llm
        with patch.dict(os.environ, {"SEARCH_USE_LLM_PARSER": "true"}):
            await LLMQueryParser(llm).parse(query, fallback)

        self.assertTrue(captured["json_mode"])
        self.assertEqual(captured["temperature"], 0.0)

    async def test_invalid_llm_output_falls_back_and_is_logged(self):
        query = "căn hộ quận 7"
        fallback = RuleBasedQueryParser().parse(query)

        class BrokenLLM:
            async def ask_llm(self, **_kwargs):
                return True, "not json", None

        with patch.dict(os.environ, {"SEARCH_USE_LLM_PARSER": "true"}), patch(
            "src.search.query_parser.logger.warning"
        ) as warning:
            parsed = await LLMQueryParser(BrokenLLM()).parse(query, fallback)

        self.assertIs(parsed, fallback)
        warning.assert_called_once()

    async def test_rule_location_is_retained_when_llm_has_no_location(self):
        query = "mua nhà phường an khánh"
        fallback = RuleBasedQueryParser().parse(query)
        llm_result = ParsedSearchQuery(semantic_query=query)

        with patch.dict(os.environ, {"SEARCH_USE_LLM_PARSER": "true"}):
            parsed = await LLMQueryParser(FakeLLM(llm_result)).parse(query, fallback)

        self.assertEqual(parsed.hard_filters.districts, [])
        self.assertEqual(parsed.preference_filters.districts, ["Phường An Khánh"])


if __name__ == "__main__":
    unittest.main()
