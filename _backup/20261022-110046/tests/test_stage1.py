"""단계 1 RAG 검증 (네트워크·키 불필요). 실행: python -m unittest discover -s tests -t .

명세서 4장 확인 항목을 자동화했다: 4.1 색인, 4.2 수집·캐시·청크 설정, 4.3 2노드·근거.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from langchain_core.embeddings import DeterministicFakeEmbedding

from data_prep import settings as paths
from data_prep.collector import WikiCollector
from data_prep.diseases import build_index_text, build_metadata, load_diseases
from data_prep.wiki import is_disambiguation
from data_prep.wiki_titles import resolve_titles
from rag.graph import RagServices, build_graph, build_evidence, run
from rag.parsing import cited_numbers, parse_json
from service.explore import find_diseases, grade_filter
from tests.fakes import FakeMediaWiki, RuleChatModel, fake_wiki_client
from vectorstore.store import DiseaseStore

CSV = paths.DISEASES_CSV


def tmp_settings(tmp: Path, **kw) -> dict:
    s = paths.load_rag_settings(**kw)
    s["chroma_dir"] = str(tmp / "chroma")
    return s


class DiseaseLoadTest(unittest.TestCase):
    def test_load_133(self):
        ds = load_diseases(CSV)
        self.assertEqual(len(ds), 133)
        self.assertEqual(len({d.disease_id for d in ds}), 133)
        noro = next(d for d in ds if "노로바이러스" in d.name)
        self.assertEqual((noro.group, noro.display_name, noro.grade), ("장관감염증", "노로바이러스 감염증", "4"))
        sars = next(d for d in ds if "SARS" in d.name)
        self.assertEqual(sars.abbreviations, ["SARS"])
        self.assertIn("중증급성호흡기증후군", sars.wiki_title_candidates())

    def test_index_text_has_all_attributes(self):  # 4.1 의도한 속성이 모두 들어갔는가
        d = next(x for x in load_diseases(CSV) if x.display_name == "홍역")
        text = build_index_text(d, "홍역 요약")
        for must in ("홍역", "제2급", "24시간 이내", "환자 포함", "병원체보유자 제외", "B05", "감염병관리과", "홍역 요약"):
            self.assertIn(must, text)
        meta = build_metadata(d)
        self.assertTrue(all(v is not None for v in meta.values()))


class ParsingTest(unittest.TestCase):
    def test_json_and_citations(self):
        self.assertEqual(parse_json('설명 ```json\n{"a": [1]}\n``` 끝'), {"a": [1]})
        self.assertEqual(parse_json('["x"]'), ["x"])
        self.assertEqual(cited_numbers("A [근거 2]. B [근거 1, 3] C [근거 2~4] D [근거 9]", 4), [2, 1, 3, 4])
        self.assertEqual(cited_numbers("근거 없음", 3), [])

    def test_grade_filter(self):
        self.assertEqual(grade_filter("모기가 옮기는 3급 감염병"), {"grade": 3})
        self.assertEqual(grade_filter("1급이나 제2급"), {"grade": {"$in": [1, 2]}})
        self.assertIsNone(grade_filter("모기"))



class WikiClientTest(unittest.TestCase):
    """진짜 wikipediaapi 라이브러리 + 가짜 MediaWiki 서버(httpx.MockTransport)."""

    def setUp(self):
        self.client, self.server = fake_wiki_client()

    def test_requests_follow_etiquette(self):
        self.client.resolve("홍역")
        for r in self.server.requests:
            self.assertIn("contact: test@example.com", r["_ua"])   # 연락처 User-Agent (NF2006)
            self.assertEqual(r["maxlag"], "5")
            self.assertEqual(r["redirects"], "1")

    def test_resolve_redirect_and_missing(self):
        r = self.client.resolve("Measles")
        self.assertEqual((r["title"], r["pageid"], r["redirected"]), ("홍역", 100, True))
        self.assertIsNone(self.client.resolve("없는 문서"))

    def test_search_candidates(self):
        cands = self.client.search_candidates("홍역 바이러스", limit=5)
        self.assertEqual([c["title"] for c in cands], ["홍역 바이러스", "홍역 (영화)", "홍역 (동음이의)"])
        self.assertEqual(cands[0]["summary"], "홍역 바이러스 요약")          # 스니펫 HTML 태그 제거
        self.assertEqual([c["disambiguation"] for c in cands], [False, False, True])
        self.assertTrue(is_disambiguation("수두 (동음이의)"))

    def test_fetch_page_sections(self):
        page = self.client.fetch_page("홍역")
        self.assertEqual((page["doc_id"], page["revid"], page["license"]), ("wiki-100", 1000, "CC BY-SA 4.0"))
        names = [n for n, _ in page["sections"]]
        self.assertEqual(names, ["개요", "증상", "증상 > 합병증"])   # 각주·외부 링크 제외
        self.assertTrue(page["summary"].startswith("홍역은"))
        self.assertEqual(self.server.counts(), {"info": 1, "extracts": 1})  # 문서당 2회 호출
        with self.assertRaises(LookupError):
            self.client.fetch_page("없는 문서")


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.settings = tmp_settings(self.tmp)
        self.emb = DeterministicFakeEmbedding(size=64)
        self.store = DiseaseStore(self.settings, self.emb)
        self.diseases = load_diseases(CSV)
        self.by_id = {d.disease_id: d for d in self.diseases}
        self.measles = next(d for d in self.diseases if d.display_name == "홍역")
        self.wiki, self.server = fake_wiki_client()
        self.collector = WikiCollector(self.wiki, self.tmp / "pages", self.tmp / "cache.json", 5, 2)
        self.store.index_meta([build_index_text(d) for d in self.diseases], [build_metadata(d) for d in self.diseases])
        RuleChatModel.calls = []
        self.llm = RuleChatModel()
        titles = {self.measles.disease_id: {"title": "홍역", "pageid": 100, "url": "u", "method": "exact"}}
        self.svc = RagServices(self.llm, self.store, self.collector, self.by_id, titles, self.settings)
        self.graph = build_graph(self.svc)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_meta_index_idempotent(self):  # 4.1 두 번 색인해도 늘지 않음
        n1 = self.store.meta_count()
        self.store.index_meta([build_index_text(d) for d in self.diseases], [build_metadata(d) for d in self.diseases])
        self.assertEqual((n1, self.store.meta_count()), (133, 133))

    def test_graph_has_two_nodes(self):  # 4.3
        nodes = set(self.graph.get_graph().nodes) - {"__start__", "__end__"}
        self.assertEqual(nodes, {"retrieve", "generate"})

    def test_end_to_end_and_cache(self):  # 4.2·4.3
        events = []
        state = run(self.graph, "홍역은 어떻게 전파되나요?", self.measles.disease_id, events.append)
        self.assertEqual(state["queries"], ["홍역 바이러스", "MMR 백신"])
        log = {r["query"]: r for r in state["collect_log"]}
        self.assertEqual(log["홍역"]["kind"], "item")                    # 항목 자체 문서 항상 포함
        self.assertIn("홍역", log["홍역"]["indexed"])
        self.assertEqual(log["홍역 바이러스"]["indexed"], ["홍역 바이러스"])  # 선별된 문서만 색인
        self.assertIn("홍역 (영화)", log["홍역 바이러스"]["rejected"])      # 버려진 후보 기록
        self.assertNotIn("홍역 (동음이의)", log["홍역 바이러스"]["rejected"])  # 동음이의 문서는 후보에서 제외
        ev = state["evidence"]
        self.assertEqual((ev[0]["n"], ev[0]["source_type"]), (1, "meta"))  # 메타 청크가 항상 근거 1
        self.assertTrue(all(e["source_type"] == "wiki" for e in ev[1:]))
        self.assertEqual(len({e["chunk_id"] for e in ev}), len(ev))       # 중복 없음
        self.assertEqual([e["n"] for e in state["cited"]], [1, 2])
        self.assertTrue(any("새로 색인한 문서" in e["message"] for e in events))  # 진행 상태 이벤트

        n_requests = len(self.server.requests)
        state2 = run(self.graph, "홍역은 어떻게 전파되나요?", self.measles.disease_id)
        self.assertEqual(len(self.server.requests), n_requests)  # NF2002: 재질문 시 위키 호출 0회
        self.assertTrue(all(r["from_cache"] for r in state2["collect_log"]))

    def test_docs_scoped_to_disease_and_ids(self):  # F2005 청크 ID, F2006 범위 한정
        run(self.graph, "전파 경로", self.measles.disease_id)
        other = next(d for d in self.diseases if d.display_name == "수두")
        self.svc.wiki_titles[other.disease_id] = {"title": "홍역", "pageid": 100}  # 같은 문서를 다른 항목이 수집
        run(self.graph, "전파 경로", other.disease_id)
        got = self.store.docs._collection.get(include=["metadatas"])
        self.assertTrue(all(i.startswith(f"{m['disease_id']}:{m['doc_id']}:") for i, m in zip(got["ids"], got["metadatas"])))
        self.assertTrue(self.store.is_indexed(self.measles.disease_id, "wiki-100"))
        self.assertTrue(self.store.is_indexed(other.disease_id, "wiki-100"))
        hits = self.store.search_docs("전파", self.measles.disease_id, k=20)
        self.assertTrue(all(h.metadata["disease_id"] == self.measles.disease_id for h in hits))
        self.assertTrue(all(h.metadata["section"] != "각주" for h in hits))  # 꼬리 섹션 제외

    def test_chunk_settings_change_count(self):  # 4.2 청크 크기를 바꾸면 청크 수가 달라짐
        page = self.wiki.fetch_page("홍역")
        n_big = self.store.index_page("inf_0001", "x", page, "q")
        self.store.settings = {**self.settings, "chunk_size": 300, "chunk_overlap": 50}
        n_small = self.store.index_page("inf_0001", "x", page, "q")
        self.assertGreater(n_small, n_big)
        self.assertEqual(self.store.docs_count("inf_0001"), n_small)  # 낡은 청크가 남지 않음

    def test_topk_and_mmr(self):  # 4.3 Top-k, F2014
        run(self.graph, "홍역 증상", self.measles.disease_id)
        a = self.store.search_docs("발진", self.measles.disease_id, k=2)
        b = self.store.search_docs("발진", self.measles.disease_id, k=4)
        m = self.store.search_docs("발진", self.measles.disease_id, k=3, search_type="mmr")
        self.assertEqual((len(a), len(b), len(m)), (2, 4, 3))

    def test_no_citation(self):  # F2008 인용 없음 안내 경로
        self.llm.answer_text = "제공된 문서에서 확인되지 않습니다."
        state = run(self.graph, "치료제 가격은?", self.measles.disease_id)
        self.assertEqual(state["cited"], [])
        self.assertIn("확인되지 않습니다", state["answer"])

    def test_collect_failure_falls_back(self):
        broken, _ = fake_wiki_client(FakeMediaWiki(fail=True))   # 503 → 재시도 후 WikiHttpError
        self.svc.collector = WikiCollector(broken, self.tmp / "p2", self.tmp / "c2.json")
        self.svc.wiki_titles = {}
        state = run(build_graph(self.svc), "전파", self.measles.disease_id)
        self.assertTrue(state["warnings"])
        self.assertEqual(state["evidence"][0]["source_type"], "meta")

    def test_condition_search_drops_unknown_ids(self):  # F2011
        res = find_diseases("1급 감염병", self.store, self.llm, k=10)
        self.assertTrue(res)
        self.assertTrue(all(r["grade"] == 1 for r in res))
        self.assertNotIn("inf_9999", [r["disease_id"] for r in res])

    def test_resolve_titles(self):
        ds = [self.measles, next(d for d in self.diseases if d.display_name == "수두")]
        out = resolve_titles(ds, self.wiki, overrides={})
        self.assertEqual(out[self.measles.disease_id]["method"], "exact")
        self.assertEqual(out[ds[1].disease_id]["method"], "search")  # 수두 문서가 없어 검색 1순위
        out2 = resolve_titles(ds, self.wiki, overrides={"수두": "Measles"}, done={})
        self.assertEqual(out2[ds[1].disease_id]["method"], "override")
        n = len(self.server.requests)
        resolve_titles(ds, self.wiki, overrides={}, done=out)        # 이미 해석한 항목은 다시 묻지 않음
        self.assertEqual(len(self.server.requests), n)

    def test_evidence_dedupe_by_text(self):
        from langchain_core.documents import Document
        meta = self.store.get_meta(self.measles.disease_id)
        dup = [Document(id="a:wiki-1:0000", page_content="[머리말 A]\n같은 본문", metadata={"title": "t", "source_url": "u"}),
               Document(id="b:wiki-2:0000", page_content="[머리말 B]\n같은  본문", metadata={"title": "t", "source_url": "u"})]
        self.assertEqual(len(build_evidence(meta, dup)), 2)  # 메타 1 + 중복 본문 1


class BuildIndexScriptTest(unittest.TestCase):
    def test_script_twice_same_count(self):
        from scripts import build_index
        tmp = Path(tempfile.mkdtemp())
        saved = {k: getattr(paths, k) for k in ("WIKI_TITLES", "WIKI_PAGES", "QUERY_CACHE", "load_rag_settings")}
        orig_load = paths.load_rag_settings
        try:
            paths.WIKI_TITLES = tmp / "titles.json"
            paths.WIKI_PAGES, paths.QUERY_CACHE = tmp / "pages", tmp / "cache.json"
            paths.load_rag_settings = lambda **kw: {**orig_load(**kw), "chroma_dir": str(tmp / "chroma")}
            emb, (wiki, server) = DeterministicFakeEmbedding(size=32), fake_wiki_client()
            self.assertEqual(build_index.main(["--prefetch-docs"], embeddings=emb, wiki_client=wiki), 0)
            first_calls = len(server.requests)
            self.assertEqual(build_index.main(["--prefetch-docs"], embeddings=emb, wiki_client=wiki), 0)
            self.assertEqual(len(server.requests), first_calls)  # 두 번째 실행은 제목·본문 모두 캐시
            store = DiseaseStore(paths.load_rag_settings(), emb)
            self.assertEqual(store.meta_count(), 133)
            self.assertIn("개요(위키백과 요약)", store.get_meta("inf_0021").page_content)  # 홍역
            titles = json.loads(paths.WIKI_TITLES.read_text(encoding="utf-8"))
            self.assertEqual(len(titles), 133)
            first_docs = store.docs_count()
            self.assertEqual(build_index.main(["--rechunk", "--chunk-size", "300", "--chunk-overlap", "50", "--no-wiki"],
                                              embeddings=emb), 0)
            self.assertGreater(DiseaseStore(paths.load_rag_settings(), emb).docs_count(), first_docs)
        finally:
            for k, v in saved.items():
                setattr(paths, k, v)
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
