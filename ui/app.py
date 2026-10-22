"""법정감염병 안내 도우미 - Streamlit 진입점 (페이지 라우터).

페이지 (ui/views/):
  home.py    홈: 제목·안내 → 감염병 검색창 → 돌리는 지구본. 나라를 누르면 지구본이 왼쪽으로 비키고 오른쪽에 팝업
  chat.py    감염병 질문하기: 큰 대화창 + 오른쪽 대화 요약 (검색창에서 엔터 → 이 페이지)  (F2009, F2011, F2012)
  detail.py  감염병 상세보기: 설명 + 연령·성별 인포그래픽 + 챗봇에게 물어보기 (목차에는 숨김)
  age.py     연령별 알아보기: 연령대별 상위 감염병, 감염병별 연령 분포, 유행 시기

목차 바: 화면 맨 위에 항상 보이며, 로고를 누르면 홈으로 간다 (ui/styles.py).
실행: streamlit run ui/app.py   (프로젝트 폴더에서 실행해야 .streamlit/config.toml의 다크 테마가 적용됨)
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from rag.llm import missing_keys  # noqa: E402
from service.runtime import setup_logging  # noqa: E402
from ui.common import PAGE_AGE, PAGE_CHAT, PAGE_DETAIL, PAGE_HOME, get_runtime  # noqa: E402
from ui.styles import CSS  # noqa: E402

log = logging.getLogger("kdca_rag")
st.set_page_config(page_title="법정감염병 안내 도우미", page_icon="🩺", layout="wide",
                   initial_sidebar_state="collapsed")
setup_logging()
st.markdown(CSS, unsafe_allow_html=True)

pages = {
    "home": st.Page(PAGE_HOME, title="나라별 주의 감염병", default=True),
    "chat": st.Page(PAGE_CHAT, title="감염병 질문하기", url_path="chat"),
    "age": st.Page(PAGE_AGE, title="연령별 알아보기", url_path="age"),
    "detail": st.Page(PAGE_DETAIL, title="감염병 상세보기", url_path="detail"),
}
nav = st.navigation(list(pages.values()), position="hidden")

# ---- 목차 바 (항상 보임): 로고를 누르면 홈, 테두리 없는 글자 목차 ------------------------------------
with st.container(key="kd_nav", horizontal=True, vertical_alignment="center", gap="medium"):
    with st.container(key="kd_logo", width="content"):
        st.page_link(pages["home"], label="법정감염병 안내")
    for k in ("home", "chat", "age"):
        st.page_link(pages[k], label=pages[k].title)

# ---- 사전 점검: 키 누락, 색인 없음 (F2012) ---------------------------------------------
missing = missing_keys()
if missing:
    st.error("필요한 환경변수가 설정되지 않았습니다: " + ", ".join(missing))
    st.markdown("프로젝트 폴더의 `.env.example`을 `.env`로 복사해 값을 채운 뒤 다시 실행하세요.")
    st.stop()
try:
    rt = get_runtime()
except Exception:  # noqa: BLE001
    log.exception("런타임 초기화 실패")
    st.error("서비스를 초기화하지 못했습니다. 설정과 네트워크를 확인한 뒤 다시 실행하세요.")
    st.stop()
if rt.store.meta_count() == 0:
    st.warning("색인이 없습니다. 터미널에서 아래 명령을 먼저 실행하세요.")
    st.code("python -m scripts.build_index", language="bash")
    st.stop()

nav.run()
