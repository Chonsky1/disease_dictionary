"""법정감염병 안내 도우미 - Streamlit 화면.

레이아웃 (한 페이지 + 상단 내비게이션, 다크 테마):
  [로고] [나라별 알아보기] [감염병 질문하기] [연령별 알아보기]   ← 누르면 해당 섹션으로 이동
  제목 / 면책 안내
  ① 나라별 주의 감염병: 돌릴 수 있는 지구본 + 나라 패널(천천히 나타남)
  ② 감염병 질문하기: 질의응답 | 이런 감염병 찾기  (F2009, F2011, F2012)
  ③ 연령별 알아보기: 연령대별 많이 신고된 감염병 | 감염병별 연령 분포 | 유행 시기 (차트 + 정보 창)

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
from service.age import fmt_value, load_age_stats, profile, top_for_age, trend_summary  # noqa: E402
from service.countries import country_from_event, country_names, load_watch  # noqa: E402
from service.explore import find_diseases  # noqa: E402
from service.runtime import DISCLAIMER, build_runtime, setup_logging  # noqa: E402
from ui.age_view import age_sex_bar, sari_bar, top_bar, trend_lines  # noqa: E402
from ui.map_view import HIGH, LOW, NEUTRAL, SELECTED, build_world_map  # noqa: E402
from ui.styles import CSS, NAV_HTML  # noqa: E402

log = logging.getLogger("kdca_rag")
st.set_page_config(page_title="법정감염병 안내 도우미", page_icon="🩺", layout="wide",
                   initial_sidebar_state="collapsed")
setup_logging()
st.markdown(CSS + NAV_HTML, unsafe_allow_html=True)

VIEW_CHAT, VIEW_FIND = "질의응답", "이런 감염병 찾기"
COUNTRY_WATCH = paths.PROCESSED / "country_watch.json"
AGE_STATS = paths.PROCESSED / "age_stats.json"
# 지도 경계 파일을 CDN 대신 앱과 함께 둔 파일로 받는다 (ui/static/topojson, 정적 파일 제공 필요)
MAP_CONFIG = {"displayModeBar": False, "scrollZoom": False, "topojsonURL": "app/static/topojson/"}
HOME_ISO = "KOR"


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
    st.toast(f"'{rt.by_id[did].display_name}'을(를) 골랐어요. '감염병 질문하기'에서 질문하세요.")


def on_country_select() -> None:
    ss["country"] = ss["country_select"]


# ---- 제목 ---------------------------------------------------------------------------
anchor("top")
st.title("법정감염병 안내 도우미")
st.caption(DISCLAIMER)


# ---- ① 나라별 주의 감염병 (질병관리청 검역관리지역정보) - 맨 위 ----------------------------
# 고를 수 있는 나라 = 국가명 변환표의 모든 나라 (지정 없는 나라도 눌러서 '지정 없음' 확인) + API 나라 이름
choices: dict[str, str] = country_names(paths.CONFIG / "country_names_ko.json")
choices.update({k: w.name_ko for k, w in watch.countries.items()})
choice_keys = sorted(choices, key=lambda k: (k not in watch.countries, choices[k]))  # 지정 국가 먼저, 가나다순


def country_panel(key: str | None) -> None:
    if not key:
        st.markdown("<div class='kd-country-name'>나라를 골라 주세요</div>", unsafe_allow_html=True)
        st.markdown("<div class='kd-muted'>지구본을 끌어서 돌리고, 나라를 누르면 그 나라에서 주의해야 할 "
                    "검역감염병이 나타납니다.</div>", unsafe_allow_html=True)
        if watch.loaded:
            busiest = sorted(watch.countries.values(), key=lambda w: (-len(w.diseases), w.name_ko))[:6]
            st.markdown("<div class='kd-muted' style='margin-top:.8rem'>지정 감염병이 많은 나라</div>"
                        + "".join(f"<span class='kd-chip'>{w.name_ko} {len(w.diseases)}</span>" for w in busiest),
                        unsafe_allow_html=True)
        return
    st.markdown(f"<div class='kd-country-name'>{choices.get(key, key)}</div>", unsafe_allow_html=True)
    if key == HOME_ISO:
        st.markdown("국내 법정감염병 133종의 급수·신고 기준과 설명은 아래 **감염병 질문하기**에서 확인할 수 있어요.")
        return
    if not watch.loaded:
        st.info("검역관리지역 정보를 아직 수집하지 않았습니다. 터미널에서 "
                "`python -m scripts.fetch_quarantine`을 실행하세요.")
        return
    w = watch.countries.get(key)
    if not w or not w.diseases:
        st.success(f"{watch.as_of} 기준, 이 나라는 검역관리지역으로 지정된 검역감염병이 없습니다.")
    else:
        st.markdown(f"<div class='kd-alert'>주의가 필요해요 · 검역감염병 {len(w.diseases)}종</div>",
                    unsafe_allow_html=True)
        for i, item in enumerate(w.diseases):
            with st.container(key=f"cwrow{min(i, 11)}_{i}_{key}"):   # 줄마다 조금씩 늦게 나타나게 (styles.py)
                row = st.columns([3, 2], vertical_alignment="center")
                row[0].markdown(f"**{item['name']}**" + (f"  \n<span class='kd-muted'>{item['note']}</span>"
                                                         if item.get("note") else ""), unsafe_allow_html=True)
                did = item.get("disease_id")
                if did in rt.by_id:
                    row[1].button("질문하기", key=f"ask_{key}_{i}", on_click=select_disease, args=(did,),
                                  width="stretch")
    src = f"[{watch.source}]({watch.source_url})" if watch.source_url else watch.source
    st.caption(f"기준일 {watch.as_of} · 출처 {src}  \n검역관리지역: 검역법에 따라 WHO·현지 공관 등의 감염병 발생 "
               "정보를 기준으로 지정한 지역. 귀국 후 의심 증상이 있으면 1339에 문의하세요.")


anchor("country")
sub = (f"질병관리청 검역관리지역 {watch.as_of} 기준 · 지정 국가 {len(watch.countries)}개국. "
       if watch.loaded else "검역관리지역 정보 수집 전입니다 (python -m scripts.fetch_quarantine). ")
st.markdown("<div class='kd-section-title'>나라별 주의 감염병 알아보기</div>"
            f"<div class='kd-sub'>{sub}출국 전 확인용 일반 정보이며, 최신 상황은 질병관리청 안내를 확인하세요.</div>",
            unsafe_allow_html=True)

picked = country_from_event(ss.get("world_map"), set(choices))
if picked and picked != ss["_map_pick"]:  # 지도를 새로 눌렀을 때만 반영 (목록 선택을 덮어쓰지 않게)
    ss["country"] = ss["_map_pick"] = picked

left, right = st.columns([2.3, 1.2], gap="medium")
with left:
    with st.container(key="map_card"):
        st.plotly_chart(build_world_map(watch, ss["country"], choices), key="world_map", on_select="rerun",
                        selection_mode="points", config=MAP_CONFIG)
    st.markdown("<div class='kd-legend'>"
                f"<span><i class='kd-dot' style='background:linear-gradient(90deg,{LOW},{HIGH})'></i>검역관리지역 (밝을수록 감염병 많음)</span>"
                f"<span><i class='kd-dot' style='background:{NEUTRAL}'></i>지정 없음</span>"
                f"<span><i class='kd-dot' style='background:{SELECTED}'></i>선택한 나라</span>"
                "<span>· 끌어서 돌리기</span></div>",
                unsafe_allow_html=True)
    ss["country_select"] = ss["country"] if ss["country"] in choices else None
    st.selectbox("나라 직접 고르기", choice_keys, key="country_select", index=None,
                 placeholder="지구본을 누르거나 여기서 고르세요 (작은 나라 포함)",
                 format_func=lambda k: choices[k], on_change=on_country_select)
with right:
    # 나라가 바뀔 때마다 새 컨테이너로 그려 '천천히 나타나는' 애니메이션이 다시 재생되게 한다
    with st.container(key=f"country_panel_{ss['country'] or 'none'}"):
        country_panel(ss["country"])


# ---- ② 감염병 질문하기 ----------------------------------------------------------------
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


# ---- ③ 연령별 알아보기 (감염병포털 통계, scripts/build_age_stats.py) ---------------------------
MODE_AGE, MODE_DISEASE, MODE_TREND = "연령대별 많이 신고된 감염병", "감염병별 연령 분포", "유행 시기"
SARI_KEY = "sari"
CHART_CONFIG = {"displayModeBar": False, "scrollZoom": False}
stats = load_age_stats(AGE_STATS)
ss.setdefault("age_mode", MODE_AGE)
ss.setdefault("age_pick", None)
ss.setdefault("_age_bar_pick", None)


def ask_button(did: str, key: str) -> None:
    if did in rt.by_id:
        st.button(f"'{rt.by_id[did].display_name}' 질문하기", key=key, on_click=select_disease, args=(did,),
                  width="stretch")


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


anchor("age")
sub = (f"{stats.source} 통계 · 신고 건수 기준이며 인구 대비 발생률이 아닙니다. "
       if stats.loaded else "통계 파일을 아직 묶지 않았습니다. ")
st.markdown("<div class='kd-section-title'>연령별 알아보기</div>"
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

st.divider()
st.caption("⚠️ " + DISCLAIMER)
