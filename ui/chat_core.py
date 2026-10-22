"""감염병 질문하기 페이지와 상세보기 페이지가 함께 쓰는 챗봇 부품.

- 답변을 글자 단위로 흘려 보여 주고(스트리밍), 끝나면 '### 카테고리'별 카드로 다시 그린다.
- 답변마다 걸린 시간(총 시간·첫 글자·LLM 호출 수)을 작게 표시한다 (rag/metrics.py).
- 근거·출처는 접어 두는 칸에 (F2008).
"""
from __future__ import annotations

import html
import re

import streamlit as st

from rag.graph import IndexMissingError, run
from rag.metrics import describe
from ui.common import log

ICONS = {"핵심 답변": "💡", "초기 증상": "🌡️", "전체 증상": "🩺", "잠복기": "⏳", "치사율·중증도": "⚠️",
         "전염성·전파 경로": "🦠", "발생 지역": "🌍", "치료": "💊", "예방": "🛡️", "신고·관리": "📋"}
_CITE = re.compile(r"\[근거\s*[\d,\s]+\]")


def is_quota_error(exc: BaseException) -> bool:
    """GMS·OpenAI 사용 한도 초과 (429 insufficient_quota / spend limit)."""
    text = f"{type(exc).__name__} {exc}"
    return any(k in text for k in ("insufficient_quota", "spend_limit", "RateLimitError", "Error code: 429"))


def split_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    """'### 제목' 기준으로 (머리말, [(제목, 본문)])."""
    parts = re.split(r"^\s*#{2,4}\s*(.+?)\s*$", text.strip(), flags=re.M)
    head, sections = parts[0].strip(), []
    for i in range(1, len(parts) - 1, 2):
        body = parts[i + 1].strip()
        if body:
            sections.append((parts[i].strip(), body))
    return head, sections


def _cites(md: str) -> str:
    return _CITE.sub(lambda m: f"<span class='kd-cite'>{m.group(0)}</span>", md)


def render_answer(text: str) -> None:
    """카테고리가 있으면 핵심 답변은 강조 상자, 나머지는 두 칸 카드로. 없으면 그대로."""
    head, sections = split_sections(text)
    if not sections:
        st.markdown(_cites(text), unsafe_allow_html=True)
        return
    if head:
        st.markdown(_cites(head), unsafe_allow_html=True)
    core = [b for t, b in sections if t.replace(" ", "") == "핵심답변"]
    rest = [(t, b) for t, b in sections if t.replace(" ", "") != "핵심답변"]
    if core:
        st.markdown(f"<div class='kd-core'>💡 {_cites(html.escape(core[0]).replace(chr(10), '<br>'))}</div>",
                    unsafe_allow_html=True)
    for i in range(0, len(rest), 2):
        cols = st.columns(2, gap="small")
        for col, (title, body) in zip(cols, rest[i:i + 2]):
            with col, st.container(key=f"cat_{abs(hash((title, body))) % 10**8}"):
                st.markdown(f"<div class='kd-cat-title'>{ICONS.get(title, '•')} {html.escape(title)}</div>",
                            unsafe_allow_html=True)
                st.markdown(_cites(body), unsafe_allow_html=True)


def render_evidence(msg: dict) -> None:
    cited = msg.get("cited", [])
    for warn in msg.get("warnings", []):
        st.warning(warn)
    if not cited:
        st.caption("인용된 근거가 없습니다. 제공된 문서로 확인되지 않는 내용일 수 있습니다.")
        return
    with st.expander(f"근거 {len(cited)}건"):
        for e in cited:
            sec = f" / {e['section']}" if e.get("section") else ""
            line = f"**[근거 {e['n']}]** {e['title']}{sec} · {e['source_label']} · [원문]({e['url']})"
            if e.get("wiki_url"):
                line += f" · [요약 출처: 위키백과]({e['wiki_url']})"
            st.markdown(line)


def show_message(msg: dict, rt, show_disease: bool = True) -> None:
    with st.chat_message(msg["role"]):
        if msg["role"] == "user":
            chip = (f"<span class='kd-chip'>{rt.by_id[msg['disease_id']].display_name}</span> "
                    if show_disease and msg.get("disease_id") in rt.by_id else "")
            st.markdown(f"{chip}<b>{html.escape(msg['content'])}</b>", unsafe_allow_html=True)
            return
        render_answer(msg["content"])
        if msg.get("timings"):
            st.markdown(f"<div class='kd-timing'>⏱ {describe(msg['timings'])}</div>", unsafe_allow_html=True)
        render_evidence(msg)


def ask(question: str, did: str, rt, history: list[dict], box, show_disease: bool = True) -> dict | None:
    """질문을 기록에 넣고 box 안에 답을 스트리밍으로 그린다. 끝나면 답 메시지를 기록에 넣고 돌려준다."""
    user = {"role": "user", "content": question, "disease_id": did}
    history.append(user)
    with box:
        show_message(user, rt, show_disease)
        with st.chat_message("assistant"):
            status = st.status("근거를 찾는 중", expanded=False)
            live = st.empty()
            buf: list[str] = []

            def on_event(e: dict) -> None:
                if e.get("stage") == "token":
                    buf.append(e["text"])
                    if len(buf) % 6 == 1:              # 너무 자주 다시 그리지 않게
                        live.markdown("".join(buf) + " ▌")
                elif e.get("message"):
                    status.write(e["message"])
            try:
                state = run(rt.graph, question, did, on_event=on_event)
                t = state.get("timings", {})
                status.update(label=f"완료 · {describe(t)}", state="complete")
                live.empty()
                msg = {"role": "assistant", "content": state["answer"], "cited": state.get("cited", []),
                       "warnings": state.get("warnings", []), "disease_id": did, "timings": t}
                render_answer(msg["content"])
                render_evidence(msg)
                history.append(msg)
                return msg
            except IndexMissingError:
                status.update(label="색인 없음", state="error")
                st.warning("이 감염병의 색인이 없습니다. `python -m scripts.build_index`를 실행하세요.")
            except Exception as exc:  # noqa: BLE001 - 추적 정보는 로그에만 (NF2004)
                log.exception("답변 생성 실패")
                status.update(label="실패", state="error")
                if is_quota_error(exc):
                    st.error("AI 사용 한도를 넘었습니다 (GMS 429). 한도가 다시 열린 뒤 질문해 주세요. "
                             "같은 질문은 저장된 답이 있으면 바로 보여 드립니다.")
                else:
                    st.error("답변을 만드는 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요.")
    return None
