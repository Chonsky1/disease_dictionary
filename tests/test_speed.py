"""챗봇 속도 설정과 시간 기록 (rag/graph.py, rag/metrics.py, rag/answer_cache.py), 답변 카테고리 표시."""
import shutil
import tempfile
import unittest
from pathlib import Path

from langchain_core.embeddings import DeterministicFakeEmbedding

from data_prep import settings as paths
from data_prep.collector import WikiCollector
from data_prep.diseases import build_index_text, build_metadata, load_diseases
from rag.answer_cache import AnswerCache, normalize_question
from rag.graph import RagServices, build_graph, run
from rag.metrics import Meter, describe
from tests.fakes import RuleChatModel, fake_wiki_client
from ui.chat_core import split_sections
from vectorstore.store import DiseaseStore

Q = "홍역은 어떻게 전파되나요?"


class SpeedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.diseases = load_diseases(paths.DISEASES_CSV)
        self.did = next(d.disease_id for d in self.diseases if d.display_name == "홍역")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def svc(self, sub: str = "a", **opts) -> RagServices:
        root = self.tmp / sub
        settings = {**paths.load_rag_settings(), "chroma_dir": str(root / "chroma"), **opts}
        store = DiseaseStore(settings, DeterministicFakeEmbedding(size=32))
        store.index_meta([build_index_text(d) for d in self.diseases], [build_metadata(d) for d in self.diseases])
        llm = RuleChatModel()
        llm.calls = []
        collector = WikiCollector(fake_wiki_client()[0], root / "pages", root / "c.json")
        return RagServices(llm, store, collector, {d.disease_id: d for d in self.diseases},
                           {self.did: {"title": "홍역", "pageid": 100}}, settings,
                           )

    def ask(self, svc, q=Q, events=None):
        return run(build_graph(svc), q, self.did, on_event=(events.append if events is not None else None))

    def test_always_collects_every_time(self):
        svc = self.svc(collect_policy="always")
        first, second = self.ask(svc), self.ask(svc)
        self.assertEqual(first["timings"]["calls"]["llm.queries"], 1)
        self.assertEqual(first["timings"]["llm_calls"], 4)          # 검색어 1 + 선별 2 + 답변 1
        self.assertEqual(second["timings"]["calls"]["llm.queries"], 1)
        self.assertNotIn("llm.select", second["timings"]["calls"])  # 검색어 캐시라 선별은 생략
        self.assertEqual(first["answer"], second["answer"])

    def test_when_needed_skips_collection(self):
        svc = self.svc(collect_policy="when_needed")
        first = self.ask(svc)
        second = self.ask(svc, "홍역의 예방법은?")
        self.assertEqual(first["timings"]["llm_calls"], 4)           # 처음엔 문서가 없어 수집
        self.assertEqual(second["timings"]["calls"], {"embed.query": 1, "llm.answer": 1})   # 둘째부터 LLM 1회
        self.assertIn("수집 생략", second["timings"]["notes"][0])
        self.assertTrue(second["cited"])

    def test_background_collects_after_answer(self):
        svc = self.svc(collect_policy="background")
        self.ask(svc)
        before = svc.store.docs_count(self.did)
        second = self.ask(svc, "수두 백신과 홍역")
        self.assertEqual(second["timings"]["llm_calls"], 1)
        import threading
        for t in threading.enumerate():
            if t.name.startswith("collect-"):
                t.join(10)
        self.assertGreaterEqual(svc.store.docs_count(self.did), before)
        self.assertEqual(svc.background, set())

    def test_background_cold_uses_item_doc_only(self):
        """처음 묻는 감염병: 항목 자체 문서만 바로 색인(LLM 없음) → 답변 LLM 1회, 추가 수집은 배경."""
        svc = self.svc(collect_policy="background")
        first = self.ask(svc)
        self.assertEqual(first["timings"]["llm_calls"], 1)
        self.assertEqual(first["timings"]["calls"]["embed.index"], 1)            # 항목 문서 '홍역'
        self.assertIn("홍역", [e["title"] for e in first["cited"]] + [e["title"] for e in first["evidence"]])
        import threading
        for t in threading.enumerate():
            if t.name.startswith("collect-"):
                t.join(10)
        self.assertGreater(len(svc.store.list_docs(self.did)), 1)                 # 배경에서 검색어 문서까지

    def test_background_report_shown_next_answer(self):
        """배경 수집 결과(프롬프트 1 검색어·새로 색인한 문서)를 다음 답변 진행 상태에 보여 준다 (F2003·F2004·F2012)."""
        import threading
        svc = self.svc(collect_policy="background")
        ev1: list = []
        self.ask(svc, events=ev1)
        self.assertTrue(any("배경에서 모읍니다" in e.get("message", "") for e in ev1))
        for t in threading.enumerate():
            if t.name.startswith("collect-"):
                t.join(10)
        report = svc.background_log[self.did]
        self.assertTrue(report["queries"])
        ev2: list = []
        second = self.ask(svc, "홍역의 예방법은?", events=ev2)
        msgs = [e.get("message", "") for e in ev2]
        self.assertTrue(any(m.startswith("지난 질문 뒤 배경 수집 완료 · 검색어(프롬프트 1):") for m in msgs), msgs)
        self.assertTrue(any(m.startswith("새로 색인한 문서(프롬프트 2 선별):") for m in msgs), msgs)
        self.assertEqual(second["background_report"]["queries"], report["queries"])
        self.assertNotIn(self.did, svc.background_log)      # 한 번 보여 주면 비움

    def test_describe_background_error(self):
        from rag.graph import describe_background
        self.assertIn("실패", describe_background({"error": "Timeout"})[0])

    def test_parallel_same_result_as_sequential(self):
        a = self.ask(self.svc("par", collect_policy="always", parallel_collect=True))
        b = self.ask(self.svc("seq", collect_policy="always", parallel_collect=False))
        self.assertEqual([e["title"] for e in a["evidence"]], [e["title"] for e in b["evidence"]])
        self.assertEqual(a["timings"]["calls"], b["timings"]["calls"])

    def test_stream_tokens_and_first_token(self):
        events = []
        st = self.ask(self.svc(collect_policy="always", stream_answer=True), events=events)
        tokens = "".join(e["text"] for e in events if e.get("stage") == "token")
        self.assertEqual(tokens.strip(), st["answer"])
        self.assertLessEqual(st["timings"]["first_token_s"], st["timings"]["total_s"])
        st2 = self.ask(self.svc("b", collect_policy="always", stream_answer=False))
        self.assertEqual(st2["answer"], st["answer"])

    def test_aux_model_used_for_helpers(self):
        aux = RuleChatModel()
        aux.calls = []
        svc = self.svc(collect_policy="always")
        svc.aux_llm = aux
        svc.llm.calls = []
        self.ask(svc)
        self.assertEqual(sorted(set(aux.calls)), ["query", "select"])
        self.assertEqual(svc.llm.calls, ["answer"])

    def test_answer_cache(self):
        svc = self.svc(collect_policy="always")
        svc.answer_cache = AnswerCache(self.tmp / "answers.json")
        first = self.ask(svc)
        again = self.ask(svc, "  홍역은 어떻게   전파되나요 ")          # 띄어쓰기·물음표 달라도 같은 질문
        self.assertTrue(again["timings"]["cache_hit"])
        self.assertEqual(again["answer"], first["answer"])
        self.assertEqual(again["cited"], first["cited"])
        self.assertIn("저장된 답변", describe(again["timings"]))
        self.assertEqual(normalize_question("A b?"), "a b")


