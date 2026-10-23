"""공공데이터 문서 색인(F2016)과 여행 전 안내(F2010)."""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from langchain_core.embeddings import DeterministicFakeEmbedding

from data_prep import settings as paths
from data_prep.diseases import build_index_text, build_metadata, load_diseases
from data_prep.public_docs import index_public_docs, quarantine_pages, stats_pages
from service.age import load_age_stats
from service.countries import load_watch
from tests.age_fixtures import build_sample, make_stats_dir
from tests.fakes import RuleChatModel
from tests.test_ui import write_sample_watch
from vectorstore.store import DiseaseStore


class PublicDocsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.diseases = load_diseases(paths.DISEASES_CSV)
        self.by_id = {d.disease_id: d for d in self.diseases}
        write_sample_watch(self.tmp / "country_watch.json")
        build_sample(self.tmp / "age_stats.json", make_stats_dir(self.tmp / "stats_in"))
        self.watch = load_watch(self.tmp / "country_watch.json")
        self.stats = load_age_stats(self.tmp / "age_stats.json")
        settings = {**paths.load_rag_settings(), "chroma_dir": str(self.tmp / "chroma")}
        self.store = DiseaseStore(settings, DeterministicFakeEmbedding(size=32))
        self.store.index_meta([build_index_text(d) for d in self.diseases],
                              [build_metadata(d) for d in self.diseases])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def did(self, name):
        return next(d.disease_id for d in self.diseases if d.display_name == name)

    def test_quarantine_record_doc(self):
        pages = quarantine_pages(self.watch, self.by_id)
        yellow = pages[self.did("황열")]
        text = yellow["sections"][0][1]
        self.assertIn("2026-10-22 기준", text)
        self.assertIn("나이지리아", text)
        self.assertEqual(yellow["source_type"], "public_api")
        self.assertEqual(yellow["chunking"], "record")
        self.assertNotIn("THA", {k for k, c in self.watch.countries.items() if c.diseases})   # 지정 해제는 빠짐

    def test_stats_record_doc(self):
        pages = stats_pages(self.stats, self.by_id)
        chick = pages[self.did("수두")]
        text = " ".join(p["sections"][0][1] for p in chick)
        self.assertIn("가장 많은 연령대: 0~4세", text)
        self.assertIn("남 162건", text)
        self.assertTrue(all(p["source_type"] == "public_stats" for p in chick))

    def test_index_record_chunking_idempotent_and_types(self):
        r1 = index_public_docs(self.store, self.diseases, self.watch, self.stats)
        n1 = self.store.docs_count()
        r2 = index_public_docs(self.store, self.diseases, self.watch, self.stats)
        self.assertEqual(n1, self.store.docs_count())                 # 두 번 실행해도 청크 수 불변
        self.assertEqual(r1["chunks"], r2["chunks"])
        self.assertEqual(r1["chunks"], sum(len(p["sections"]) for p in
                                            list(quarantine_pages(self.watch, self.by_id).values()) +
                                            [p for ps in stats_pages(self.stats, self.by_id).values() for p in ps]))
        types = self.store.count_by_type()
        self.assertIn("public_api", types)
        self.assertIn("public_stats", types)
        self.assertEqual(self.store.docs_count(self.did("황열"), "wiki"), 0)   # 위키 문서 수에는 안 들어감
        # 지정이 풀리면 다음 색인 때 문서가 지워진다
        empty = type(self.watch)(True, "2026-11-01", "", "", {})
        r3 = index_public_docs(self.store, self.diseases, empty, self.stats)
        self.assertGreater(r3["removed"], 0)
        self.assertNotIn("public_api", self.store.count_by_type())

    def test_long_country_list_split_with_header(self):
        from service.countries import CountryWatch, WatchData
        did = self.did("황열")
        countries = {f"C{i:03d}": CountryWatch(f"C{i:03d}", f"나라{i:03d}", "", [{"disease_id": did, "name": "황열"}])
                     for i in range(95)}
        page = quarantine_pages(WatchData(True, "2026-10-22", "s", "u", countries), self.by_id)[did]
        self.assertEqual(len(page["sections"]), 3)                    # 40 + 40 + 15
        self.assertTrue(all("황열(제" in body and "95개국" in body for _, body in page["sections"]))

    def test_travel_brief_cites_public_api(self):
        from service.travel import travel_brief
        index_public_docs(self.store, self.diseases, self.watch, self.stats)
        rt = mock.Mock(by_id=self.by_id, store=self.store, services=mock.Mock(llm=RuleChatModel()))
        c = self.watch.countries["VNM"]
        ids = [x["disease_id"] for x in c.diseases if x.get("disease_id")]
        res = travel_brief("베트남", self.watch.as_of, ids, rt)
        kinds = {e["source_type"] for e in res["cited"]}
        self.assertIn("public_api", kinds)                            # 특화 기능의 답에도 근거
        self.assertIn("meta", kinds)
        self.assertRegex(res["answer"], r"\[근거 \d+\]")
        self.assertEqual([e["n"] for e in res["evidence"]], list(range(1, len(res["evidence"]) + 1)))

    def test_retrieve_ignores_public_docs_for_fast_path(self):
        """공공데이터 문서만 있으면 '이미 모은 위키 문서'가 없는 것으로 보고 항목 문서를 수집한다."""
        from data_prep.collector import WikiCollector
        from rag.graph import RagServices, build_graph, run
        from tests.fakes import fake_wiki_client
        index_public_docs(self.store, self.diseases, self.watch, self.stats)
        did = self.did("홍역")
        svc = RagServices(RuleChatModel(), self.store, WikiCollector(fake_wiki_client()[0], self.tmp / "p", self.tmp / "c.json"),
                          self.by_id, {did: {"title": "홍역", "pageid": 100}},
                          {**self.store.settings, "collect_policy": "background"})
        state = run(build_graph(svc), "홍역은 어떻게 전파되나요?", did)
        import threading
        for t in threading.enumerate():
            if t.name.startswith("collect-"):
                t.join(10)
        self.assertGreater(self.store.docs_count(did, "wiki"), 0)
        self.assertTrue(state["evidence"])


if __name__ == "__main__":
    unittest.main()
