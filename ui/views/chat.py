"""감염병 질문하기: 페이지 대부분을 대화창이, 오른쪽을 대화 전체 요약이 차지한다.

- 홈·상세보기 검색창에서 엔터 → 이 페이지로 오며 그 질문에 바로 답한다 (session_state["pending_q"]).
- 질문에 감염병 이름이 있으면 그 감염병으로, 없으면 '질문 대상'으로 고른 감염병으로 답한다 (F2009).
- 대화는 하나로 이어지고(감염병이 바뀌어도), 답마다 근거·출처를 펼쳐 볼 수 있다 (F2008).
- '이런 감염병 찾기': 조건으로 감염병 후보를 찾는다 (F2011).
"""
from __future__ import annotations

import re

import streamlit as st
from langchain_core.output_parsers import StrOutputParser

from rag.graph import IndexMissingError, run
from rag.prompts import CONVERSATION_SUMMARY
from service.explore import find_diseases
from service.runtime import DISCLAIMER
from ui.common import detail_button, go_detail, guess_disease, log, runtime

VIEW_CHAT, VIEW_FIND = "질의응답", "이런 감염병 찾기"
rt = runtime()
ss = st.session_state
ss.setdefault("chat_log", [])          # [{role, content, disease_id, cited, warnings}]
ss.setdefault("disease", rt.diseases[0].disease_id)
ss.setdefault("chat_view", VIEW_CHAT)
ss.setdefault("found", None)
ss.setdefault("summary", {"n": 0, "text": ""})

pending = ss.pop("pending_q", None)
if pending:                            # 다른 페이지의 검색창에서 온 질문
    ss["chat_view"] = VIEW_CHAT
    ss["disease"] = pending.get("disease_id") or guess_disease(pending["q"], rt) or ss["disease"]


# ---- 답변 -------------------------------------------------------------------------------
def render_evidence(msg: dict) -> None:
    cited = msg.get("cited", [])
    for warn in msg.get("warnings", []):
        st.warning(warn)
    if not cited:
        st.info("이 답변에 인용된 근거가 없습니다. 제공된 문서로 확인되지 않는 내용일 수 있습니다.")
        return
    with st.expander(f"답변에 사용한 근거 {len(cited)}건"):
        for e in cited:
            sec = f" / {e['section']}" if e.get("section") else ""
            line = f"**[근거 {e['n']}]** {e['title']}{sec} · {e['source_label']} · [원문]({e['url']})"
            if e.get("wiki_url"):
                line += f" · [요약 출처: 위키백과]({e['wiki_url']})"
            st.markdown(line)


def show_message(msg: dict) -> None:
    with st.chat_message(msg["role"]):
        if msg["role"] == "user" and msg.get("disease_id") in rt.by_id:
            st.markdown(f"<span class='kd-chip'>{rt.by_id[msg['disease_id']].display_name}</span>",
                        unsafe_allow_html=True)
        st.markdown(msg["content"])
        if msg["role"] == "assistant":
            render_evidence(msg)


def answer(question: str, did: str, box) -> None:
    log_ = ss["chat_log"]
    user = {"role": "user", "content": question, "disease_id": did}
    log_.append(user)
    with box:
        show_message(user)
        with st.chat_message("assistant"):
            try:
                with st.status(f"'{rt.by_id[did].display_name}' 근거를 찾는 중", expanded=False) as status:
                    state = run(rt.graph, question, did, on_event=lambda e: status.write(e["message"]))
                    new_docs = [t for r in state.get("collect_log", []) for t in r["indexed"]]
                    hits = sum(1 for r in state.get("collect_log", []) if r["from_cache"])
                    if new_docs:
                        status.write("수집한 문서: " + ", ".join(new_docs))
                    status.update(label=f"완료 · 새로 수집한 문서 {len(new_docs)}건 · 캐시 사용 {hits}건",
                                  state="complete")
                msg = {"role": "assistant", "content": state["answer"], "cited": state.get("cited", []),
                       "warnings": state.get("warnings", []), "disease_id": did}
                st.markdown(msg["content"])
                render_evidence(msg)
                log_.append(msg)
            except IndexMissingError:
                st.warning("이 감염병의 색인이 없습니다. `python -m scripts.build_index`를 실행하세요.")
            except Exception:  # noqa: BLE001 - 추적 정보는 로그에만 (NF2004)
                log.exception("답변 생성 실패")
                st.error("답변을 만드는 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요.")


# ---- 요약 -------------------------------------------------------------------------------
def _plain(text: str) -> str:
    return re.sub(r"\s*\[근거[^\]]*\]", "", text).strip()


def extractive_summary(log_: list[dict]) -> str:
    """LLM 요약이 안 될 때: 감염병별로 답의 첫 문장."""
    lines, seen = [], set()
    for m in log_:
        if m["role"] != "assistant":
            continue
        first = re.split(r"(?<=[.다요])\s", _plain(m["content"]), maxsplit=1)[0]
        name = rt.by_id[m["disease_id"]].display_name if m.get("disease_id") in rt.by_id else "기타"
        if (name, first) not in seen:
            seen.add((name, first))
            lines.append(f"- **{name}**: {first}")
    return "\n".join(lines[-8:])


