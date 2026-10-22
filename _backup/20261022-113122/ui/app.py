"""법정감염병 안내 도우미 - Streamlit 화면.

레이아웃 (도안 기준, 한 페이지 + 상단 내비게이션):
  [로고] [감염병 질문하기] [나라별 알아보기] [연령별 알아보기]   ← 누르면 해당 섹션으로 이동
  제목 / 면책 안내
  ① 감염병 질문하기: 질의응답 | 이런 감염병 찾기  (F2009, F2011, F2012)
  ② 나라별 주의 감염병: 클릭 가능한 세계 지도 + 나라 상세 패널 (단계 3 데이터 연결 지점)
  ③ 연령별 알아보기: 단계 2 통계 자리

실행: streamlit run ui/app.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from data_prep import settings as paths  # noqa: E402
from rag.graph import IndexMissingError, run  # noqa: E402
from rag.llm import missing_keys  # noqa: E402
from service.countries import country_from_event, load_countries, load_watch  # noqa: E402
from service.explore import find_diseases  # noqa: E402
from service.runtime import DISCLAIMER, build_runtime, setup_logging  # noqa: E402
from ui.map_view import HOME, SELECTED, TARGET, build_world_map  # noqa: E402
from ui.styles import CSS, NAV_HTML  # noqa: E402

log = logging.getLogger("kdca_rag")
st.set_page_config(page_title="법정감염병 안내 도우미", page_icon="🩺", layout="wide",
                   initial_sidebar_state="collapsed")
setup_logging()
st.markdown(CSS + NAV_HTML, unsafe_allow_html=True)

VIEW_CHAT, VIEW_FIND = "질의응답", "이런 감염병 찾기"
COUNTRY_WATCH = paths.PROCESSED / "country_watch.json"
# 지도 경계 파일을 CDN 대신 앱과 함께 둔 파일로 받는다 (ui/static/topojson, 정적 파일 제공 필요)
MAP_CONFIG = {"displayModeBar": False, "scrollZoom": False, "topojsonURL": "app/static/topojson/"}


def anchor(name: str) -> None:
    st.markdown(f'<div id="{name}" class="kd-anchor"></div>', unsafe_allow_html=True)


# ---- 사전 점검: 키 누락, 색인 없음 (F2012) -------------------------------------------
missing = missing_keys()
if missing:
    st.error("필요한 환경변수가 설정되지 않았습니다: " + ", ".join(missing))
    st.markdown("프로젝트 폴더의 `.env.example`을 `.env`로 복사해 값을 채운 뒤 다시 실행하세요.")
    st.stop()


@st.cache_resource(show_spinner="모델과 색인을 불러오는 중")
def get_runtime():
    return build_runtime()


try:
    rt = get_runtime()
except Exception:  # noqa: BLE001
    log.exception("런타임 초기화 실패")
    st.error("서비스를 초기화하지 못했습니다. 설정과 네트워크를 확인한 뒤 다시 실행하세요.")
    st.stop()

meta_count = rt.store.meta_count()
if meta_count == 0:
    st.warning("색인이 없습니다. 터미널에서 아래 명령을 먼저 실행하세요.")
    st.code("python -m scripts.build_index", language="bash")
    st.stop()

countries = load_countries(paths.CONFIG / "countries.json")
by_iso = {c.iso3: c for c in countries}
watch = load_watch(COUNTRY_WATCH)

ss = st.session_state
ss.setdefault("chats", {})
ss.setdefault("disease", rt.diseases[0].disease_id)
ss.setdefault("view", VIEW_CHAT)
ss.setdefault("found", None)
ss.setdefault("country", None)
ss.setdefault("_map_pick", None)


def select_disease(did: str) -> None:
    ss["disease"] = did
    ss["view"] = VIEW_CHAT
    st.toast(f"'{rt.by_id[did].display_name}'을(를) 골랐어요. 위 '감염병 질문하기'에서 질문하세요.")


def on_country_select() -> None:
    ss["country"] = ss["country_select"]


# ---- 제목 ---------------------------------------------------------------------------
anchor("top")
st.title("법정감염병 안내 도우미")
st.caption(DISCLAIMER)


# ---- ① 감염병 질문하기 ----------------------------------------------------------------
def render_evidence(msg: dict) -> None:
    cited = msg.get("cited", [])
    for w in msg.get("warnings", []):
        st.warning(w)
    if not cited:
        st.info("이 답변에 인용된 근거가 없습니다. 제공된 문서로 확인되지 않는 내용일 수 있습니다.")
    else:
        with st.expander(f"답변에 사용한 근거 {len(cited)}건"):
            for e in cited:
                sec = f" / {e['section']}" if e.get("section") else ""
                line = f"**[근거 {e['n']}]** {e['title']}{sec} · {e['source_label']} · [원문]({e['url']})"
                if e.get("wiki_url"):
                    line += f" · [요약 출처: 위키백과]({e['wiki_url']})"
                st.markdown(line)


def chat_view() -> None:
    top = st.columns([2.2, 3, 1], vertical_alignment="bottom")
    ids = [d.disease_id for d in rt.diseases]
    top[0].selectbox("질문할 감염병", ids, key="disease", format_func=lambda i: rt.by_id[i].label())
    d = rt.by_id[ss["disease"]]
    scope = " · ".join(f"{k} {'O' if v else 'X'}" for k, v in d.scope.items())
    top[1].markdown(f"**제{d.grade}급** · {d.rule['surveillance']} · 신고 {d.rule['deadline']}  \n"
                    f"<span class='kd-muted'>신고 범위 {scope} · KCD {d.kcd or '-'}</span>",
                    unsafe_allow_html=True)
    if top[2].button("대화 지우기", width="stretch"):
        ss["chats"].pop(d.disease_id, None)
        st.rerun()
    docs = rt.store.list_docs(d.disease_id)
    st.caption(f"감염병 메타데이터 색인: {meta_count}건 · 이 감염병으로 수집된 위키 문서 {len(docs)}건"
               + (f": {', '.join(x['title'] for x in docs[:8])}" if docs else ""))

    history = ss["chats"].setdefault(d.disease_id, [])  # 감염병별로 대화 기록을 따로 유지
    box = st.container(height=420, border=False)
    with box:
        if not history:
            st.markdown(f"<div class='kd-muted'>{d.display_name}에 대해 무엇이든 물어보세요. "
                        "예: “전파 경로와 예방법은?”, “몇 급이고 언제까지 신고하나요?”</div>", unsafe_allow_html=True)
        for msg in history:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                if msg["role"] == "assistant":
                    render_evidence(msg)

    question = st.chat_input(f"{d.display_name}에 대해 질문하세요")
    if not question:
        return
    history.append({"role": "user", "content": question})
    with box:
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            try:
                with st.status("근거를 찾는 중", expanded=False) as status:
                    state = run(rt.graph, question, d.disease_id, on_event=lambda e: status.write(e["message"]))
                    new_docs = [t for r in state.get("collect_log", []) for t in r["indexed"]]
                    cache_hits = sum(1 for r in state.get("collect_log", []) if r["from_cache"])
                    if new_docs:
                        status.write("수집한 문서: " + ", ".join(new_docs))
                    status.update(label=f"완료 · 새로 수집한 문서 {len(new_docs)}건 · 캐시 사용 {cache_hits}건",
                                  state="complete")
                msg = {"role": "assistant", "content": state["answer"], "cited": state.get("cited", []),
                       "warnings": state.get("warnings", [])}
                st.markdown(msg["content"])
                render_evidence(msg)
                history.append(msg)
            except IndexMissingError:
                st.warning("이 감염병의 색인이 없습니다. `python -m scripts.build_index`를 실행하세요.")
            except Exception:  # noqa: BLE001 - 추적 정보는 로그에만 (NF2004)
                log.exception("답변 생성 실패")
                st.error("답변을 만드는 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요.")


def find_view() -> None:
    with st.form("find", border=False):
        c1, c2 = st.columns([5, 1], vertical_alignment="bottom")
        cond = c1.text_input("찾고 싶은 감염병의 조건",
                             placeholder="예: 모기가 옮기는 3급 감염병, 병원체보유자도 신고하는 1급 감염병")
        submitted = c2.form_submit_button("찾기", width="stretch")
    if submitted and cond.strip():
        try:
            with st.spinner("후보를 고르는 중"):
                ss["found"] = (cond, find_diseases(cond, rt.store, rt.services.llm, rt.settings["condition_candidates"]))
        except Exception:  # noqa: BLE001
            log.exception("조건 탐색 실패")
            st.error("탐색 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요.")
    if not ss["found"]:
        st.markdown("<div class='kd-muted'>조건을 입력하면 메타데이터 색인에서 후보를 찾은 뒤 조건에 맞는 감염병만 골라 보여 줍니다.</div>",
                    unsafe_allow_html=True)
        return
    cond, results = ss["found"]
    st.caption(f"조건: {cond} · {len(results)}건")
    if not results:
        st.info("조건에 맞는 감염병을 찾지 못했습니다. 조건을 바꿔 보세요.")
    cols = st.columns(3)
    for i, r in enumerate(results):
        with cols[i % 3].container(border=True):
            st.markdown(f"**{r['name']}**  \n제{r['grade']}급" + (f" · {r['group']}" if r["group"] else ""))
            st.caption(r["reason"])
            st.button("이 감염병에 질문하기", key=f"go_{r['disease_id']}", on_click=select_disease,
                      args=(r["disease_id"],))


anchor("chat")
st.markdown("<div class='kd-section-title'>감염병 질문하기</div>", unsafe_allow_html=True)
st.radio("화면", [VIEW_CHAT, VIEW_FIND], key="view", horizontal=True, label_visibility="collapsed")
with st.container(key="chat_card"):
    if ss["view"] == VIEW_CHAT:
        chat_view()
    else:
        find_view()


# ---- ② 나라별 주의 감염병 -------------------------------------------------------------
def country_panel(iso3: str | None) -> None:
    if not iso3:
        st.markdown("<div class='kd-country-name'>나라를 골라 주세요</div>", unsafe_allow_html=True)
        st.markdown("<div class='kd-muted'>지도에서 색칠된 나라나 점을 누르면 그 나라에서 주의가 필요한 "
                    "법정감염병을 보여 줍니다.</div>", unsafe_allow_html=True)
        st.markdown("".join(f"<span class='kd-chip'>{c.name_ko}</span>" for c in countries if not c.home),
                    unsafe_allow_html=True)
        return
    c = by_iso[iso3]
    st.markdown(f"<div class='kd-country-name'>{c.name_ko}</div>", unsafe_allow_html=True)
    if c.home:
        st.markdown("국내 법정감염병 133종의 급수·신고 기준과 설명은 위 **감염병 질문하기**에서 확인할 수 있어요.")
        return
    w = watch.get(iso3)
    if not w or not w.diseases:
        st.info("이 나라의 검역관리지역 정보가 아직 연결되지 않았습니다. 단계 3에서 질병관리청 "
                "검역관리지역정보 API로 연결하면 여기에 표시됩니다.")
        return
    st.markdown("**주의가 필요해요**")
    for i, item in enumerate(w.diseases):
        row = st.columns([3, 2], vertical_alignment="center")
        row[0].markdown(f"- {item['name']}" + (f"  \n<span class='kd-muted'>{item['note']}</span>"
                                                 if item.get("note") else ""), unsafe_allow_html=True)
        did = item.get("disease_id")
        if did in rt.by_id:
            row[1].button("질문하기", key=f"ask_{iso3}_{i}", on_click=select_disease, args=(did,), width="stretch")
    src = f"[{w.source}]({w.source_url})" if w.source_url else w.source
    st.caption(f"기준일 {w.as_of} · 출처 {src}")


anchor("country")
st.markdown("<div class='kd-section-title'>나라별 주의 감염병 알아보기</div>"
            "<div class='kd-sub'>주요 여행지 10개국. 출국 전 확인용 일반 정보이며, 최신 상황은 질병관리청 안내를 확인하세요.</div>",
            unsafe_allow_html=True)

valid = set(by_iso)
picked = country_from_event(ss.get("world_map"), valid)
if picked and picked != ss["_map_pick"]:  # 지도를 새로 눌렀을 때만 반영 (목록 선택을 덮어쓰지 않게)
    ss["country"] = ss["_map_pick"] = picked

left, right = st.columns([2.6, 1.15], gap="medium")
with left:
    with st.container(key="map_card"):
        st.plotly_chart(build_world_map(countries, ss["country"]), key="world_map", on_select="rerun",
                        selection_mode="points", config=MAP_CONFIG)
    st.markdown(f"<div class='kd-legend'><span><i class='kd-dot' style='background:{TARGET}'></i>주요 여행지</span>"
                f"<span><i class='kd-dot' style='background:{HOME}'></i>대한민국</span>"
                f"<span><i class='kd-dot' style='background:{SELECTED}'></i>선택한 나라</span></div>",
                unsafe_allow_html=True)
    ss["country_select"] = ss["country"]
    st.selectbox("나라 직접 고르기", list(by_iso), key="country_select", index=None,
                 placeholder="지도를 누르거나 여기서 고르세요", format_func=lambda i: by_iso[i].name_ko,
                 on_change=on_country_select)
with right:
    with st.container(key="country_panel"):
        country_panel(ss["country"])


# ---- ③ 연령별 알아보기 ----------------------------------------------------------------
anchor("age")
st.markdown("<div class='kd-section-title'>연령별 알아보기</div>", unsafe_allow_html=True)
with st.container(key="age_card"):
    st.markdown("**감염병별 취약한 연령대** (단계 2에서 연결)")
    st.markdown("<div class='kd-muted'>성별 연령별 감염병 발생현황(1~3급)과 법정감염병 표본감시 통계(4급)를 "
                "공통 연령대(0-6, 7-12, 13-18, 19-49, 50-64, 65+)로 맞춰 그림으로 보여 줄 자리입니다.</div>",
                unsafe_allow_html=True)

st.divider()
st.caption("⚠️ " + DISCLAIMER)
