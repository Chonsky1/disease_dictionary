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
                        mock.patch.object(runtime_mod, "build_runtime", lambda *a, **k: rt),
                        mock.patch("data_prep.settings.PROCESSED", self.tmp)]   # 실제 수집 결과와 섞이지 않게
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

    def _app_with_watch(self, build: bool):
        at, rt = self._app()
        p = mock.patch("data_prep.settings.PROCESSED", self.tmp)
        p.start()
        self.addCleanup(p.stop)
        if build:
            write_sample_watch(self.tmp / "country_watch.json")
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

    def test_country_panel_with_quarantine_data(self):
        at, rt = self._app_with_watch(True)
        at.run()
        html = " ".join(m.value for m in at.markdown)
        self.assertIn("2026-10-22 기준 · 지정 국가 4개국", html)
        at.selectbox(key="country_select").set_value("VNM").run()
        html = " ".join(m.value for m in at.markdown)
        self.assertIn("베트남", html)
        self.assertIn("검역감염병 2종", html)
        self.assertIn("콜레라", html)
        self.assertIn("2024-09-02 지정", html)
        ask = [b for b in at.button if b.key and b.key.startswith("ask_VNM")]
        self.assertEqual(len(ask), 2)
        ask[0].click().run()
        self.assertEqual(rt.by_id[at.session_state["disease"]].display_name, "뎅기열")  # 가나다순 첫 항목
        # 지정이 해지된 나라(태국)는 '지정 없음'
        at.selectbox(key="country_select").set_value("THA").run()
        self.assertIn("지정된 검역감염병이 없습니다", " ".join(x.value for x in at.success))
        # 지도 코드를 못 찾은 나라도 목록에서 고를 수 있음
        at.selectbox(key="country_select").set_value("NAME:가상공화국").run()
        self.assertIn("황열", " ".join(m.value for m in at.markdown))
        self.assertEqual(len(at.exception), 0)

    def test_country_panel_before_fetch_and_home(self):
        at, _ = self._app_with_watch(False)
        at.run()
        at.selectbox(key="country_select").set_value("JPN").run()
        self.assertIn("fetch_quarantine", " ".join(i.value for i in at.info))
        at.selectbox(key="country_select").set_value("KOR").run()
        self.assertIn("감염병 질문하기", " ".join(m.value for m in at.markdown))


class AgeSectionTest(_UiBase):
    """연령별 알아보기: 세 화면, 정보 창, 질문하기 연결."""

    def _app_with_stats(self, build: bool = True):
        from tests.age_fixtures import build_sample, make_stats_dir
        at, rt = self._app()
        if build:
            build_sample(self.tmp / "age_stats.json", make_stats_dir(self.tmp / "stats_in"))
        return at, rt

    def _panel(self, at) -> str:
        return " ".join(m.value for m in at.markdown)

    def test_before_build(self):
        at, _ = self._app_with_stats(build=False)
        at.run()
        self.assertIn("scripts.build_age_stats", " ".join(c.value for c in at.code))
        self.assertEqual(len(at.exception), 0)

    def test_age_mode_panel_and_ask(self):
        at, rt = self._app_with_stats()
        at.run()
        self.assertEqual(len(at.get("plotly_chart")), 2)              # 지도 + 연령대 막대
        self.assertIn("수두", self._panel(at))                          # 0~4세 기본 선택 → 정보 창
        self.assertIn("가장 많은 연령대", self._panel(at))
        self.assertTrue(any("추정" in c.value for c in at.caption))   # 열 이름 추정 표시
        at.select_slider(key="age_group").set_value("65세 이상").run()
        self.assertIn("카바페넴내성장내세균목(CRE) 감염증", self._panel(at))
        ask = next(b for b in at.button if b.key == "age_ask")
        ask.click().run()
        self.assertEqual(rt.by_id[at.session_state["disease"]].display_name, "카바페넴내성장내세균목(CRE) 감염증")
        self.assertEqual(len(at.exception), 0)

    def test_to_distribution_and_sari(self):
        at, _ = self._app_with_stats()
        at.run()
        next(b for b in at.button if b.key == "age_to_dist").click().run()
        self.assertEqual(at.session_state["age_mode"], "감염병별 연령 분포")
        self.assertEqual(at.session_state["age_disease"], "notifiable|수두")
        self.assertIn("10세 미만 93%", self._panel(at))
        at.selectbox(key="age_disease").set_value("sti|임질").run()
        self.assertIn("20~24세", self._panel(at))
        at.selectbox(key="age_disease").set_value("sari").run()
        self.assertIn("2025-2026절기 입원환자", self._panel(at))
        self.assertEqual(len(at.exception), 0)

    def test_trend_mode(self):
        at, _ = self._app_with_stats()
        at.run()
        at.radio(key="age_mode").set_value("유행 시기").run()
        panel = self._panel(at)
        self.assertIn("인플루엔자", panel)
        self.assertIn("2024-2025절기 · 1주 · 99.8", panel)
        at.selectbox(key="trend_id").set_value("yw_수족구병").run()
        self.assertIn("보통 **7월 전후**", self._panel(at))
        at.selectbox(key="trend_id").set_value("pw_급성호흡기감염증").run()
        at.selectbox(key="trend_item").set_value("호흡기세포융합바이러스").run()
        self.assertTrue(any(b.key == "trend_ask" for b in at.button))
        self.assertEqual(len(at.exception), 0)


