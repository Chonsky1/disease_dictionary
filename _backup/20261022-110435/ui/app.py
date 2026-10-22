"""법정감염병 안내 도우미 - Streamlit 화면 (F2009, F2011, F2012, NF2004).

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
from service.explore import find_diseases  # noqa: E402
from service.runtime import DISCLAIMER, build_runtime, setup_logging  # noqa: E402

log = logging.getLogger("kdca_rag")
st.set_page_config(page_title="법정감염병 안내 도우미", page_icon="🩺", layout="wide")
setup_logging()

VIEW_CHAT, VIEW_FIND = "질의응답", "이런 감염병 찾기"


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

ss = st.session_state
ss.setdefault("chats", {})
ss.setdefault("disease", rt.diseases[0].disease_id)
ss.setdefault("view", VIEW_CHAT)
ss.setdefault("found", None)


def select_disease(did: str) -> None:
    ss["disease"] = did
    ss["view"] = VIEW_CHAT


# ---- 사이드바: 항목 선택과 색인 상태 (F2002, F2009) --------------------------------
with st.sidebar:
    st.header("감염병 선택")
    ids = [d.disease_id for d in rt.diseases]
    st.selectbox("질문할 감염병", ids, key="disease", format_func=lambda i: rt.by_id[i].label())
    st.caption(f"감염병 메타데이터 색인: {meta_count}건")
    d = rt.by_id[ss["disease"]]
    scope = " · ".join(f"{k} {'O' if v else 'X'}" for k, v in d.scope.items())
    st.markdown(
        f"**제{d.grade}급** · {d.rule['surveillance']}  \n"
        f"신고: {d.rule['deadline']}  \n"
        f"신고 범위: {scope}  \n"
        f"KCD: {d.kcd or '-'}" + (f"  \n분류: {d.group}" if d.group else "") +
        f"  \n담당: {d.department}")
    docs = rt.store.list_docs(d.disease_id)
    st.caption(f"수집된 위키 문서 {len(docs)}건")
    if docs:
        st.caption(" · ".join(x["title"] for x in docs))
    if st.button("이 감염병 대화 지우기", width="stretch"):
        ss["chats"].pop(d.disease_id, None)
        st.rerun()

st.title("법정감염병 안내 도우미")
st.caption(DISCLAIMER)
st.radio("화면", [VIEW_CHAT, VIEW_FIND], key="view", horizontal=True, label_visibility="collapsed")


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
    st.caption("⚠️ " + DISCLAIMER)


# ---- 질의응답 (F2009, F2012) -------------------------------------------------------
if ss["view"] == VIEW_CHAT:
    did = ss["disease"]
    history = ss["chats"].setdefault(did, [])  # 감염병별로 대화 기록을 따로 유지
    st.subheader(rt.by_id[did].label())
    for msg in history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg["role"] == "assistant":
                render_evidence(msg)

    question = st.chat_input(f"{rt.by_id[did].display_name}에 대해 질문하세요")
    if question:
        history.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            try:
                with st.status("근거를 찾는 중", expanded=False) as status:
                    state = run(rt.graph, question, did, on_event=lambda e: status.write(e["message"]))
                    new_docs = [t for r in state.get("collect_log", []) for t in r["indexed"]]
                    cache_hits = sum(1 for r in state.get("collect_log", []) if r["from_cache"])
                    label = f"완료 · 새로 수집한 문서 {len(new_docs)}건 · 캐시 사용 {cache_hits}건"
                    if new_docs:
                        status.write("수집한 문서: " + ", ".join(new_docs))
                    status.update(label=label, state="complete")
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

# ---- 조건 기반 탐색 (F2011) --------------------------------------------------------
else:
    st.subheader("조건으로 감염병 찾기")
    with st.form("find"):
        cond = st.text_input("찾고 싶은 감염병의 조건", placeholder="예: 모기가 옮기는 3급 감염병, 병원체보유자도 신고하는 1급 감염병")
        submitted = st.form_submit_button("찾기")
    if submitted and cond.strip():
        try:
            with st.spinner("후보를 고르는 중"):
                ss["found"] = (cond, find_diseases(cond, rt.store, rt.services.llm, rt.settings["condition_candidates"]))
        except Exception:  # noqa: BLE001
            log.exception("조건 탐색 실패")
            st.error("탐색 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요.")
    if ss["found"]:
        cond, results = ss["found"]
        st.caption(f"조건: {cond} · {len(results)}건")
        if not results:
            st.info("조건에 맞는 감염병을 찾지 못했습니다. 조건을 바꿔 보세요.")
        cols = st.columns(3)
        for i, r in enumerate(results):
            with cols[i % 3].container(border=True):
                st.markdown(f"**{r['name']}**  \n제{r['grade']}급" + (f" · {r['group']}" if r["group"] else ""))
                st.caption(r["reason"])
                st.button("이 감염병에 질문하기", key=f"go_{r['disease_id']}",
                          on_click=select_disease, args=(r["disease_id"],))
