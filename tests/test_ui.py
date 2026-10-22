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
        st.cache_data.clear()

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


CHAT, AGE, DETAIL = "views/chat.py", "views/age.py", "views/detail.py"


def md(at) -> str:
    return " ".join(m.value for m in at.markdown)


class UiTest(_UiBase):
    """감염병 질문하기 페이지 (F2009, F2011, F2012)."""

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

    def test_chat_with_evidence_summary_and_auto_disease(self):
        at, rt = self._app()
        at.run()
        at.switch_page(CHAT).run()
        self.assertIn("감염병 질문하기", md(at))
        at.chat_input[0].set_value("홍역은 어떻게 전파되나요?").run()
        texts = md(at)
        self.assertEqual(rt.by_id[at.session_state["disease"]].display_name, "홍역")   # 질문 속 이름으로 자동 선택
        self.assertIn("[근거 1]", texts)
        self.assertIn("**[근거 1]** 홍역 · 항목 메타데이터 · [원문](https://dportal.kdca.go.kr", texts)
        self.assertRegex(texts, r"\*\*\[근거 2\]\*\* .+ · 위키백과 · \[원문\]\(https://ko\.wikipedia\.org/")
        self.assertNotIn("**[근거 3]**", texts)        # 인용되지 않은 근거는 표시하지 않음
        self.assertTrue(any("전문가의 진단" in c.value for c in at.caption))   # 면책 안내
        self.assertIn("대화 요약", texts)
        self.assertIn("- **홍역**: 홍역은 제2급 감염병이며", texts)            # 오른쪽 요약 (LLM 형식이 안 맞으면 추출 요약)
        self.assertNotIn("[근거", at.session_state["summary"]["text"])
        # 다른 감염병 질문도 한 대화로 이어짐
        at.chat_input[0].set_value("수두는 몇 급인가요?").run()
        self.assertEqual(rt.by_id[at.session_state["disease"]].display_name, "수두")
        self.assertEqual(len(at.session_state["chat_log"]), 4)
        self.assertIn("홍역은 어떻게 전파되나요", md(at))
        next(b for b in at.button if b.key == "clear_chat").click().run()
        self.assertEqual(at.session_state["chat_log"], [])
        self.assertEqual(len(at.exception), 0)

    def test_condition_search(self):
        at, rt = self._app()
        at.run()
        at.switch_page(CHAT).run()
        at.radio(key="chat_view").set_value("이런 감염병 찾기").run()
        at.text_input[0].set_value("1급 감염병")
        next(b for b in at.button if b.label == "찾기").click().run()   # form submit
        cards = [b for b in at.button if b.key and b.key.startswith("go_")]
        self.assertTrue(cards)
        target = cards[0].key.removeprefix("go_")
        self.assertEqual(rt.by_id[target].grade, "1")
        cards[0].click().run()
        self.assertEqual(at.session_state["chat_view"], "질의응답")
        self.assertEqual(at.session_state["disease"], target)
        self.assertEqual(len(at.exception), 0)


