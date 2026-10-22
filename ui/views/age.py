"""연령별 알아보기 페이지: 연령대별 많이 신고된 감염병 | 감염병별 연령 분포 | 유행 시기 (차트 + 정보 창).

자료: scripts/build_age_stats.py가 만든 data/processed/age_stats.json (감염병포털 통계).
감염병마다 '상세보기'로 상세 페이지에 이어진다.
"""
from __future__ import annotations

import streamlit as st

from service.age import fmt_value, profile, top_for_age, trend_summary
from service.countries import country_from_event
from service.runtime import DISCLAIMER
from ui.age_view import age_sex_bar, sari_bar, top_bar, trend_lines
from ui.common import CHART_CONFIG, age_stats, detail_button, runtime

rt = runtime()
ss = st.session_state

MODE_AGE, MODE_DISEASE, MODE_TREND = "연령대별 많이 신고된 감염병", "감염병별 연령 분포", "유행 시기"
SARI_KEY = "sari"
CHART_CONFIG = {"displayModeBar": False, "scrollZoom": False}
stats = age_stats()
ss.setdefault("age_mode", MODE_AGE)
ss.setdefault("age_pick", None)
ss.setdefault("_age_bar_pick", None)


def ask_button(did: str, key: str) -> None:
    """감염병 상세보기로 (설명·연령 인포그래픽·챗봇 검색창)."""
    if did in rt.by_id:
        detail_button(did, key, f"'{rt.by_id[did].display_name}' 상세보기", rt)


def show_distribution(set_id: str, name: str) -> None:
    ss["age_mode"] = MODE_DISEASE
    ss["age_disease"] = f"{set_id}|{name}"


def set_caption(aset) -> str:
    bits = [f"출처: {stats.source} ({aset.note})"]
    if aset.period:
        bits.append(f"기간 {aset.period}")
    if not aset.verified:
        bits.append("감염병 이름은 파일의 열 순서로 추정")
    return " · ".join(b for b in bits if b)


def pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def age_view() -> None:
    sets = {k: v for k, v in stats.sets.items() if v.diseases}
    if not sets:
        st.info("연령별 자료가 없습니다.")
        return
    c = st.columns([2.4, 1.5, 1.2, 1.4], vertical_alignment="bottom")
    ss.setdefault("age_group", stats.age_groups[0])
    age = c[0].select_slider("연령대", stats.age_groups, key="age_group")
    set_id = c[1].selectbox("자료", list(sets), key="age_set", format_func=lambda k: sets[k].label)
    sex = c[2].radio("성별", ["전체", "남", "여"], key="age_sex", horizontal=True)
    by = c[3].radio("정렬", ["count", "share"], key="age_sort", horizontal=True,
                    format_func=lambda k: "신고 건수" if k == "count" else "연령대 비중")
    aset = sets[set_id]
    rows = top_for_age(aset, age, sex, by)
    names = [r["name"] for r in rows]

    chart_key = f"age_top_{set_id}_{age}_{sex}_{by}"   # 조건이 바뀌면 새 차트 → 막대가 자라는 애니메이션 재생
    picked = country_from_event(ss.get(chart_key), set(names))   # 막대 customdata[0] = 감염병 이름
    if picked and picked != ss["_age_bar_pick"]:
        ss["age_pick"] = ss["_age_bar_pick"] = picked
    if ss["age_pick"] not in names:
        ss["age_pick"] = names[0] if names else None

    left, right = st.columns([2.6, 1.15], gap="medium")
    with left:
        if not rows:
            st.info(f"{age} {'' if sex == '전체' else sex + ' '}신고 건수가 있는 감염병이 없습니다.")
        else:
            with st.container(key="agebar_h"):
                st.plotly_chart(top_bar(rows, ss["age_pick"], by), key=chart_key, on_select="rerun",
                                selection_mode="points", config=CHART_CONFIG)
            st.caption(("연령대 비중 = 이 연령대 건수 ÷ 그 감염병 전체 건수 (전체 30건 이상만). " if by == "share" else "")
                       + "막대를 누르면 오른쪽에 자세히 보여 줍니다.")
    with right, st.container(key=f"age_panel_a_{ss['age_pick']}"):
        st.markdown(f"<div class='kd-country-name'>{age}{'' if sex == '전체' else ' · ' + sex}</div>",
                    unsafe_allow_html=True)
        r = next((x for x in rows if x["name"] == ss["age_pick"]), None)
        if not r:
            st.markdown("<div class='kd-muted'>왼쪽 막대를 눌러 감염병을 고르세요.</div>", unsafe_allow_html=True)
            return
        d = r["disease"]
        p = profile(d, stats.age_groups)
        grade = f"제{d.grade}급 · " if d.grade else ""
        st.markdown(f"**{d.name}**  \n<span class='kd-muted'>{grade}{aset.label}</span>", unsafe_allow_html=True)
        st.markdown(f"- 이 연령대 **{r['count']:,}건** (이 감염병 전체의 {pct(r['share'])})\n"
                    f"- 가장 많은 연령대: **{p['peak_age']}** ({pct(p['peak_share'])})\n"
                    f"- 남 {pct(p['male_share'])} · 여 {pct(1 - p['male_share'])}")
        st.button("연령 분포 자세히 보기", key="age_to_dist", on_click=show_distribution, args=(set_id, d.name),
                  width="stretch")
        ask_button(d.disease_id, "age_ask")
        st.caption(set_caption(aset) + ". 신고 건수이며 인구 대비 발생률이 아닙니다.")


