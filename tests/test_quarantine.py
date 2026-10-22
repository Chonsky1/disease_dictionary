"""검역관리지역 정보 변환·수집 스크립트 테스트 (합성 픽스처, 네트워크 불필요)."""
import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from data_prep import settings as paths
from data_prep.diseases import load_diseases
from data_prep.kdca_api import extract_items
from data_prep.quarantine import (ICD_CODES, _ymd, build_watch, from_api_items, from_csv_rows, is_active,
                                  load_country_codes)

FX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 22)


def api_items():
    data = json.loads((FX / "quarantine_api_sample.json").read_text(encoding="utf-8"))
    items, total = extract_items(data)   # 실제 수집과 같은 응답 해석 함수
    assert total == 8
    return items


def build(records):
    return build_watch(records, load_diseases(paths.DISEASES_CSV), load_country_codes(paths.CONFIG / "country_names_ko.json"),
                       paths.CONFIG / "name_aliases.json", TODAY, "test")


class TransformTest(unittest.TestCase):
    def test_dates_and_active(self):
        self.assertEqual([_ymd(x) for x in ("2024-09-02", "20240902", "2024.09.02", "2023-11-01 00:00", "", None)],
                         ["2024-09-02"] * 3 + ["2023-11-01", "", ""])
        recs = {(r.country, r.disease): r for r in from_api_items(api_items())}
        self.assertTrue(is_active(recs[("베트남", "콜레라")], TODAY))          # 해지일 없음
        self.assertTrue(is_active(recs[("베트남", "뎅기열")], TODAY))          # 해지일이 미래
        self.assertFalse(is_active(recs[("태국", "코로나19")], TODAY))         # 해지됨
        self.assertFalse(is_active(recs[("콩고 민주 공화국", "에볼라바이러스병")], TODAY))  # useYn=N

    def test_build_watch(self):
        watch, st = build(from_api_items(api_items()))
        self.assertEqual((st["records"], st["active"], st["countries"], st["mapped"]), (8, 6, 4, 3))
        self.assertEqual(set(watch["countries"]), {"VNM", "SGP", "NGA", "NAME:가상공화국"})
        self.assertEqual(st["unmapped_countries"], ["가상공화국"])
        self.assertEqual(st["unmatched_diseases"], [])                         # 15종 모두 법정감염병과 연결
        vnm = watch["countries"]["VNM"]
        self.assertEqual([d["name"] for d in vnm["diseases"]], ["뎅기열", "콜레라"])
        self.assertTrue(all(d["disease_id"].startswith("inf_") for d in vnm["diseases"]))
        zika = watch["countries"]["SGP"]["diseases"][0]
        self.assertEqual((zika["api_name"], zika["name"]), ("지카바이러스", "지카바이러스감염증"))  # 기본 별칭
        self.assertEqual(watch["as_of"], "2026-10-22")

    def test_all_icd_names_link_to_disease_list(self):
        from data_prep.quarantine import QuarantineRecord
        recs = [QuarantineRecord("베트남", n, "2025-01-01", "", "Y") for n in ICD_CODES.values()]
        _, st = build(recs)
        self.assertEqual(st["unmatched_diseases"], [])

    def test_csv(self):
        from data_prep.csv_loader import read_table
        header, rows, _ = read_table(FX / "quarantine_file_sample.csv")
        recs = from_csv_rows(header, rows)
        self.assertEqual(len(recs), 3)
        watch, st = build(recs)
        self.assertEqual(set(watch["countries"]), {"VNM", "SGP"})
        with self.assertRaises(ValueError):
            from_csv_rows(["나라", "병"], [])


class FetchScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(paths, "PROCESSED", self.tmp), mock.patch.object(paths, "REPORTS", self.tmp),
                        mock.patch.object(paths, "RAW_API", self.tmp / "raw")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, argv):
        import importlib
        import scripts.fetch_quarantine as fq
        importlib.reload(fq)   # WATCH_PATH가 패치된 PROCESSED를 쓰도록
        return fq.main(argv)

    def test_csv_mode(self):
        self.assertEqual(self._run(["--csv", str(FX / "quarantine_file_sample.csv"), "--date", "2026-10-22"]), 0)
        watch = json.loads((self.tmp / "country_watch.json").read_text(encoding="utf-8"))
        self.assertEqual(set(watch["countries"]), {"VNM", "SGP"})
        self.assertIn("지도에 못 올린 국가", (self.tmp / "quarantine_report.md").read_text(encoding="utf-8"))

    def test_resolve_endpoint(self):
        from scripts.fetch_quarantine import DEFAULT_ENDPOINT, resolve_endpoint
        self.assertEqual(DEFAULT_ENDPOINT, "https://apis.data.go.kr/1790387/QuarantineRegion/NationDisease")
        for old in (None, "", "TODO: …<오퍼레이션 이름>", "https://apis.data.go.kr/1790387/QuarantineRegion/"):
            self.assertEqual(resolve_endpoint(old), DEFAULT_ENDPOINT)   # 옛 설정(TODO)·기본 주소만 → NationDisease
        self.assertEqual(resolve_endpoint(DEFAULT_ENDPOINT), DEFAULT_ENDPOINT)
        with self.assertRaises(SystemExit) as ctx:                     # 설정 조회 오퍼레이션은 지도에 안 맞음
            resolve_endpoint("https://apis.data.go.kr/1790387/QuarantineRegion/DiseaseNation")
        self.assertIn("NationDisease", str(ctx.exception))

    def test_raw_mode_rebuilds_without_api(self):
        with self.assertRaises(SystemExit) as ctx:                     # 저장본이 없으면 안내
            self._run(["--raw"])
        self.assertIn("먼저", str(ctx.exception))
        with mock.patch.dict("os.environ", {"DATA_GO_KR_SERVICE_KEY": "k"}), \
                mock.patch("data_prep.kdca_api.fetch_all", lambda *a, **k: api_items()):
            self._run(["--date", "2026-10-22"])                        # API 수집 → 원본 저장
        first = (self.tmp / "country_watch.json").read_text(encoding="utf-8")
        (self.tmp / "country_watch.json").unlink()

        def no_api(*a, **k):
            raise AssertionError("--raw 인데 API를 불렀습니다")
        with mock.patch("data_prep.kdca_api.fetch_all", no_api):
            self.assertEqual(self._run(["--raw", "--date", "2026-10-22"]), 0)
            raw = next((self.tmp / "raw" / "quarantine_area").glob("*.json"))
            self.assertEqual(self._run(["--raw", str(raw), "--date", "2026-10-22"]), 0)   # 경로 지정
        again = json.loads((self.tmp / "country_watch.json").read_text(encoding="utf-8"))
        self.assertEqual(again["countries"], json.loads(first)["countries"])           # 같은 결과
        self.assertIn("저장본", again["collected_from"])

    def test_http_403_guidance(self):
        import requests
        resp = requests.Response()
        resp.status_code = 403
        err = requests.HTTPError("HTTP 403: https://apis.data.go.kr/x?serviceKey=***\n    응답: Forbidden", response=resp)

        def fake_fetch(*a, **k):
            raise err
        with mock.patch.dict("os.environ", {"DATA_GO_KR_SERVICE_KEY": "abc%2B"}), \
                mock.patch("data_prep.kdca_api.fetch_all", fake_fetch):
            with self.assertRaises(SystemExit) as ctx:
                self._run(["--date", "2026-10-22"])
        msg = str(ctx.exception)
        self.assertIn("활용신청", msg)
        self.assertIn("Decoding", msg)
        self.assertIn("--csv", msg)

    def test_http_error_shows_body(self):
        import requests
        from data_prep.http_client import PoliteSession
        resp = requests.Response()
        resp.status_code, resp.url, resp._content = 403, "https://x/y?serviceKey=SECRET", b"Forbidden"
        s = PoliteSession("t", min_interval=0, sleep=lambda _: None)
        s.session = mock.Mock(get=mock.Mock(return_value=resp))
        with self.assertRaises(requests.HTTPError) as ctx:
            s.get("https://x/y")
        self.assertIn("응답: Forbidden", str(ctx.exception))
        self.assertNotIn("SECRET", str(ctx.exception))

    def test_api_mode_params_and_todo(self):
        with mock.patch.dict("os.environ", {"DATA_GO_KR_SERVICE_KEY": "k"}):
            seen = {}

            def fake_fetch(src, session, key, max_pages=50):
                seen.update(src["api"])
                return api_items()
            with mock.patch("data_prep.kdca_api.fetch_all", fake_fetch):
                self.assertEqual(self._run(["--date", "2026-10-22"]), 0)       # --endpoint 없이 기본 주소
                self.assertTrue(seen["endpoint"].endswith("/QuarantineRegion/NationDisease"))
                self.assertEqual(self._run(["--endpoint", "https://apis.example/quarantine", "--date", "2026-10-22"]), 0)
        self.assertEqual(seen["endpoint"], "https://apis.example/quarantine")
        self.assertEqual((seen["params"]["resType"], seen["params"]["useYn"]), ("2", "Y"))
        self.assertNotIn("type", seen["params"])
        watch = json.loads((self.tmp / "country_watch.json").read_text(encoding="utf-8"))
        self.assertEqual(len(watch["countries"]), 4)
        self.assertTrue(list((self.tmp / "raw" / "quarantine_area").glob("*.json")))   # 원본 저장


