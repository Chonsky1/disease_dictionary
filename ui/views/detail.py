"""감염병 상세보기: 기본 설명 + 연령·성별 인포그래픽(애니메이션) + 유행 시기 + 지정 국가 + 챗봇에게 물어보기.

챗봇은 이 페이지 안에서 답한다 (감염병마다 대화를 따로 기억, session_state["detail_chat"]).

주소: /detail?d=inf_0020  (홈·연령별·질문하기 페이지의 '상세보기' 버튼이 이 주소로 보낸다)
"""
from __future__ import annotations

import html

import streamlit as st

from data_prep.sources import license_label, stats_terms
from service.age import profile, trend_summary
from service.runtime import DISCLAIMER
from ui.age_view import pyramid, trend_lines
from ui.chat_core import ask, show_message
from ui.common import CHART_CONFIG, NO_CHAT, PAGE_HOME, age_stats, log, runtime, watch

rt = runtime()
ss = st.session_state
did = st.query_params.get("d") or ss.get("detail_id")
if did not in rt.by_id:
    st.markdown("<div class='kd-page-title'>감염병 상세보기</div>", unsafe_allow_html=True)
    pick = st.selectbox("감염병", [d.disease_id for d in rt.diseases], index=None, placeholder="감염병을 고르세요",
                        format_func=lambda i: rt.by_id[i].label())
    if pick:
        ss["detail_id"] = pick
        st.query_params["d"] = pick
        st.rerun()
    st.stop()

ss["detail_id"] = did
st.query_params["d"] = did
d = rt.by_id[did]
stats = age_stats()
w = watch()


def esc(s: str) -> str:
    return html.escape(str(s))


@st.cache_data(show_spinner=False, ttl=3600)
def wiki_summary(disease_id: str) -> dict | None:
    """수집해 둔 위키백과 문서(없으면 받아서 저장)의 요약. 실패하면 None."""
    svc = runtime().services
    info = svc.wiki_titles.get(disease_id) or {}
    if not info.get("pageid") or svc.collector is None:
        return None
    if NO_CHAT and not svc.collector.has_page(int(info["pageid"])):   # 챗봇 없는 모드는 받아 둔 본문만
        return None
    try:
        page = svc.collector.get_page(int(info["pageid"]), info["title"])
    except Exception:  # noqa: BLE001
        log.exception("상세보기 위키 요약 실패: %s", disease_id)
        return None
    text = (page.get("summary") or page.get("text") or "").strip()
    if not text:
        return None
    cut = text[:520]
    if len(text) > 520:
        cut = cut[:cut.rfind(".") + 1] or cut + "…"
    return {"text": cut, "title": page.get("title", info["title"]), "url": page.get("url", "")}


# ---- 머리 -----------------------------------------------------------------------------
st.page_link(PAGE_HOME, label="← 지구본으로")
scope = " · ".join(f"{k} {'O' if v else 'X'}" for k, v in d.scope.items())
chips = [f"제{d.grade}급", d.rule["surveillance"], f"신고 {d.rule['deadline']}"] + ([d.group] if d.group else [])
st.markdown(
    f"<div class='kd-detail-head'><div class='kd-detail-name'>{esc(d.display_name)}</div>"
    + "".join(f"<span class='kd-chip kd-chip-strong'>{esc(c)}</span>" for c in chips)
    + f"<div class='kd-muted' style='margin-top:.4rem'>신고 범위 {esc(scope)} · KCD {esc(d.kcd or '-')}"
    + (f" · 담당 {esc(d.department)}" if d.department else "") + "</div></div>", unsafe_allow_html=True)