class HomeTest(_UiBase):
    """홈: 제목·안내·검색창·지구본, 나라 팝업, 상세보기, 목차 바."""

    def _home(self, build: bool = True):
        at, rt = self._app()
        if build:
            write_sample_watch(self.tmp / "country_watch.json")
        return at, rt

    def test_layout_and_nav(self):
        at, _ = self._home()
        at.run()
        html = md(at)
        self.assertIn("kd-logo", html)                       # 목차 바 로고
        self.assertIn("법정감염병 안내 도우미", html)
        self.assertIn("전문가의 진단", html)                   # 안내 문구
        self.assertIn("kdHeroTitle", html)                    # 첫 접속 인트로
        self.assertEqual(len(at.get("plotly_chart")), 1)
        self.assertEqual(len(at.text_input), 1)               # 검색창
        at.run()
        self.assertNotIn("kdHeroTitle", md(at))               # 인트로는 세션당 한 번
        self.assertEqual(len(at.exception), 0)

    def test_search_goes_to_chat(self):
        at, rt = self._home()
        at.run()
        at.text_input[0].set_value("홍역은 어떻게 전파되나요?")
        next(b for b in at.button if b.label == "→").click().run()
        self.assertIn("대화 요약", md(at))                     # 질문하기 페이지로 이동해 바로 답함
        self.assertEqual(rt.by_id[at.session_state["disease"]].display_name, "홍역")
        self.assertEqual([m["role"] for m in at.session_state["chat_log"]], ["user", "assistant"])
        self.assertEqual(len(at.exception), 0)

    def test_country_popup_detail_and_close(self):
        at, rt = self._home()
        at.run()
        self.assertIn("2026-10-22 기준 · 지정 국가 4개국", md(at))
        at.selectbox(key="country_select").set_value("VNM").run()
        html = md(at)
        self.assertIn("베트남", html)
        self.assertIn("검역감염병 2종", html)
        self.assertIn("2024-09-02 지정", html)
        details = [b for b in at.button if b.key and b.key.startswith("detail_VNM")]
        self.assertEqual([b.label for b in details], ["상세보기", "상세보기"])
        details[0].click().run()                               # 가나다순 첫 항목 = 뎅기열
        self.assertEqual(rt.by_id[at.query_params["d"][0]].display_name, "뎅기열")
        self.assertIn("kd-detail-name", md(at))
        self.assertEqual(len(at.exception), 0)

    def test_close_and_other_countries(self):
        at, _ = self._home()
        at.run()
        at.selectbox(key="country_select").set_value("THA").run()      # 지정 해지된 나라
        self.assertIn("지정된 검역감염병이 없습니다", " ".join(x.value for x in at.success))
        next(b for b in at.button if b.key == "close_panel").click().run()
        self.assertIsNone(at.session_state["country"])
        at.selectbox(key="country_select").set_value("NAME:가상공화국").run()   # 지도 코드가 없는 나라도 목록에서
        self.assertIn("황열", md(at))
        at.selectbox(key="country_select").set_value("JPN").run()
        self.assertIn("지정된 검역감염병이 없습니다", " ".join(x.value for x in at.success))
        self.assertEqual(len(at.exception), 0)

    def test_before_fetch_and_home_country(self):
        at, _ = self._home(build=False)
        at.run()
        at.selectbox(key="country_select").set_value("JPN").run()
        self.assertIn("fetch_quarantine", " ".join(i.value for i in at.info))
        at.selectbox(key="country_select").set_value("KOR").run()
        self.assertIn("감염병 질문하기", md(at))


class DetailTest(_UiBase):
    def _detail(self, name: str):
        from tests.age_fixtures import build_sample, make_stats_dir
        at, rt = self._app()
        build_sample(self.tmp / "age_stats.json", make_stats_dir(self.tmp / "stats_in"))
        write_sample_watch(self.tmp / "country_watch.json")
        did = next(d.disease_id for d in rt.diseases if d.display_name == name)
        at.query_params["d"] = did
        at.run()
        at.switch_page(DETAIL).run()
        return at, rt, did

    def test_infographic_and_chatbot(self):
        st.cache_data.clear()
        at, rt, did = self._detail("수두")
        html = md(at)
        self.assertIn("kd-detail-name", html)
        self.assertIn("제2급", html)
        for text in ("300", "신고 건수", "10세 미만", "93%", "kd-split", "kd-age-strip", "0~4세 · 5~9세"):
            self.assertIn(text, html)
        self.assertEqual(len(at.get("plotly_chart")), 1)      # 인구 피라미드
        self.assertTrue(any("열 순서로 추정" in c.value for c in at.caption))
        at.text_input[0].set_value("어떻게 예방하나요?")          # 챗봇에게 물어보기 → 질문하기 페이지
        next(b for b in at.button if b.label == "→").click().run()
        self.assertEqual(at.session_state["disease"], did)
        self.assertEqual(at.session_state["chat_log"][0]["disease_id"], did)
        self.assertEqual(len(at.exception), 0)

    def test_wiki_summary_countries_and_trend(self):
        st.cache_data.clear()
        at, _, _ = self._detail("홍역")
        html = md(at)
        self.assertIn("홍역은 홍역 바이러스에 의한 급성 감염병이다", html)      # 수집된 위키 요약
        self.assertTrue(any("위키백과" in c.value for c in at.caption))
        self.assertIn("연령별·성별 통계 자료가 없습니다", " ".join(i.value for i in at.info))
        at, _, _ = self._detail("뎅기열")
        self.assertIn("베트남", md(at))                                          # 지정 국가
        at, _, _ = self._detail("인플루엔자")
        self.assertIn("언제 유행할까", md(at))                                     # 유행 시기 차트
        self.assertEqual(len(at.exception), 0)

    def test_unknown_id_shows_picker(self):
        at, _ = self._app()
        at.query_params["d"] = "nope"
        at.run()
        at.switch_page(DETAIL).run()
        self.assertEqual(len(at.selectbox), 1)