class MeterTest(unittest.TestCase):
    def test_summary_and_describe(self):
        m = Meter()
        with m.stage("queries"), m.llm("queries"):
            pass
        m.count("wiki.search", 2)
        m.first_token()
        s = m.summary()
        self.assertEqual((s["calls"]["llm.queries"], s["calls"]["wiki.search"], s["llm_calls"]), (1, 2, 1))
        self.assertIn("LLM 1회", describe(s))
        self.assertIn("검색어", describe(s))


class WarmupTest(unittest.TestCase):
    def test_warmup_embeds_once_in_background(self):
        from unittest import mock
        from service.runtime import warmup
        store = mock.Mock()
        warmup(store).join(5)
        store.docs.embeddings.embed_query.assert_called_once()
        store.docs.embeddings.embed_query.side_effect = RuntimeError("네트워크")
        warmup(store).join(5)          # 실패해도 예외 없이 끝남


class QuotaTest(unittest.TestCase):
    def test_quota_detection(self):
        from scripts.bench_chat import is_quota
        from ui.chat_core import is_quota_error
        err = RuntimeError("Error code: 429 - {'code': 'organization_spend_limit_exceeded', 'type': 'insufficient_quota'}")
        self.assertTrue(is_quota(err) and is_quota_error(err))
        self.assertFalse(is_quota_error(ValueError("다른 오류")))


class AnswerToneTest(unittest.TestCase):
    def test_polite_tone_and_cache_version(self):
        from rag.prompts import ANSWER_GENERATION, ANSWER_PROMPT_VERSION
        system = ANSWER_GENERATION.messages[0].prompt.template
        self.assertIn("'~입니다', '~합니다' 체", system)
        self.assertIn("안내원", system)                 # 가짜 모델이 답변 프롬프트를 알아보는 단어 유지
        self.assertNotEqual(ANSWER_PROMPT_VERSION, "v2-categories")   # 예전 말투로 저장된 답은 다시 쓰지 않음


class AnswerFormatTest(unittest.TestCase):
    def test_split_sections(self):
        text = ("### 핵심 답변\n공기로 전파됩니다 [근거 2].\n\n### 전염성·전파 경로\n- 기침 [근거 2]\n"
                "### 예방\n- MMR 백신 [근거 3]\n### 잠복기\n")
        head, sections = split_sections(text)
        self.assertEqual(head, "")
        self.assertEqual([t for t, _ in sections], ["핵심 답변", "전염성·전파 경로", "예방"])   # 빈 칸은 버림
        self.assertEqual(split_sections("그냥 문장")[1], [])


if __name__ == "__main__":
    unittest.main()