# ---- 기본 설명 + 지정 국가 ------------------------------------------------------------------
left, right = st.columns([1.7, 1], gap="large")
with left, st.container(key="detail_about"):
    st.markdown("<div class='kd-card-title'>기본 설명</div>", unsafe_allow_html=True)
    with st.spinner("설명을 불러오는 중"):
        wiki = wiki_summary(did)
    if wiki:
        st.markdown(esc(wiki["text"]))
        st.caption(f"출처: [한국어 위키백과 · {wiki['title']}]({wiki['url']}) (CC BY-SA 4.0)")
    else:
        st.markdown(f"{esc(d.display_name)}은(는) **제{d.grade}급 법정감염병**입니다.")
    st.markdown(f"<div class='kd-rule'><b>제{d.grade}급이란?</b> {esc(d.rule.get('desc', ''))}</div>",
                unsafe_allow_html=True)
    st.caption(f"급·신고 범위·KCD 출처: [질병관리청 감염병포털 법정감염병 분류]"
               f"(https://dportal.kdca.go.kr/pot/ii/sttyInftnsds/sttyInftnsds.do) ({license_label('kdca_disease_list')})")
with right, st.container(key="detail_world"):
    where = sorted((c for c in w.countries.values() if any(x.get("disease_id") == did for x in c.diseases)),
                   key=lambda c: c.name_ko)
    st.markdown("<div class='kd-card-title'>검역관리지역 지정 국가</div>", unsafe_allow_html=True)
    if not w.loaded:
        st.caption("검역관리지역 정보를 아직 수집하지 않았습니다.")
    elif not where:
        st.markdown("<div class='kd-muted'>이 감염병으로 검역관리지역에 지정된 나라가 없습니다 "
                    "(검역감염병이 아니거나 현재 지정 없음).</div>", unsafe_allow_html=True)
    else:
        st.markdown(f"<div class='kd-kpi-num' style='font-size:2.2rem'>{len(where)}<small>개국</small></div>"
                    + "".join(f"<span class='kd-chip'>{esc(c.name_ko)}</span>" for c in where[:24])
                    + (f"<span class='kd-muted'> 외 {len(where) - 24}개국</span>" if len(where) > 24 else ""),
                    unsafe_allow_html=True)
        st.caption(f"{w.as_of} 기준 · 질병관리청 검역관리지역정보 ({license_label('quarantine_area')})")

# ---- 연령·성별 인포그래픽 --------------------------------------------------------------------
found = [(sid, s.diseases[n]) for sid, s in stats.sets.items() for n in s.diseases
         if s.diseases[n].disease_id == did] if stats.loaded else []
st.markdown("<div class='kd-section-title'>누가 많이 걸릴까? · 연령별·성별 신고</div>", unsafe_allow_html=True)
if not found:
    st.info("이 감염병은 연령별·성별 통계 자료가 없습니다." if stats.loaded else
            "연령별 통계를 아직 묶지 않았습니다 (python -m scripts.build_age_stats).")