class AgeSectionTest(_UiBase):
    """연령별 알아보기 페이지: 세 화면, 정보 창, 상세보기 연결."""

    def _app_with_stats(self, build: bool = True):
        from tests.age_fixtures import build_sample, make_stats_dir
        at, rt = self._app()
        if build:
            build_sample(self.tmp / "age_stats.json", make_stats_dir(self.tmp / "stats_in"))
        at.run()
        at.switch_page(AGE).run()
        return at, rt

    def test_before_build(self):
        at, _ = self._app_with_stats(build=False)
        self.assertIn("scripts.build_age_stats", " ".join(c.value for c in at.code))
        self.assertEqual(len(at.exception), 0)

    def test_age_mode_panel_and_detail(self):
        at, rt = self._app_with_stats()
        self.assertEqual(len(at.get("plotly_chart")), 1)
        self.assertIn("수두", md(at))
        self.assertIn("가장 많은 연령대", md(at))
        self.assertTrue(any("추정" in c.value for c in at.caption))
        at.select_slider(key="age_group").set_value("65세 이상").run()
        self.assertIn("카바페넴내성장내세균목(CRE) 감염증", md(at))
        next(b for b in at.button if b.key == "age_ask").click().run()
        self.assertEqual(rt.by_id[at.query_params["d"][0]].display_name, "카바페넴내성장내세균목(CRE) 감염증")
        self.assertEqual(len(at.exception), 0)

    def test_to_distribution_and_sari(self):
        at, _ = self._app_with_stats()
        next(b for b in at.button if b.key == "age_to_dist").click().run()
        self.assertEqual(at.session_state["age_mode"], "감염병별 연령 분포")
        self.assertEqual(at.session_state["age_disease"], "notifiable|수두")
        self.assertIn("10세 미만 93%", md(at))
        at.selectbox(key="age_disease").set_value("sti|임질").run()
        self.assertIn("20~24세", md(at))
        at.selectbox(key="age_disease").set_value("sari").run()
        self.assertIn("2025-2026절기 입원환자", md(at))
        self.assertEqual(len(at.exception), 0)

    def test_trend_mode(self):
        at, _ = self._app_with_stats()
        at.radio(key="age_mode").set_value("유행 시기").run()
        self.assertIn("인플루엔자", md(at))
        self.assertIn("2024-2025절기 · 1주 · 99.8", md(at))
        at.selectbox(key="trend_id").set_value("yw_수족구병").run()
        self.assertIn("보통 **7월 전후**", md(at))
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
        from service.countries import country_names, load_watch
        from ui.map_view import HOME_VIEW, SELECTED, build_world_map, view_center
        tmp = Path(tempfile.mkdtemp())
        write_sample_watch(tmp / "w.json")
        names, watch = country_names(paths.CONFIG / "country_names_ko.json"), load_watch(tmp / "w.json")
        fig = build_world_map(watch, "VNM", names)
        shaded = next(t for t in fig.data if t.name == "검역관리지역")
        self.assertEqual(sorted(shaded.locations), ["NGA", "SGP", "VNM"])        # 지정 중 + 지도 코드 있는 나라
        self.assertEqual(dict(zip(shaded.locations, shaded.z))["VNM"], 2)          # 감염병 수로 색칠
        plain = next(t for t in fig.data if t.name == "지정 없음")                 # 지정 없는 나라도 누를 수 있음
        self.assertIn("JPN", plain.locations)
        self.assertNotIn("VNM", plain.locations)
        self.assertNotIn("주요 여행지", [t.name for t in fig.data])                # 여행지 표시 없음
        sel = next(t for t in fig.data if t.name == "선택")
        self.assertEqual((list(sel.locations), sel.colorscale[0][1]), (["VNM"], SELECTED))
        # 지구본: orthographic, 끌어서 회전, 고른 나라 쪽으로 돌아감
        self.assertEqual(fig.layout.geo.projection.type, "orthographic")
        self.assertEqual((fig.layout.dragmode, fig.layout.clickmode, fig.layout.uirevision), ("pan", "event+select", "VNM"))
        self.assertAlmostEqual(fig.layout.geo.projection.rotation.lon, 106.29, places=1)
        self.assertEqual(view_center(None), HOME_VIEW)
        self.assertEqual(view_center("RUS")[1], 60.0)                              # 너무 북쪽은 60도로 제한
        empty = build_world_map(load_watch(tmp / "none.json"), None, names)        # 수집 전에도 그려짐
        self.assertEqual([t.name for t in empty.data], ["지정 없음"])

    def test_country_names(self):
        from service.countries import country_names
        names = country_names(paths.CONFIG / "country_names_ko.json")
        self.assertEqual((names["AFG"], names["RUS"], names["KOR"]), ("아프가니스탄", "러시아", "대한민국"))

if __name__ == "__main__":
    unittest.main()