class CountryNameTest(unittest.TestCase):
    """실제 수집(2026-10-22, 970건)에서 지도에 못 올렸던 30개 이름."""
    REPORTED = ["과들루프", "남아프리카", "러시아연방", "마르티니크", "마요트", "마이크로네시아(코스라이제도)", "미국령버진제도",
                "버뮤다제도", "생바르텔레미", "세이쉴", "세인트마틴섬", "세인트킷츠네비스", "솔로몬아일랜드", "신트마르턴",
                "아랍에미레이트", "아루바", "아프카니스탄", "안굴라", "영국령버진제도", "왈리,푸트나제도", "우르과이", "쿠크제도",
                "키르바시", "키르키즈스탄", "터크스", "토켈로", "튀니지아", "튀르키예공화국", "트발루", "프랑스령폴리네시아"]

    def test_reported_names_mapped(self):
        from data_prep.quarantine import load_country_codes, name_key
        codes = load_country_codes(paths.CONFIG / "country_names_ko.json")
        missing = [n for n in self.REPORTED if name_key(n) not in codes]
        self.assertEqual(missing, [])
        self.assertEqual(codes[name_key("러시아연방")], "RUS")
        self.assertEqual(codes[name_key("왈리,푸트나제도")], "WLF")

    def test_every_code_visible_on_map(self):
        """모든 ISO-3 코드가 지도 경계에 있거나 점 좌표가 있어야 한다 (안 보이는 나라 없음)."""
        from data_prep.quarantine import load_country_codes
        from ui.map_view import load_points
        topo = json.loads((paths.ROOT / "ui" / "static" / "topojson" / "world_110m.json").read_text(encoding="utf-8"))
        shapes = {g.get("id") for g in topo["objects"]["countries"]["geometries"]}
        codes = set(load_country_codes(paths.CONFIG / "country_names_ko.json").values())
        self.assertEqual(sorted(codes - shapes - set(load_points())), [])

    def test_small_islands_drawn_as_points(self):
        from service.countries import CountryWatch, WatchData
        from ui.map_view import SELECTED, build_world_map
        watch = WatchData(True, "2026-10-22", "t", "", {
            "PYF": CountryWatch("PYF", "프랑스령폴리네시아", "PYF", [{"name": "뎅기열"}]),
            "TUN": CountryWatch("TUN", "튀니지", "TUN", [{"name": "콜레라"}, {"name": "황열"}])})
        fig = build_world_map(watch, "PYF", {})
        dots = next(t for t in fig.data if t.name == "작은 지정 국가 점")
        self.assertEqual([c[0] for c in dots.customdata], ["PYF"])      # 경계가 있는 튀니지는 영역으로만
        self.assertEqual(dots.marker.color[0], SELECTED)


if __name__ == "__main__":
    unittest.main()
