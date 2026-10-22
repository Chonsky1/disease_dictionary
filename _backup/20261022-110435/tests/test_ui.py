"""Streamlit 화면 동작 검증 (4.4, F2009·F2011·F2012). streamlit.testing으로 브라우저 없이 실행."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import streamlit as st
from langchain_core.embeddings import DeterministicFakeEmbedding
from streamlit.testing.v1 import AppTest

import service.runtime as runtime_mod
from data_prep import settings as paths
from data_prep.collector import WikiCollector
from data_prep.diseases import build_index_text, build_metadata, load_diseases
from rag.graph import RagServices, build_graph
from service.runtime import Runtime
from tests.fakes import RuleChatModel, fake_wiki_client
from vectorstore.store import DiseaseStore

APP = str(Path(__file__).resolve().parents[1] / "ui" / "app.py")
ENV = {"OPENAI_API_KEY": "test-key", "WIKI_USER_AGENT": "kdca-rag-test (contact: test@example.com)"}


def fake_runtime(tmp: Path, indexed: bool = True) -> Runtime:
    settings = {**paths.load_rag_settings(), "chroma_dir": str(tmp / "chroma")}
    store = DiseaseStore(settings, DeterministicFakeEmbedding(size=32))
    diseases = load_diseases(paths.DISEASES_CSV)
    if indexed:
        store.index_meta([build_index_text(d) for d in diseases], [build_metadata(d) for d in diseases])
    by_id = {d.disease_id: d for d in diseases}
    measles = next(d for d in diseases if d.display_name == "홍역")
    svc = RagServices(RuleChatModel(), store, WikiCollector(fake_wiki_client()[0], tmp / "pages", tmp / "c.json"),
                      by_id, {measles.disease_id: {"title": "홍역", "pageid": 100}}, settings)
    return Runtime(settings, diseases, by_id, store, svc, build_graph(svc))


class UiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        st.cache_resource.clear()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _app(self, indexed=True, env=ENV):
        rt = fake_runtime(self.tmp, indexed)
        self.patches = [mock.patch.dict(os.environ, env, clear=False),
                        mock.patch.object(runtime_mod, "build_runtime", lambda *a, **k: rt)]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        return AppTest.from_file(APP, default_timeout=60), rt

    def test_missing_key_notice(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "", "WIKI_USER_AGENT": ""}):
            at = AppTest.from_file(APP, default_timeout=60).run()
        self.assertIn("OPENAI_API_KEY", at.error[0].value)
        self.assertNotIn("test-key", " ".join(e.value for e in at.error))

    def test_empty_index_notice(self):
        at, _ = self._app(indexed=False)
        at.run()
        self.assertIn("색인이 없습니다", at.warning[0].value)
        self.assertIn("scripts.build_index", at.code[0].value)

    def test_chat_with_evidence_and_separate_history(self):
        at, rt = self._app()
        at.run()
        self.assertIn("133건", " ".join(c.value for c in at.sidebar.caption))
        measles = next(d for d in rt.diseases if d.display_name == "홍역")
        at.sidebar.selectbox[0].set_value(measles.disease_id).run()
        at.chat_input[0].set_value("홍역은 어떻게 전파되나요?").run()
        texts = " ".join(m.value for m in at.markdown)
        self.assertIn("[근거 1]", texts)
        self.assertIn("**[근거 1]** 홍역 · 항목 메타데이터 · [원문](https://dportal.kdca.go.kr", texts)
        self.assertRegex(texts, r"\*\*\[근거 2\]\*\* .+ · 위키백과 · \[원문\]\(https://ko\.wikipedia\.org/")
        self.assertNotIn("**[근거 3]**", texts)  # 인용되지 않은 근거는 표시하지 않음
        self.assertIn("항목 메타데이터", texts)     # 출처 유형 구분
        self.assertIn("위키백과", texts)
        self.assertTrue(any("전문가의 진단" in c.value for c in at.caption))  # 면책 안내
        self.assertEqual(len(at.exception), 0)

        other = next(d for d in rt.diseases if d.display_name == "수두")
        at.sidebar.selectbox[0].set_value(other.disease_id).run()
        self.assertNotIn("어떻게 전파되나요", " ".join(m.value for m in at.markdown))  # 대화가 섞이지 않음
        at.sidebar.selectbox[0].set_value(measles.disease_id).run()
        self.assertIn("어떻게 전파되나요", " ".join(m.value for m in at.markdown))

    def test_condition_search_to_chat(self):
        at, rt = self._app()
        at.run()
        at.radio[0].set_value("이런 감염병 찾기").run()
        at.text_input[0].set_value("1급 감염병")
        at.button[0].click().run()   # form submit
        cards = [b for b in at.button if b.key and b.key.startswith("go_")]
        self.assertTrue(cards)
        target = cards[0].key.removeprefix("go_")
        self.assertEqual(rt.by_id[target].grade, "1")
        cards[0].click().run()
        self.assertEqual(at.session_state["view"], "질의응답")
        self.assertEqual(at.session_state["disease"], target)
        self.assertEqual(len(at.exception), 0)


if __name__ == "__main__":
    unittest.main()
