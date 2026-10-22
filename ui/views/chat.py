"""감염병 질문하기: 대부분을 대화창이, 오른쪽을 대화 요약이 차지한다.

- 맨 위에 지금 질문 대상 감염병 이름을 크게, 그 아래에 급수·감시 방법·신고 기한.
- 홈·상세보기 검색창에서 엔터 → 이 페이지로 오며 그 질문에 바로 답한다 (session_state["pending_q"]).
- 대화 기록은 감염병마다 따로 유지한다 (F2009): 감염병을 바꾸면 그 감염병의 대화만 보인다.
  질문에 다른 감염병 이름이 있으면 그 감염병의 대화로 옮겨 가서 답한다. 오른쪽 요약은 모든 대화를 모은다.
- 답은 카테고리별 카드로, 걸린 시간과 근거는 작게 (ui/chat_core.py).
- '이런 감염병 찾기': 조건으로 감염병 후보를 찾는다 (F2011).
"""
from __future__ import annotations

import re

import streamlit as st
from langchain_core.output_parsers import StrOutputParser

from rag.prompts import CONVERSATION_SUMMARY
from service.explore import find_diseases
from service.runtime import DISCLAIMER
from ui.chat_core import ask, show_message, split_sections
from ui.common import detail_button, go_detail, guess_disease, log, runtime

VIEW_CHAT, VIEW_FIND = "질의응답", "이런 감염병 찾기"
EXAMPLES = ["어떻게 전파되나요?", "초기 증상은?", "잠복기는 얼마나 되나요?", "예방법을 알려 주세요"]
rt = runtime()
ss = st.session_state
ss.setdefault("chat_logs", {})         # {감염병 ID: [{role, content, disease_id, cited, warnings, timings}]}
ss.setdefault("disease", rt.diseases[0].disease_id)
ss.setdefault("chat_view", VIEW_CHAT)
ss.setdefault("found", None)
ss.setdefault("summary", {"n": 0, "text": ""})

pending = ss.pop("pending_q", None)
if pending:                            # 다른 페이지의 검색창에서 온 질문
    ss["chat_view"] = VIEW_CHAT
    ss["disease"] = pending.get("disease_id") or guess_disease(pending["q"], rt) or ss["disease"]
if "_next_disease" in ss:               # 질문 속 감염병으로 '질문 대상'을 맞춘다 (위젯 그리기 전에)
    ss["disease"] = ss.pop("_next_disease")


def log_for(did: str) -> list[dict]:
    return ss["chat_logs"].setdefault(did, [])


def all_messages() -> list[dict]:
    """요약용: 모든 감염병의 대화 (감염병별로 이어 붙임)."""
    return [m for msgs in ss["chat_logs"].values() for m in msgs]


def switch_to(did: str) -> None:
    ss["disease"] = did


# ---- 요약 -------------------------------------------------------------------------------
def _plain(text: str) -> str:
    head, sections = split_sections(text)
    core = next((b for t, b in sections if t.replace(" ", "") == "핵심답변"), "")
    text = core or head or (sections[0][1] if sections else text)
    return re.sub(r"\s*\[근거[^\]]*\]", "", text).strip()


def extractive_summary(log_: list[dict]) -> str:
    """감염병별로 답의 '핵심 답변' 첫 문장 (LLM 호출 없음)."""
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
        + re.sub(r"\s*\[근거[^\]]*\]", "", m["content"]) for m in log_[-16:])
    text = ""
    if rt.settings.get("summary_llm", False):   # 기본은 LLM 없이 '핵심 답변'으로 요약 (질문당 LLM 1회 절약)
        try:
            text = (CONVERSATION_SUMMARY | rt.services.helper_llm | StrOutputParser()).invoke(
                {"conversation": convo}).strip()
        except Exception:  # noqa: BLE001
            log.exception("대화 요약 실패")
    if len(text) < 15 or "-" not in text:   # 형식이 안 맞으면 추출 요약으로
        text = extractive_summary(log_)
    ss["summary"] = {"n": n, "text": text}
    return text