def summarize(log_: list[dict]) -> str:
    n = sum(1 for m in log_ if m["role"] == "assistant")
    if ss["summary"]["n"] == n:
        return ss["summary"]["text"]
    convo = "\n".join(
        f"[{'질문' if m['role'] == 'user' else '답변'}] "
        + (f"({rt.by_id[m['disease_id']].display_name}) " if m.get("disease_id") in rt.by_id else "")
        + _plain(m["content"]) for m in log_[-16:])
    text = ""
    try:
        text = (CONVERSATION_SUMMARY | rt.services.llm | StrOutputParser()).invoke({"conversation": convo}).strip()
    except Exception:  # noqa: BLE001
        log.exception("대화 요약 실패")
    if len(text) < 15 or "-" not in text:   # 형식이 안 맞으면 추출 요약으로
        text = extractive_summary(log_)
    ss["summary"] = {"n": n, "text": text}
    return text


def summary_panel() -> None:
    log_ = ss["chat_log"]
    st.markdown("<div class='kd-card-title'>대화 요약</div>", unsafe_allow_html=True)
    if not any(m["role"] == "assistant" for m in log_):
        st.markdown("<div class='kd-muted'>질문하고 답을 받으면 여기에 전체 내용이 요약됩니다.</div>",
                    unsafe_allow_html=True)
        return
    with st.spinner("요약하는 중"):
        st.markdown(summarize(log_))
    asked = list(dict.fromkeys(m["disease_id"] for m in log_ if m.get("disease_id") in rt.by_id))
    st.markdown(f"<div class='kd-muted' style='margin-top:.8rem'>다룬 감염병 {len(asked)}종</div>",
                unsafe_allow_html=True)
    for i, did in enumerate(asked[-6:]):
        detail_button(did, f"sum_detail_{i}_{did}", f"{rt.by_id[did].display_name} 상세보기", rt)
    if st.button("대화 지우기", key="clear_chat", width="stretch"):
        ss["chat_log"], ss["summary"] = [], {"n": 0, "text": ""}
        st.rerun()


def chat_view() -> None:
    left, right = st.columns([2.6, 1], gap="large")
    with left, st.container(key="chat_card"):
        top = st.columns([2.2, 3], vertical_alignment="bottom")
        ids = [d.disease_id for d in rt.diseases]
        top[0].selectbox("질문 대상 감염병 (질문에 이름이 있으면 자동으로 바뀜)", ids, key="disease",
                         format_func=lambda i: rt.by_id[i].label())
        d = rt.by_id[ss["disease"]]
        top[1].markdown(f"**제{d.grade}급** · {d.rule['surveillance']} · 신고 {d.rule['deadline']}  \n"
                        f"<span class='kd-muted'>메타데이터 색인 {rt.store.meta_count()}건 · 이 감염병 위키 문서 "
                        f"{len(rt.store.list_docs(d.disease_id))}건</span>", unsafe_allow_html=True)
        box = st.container(height=460, border=False, key="chat_box")   # 입력창이 한 화면에 보이도록
        with box:
            if not ss["chat_log"] and not pending:
                st.markdown("<div class='kd-muted'>무엇이든 물어보세요. 예: “홍역은 어떻게 전파되나요?”, "
                            "“쯔쯔가무시증은 몇 급이고 언제까지 신고하나요?”</div>", unsafe_allow_html=True)
            for msg in ss["chat_log"]:
                show_message(msg)
        if pending:
            answer(pending["q"], ss["disease"], box)
        question = st.chat_input("감염병에 대해 질문하세요")
        if question:
            did = guess_disease(question, rt, use_search=False) or ss["disease"]
            answer(question, did, box)
            ss["_next_disease"] = did
            st.rerun()      # 오른쪽 요약을 새 답까지 반영해 다시 그림
    with right, st.container(key="chat_summary"):
        summary_panel()


def ask_about(did: str) -> None:
    ss["disease"], ss["chat_view"] = did, VIEW_CHAT


def find_view() -> None:
    with st.container(key="chat_card"):
        with st.form("find", border=False):
            c1, c2 = st.columns([5, 1], vertical_alignment="bottom")
            cond = c1.text_input("찾고 싶은 감염병의 조건",
                                 placeholder="예: 모기가 옮기는 3급 감염병, 병원체보유자도 신고하는 1급 감염병")
            submitted = c2.form_submit_button("찾기", width="stretch")
        if submitted and cond.strip():
            try:
                with st.spinner("후보를 고르는 중"):
                    ss["found"] = (cond, find_diseases(cond, rt.store, rt.services.llm,
                                                       rt.settings["condition_candidates"]))
            except Exception:  # noqa: BLE001
                log.exception("조건 탐색 실패")
                st.error("탐색 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요.")
        if not ss["found"]:
            st.markdown("<div class='kd-muted'>조건을 입력하면 메타데이터 색인에서 후보를 찾은 뒤 조건에 맞는 감염병만 "
                        "골라 보여 줍니다.</div>", unsafe_allow_html=True)
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
                b = st.columns(2)
                b[0].button("질문하기", key=f"go_{r['disease_id']}", width="stretch", on_click=ask_about,
                            args=(r["disease_id"],))
                if b[1].button("상세보기", key=f"fd_{r['disease_id']}", width="stretch"):
                    go_detail(r["disease_id"])


if "_next_disease" in ss:               # 질문 속 감염병으로 '질문 대상'을 맞춘다 (위젯 그리기 전에)
    ss["disease"] = ss.pop("_next_disease")
st.markdown("<div class='kd-page-title'>감염병 질문하기</div>", unsafe_allow_html=True)
st.radio("화면", [VIEW_CHAT, VIEW_FIND], key="chat_view", horizontal=True, label_visibility="collapsed")
if ss["chat_view"] == VIEW_CHAT:
    chat_view()
else:
    find_view()
st.caption("⚠️ " + DISCLAIMER)
