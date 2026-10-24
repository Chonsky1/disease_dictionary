"""페이지들이 함께 쓰는 것: 런타임, 데이터, 페이지 이동, 감염병 검색창.

페이지 파일(ui/views/*.py)은 Streamlit이 매번 다시 실행하고, 이 모듈은 한 번만 import되므로
테스트에서 바꿔 끼우는 함수(service.runtime.build_runtime)는 import 시점이 아니라 호출 시점에 찾는다.
"""
from __future__ import annotations

import logging
import re

import streamlit as st

import service.runtime as runtime_mod
from data_prep import settings as paths
from service.age import AgeStats, load_age_stats
from service.countries import WatchData, country_names, load_watch

log = logging.getLogger("kdca_rag")

# 페이지 파일 경로 (ui/app.py 기준). st.switch_page·st.page_link에 쓴다.
PAGE_HOME = "views/home.py"
PAGE_CHAT = "views/chat.py"
PAGE_DETAIL = "views/detail.py"
PAGE_AGE = "views/age.py"

MAP_CONFIG = {"displayModeBar": False, "scrollZoom": False, "topojsonURL": "app/static/topojson/"}
CHART_CONFIG = {"displayModeBar": False, "scrollZoom": False}
HOME_ISO = "KOR"


NO_CHAT = paths.NO_CHAT        # 챗봇 없이 지도·통계·상세보기만 (KDCA_NO_CHAT=1)


@st.cache_resource(show_spinner="모델과 색인을 불러오는 중")
def get_runtime():
    return runtime_mod.build_lite_runtime() if NO_CHAT else runtime_mod.build_runtime()


def runtime():
    """app.py가 사전 점검을 마친 뒤 부르므로 여기서는 실패하지 않는다고 본다."""
    return get_runtime()


def watch() -> WatchData:
    return load_watch(paths.PROCESSED / "country_watch.json")


def age_stats() -> AgeStats:
    return load_age_stats(paths.PROCESSED / "age_stats.json")


def all_country_names(w: WatchData) -> dict[str, str]:
    """지구본에서 고를 수 있는 모든 나라: 국가명 변환표 + API 나라 이름."""
    names = country_names(paths.CONFIG / "country_names_ko.json")
    names.update({k: c.name_ko for k, c in w.countries.items()})
    return names


# ---- 페이지 이동 --------------------------------------------------------------------
def go_detail(disease_id: str) -> None:
    """상세보기 페이지로. 주소에 ?d=<감염병 ID>가 붙어 그대로 공유할 수 있다."""
    st.session_state["detail_id"] = disease_id
    st.switch_page(PAGE_DETAIL, query_params={"d": disease_id})


def detail_button(disease_id: str, key: str, label: str = "상세보기", rt=None) -> None:
    rt = rt or runtime()
    if disease_id in rt.by_id and st.button(label, key=key, width="stretch"):
        go_detail(disease_id)


def go_chat(question: str, disease_id: str | None = None) -> None:
    """질문을 들고 감염병 질문하기 페이지로. 그 페이지가 도착하자마자 답을 만든다."""
    st.session_state["pending_q"] = {"q": question.strip(), "disease_id": disease_id}
    st.switch_page(PAGE_CHAT)


# ---- 질문 속 감염병 찾기 ----------------------------------------------------------------
def _key(s: str) -> str:
    return re.sub(r"[\s·\-()/]", "", s).lower()


def guess_disease(question: str, rt, use_search: bool = True) -> str | None:
    """질문에 감염병 이름(또는 약어)이 있으면 그것, 없으면 메타데이터 색인 검색 1위 (use_search)."""
    q = _key(question)
    best, best_len = None, 0
    for d in rt.diseases:
        names = [d.display_name, re.sub(r"\(.*?\)", "", d.display_name), *getattr(d, "abbreviations", [])]
        for n in names:
            k = _key(n)
            if len(k) >= 2 and k in q and len(k) > best_len:
                best, best_len = d.disease_id, len(k)
    if best or not use_search:
        return best
    try:
        hits = rt.store.search_meta(question, 1)
        return hits[0].metadata.get("disease_id") if hits else None
    except Exception:  # noqa: BLE001
        log.exception("감염병 추정 검색 실패")
        return None


def search_box(key: str, placeholder: str, disease_id: str | None = None) -> None:
    """구글 검색창처럼 생긴 질문 입력. 엔터를 누르면 감염병 질문하기 페이지로 이동."""
    with st.form(key, border=False, clear_on_submit=True):
        c = st.columns([12, 1], vertical_alignment="center", gap="small")
        q = c[0].text_input("질문", placeholder=placeholder, label_visibility="collapsed")
        sent = c[1].form_submit_button("→", width="stretch")
    if sent and q.strip():
        if NO_CHAT:      # 챗봇이 없으면 이름으로 감염병을 찾아 상세보기로
            did = disease_id or guess_disease(q, runtime(), use_search=False)
            if did:
                go_detail(did)
            else:
                st.warning("감염병 이름을 찾지 못했습니다. 감염병 이름을 넣어 다시 검색해 보세요 (예: 홍역, 뎅기열).")
            return
        go_chat(q, disease_id)


def disease_chip(d) -> str:
    return f"<span class='kd-chip'>제{d.grade}급</span>" + (f"<span class='kd-chip'>{d.group}</span>" if d.group else "")
