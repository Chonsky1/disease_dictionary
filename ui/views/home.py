"""홈: 나라별 주의 감염병 (질병관리청 검역관리지역정보).

처음 들어오면(세션당 한 번) 인트로: 제목이 왼쪽에 크게, 지구본이 오른쪽 절반에 있다가
제목은 왼쪽 위로 작아지며 올라가고, 지구본은 가운데로 오고, 그 사이에 안내 문구와 검색창이 나타난다.
나라를 누르면 지구본이 왼쪽으로 비키고 오른쪽에 그 나라의 검역감염병 팝업이 천천히 나타난다.
팝업의 '여행 전 안내'는 그 나라 검역감염병의 전파 경로·예방을 근거와 함께 정리한다 (F2010, service/travel.py).
주소에 ?country=VNM 처럼 ISO-3 코드를 붙이면 그 나라 팝업이 열린 채로 시작한다 (공유·화면 캡처용).
"""
from __future__ import annotations

import streamlit as st

from data_prep.sources import license_label
from service.countries import country_from_event
from service.runtime import DISCLAIMER
from service.travel import travel_brief
from ui.chat_core import is_quota_error, render_answer, render_evidence
from ui.common import (HOME_ISO, MAP_CONFIG, NO_CHAT, all_country_names, detail_button, log, runtime, search_box,
                       watch)
from ui.map_view import HIGH, LOW, NEUTRAL, SELECTED, build_world_map
from ui.styles import INTRO_CSS

rt = runtime()
w = watch()
names = all_country_names(w)
choice_keys = sorted(names, key=lambda k: (k not in w.countries, names[k]))   # 지정 국가 먼저, 가나다순

ss = st.session_state
ss.setdefault("country", None)
ss.setdefault("_map_pick", None)
ss.setdefault("map_gen", 0)          # 팝업을 닫을 때 지도 선택 상태를 비우려고 차트 key를 바꾼다
ss.setdefault("travel", {})          # 나라 키 → 여행 전 안내 결과
_qp = st.query_params.get("country")
if _qp in names and not ss.get("_country_qp"):     # ?country=VNM 으로 들어온 경우 (처음 한 번)
    ss["country"] = ss["_country_qp"] = _qp
if not ss.get("intro_done"):         # 인트로는 세션당 한 번만
    st.markdown(INTRO_CSS, unsafe_allow_html=True)
    ss["intro_done"] = True

map_key = f"world_map_{ss['map_gen']}"
picked = country_from_event(ss.get(map_key), set(names))
if picked and picked != ss["_map_pick"]:      # 지구본을 새로 눌렀을 때만 반영 (목록 선택을 덮어쓰지 않게)
    ss["country"] = ss["_map_pick"] = picked


def on_country_select() -> None:
    ss["country"] = ss["country_select"]


def close_panel() -> None:
    ss["country"] = ss["_map_pick"] = None
    ss["map_gen"] += 1


# ---- 제목·안내 → 검색창 ----------------------------------------------------------------
st.markdown("<div class='kd-hero'><h1 class='kd-hero-title'>법정감염병 안내 도우미</h1>"
            f"<p class='kd-hero-note'>{DISCLAIMER}</p></div>", unsafe_allow_html=True)
with st.container(key="home_search"):
    search_box("home_search_form", "감염병 이름으로 찾기  (예: 홍역, 뎅기열)" if NO_CHAT
               else "감염병에 대해 무엇이든 물어보세요  (예: 홍역은 어떻게 전파되나요?)")
sub = (f"질병관리청 검역관리지역 {w.as_of} 기준 · 지정 국가 {len(w.countries)}개국 · 지구본을 끌어 돌리고 나라를 눌러 보세요"
       if w.loaded else "검역관리지역 정보 수집 전입니다 (python -m scripts.fetch_quarantine)")
st.markdown(f"<div class='kd-globe-sub'>{sub}</div>", unsafe_allow_html=True)


