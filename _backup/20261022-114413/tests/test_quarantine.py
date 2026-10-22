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

    def test_api_mode_params_and_todo(self):
        with mock.patch.dict("os.environ", {"DATA_GO_KR_SERVICE_KEY": "k"}):
            with self.assertRaises(SystemExit) as ctx:     # 설정이 TODO면 친절한 안내
                self._run(["--date", "2026-10-22"])
            self.assertIn("요청주소", str(ctx.exception))
            with self.assertRaises(SystemExit) as ctx:     # 서비스 기본 주소만 넘기면 오퍼레이션 이름 안내
                self._run(["--endpoint", "https://apis.data.go.kr/1790387/QuarantineRegion", "--date", "2026-10-22"])
            self.assertIn("오퍼레이션 이름", str(ctx.exception))
            seen = {}

            def fake_fetch(src, session, key, max_pages=50):
                seen.update(src["api"])
                return api_items()
            with mock.patch("data_prep.kdca_api.fetch_all", fake_fetch):
                self.assertEqual(self._run(["--endpoint", "https://apis.example/quarantine", "--date", "2026-10-22"]), 0)
        self.assertEqual(seen["endpoint"], "https://apis.example/quarantine")
        self.assertEqual((seen["params"]["resType"], seen["params"]["useYn"]), ("2", "Y"))
        self.assertNotIn("type", seen["params"])
        watch = json.loads((self.tmp / "country_watch.json").read_text(encoding="utf-8"))
        self.assertEqual(len(watch["countries"]), 4)
        self.assertTrue(list((self.tmp / "raw" / "quarantine_area").glob("*.json")))   # 원본 저장


if __name__ == "__main__":
    unittest.main()
