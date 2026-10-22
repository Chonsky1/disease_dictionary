"""단계 2: 연령별·시기별 통계 파싱과 화면용 계산."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from data_prep import settings as paths
from data_prep.age_stats import (AGE_GROUPS, SEASON_WEEKS, kind_of_csv, kind_of_xlsx, num, parse_basic_csv,
                                 read_meta, season_slot, subject_of)
from data_prep.xlsx_lite import read_xlsx, write_xlsx
from service.age import load_age_stats, profile, top_for_age, trend_summary
from tests.age_fixtures import basic_csv_rows, build_sample, make_stats_dir


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class ParseTest(_Tmp):
    def test_xlsx_roundtrip_and_meta(self):
        grid = [[], ["", "감염병", "", "성매개감염병 선택됨, (1기)매독", "", "", "", "조회기간", "", "2021년 01주 ~ 2026년 43주, 주별"],
                [], ["", "조회구분", "", "연령별, 성별"]]
        write_xlsx(self.tmp / "a.xlsx", grid)
        g = read_xlsx(self.tmp / "a.xlsx")
        meta = read_meta(g)
        self.assertEqual(meta["조회구분"], "연령별, 성별")
        self.assertEqual(subject_of(meta), ("성매개감염병", "매독(1기)"))
        self.assertEqual(kind_of_xlsx(meta), "age_sex")
        self.assertEqual(subject_of({"감염병": "안과감염병 선택됨, 전체"}), ("안과감염병", "안과감염병"))

    def test_kinds(self):
        self.assertEqual(kind_of_xlsx({"조회구분": "연령별", "조회기간": "2025 ~ 2026 , 절기"}), "sari")
        self.assertEqual(kind_of_xlsx({"조회구분": "연령(전체)", "조회기간": "2021년 ~ 2026년, 주차별"}), "year_week")
        self.assertEqual(kind_of_xlsx({"조회구분": "-", "조회기간": "2021년 01주 ~ 2026년 43주, 주별"}), "pathogen_weekly")
        self.assertEqual(kind_of_xlsx({"조회구분": "혈액검체발생률", "조회기간": "2026년, 월별"}), "unsupported")
        self.assertEqual(kind_of_csv([["전체(2021-2022절기)", "1.0"]]), "flu_csv")
        self.assertEqual(kind_of_csv([["계", "계", "0"]]), "basic_csv")

    def test_numbers_and_weeks(self):
        self.assertEqual((num("4,983"), num("0.4"), num("-"), num("집계 중"), num("")), (4983, 0.4, None, None, None))
        self.assertEqual(season_slot(2024, 36), ("2024-2025", 0))
        self.assertEqual(season_slot(2024, 53), ("2024-2025", 17))
        self.assertEqual(season_slot(2025, 1), ("2024-2025", 18))
        self.assertEqual((len(SEASON_WEEKS), SEASON_WEEKS[18]), (53, "1주"))

    def test_basic_csv_column_count_must_match(self):
        rows = [r[:-1] for r in basic_csv_rows()]
        with self.assertRaises(ValueError) as ctx:
            parse_basic_csv(rows, ["a", "b"])
        self.assertIn("age_stats_columns.json", str(ctx.exception))


class BuildTest(_Tmp):
    def test_build_all_kinds(self):
        out = build_sample(self.tmp / "age.json", make_stats_dir(self.tmp / "in"))
        d = json.loads(out.read_text(encoding="utf-8"))
        n = d["age_sets"]["notifiable"]["diseases"]
        self.assertEqual(set(n), {"수두", "카바페넴내성장내세균목(CRE) 감염증"})       # 0인 열은 뺌
        self.assertEqual(n["수두"]["by_age"]["0~4세"], [80, 60])
        self.assertEqual(n["수두"]["total"], 300)
        self.assertTrue(n["수두"]["disease_id"])
        self.assertFalse(d["age_sets"]["notifiable"]["columns_verified"])
        sti = d["age_sets"]["sti"]["diseases"]["임질"]
        self.assertEqual((sti["total"], sti["by_age"]["20~24세"]), (1400, [600, 300]))
        # 절기 두 파일 병합, '-'만 있는 주는 버림
        self.assertEqual(sorted(d["sari_by_age"]["seasons"]), ["2024-2025", "2025-2026"])
        self.assertEqual(d["sari_by_age"]["seasons"]["2024-2025"]["65세 이상"], 10 + 26 + 30)
        ids = [t["id"] for t in d["trends"]]
        self.assertEqual(ids[:2], ["flu", "sari"])
        self.assertIn("yw_수족구병", ids)
        pw = next(t for t in d["trends"] if t["id"] == "pw_급성호흡기감염증")
        self.assertEqual(set(pw["items"]), {"아데노바이러스", "호흡기세포융합바이러스"})   # '계' 열 제외
        self.assertTrue(pw["items"]["호흡기세포융합바이러스"]["disease_id"])
        self.assertIsNone(pw["items"]["아데노바이러스"]["series"]["2026"][0])           # '집계 중'

    def test_script_report_and_empty_dir(self):
        import scripts.build_age_stats as bas
        make_stats_dir(self.tmp / "in")
        with mock.patch.object(bas, "OUT", self.tmp / "age.json"), mock.patch.object(paths, "REPORTS", self.tmp):
            self.assertEqual(bas.main(["--dir", str(self.tmp / "in")]), 0)
            report = (self.tmp / "age_stats_report.md").read_text(encoding="utf-8")
            self.assertIn("사용 안 함", report)                 # 의료관련감염병(월별)
            self.assertIn("추정", report)
            with self.assertRaises(SystemExit):
                bas.main(["--dir", str(self.tmp / "empty")])
            # 압축을 풀어 하위 폴더째 넣은 경우도 찾음, 부속 파일은 제외
            nested = self.tmp / "nested"
            shutil.copytree(self.tmp / "in", nested / "감염병통계")
            (nested / "__MACOSX").mkdir()
            (nested / "__MACOSX" / "._x.csv").write_bytes(b"junk")
            (nested / "~$열린파일.xlsx").write_bytes(b"lock")
            self.assertEqual(len(bas.find_files(nested)), len(bas.find_files(self.tmp / "in")))
            (self.tmp / "zipped").mkdir()
            (self.tmp / "zipped" / "통계.zip").write_bytes(b"PK")
            with self.assertRaises(SystemExit) as ctx:
                bas.main(["--dir", str(self.tmp / "zipped")])
            self.assertIn("압축을 먼저", str(ctx.exception))


class ServiceTest(_Tmp):
    def setUp(self):
        super().setUp()
        self.stats = load_age_stats(build_sample(self.tmp / "age.json", make_stats_dir(self.tmp / "in")))

    def test_missing_file(self):
        self.assertFalse(load_age_stats(self.tmp / "none.json").loaded)

    def test_top_for_age(self):
        s = self.stats.sets["notifiable"]
        self.assertEqual([r["name"] for r in top_for_age(s, "0~4세")], ["수두"])
        old = top_for_age(s, "65세 이상", by="share")
        self.assertEqual((old[0]["name"], old[0]["share"]), ("카바페넴내성장내세균목(CRE) 감염증", 1.0))
        self.assertEqual(top_for_age(s, "0~4세", sex="여")[0]["count"], 60)
        self.assertEqual(top_for_age(s, "0~4세", by="share", min_total=1000), [])

    def test_profile(self):
        p = profile(self.stats.sets["notifiable"].diseases["수두"], AGE_GROUPS)
        self.assertEqual((p["peak_age"], p["total"]), ("0~4세", 300))   # 같으면 어린 연령대
        self.assertAlmostEqual(p["kids_share"], 280 / 300)
        self.assertAlmostEqual(p["male_share"], 162 / 300)

    def test_trend_summary(self):
        flu = self.stats.trends["flu"]
        sm = trend_summary(flu, flu.items["인플루엔자"])
        self.assertEqual([(p["key"], p["week"]) for p in sm["peaks"]], [("2024-2025", "1주"), ("2025-2026", "48주")])
        self.assertIn(sm["typical_month"], {"11월", "12월"})   # 2025년 1주 월요일 = 2024-12-30
        self.assertEqual(sm["latest"]["key"], "2025-2026")
        hfmd = self.stats.trends["yw_수족구병"]
        self.assertEqual(trend_summary(hfmd, hfmd.items["수족구병"])["typical_month"], "7월")


if __name__ == "__main__":
    unittest.main()
