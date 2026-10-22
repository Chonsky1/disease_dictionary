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


class _UiBase(unittest.TestCase):
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


class UiTest(_UiBase):
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
        self.assertIn("감염병 메타데이터 색인: 133건", " ".join(c.value for c in at.caption))
        measles = next(d for d in rt.diseases if d.display_name == "홍역")
        at.selectbox(key="disease").set_value(measles.disease_id).run()
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
        at.selectbox(key="disease").set_value(other.disease_id).run()
        self.assertNotIn("홍역은 어떻게 전파되나요", " ".join(m.value for m in at.markdown))  # 대화가 섞이지 않음
        at.selectbox(key="disease").set_value(measles.disease_id).run()
        self.assertIn("홍역은 어떻게 전파되나요", " ".join(m.value for m in at.markdown))

    def test_condition_search_to_chat(self):
        at, rt = self._app()
        at.run()
        at.radio[0].set_value("이런 감염병 찾기").run()
        at.text_input[0].set_value("1급 감염병")
        next(b for b in at.button if b.label == "찾기").click().run()   # form submit
        cards = [b for b in at.button if b.key and b.key.startswith("go_")]
        self.assertTrue(cards)
        target = cards[0].key.removeprefix("go_")
        self.assertEqual(rt.by_id[target].grade, "1")
        cards[0].click().run()
        self.assertEqual(at.session_state["view"], "질의응답")
        self.assertEqual(at.session_state["disease"], target)
        self.assertEqual(len(at.exception), 0)


class LayoutTest(_UiBase):
    """도안 레이아웃: 상단 내비게이션, 세 섹션, 지도·나라 패널."""

    def _app_with_watch(self, watch_file):
        at, rt = self._app()
        p = mock.patch("data_prep.settings.PROCESSED", self.tmp)
        p.start()
        self.addCleanup(p.stop)
        if watch_file:
            shutil.copy(watch_file, self.tmp / "country_watch.json")
        return at, rt

    def test_nav_and_sections(self):
        at, _ = self._app()
        at.run()
        html = " ".join(m.value for m in at.markdown)
        for anchor in ('href="#chat"', 'href="#country"', 'href="#age"', 'id="chat"', 'id="country"', 'id="age"'):
            self.assertIn(anchor, html)
        for title in ("감염병 질문하기", "나라별 주의 감염병 알아보기", "연령별 알아보기"):
            self.assertIn(title, html)
        self.assertEqual(len(at.get("plotly_chart")), 1)
        self.assertNotIn("`None`", html)   # if/else 식이 화면에 None을 찍던 버그 회귀 방지
        self.assertEqual(len(at.exception), 0)

    def test_country_panel_with_data(self):
        at, rt = self._app_with_watch(Path(__file__).parent / "fixtures" / "country_watch_sample.json")
        at.run()
        self.assertIn("나라를 골라 주세요", " ".join(m.value for m in at.markdown))
        at.selectbox(key="country_select").set_value("THA").run()
        html = " ".join(m.value for m in at.markdown)
        self.assertIn("태국", html)
        self.assertIn("테스트감염병A", html)
        self.assertIn("기준일 2026-10-22", " ".join(c.value for c in at.caption))
        ask = [b for b in at.button if b.key and b.key.startswith("ask_THA")]
        self.assertEqual(len(ask), 1)             # disease_id가 있는 항목만 질문 버튼
        ask[0].click().run()
        self.assertEqual(at.session_state["disease"], "inf_0059")
        self.assertEqual(len(at.exception), 0)

    def test_country_panel_without_data_and_home(self):
        at, _ = self._app_with_watch(None)
        at.run()
        at.selectbox(key="country_select").set_value("JPN").run()
        self.assertIn("아직 연결되지 않았습니다", " ".join(i.value for i in at.info))
        at.selectbox(key="country_select").set_value("KOR").run()
        self.assertIn("감염병 질문하기", " ".join(m.value for m in at.markdown))


class MapEventTest(unittest.TestCase):
    def test_country_from_event(self):
        from service.countries import country_from_event
        valid = {"THA", "SGP"}
        self.assertEqual(country_from_event({"selection": {"points": [{"location": "THA"}]}}, valid), "THA")
        self.assertEqual(country_from_event({"selection": {"points": [{"customdata": ["SGP", "싱가포르"]}]}}, valid), "SGP")
        self.assertIsNone(country_from_event({"selection": {"points": [{"location": "FRA"}]}}, valid))
        self.assertIsNone(country_from_event(None, valid))

    def test_map_figure(self):
        from service.countries import load_countries
        from ui.map_view import SELECTED, build_world_map
        cs = load_countries(paths.CONFIG / "countries.json")
        fig = build_world_map(cs, "THA")
        self.assertEqual(len(fig.data), len(cs) + 1)           # 나라별 영역 + 마커 1개
        tha = next(t for t in fig.data if t.type == "choropleth" and t.locations[0] == "THA")
        self.assertEqual(tha.colorscale[0][1], SELECTED)
        self.assertEqual(fig.layout.clickmode, "event+select")


if __name__ == "__main__":
    unittest.main()
