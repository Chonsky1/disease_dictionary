"""검증 도구 (scripts/verify_checklist.py, compare_rag.py, explore_check.py)와 조건 탐색 규칙 (F2011, F2015)."""
import unittest

from data_prep import settings as paths
from data_prep.diseases import load_diseases
from scripts.compare_rag import build_cases, josa, judge
from scripts.verify_checklist import counts, parse_ask
from service.explore import satisfies, structured_conditions, where_filter

ASK_OUT = """== 홍역 (제2급) | top_k=5 search=similarity policy=always
  … 검색어: 홍역 바이러스, MMR 백신

[프롬프트 1] 검색어: ['홍역 바이러스', 'MMR 백신']
[수집] (item/수집) 홍역 → 색인 ['홍역'] / 버림 - / 청크 4
[수집] (query/캐시) 홍역 바이러스 → 색인 - / 버림 ['홍역 (영화)'] / 청크 0

[근거 목록]
  [근거 1] 항목 메타데이터 · 홍역  · https://dportal.kdca.go.kr/x
  [근거 2] 위키백과 · 홍역 / 개요 · https://ko.wikipedia.org/wiki/홍역

[답변]
홍역은 제2급입니다 [근거 1]. 공기로 전파됩니다 [근거 1, 2].

[인용된 근거] ['근거 1', '근거 2']

[시간] 3.2초 · LLM 4회
"""


class VerifyToolTest(unittest.TestCase):
    def test_parse_ask(self):
        a = parse_ask(ASK_OUT)
        self.assertEqual(a["queries"], "['홍역 바이러스', 'MMR 백신']")
        self.assertEqual([(r[0], r[1]) for r in a["rows"]], [("item", "수집"), ("query", "캐시")])
        self.assertEqual([e[1] for e in a["evidence"]], ["항목 메타데이터", "위키백과"])
        self.assertEqual(a["answer_nums"], [1, 2])
        self.assertEqual(a["sec"], 3.2)

    def test_counts(self):
        self.assertEqual(counts("로드한 항목 133건 / disease_meta 청크 133건 / disease_docs 청크 42건"), (133, 133, 42))


class CompareRagTest(unittest.TestCase):
    def setUp(self):
        self.cases = {c.kind + c.disease: c for c in build_cases(load_diseases(paths.DISEASES_CSV))}

    def test_cases_from_data(self):
        self.assertGreaterEqual(len(self.cases), 5)
        covid = self.cases["grade코로나바이러스감염증-19"]
        self.assertEqual(covid.expected, "제4급")
        self.assertIn("코로나바이러스감염증-19는", covid.question)
        self.assertEqual(josa("엠폭스(Mpox)", "을를"), "엠폭스(Mpox)를")

    def test_judge(self):
        covid = self.cases["grade코로나바이러스감염증-19"]
        self.assertTrue(judge(covid, "제4급 감염병입니다 [근거 1].")["correct"])
        self.assertFalse(judge(covid, "제2급 감염병입니다. 과거에는 제1급이었습니다.")["correct"])
        unknown = self.cases["abstain뎅기열"]
        r = judge(unknown, "제공된 문서에서 확인되지 않습니다.")
        self.assertTrue(r["correct"] and r["abstained"] and not r["cited"])
        kcd = self.cases["kcd쯔쯔가무시증"]
        self.assertTrue(judge(kcd, "KCD 코드는 A75.3입니다 [근거 1]")["correct"])


class ExploreRuleTest(unittest.TestCase):
    def test_structured_conditions(self):
        c = structured_conditions("병원체보유자도 신고해야 하는 제3급 감염병")
        self.assertEqual(c, {"grade": [3], "scope": {"병원체보유자": True}})
        self.assertEqual(where_filter(c), {"$and": [{"grade": 3}, {"scope_병원체보유자": True}]})
        self.assertEqual(structured_conditions("의사환자는 신고하지 않는 2급")["scope"], {"의사환자": False})
        self.assertEqual(structured_conditions("모기가 옮기는 감염병"), {})

    def test_post_filter_drops_mismatch(self):
        cons = {"grade": [3], "scope": {"병원체보유자": True}}
        self.assertTrue(satisfies({"grade": 3, "scope_병원체보유자": True}, cons))
        self.assertFalse(satisfies({"grade": 2, "scope_병원체보유자": True}, cons))
        self.assertFalse(satisfies({"grade": 3, "scope_병원체보유자": False}, cons))


if __name__ == "__main__":
    unittest.main()
