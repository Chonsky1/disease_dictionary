"""data_prep 단위 테스트 (네트워크 없이 실행). 실행: python -m unittest -v"""
import json
import unittest
from pathlib import Path

import requests

from data_prep import settings
from data_prep.age_groups import AgeBand, load_common_groups, map_to_common, parse_age_label
from data_prep.csv_loader import find_header_row, read_table, sniff_encoding, to_number
from data_prep.http_client import PoliteSession, mask_secrets
from data_prep.kdca_api import ApiError, extract_items, fetch_all, parse_response
from data_prep.names import match_names, name_key
from data_prep.wiki import _parse_query

FX = Path(__file__).parent / "fixtures"


class AgeTest(unittest.TestCase):
    def setUp(self):
        self.groups = load_common_groups(settings.CONFIG / "age_groups.json")

    def test_parse(self):
        self.assertEqual(parse_age_label("0-6세"), AgeBand(0, 6))
        self.assertEqual(parse_age_label("0~6세"), AgeBand(0, 6))
        self.assertEqual(parse_age_label("65세 이상"), AgeBand(65, 120))
        self.assertEqual(parse_age_label("65+"), AgeBand(65, 120))
        self.assertEqual(parse_age_label("1세 미만"), AgeBand(0, 0))
        self.assertEqual(parse_age_label("0세"), AgeBand(0, 0))
        self.assertEqual(parse_age_label("계"), "total")
        self.assertEqual(parse_age_label("미상"), "unknown")
        self.assertIsNone(parse_age_label("남"))

    def test_is_age_label_rejects_counts(self):
        from data_prep.age_groups import is_age_label
        self.assertTrue(is_age_label("5-9세"))
        self.assertTrue(is_age_label("65+"))
        self.assertFalse(is_age_label("3"))
        self.assertFalse(is_age_label("계"))

    def test_map(self):
        self.assertEqual(map_to_common(AgeBand(0, 6), self.groups), ("0-6", 1.0, True))
        gid, ratio, exact = map_to_common(AgeBand(5, 9), self.groups)
        self.assertEqual((gid, exact), ("7-12", False))
        self.assertAlmostEqual(ratio, 0.6)
        self.assertEqual(map_to_common(AgeBand(70, 120), self.groups)[0], "65+")


class NameTest(unittest.TestCase):
    def test_key_and_match(self):
        self.assertEqual(name_key("중증급성호흡기증후군(SARS)"), name_key("중증급성 호흡기증후군"))
        matched, unmatched = match_names(["야토 병", "에볼라 바이러스병", "독감"],
                                         ["야토병", "에볼라바이러스병"], {name_key("독감"): "인플루엔자"})
        self.assertEqual(matched["야토 병"], "야토병")
        self.assertEqual(matched["독감"], "인플루엔자")
        self.assertEqual(unmatched, [])


class CsvTest(unittest.TestCase):
    def test_cp949_with_preamble(self):
        path = FX / "synthetic_age_wide.csv"
        self.assertEqual(sniff_encoding(path), "cp949")
        header, rows, meta = read_table(path)
        self.assertEqual(meta["header_row"], 2)
        self.assertEqual(header[0], "감염병명")
        self.assertEqual(len(rows), 5)

    def test_numbers(self):
        self.assertEqual(to_number("1,204"), 1204.0)
        self.assertIsNone(to_number("-"))
        self.assertEqual(to_number("0"), 0.0)

    def test_header_guess_plain(self):
        self.assertEqual(find_header_row([["a", "b", "c"], ["1", "2", "3"]]), 0)


class ApiTest(unittest.TestCase):
    def test_shapes(self):
        p1 = {"response": {"header": {"resultCode": "00"},
                           "body": {"items": {"item": {"a": 1}}, "totalCount": 1}}}
        self.assertEqual(extract_items(p1), ([{"a": 1}], 1))
        p2 = {"response": {"header": {"resultCode": "00"}, "body": {"items": [{"a": 1}, {"a": 2}]}}}
        self.assertEqual(len(extract_items(p2)[0]), 2)
        p3 = {"data": [{"a": 1}], "totalCount": 9}
        self.assertEqual(extract_items(p3), ([{"a": 1}], 9))

    def test_errors(self):
        xml = ("<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg>"
               "<returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>"
               "<returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>")
        with self.assertRaises(ApiError):
            extract_items(parse_response(xml))
        with self.assertRaises(ApiError):
            extract_items({"response": {"header": {"resultCode": "22", "resultMsg": "LIMIT"}}})

    def test_paging(self):
        pages = {1: [{"i": 1}, {"i": 2}], 2: [{"i": 3}]}

        class FakeResp:
            def __init__(self, page):
                self.text = json.dumps({"response": {"header": {"resultCode": "00"},
                                        "body": {"items": {"item": pages[page]}, "totalCount": 3}}})
                self.url = "https://x?serviceKey=SECRET"

        class FakeSession:
            def get(self, url, params=None):
                return FakeResp(params["pageNo"])

        src = {"api": {"endpoint": "https://x", "page_size": 2, "params": {}}}
        items = fetch_all(src, FakeSession(), "SECRET")
        self.assertEqual([i["i"] for i in items], [1, 2, 3])


class HttpTest(unittest.TestCase):
    def test_retry_then_ok(self):
        calls = []

        class R:
            def __init__(self, code):
                self.status_code, self.url = code, "https://x?serviceKey=SECRET"

        class S:
            headers = {}

            def get(self, url, params=None, timeout=None):
                calls.append(1)
                return R(503 if len(calls) < 3 else 200)

        ps = PoliteSession("ua", min_interval=0, session=S(), sleep=lambda s: None)
        self.assertEqual(ps.get("https://x").status_code, 200)
        self.assertEqual(len(calls), 3)

    def test_4xx_masks_key(self):
        class R:
            status_code, url = 401, "https://x?serviceKey=SECRET&a=1"

        class S:
            headers = {}

            def get(self, url, params=None, timeout=None):
                return R()

        ps = PoliteSession("ua", min_interval=0, session=S(), sleep=lambda s: None)
        with self.assertRaises(requests.HTTPError) as ctx:
            ps.get("https://x")
        self.assertNotIn("SECRET", str(ctx.exception))
        self.assertEqual(mask_secrets("a?serviceKey=abc&b=1"), "a?serviceKey=***&b=1")


class WikiTest(unittest.TestCase):
    def test_parse_query(self):
        payload = {"query": {
            "redirects": [{"from": "수두", "to": "수두 (질병)"}],
            "pages": [{"title": "수두 (질병)", "pageid": 7, "length": 9000},
                      {"title": "없는문서", "missing": True}]}}
        out = _parse_query(["수두", "없는문서"], payload)
        self.assertTrue(out["수두"]["exists"])
        self.assertTrue(out["수두"]["redirected"])
        self.assertEqual(out["수두"]["resolved_title"], "수두 (질병)")
        self.assertFalse(out["없는문서"]["exists"])


if __name__ == "__main__":
    unittest.main()