def disease_view() -> None:
    options = [f"{sid}|{n}" for sid, s in stats.sets.items() for n in s.diseases]
    if stats.sari_seasons:
        options.append(SARI_KEY)
    if not options:
        st.info("연령별 자료가 없습니다.")
        return

    def label(k: str) -> str:
        if k == SARI_KEY:
            return "중증급성호흡기감염증 입원환자 (표본감시, 절기별)"
        sid, n = k.split("|", 1)
        return f"{n} · {stats.sets[sid].label}"
    if ss.get("age_disease") not in options:
        ss["age_disease"] = options[0]
    c = st.columns([3, 1.4], vertical_alignment="bottom")
    key = c[0].selectbox("감염병", options, key="age_disease", format_func=label)
    left, right = st.columns([2.6, 1.15], gap="medium")
    if key == SARI_KEY:
        seasons = list(stats.sari_seasons)
        season = c[1].selectbox("절기", seasons, index=len(seasons) - 1, key="sari_season")
        by_age = stats.sari_seasons[season]
        with left:
            with st.container(key="agebar_v"):
                st.plotly_chart(sari_bar(by_age), key=f"sari_{season}", config=CHART_CONFIG)
        with right, st.container(key=f"age_panel_s_{season}"):
            tot = sum(by_age.values())
            peak = max(by_age, key=by_age.get) if tot else "-"
            young = sum(v for a, v in by_age.items() if a in ("0세", "1-6세"))
            st.markdown(f"<div class='kd-country-name'>중증급성호흡기감염증</div>", unsafe_allow_html=True)
            st.markdown(f"<span class='kd-muted'>{season}절기 입원환자 (표본감시 병원)</span>", unsafe_allow_html=True)
            st.markdown(f"- 입원환자 **{tot:,}명**\n- 가장 많은 연령대: **{peak}** ({pct(by_age.get(peak, 0) / tot if tot else 0)})\n"
                        f"- 6세 이하 {pct(young / tot if tot else 0)} · 65세 이상 {pct(by_age.get('65세 이상', 0) / tot if tot else 0)}")
            st.caption(f"{stats.source} · 중증급성호흡기감염증(SARI) 표본감시. 절기 = 그해 36주 ~ 다음 해 35주. "
                       "진행 중인 절기는 일부 주만 집계됩니다.")
        return
    sid, name = key.split("|", 1)
    aset, d = stats.sets[sid], stats.sets[sid].diseases[name]
    p = profile(d, stats.age_groups)
    with left:
        with st.container(key="agebar_v"):
            st.plotly_chart(age_sex_bar(d, stats.age_groups, p["peak_age"]), key=f"age_dist_{key}", config=CHART_CONFIG)
    with right, st.container(key=f"age_panel_d_{key}"):
        st.markdown(f"<div class='kd-country-name'>{d.name}</div>", unsafe_allow_html=True)
        grade = f"제{d.grade}급 · " if d.grade else ""
        st.markdown(f"<span class='kd-muted'>{grade}{aset.label}</span>", unsafe_allow_html=True)
        st.markdown(f"- 신고 **{p['total']:,}건**\n- 가장 많은 연령대: **{p['peak_age']}** ({pct(p['peak_share'])})\n"
                    f"- 10세 미만 {pct(p['kids_share'])} · 65세 이상 {pct(p['old_share'])}\n"
                    f"- 남 {pct(p['male_share'])} · 여 {pct(1 - p['male_share'])}")
        ask_button(d.disease_id, "dist_ask")
        st.caption(set_caption(aset) + ". 연령대마다 인구가 달라 건수만으로 '잘 걸리는 정도'를 단정할 수 없습니다.")