def write_sample_watch(path: Path) -> None:
    """합성 API 응답 → build_watch → country_watch.json (실제 수집 흐름과 같은 함수 사용)."""
    import json
    from datetime import date
    from data_prep.diseases import load_diseases
    from data_prep.quarantine import build_watch, from_api_items, load_country_codes, save_watch
    data = json.loads((Path(__file__).parent / "fixtures" / "quarantine_api_sample.json").read_text(encoding="utf-8"))
    watch, _ = build_watch(from_api_items(data["response"]["body"]["items"]["item"]),
                           load_diseases(paths.DISEASES_CSV), load_country_codes(paths.CONFIG / "country_names_ko.json"),
                           paths.CONFIG / "name_aliases.json", date(2026, 10, 22), "test")
    save_watch(path, watch)


class MapEventTest(unittest.TestCase):
    def test_country_from_event(self):
        from service.countries import country_from_event
        valid = {"THA", "SGP"}
        self.assertEqual(country_from_event({"selection": {"points": [{"location": "THA"}]}}, valid), "THA")
        self.assertEqual(country_from_event({"selection": {"points": [{"customdata": ["SGP", "싱가포르"]}]}}, valid), "SGP")
        self.assertIsNone(country_from_event({"selection": {"points": [{"location": "FRA"}]}}, valid))
        self.assertIsNone(country_from_event(None, valid))

    def test_map_figure(self):
        import tempfile
        from service.countries import load_countries, load_watch
        from ui.map_view import SELECTED, build_world_map
        tmp = Path(tempfile.mkdtemp())
        write_sample_watch(tmp / "w.json")
        cs, watch = load_countries(paths.CONFIG / "countries.json"), load_watch(tmp / "w.json")
        fig = build_world_map(cs, watch, "VNM")
        shaded = next(t for t in fig.data if t.name == "검역관리지역")
        self.assertEqual(sorted(shaded.locations), ["NGA", "SGP", "VNM"])        # 지정 중 + 지도 코드 있는 나라
        self.assertEqual(dict(zip(shaded.locations, shaded.z))["VNM"], 2)          # 감염병 수로 색칠
        sel = next(t for t in fig.data if t.name == "선택")
        self.assertEqual((list(sel.locations), sel.colorscale[0][1]), (["VNM"], SELECTED))
        self.assertEqual(fig.layout.clickmode, "event+select")
        empty = build_world_map(cs, load_watch(tmp / "none.json"), None)          # 수집 전에도 그려짐
        self.assertEqual([t.name for t in empty.data], ["주요 여행지", "주요 여행지 점"])


if __name__ == "__main__":
    unittest.main()