def summary_panel() -> None:
    log_ = all_messages()
    st.markdown("<div class='kd-card-title'>📝 요약</div>", unsafe_allow_html=True)
    if not any(m["role"] == "assistant" for m in log_):
        st.markdown("<div class='kd-muted'>답을 받으면 여기에 정리됩니다.</div>", unsafe_allow_html=True)
        return
    with st.spinner("요약하는 중"):
        st.markdown(summarize(log_))
    asked = [did for did, msgs in ss["chat_logs"].items() if msgs and did in rt.by_id]
    st.markdown("<div class='kd-muted' style='margin-top:.6rem'>대화한 감염병 (누르면 그 대화로)</div>",
                unsafe_allow_html=True)
    for i, did in enumerate(asked[-6:]):
        c = st.columns([3, 1], gap="small", vertical_alignment="center")
        c[0].button(("▶ " if did == ss["disease"] else "") + rt.by_id[did].display_name, key=f"sum_go_{i}_{did}",
                    on_click=switch_to, args=(did,), width="stretch", disabled=did == ss["disease"])
        with c[1]:
            detail_button(did, f"sum_detail_{i}_{did}", "🔎", rt)


def pick_example(q: str) -> None:
    ss["_example"] = q


def chat_view() -> None:
    d = rt.by_id[ss["disease"]]
    head = st.columns([3, 1.2], vertical_alignment="bottom")
    with head[0]:
        st.markdown(f"<div class='kd-target-name'>{d.display_name}</div>"
                    f"<div class='kd-target-meta'><span class='kd-chip kd-chip-strong'>제{d.grade}급</span>"
                    f"<span class='kd-chip'>{d.rule['surveillance']}</span>"
                    f"<span class='kd-chip'>신고 {d.rule['deadline']}</span>"
                    + (f"<span class='kd-chip'>{d.group}</span>" if d.group else "") + "</div>",
                    unsafe_allow_html=True)
    ids = [x.disease_id for x in rt.diseases]
    head[1].selectbox("감염병 바꾸기", ids, key="disease", format_func=lambda i: rt.by_id[i].label())

    history = log_for(ss["disease"])
    left, right = st.columns([2.7, 1], gap="large")
    with left, st.container(key="chat_card"):
        box = st.container(height=470, border=False, key="chat_box")
        with box:
            if not history and not pending:
                st.markdown("<div class='kd-muted'>이렇게 물어보세요</div>", unsafe_allow_html=True)
                cols = st.columns(len(EXAMPLES))
                for c, q in zip(cols, EXAMPLES):
                    c.button(q, key=f"ex_{q}", on_click=pick_example, args=(q,), width="stretch")
            for msg in history:
                show_message(msg, rt, show_disease=False)
        if pending:
            ask(pending["q"], ss["disease"], rt, history, box, show_disease=False)
        question = st.chat_input(f"{d.display_name}에 대해, 또는 다른 감염병 이름을 넣어 질문하세요")
        question = question or ss.pop("_example", None)
        if question:
            did = guess_disease(question, rt, use_search=False) or ss["disease"]
            if did != ss["disease"]:      # 다른 감염병 질문 → 그 감염병의 대화로 옮겨 가서 답한다 (대화가 섞이지 않게)
                ss["_next_disease"] = did
                ss["pending_q"] = {"q": question, "disease_id": did}
                st.rerun()
            ask(question, did, rt, history, box, show_disease=False)
        if history:
            st.button(f"'{d.display_name}' 대화 지우기", key="clear_chat", type="tertiary",
                      on_click=lambda: ss["chat_logs"].pop(ss["disease"], None))
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


st.segmented_control("화면", [VIEW_CHAT, VIEW_FIND], key="chat_view", label_visibility="collapsed")
if ss["chat_view"] == VIEW_FIND:
    find_view()
else:
    chat_view()
st.caption("⚠️ " + DISCLAIMER)
