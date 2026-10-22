"""출처 등록부와 화면 이용 조건 문구 (NF2007)."""
import unittest

from data_prep.sources import license_label, registry, stats_terms


class SourcesTest(unittest.TestCase):
    def test_used_sources_have_license(self):
        for sid, s in registry().items():
            self.assertIn("used", s, sid)
            if s["used"]:
                self.assertTrue(s.get("license"), sid)
                self.assertNotIn("TODO", s["license"], sid)
                self.assertTrue(s.get("page"), sid)

    def test_labels(self):
        self.assertEqual(license_label("quarantine_area"), "이용허락범위 제한 없음")
        self.assertEqual(license_label("age_sex_incidence"), "공공누리 제4유형(출처표시·비상업·변경금지)")
        self.assertEqual(license_label("korean_wikipedia"), "CC BY-SA 4.0")
        self.assertEqual(license_label("kdca_disease_list"), "")      # 미확인은 화면에 쓰지 않음
        self.assertIn("집계", stats_terms())