def trend_view() -> None:
    trends = stats.trends
    if not trends:
        st.info("유행 시기 자료가 없습니다.")
        return
    c = st.columns([2.2, 2.2], vertical_alignment="bottom")
    tid = c[0].selectbox("자료", list(trends), key="trend_id", format_func=lambda k: trends[k].label)
    t = trends[tid]
    items = list(t.items)
    if ss.get("trend_item") not in items:
        ss["trend_item"] = items[0]
    item_name = c[1].selectbox("항목", items, key="trend_item", disabled=len(items) == 1)
    item = t.items[item_name]
    sm = trend_summary(t, item)
    left, right = st.columns([2.6, 1.15], gap="medium")
    with left:
        with st.container(key="agetrend"):
            st.plotly_chart(trend_lines(t, item), key=f"trend_{tid}_{item_name}", config=CHART_CONFIG)
        st.caption(f"단위: {t.unit}. " + ("절기 = 그해 36주 ~ 다음 해 35주." if t.kind == "season" else "주 = 그해 몇째 주."))
    with right, st.container(key=f"age_panel_t_{tid}_{item_name}"):
        st.markdown(f"<div class='kd-country-name'>{item_name}</div>", unsafe_allow_html=True)
        st.markdown(f"<span class='kd-muted'>{t.label}</span>", unsafe_allow_html=True)
        lines = []
        if sm["typical_month"]:
            lines.append(f"- 보통 **{sm['typical_month']} 전후**에 가장 많음")
        if sm["latest"]:
            lt = sm["latest"]
            when = f"{lt['key']}절기 {lt['week']}" if t.kind == "season" else f"{lt['key']}년 {lt['week']}"
            lines.append(f"- 최근 집계: {when} **{fmt_value(lt['value'])}**")
        st.markdown("\n".join(lines) or "자료가 비어 있습니다.")
        if sm["peaks"]:
            st.markdown("<span class='kd-muted'>해마다 가장 많았던 주</span>", unsafe_allow_html=True)
            st.markdown("  \n".join(f"{p['key']}{'절기' if t.kind == 'season' else '년'} · {p['week']} · {fmt_value(p['value'])}"
                                    + ("" if p["complete"] else " (진행 중)") for p in reversed(sm["peaks"][-5:])))
        ask_button(item.disease_id, "trend_ask")
        st.caption(f"{stats.source} 표본감시 · 가장 최근 주는 '집계 중'일 수 있습니다.")


sub = (f"{stats.source} 통계 · 신고 건수 기준이며 인구 대비 발생률이 아닙니다. "
       if stats.loaded else "통계 파일을 아직 묶지 않았습니다. ")
st.markdown("<div class='kd-page-title'>연령별 알아보기</div>"
            f"<div class='kd-sub'>{sub}나이에 따라 많이 신고되는 감염병과 유행 시기를 보여 줍니다.</div>",
            unsafe_allow_html=True)
if not stats.loaded:
    with st.container(key="age_card"):
        st.info("감염병포털에서 내려받은 통계 파일을 `data/raw/stats/`에 넣고 아래 명령을 실행한 뒤 새로고침하세요.")
        st.code("python -m scripts.build_age_stats", language="bash")
else:
    st.radio("연령별 화면", [MODE_AGE, MODE_DISEASE, MODE_TREND], key="age_mode", horizontal=True,
             label_visibility="collapsed")
    with st.container(key="age_card"):
        {MODE_AGE: age_view, MODE_DISEASE: disease_view, MODE_TREND: trend_view}[ss["age_mode"]]()

st.caption("⚠️ " + DISCLAIMER)