# ---- 지구본 + 나라 팝업 ------------------------------------------------------------------
def globe(height: int) -> None:
    with st.container(key="map_card"):
        st.plotly_chart(build_world_map(w, ss["country"], names, height=height), key=map_key, on_select="rerun",
                        selection_mode="points", config=MAP_CONFIG)
    st.markdown("<div class='kd-legend'>"
                f"<span><i class='kd-dot' style='background:linear-gradient(90deg,{LOW},{HIGH})'></i>검역관리지역 (밝을수록 감염병 많음)</span>"
                f"<span><i class='kd-dot' style='background:{NEUTRAL}'></i>지정 없음</span>"
                f"<span><i class='kd-dot' style='background:{SELECTED}'></i>선택한 나라</span></div>",
                unsafe_allow_html=True)
    ss["country_select"] = ss["country"] if ss["country"] in names else None
    st.selectbox("나라 직접 고르기", choice_keys, key="country_select", index=None,
                 placeholder="나라 이름으로 찾기 (지구본에서 누르기 어려운 작은 나라 포함)",
                 format_func=lambda k: names[k], on_change=on_country_select, label_visibility="collapsed")


def travel_section(key: str, c) -> None:
    ids = [x.get("disease_id") for x in c.diseases if x.get("disease_id") in rt.by_id]
    if not ids or NO_CHAT:          # 여행 전 안내는 LLM이 필요
        return
    if key not in ss["travel"]:
        if not st.button("✈️ 여행 전 안내 만들기 (근거 포함)", key=f"travel_{key}", width="stretch"):
            return
        try:
            with st.spinner("감염병별 근거를 모아 정리하는 중"):
                ss["travel"][key] = travel_brief(names.get(key, key), w.as_of, ids, rt)
        except Exception as exc:  # noqa: BLE001 - 추적 정보는 로그에만 (NF2004)
            log.exception("여행 전 안내 실패")
            st.error("AI 사용 한도를 넘었습니다 (GMS 429). 잠시 후 다시 시도해 주세요." if is_quota_error(exc)
                     else "안내를 만드는 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요.")
            return
        st.rerun()          # 버튼을 지우고 결과만 보이게
    res = ss["travel"][key]
    with st.container(key=f"travel_box_{key}"):
        st.markdown("<div class='kd-card-title'>✈️ 여행 전 안내</div>", unsafe_allow_html=True)
        render_answer(res["answer"], ncols=1)
        render_evidence(res)


def country_panel(key: str) -> None:
    head = st.columns([5, 1], vertical_alignment="center")
    head[0].markdown(f"<div class='kd-country-name'>{names.get(key, key)}</div>", unsafe_allow_html=True)
    head[1].button("✕", key="close_panel", on_click=close_panel, help="닫기")
    if key == HOME_ISO:
        st.markdown("국내 법정감염병 133종의 급수·신고 기준과 설명은 위 검색창이나 **감염병 질문하기**에서 확인할 수 있어요.")
        return
    if not w.loaded:
        st.info("검역관리지역 정보를 아직 수집하지 않았습니다. 터미널에서 `python -m scripts.fetch_quarantine`을 실행하세요.")
        return
    c = w.countries.get(key)
    if not c or not c.diseases:
        st.success(f"{w.as_of} 기준, 이 나라는 검역관리지역으로 지정된 검역감염병이 없습니다.")
    else:
        st.markdown(f"<div class='kd-alert'>주의가 필요해요 · 검역감염병 {len(c.diseases)}종</div>",
                    unsafe_allow_html=True)
        for i, item in enumerate(c.diseases):
            with st.container(key=f"cwrow{min(i, 11)}_{i}_{key}"):   # 줄마다 조금씩 늦게 나타남 (styles.py)
                row = st.columns([3, 2], vertical_alignment="center")
                row[0].markdown(f"**{item['name']}**" + (f"  \n<span class='kd-muted'>{item['note']}</span>"
                                                         if item.get("note") else ""), unsafe_allow_html=True)
                with row[1]:
                    detail_button(item.get("disease_id", ""), f"detail_{key}_{i}", rt=rt)
        travel_section(key, c)
    src = f"[{w.source}]({w.source_url})" if w.source_url else w.source
    st.caption(f"기준일 {w.as_of} · 출처 {src} ({license_label('quarantine_area')})  \n검역관리지역: 검역법에 따라 WHO·현지 공관 등의 감염병 발생 "
               "정보를 기준으로 지정한 지역. 귀국 후 의심 증상이 있으면 1339에 문의하세요.")


if not ss["country"]:
    with st.container(key="globe_center"):
        globe(600)
else:
    left, right = st.columns([1.55, 1], gap="large")
    with left, st.container(key="globe_side"):          # 처음 고를 때 지구본이 왼쪽으로 미끄러짐
        globe(560)
    with right, st.container(key=f"country_panel_{ss['country']}"):   # 나라가 바뀔 때마다 팝업 애니메이션
        country_panel(ss["country"])