else:
    if len(found) > 1:
        sid = st.radio("자료", [s for s, _ in found], horizontal=True, key="detail_set",
                       format_func=lambda s: stats.sets[s].label)
        ad = dict(found)[sid]
    else:
        sid, ad = found[0]
    aset, p = stats.sets[sid], profile(ad, stats.age_groups)
    counts = {a: ad.count(a) for a in stats.age_groups}
    top3 = sorted(counts, key=lambda a: -counts[a])[:3]
    kpis = [(f"{p['total']:,}", "신고 건수"), (p["peak_age"], "가장 많은 연령대"),
            (f"{p['kids_share'] * 100:.0f}%", "10세 미만"), (f"{p['old_share'] * 100:.0f}%", "65세 이상")]
    m = p["male_share"] * 100
    peak = max(counts.values()) or 1
    strip = "".join(
        f"<div class='kd-age-cell' style='--i:{i};--a:{0.12 + 0.88 * counts[a] / peak:.2f}' "
        f"title='{a} {counts[a]:,}건'><b>{a.replace('세 이상', '+').replace('세', '')}</b>"
        f"<span>{counts[a] / max(p['known'], 1) * 100:.0f}%</span></div>" for i, a in enumerate(stats.age_groups))
    with st.container(key=f"detail_info_{did}_{sid}"):     # 감염병·자료가 바뀌면 애니메이션 다시 재생
        st.markdown(
            "<div class='kd-kpis'>" + "".join(
                f"<div class='kd-kpi' style='--i:{i}'><div class='kd-kpi-num'>{esc(v)}</div>"
                f"<div class='kd-kpi-label'>{esc(lbl)}</div></div>" for i, (v, lbl) in enumerate(kpis)) + "</div>"
            + "<div class='kd-split-label'><span>남 " + f"{m:.0f}%</span><span>여 {100 - m:.0f}%</span></div>"
            + f"<div class='kd-split'><div class='kd-split-m' style='--w:{m:.1f}%'></div>"
            + f"<div class='kd-split-f' style='--w:{100 - m:.1f}%'></div></div>"
            + "<div class='kd-card-title' style='margin-top:1.1rem'>나이별 비중 (진할수록 많음)</div>"
            + f"<div class='kd-age-strip'>{strip}</div>"
            + f"<p class='kd-insight'>{esc(d.display_name)} 신고는 <b>{' · '.join(top3)}</b> 순으로 많고, "
            + f"가장 많은 {esc(p['peak_age'])}가 전체의 {p['peak_share'] * 100:.0f}%를 차지합니다.</p>",
            unsafe_allow_html=True)
        with st.container(key="pyramid"):
            st.plotly_chart(pyramid(ad, stats.age_groups), key=f"pyr_{did}_{sid}", config=CHART_CONFIG)
    st.caption(f"출처: {stats.source} ({aset.note}{', ' + aset.period if aset.period else ''})"
               + ("" if aset.verified else " · 감염병 이름은 파일의 열 순서로 추정")
               + f". 신고 건수이며 인구 대비 발생률이 아닙니다. {stats_terms()}.")

# ---- 유행 시기 (자료가 있을 때) -----------------------------------------------------------
trend = next(((t, it) for t in stats.trends.values() for it in t.items.values() if it.disease_id == did), None) \
    if stats.loaded else None
if trend:
    t, it = trend
    sm = trend_summary(t, it)
    st.markdown("<div class='kd-section-title'>언제 유행할까?</div>"
                + (f"<div class='kd-sub'>보통 <b>{sm['typical_month']} 전후</b>에 가장 많습니다 · {esc(t.label)}</div>"
                   if sm["typical_month"] else ""), unsafe_allow_html=True)
    with st.container(key="agetrend"):
        st.plotly_chart(trend_lines(t, it), key=f"detail_trend_{did}", config=CHART_CONFIG)
    st.caption(f"단위: {t.unit}")

# ---- 챗봇에게 물어보기 (이 페이지 안에서 대화) ------------------------------------------------
if NO_CHAT:
    st.caption("⚠️ " + DISCLAIMER)
    st.stop()
st.markdown("<div class='kd-section-title'>💬 챗봇에게 물어보기</div>", unsafe_allow_html=True)
history = ss.setdefault("detail_chat", {}).setdefault(did, [])
with st.container(key="detail_chat"):
    for msg in history:
        show_message(msg, rt, show_disease=False)
    box = st.container()
    with st.container(key="big_search"):
        with st.form(f"detail_ask_{did}", border=False, clear_on_submit=True):
            c = st.columns([12, 1], vertical_alignment="center", gap="small")
            q = c[0].text_input("질문", placeholder=f"{d.display_name}에 대해 물어보세요  (예: 초기 증상과 예방법은?)",
                                label_visibility="collapsed")
            sent = c[1].form_submit_button("→", width="stretch")
    if sent and q.strip():
        ask(q.strip(), did, rt, history, box, show_disease=False)
    if history:
        st.button("이 대화 지우기", key="clear_detail_chat", type="tertiary",
                  on_click=lambda: ss["detail_chat"].pop(did, None))
st.caption("⚠️ " + DISCLAIMER)
