"""화면 스타일 (다크 테마): 목차 바, 홈 인트로, 검색창, 지구본·나라 팝업, 상세보기 인포그래픽, 애니메이션.

Streamlit의 st-key-<key> 클래스로 컨테이너를 꾸민다. 키가 바뀌면 새 요소로 그려지므로
나라 팝업(country_panel_<나라>), 상세 인포그래픽(detail_info_<감염병>), 차트(key에 조건 포함)는
바뀔 때마다 애니메이션이 다시 재생된다. 움직임 줄이기 설정(prefers-reduced-motion)이면 애니메이션을 끈다.
"""

# 나라 팝업의 감염병 줄: 0.7초 뒤부터 한 줄씩 0.18초 간격으로 (최대 12줄)
_ROW_DELAYS = "\n".join(
    f'[class*="st-key-cwrow{i}_"] {{ animation-delay: {0.7 + 0.18 * i:.2f}s; }}' for i in range(12))

CSS = """
<style>
:root {
  --kd-bg:#0e141b; --kd-surface:#16202a; --kd-surface-2:#1b2733; --kd-line:#2a3846;
  --kd-ink:#e6edf3; --kd-muted:#93a3b3; --kd-teal:#45dcc8; --kd-teal-dim:#1f9e8f; --kd-accent:#e5603f;
  --kd-male:#4a8cc7; --kd-female:#d06a4e;
}
html { scroll-behavior: smooth; }
/* Streamlit 기본 헤더(툴바)는 목차 바와 겹치므로 숨긴다. 다시 실행은 키보드 R. */
[data-testid="stHeader"] { display: none; }
.block-container { padding-top: 4.4rem; max-width: 1180px; }

/* ---- 목차 바 (항상 보임): 로고(→ 홈) + 테두리 없는 글자 목차 --------------------------------- */
.st-key-kd_nav { position: fixed; top: 0; left: 0; right: 0; z-index: 1000000; width: 100% !important;
  padding: 10px max(16px, calc((100vw - 1180px) / 2 + 16px)); gap: 26px !important;
  background: rgba(14,20,27,.92); backdrop-filter: blur(8px); border-bottom: 1px solid var(--kd-line); }
.st-key-kd_nav [data-testid="stPageLink"] a { border: none !important; background: none !important; padding: 4px 2px;
  box-shadow: none !important; }
.st-key-kd_nav [data-testid="stPageLink"] a p, .st-key-kd_nav [data-testid="stPageLink"] a span {
  color: var(--kd-muted) !important; font-weight: 600; font-size: .95rem; }
.st-key-kd_nav [data-testid="stPageLink"] a:hover p { color: var(--kd-teal) !important; }
.st-key-kd_logo { margin-right: 18px; }
.st-key-kd_logo [data-testid="stPageLink"] a p { color: var(--kd-ink) !important; font-weight: 800 !important; font-size: 1.02rem !important; }
.st-key-kd_logo [data-testid="stPageLink"] a::before { content: "+"; display:inline-flex; align-items:center; justify-content:center;
  width:28px; height:28px; margin-right: 8px; border-radius:8px; background: var(--kd-accent); color:#fff; font-weight:800; }

/* ---- 공통 글자·카드 ----------------------------------------------------------------- */
.kd-page-title { font-size: 1.7rem; font-weight: 800; color: var(--kd-ink); margin: .6rem 0 .4rem; }
.kd-section-title { font-size: 1.25rem; font-weight: 700; color: var(--kd-ink); margin: 1.8rem 0 0.6rem; }
.kd-card-title { font-size: 1.02rem; font-weight: 700; color: var(--kd-ink); margin-bottom: .5rem; }
.kd-sub { color: var(--kd-muted); font-size: 0.9rem; margin-top:-0.2rem; }
.kd-muted { color: var(--kd-muted); font-size:.85rem; }
.st-key-chat_card, .st-key-age_card, .st-key-detail_about, .st-key-detail_world, .st-key-chat_summary {
  border: 1px solid var(--kd-line); border-radius: 22px; padding: 18px 20px 14px;
  background: var(--kd-surface); box-shadow: 0 8px 24px rgba(0,0,0,.25); }
.st-key-chat_summary { background: var(--kd-surface-2); position: sticky; top: 70px; }
.kd-chip { display:inline-block; margin: 0 6px 6px 0; padding: 3px 10px; border-radius: 999px;
  background: rgba(69,220,200,.12); color: var(--kd-teal); font-size: .85rem; }
.kd-chip-strong { background: rgba(69,220,200,.18); font-weight: 700; }
.kd-alert { display:inline-block; margin: .3rem 0 .6rem; padding: 4px 12px; border-radius: 999px;
  background: rgba(229,96,63,.16); color: #ff9a80; font-weight: 700; font-size: .9rem; }
.kd-legend { display:flex; gap:16px; flex-wrap: wrap; justify-content: center; font-size:.82rem; color: var(--kd-muted); margin:8px 2px 4px; }
.kd-dot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:5px; vertical-align:middle; }

/* ---- 홈: 제목(왼쪽 위) → 안내 → 검색창 → 지구본(가운데) ---------------------------------- */
.kd-hero-title { font-size: 1.9rem !important; font-weight: 800; margin: .2rem 0 .2rem !important; padding: 0 !important;
  color: var(--kd-ink); letter-spacing: -.02em; }
.kd-hero-note { color: var(--kd-muted); font-size: .86rem; max-width: 760px; margin: 0 0 1.4rem; }
.kd-globe-sub { text-align: center; color: var(--kd-muted); font-size: .85rem; margin: .4rem 0 .2rem; }
.st-key-home_search [data-testid="stForm"] { max-width: 720px; margin: 0 auto; }
.st-key-home_search input { border-radius: 999px !important; height: 54px; padding-left: 24px !important;
  font-size: 1.05rem !important; background: #1b2733 !important; }
.st-key-home_search [data-baseweb="input"], .st-key-home_search [data-baseweb="base-input"] {
  border-radius: 999px !important; background: #1b2733 !important; border-color: #33475a !important;
  box-shadow: 0 6px 22px rgba(0,0,0,.35); }
.st-key-home_search [data-baseweb="input"]:focus-within { border-color: var(--kd-teal) !important; }
.st-key-home_search button { border-radius: 999px !important; height: 54px; font-size: 1.2rem; }
.st-key-map_card { border-radius: 22px; overflow: hidden; cursor: grab;
  background: radial-gradient(circle at 50% 50%, rgba(31,90,110,.35) 0%, rgba(12,22,32,0) 58%); }
.st-key-map_card:active { cursor: grabbing; }
.st-key-globe_center { max-width: 900px; margin: 0 auto; }
[class*="st-key-country_panel"] { border: 1px solid #355066; border-radius: 22px; padding: 18px 18px 10px;
  background: linear-gradient(180deg, #1a2733 0%, #16202a 100%); min-height: 560px;
  box-shadow: 0 16px 40px rgba(0,0,0,.45); }
[class*="st-key-country_panel"] button[kind="secondary"]:has(p:only-child) { min-height: 0; }
.kd-country-name { font-size: 1.5rem; font-weight: 700; color: var(--kd-ink); margin-bottom: .2rem; }

/* ---- 감염병 질문하기·답변 카드 ----------------------------------------------------------- */
.kd-target-name { font-size: 2.4rem; font-weight: 800; color: var(--kd-ink); letter-spacing: -.02em; line-height: 1.15; }
.kd-target-meta { margin: .45rem 0 .2rem; }
.st-key-chat_card { padding: 10px 14px 12px; }
.st-key-chat_card [data-testid="stChatMessage"] { background: transparent; padding: .4rem .2rem; }
.st-key-chat_card [data-testid="stChatMessage"] p, .st-key-detail_chat [data-testid="stChatMessage"] p { font-size: 1rem; line-height: 1.65; }
.kd-core { background: rgba(69,220,200,.10); border: 1px solid rgba(69,220,200,.30); border-radius: 14px;
  padding: 12px 16px; margin: .2rem 0 .7rem; font-size: 1.05rem; line-height: 1.6; color: var(--kd-ink); }
[class*="st-key-cat_"] { background: var(--kd-surface-2); border: 1px solid var(--kd-line); border-radius: 14px;
  padding: 10px 14px 20px; min-height: 100%; height: auto !important; overflow: visible; animation: kdFade .5s ease-out both; }
[class*="st-key-cat_"] ul { margin: 0; padding-left: 1.1rem; }
[class*="st-key-cat_"] [data-testid="stMarkdownContainer"] { overflow: visible; }
[class*="st-key-cat_"] li { font-size: .95rem; line-height: 1.55; margin-bottom: .15rem; }
.kd-cat-title { font-weight: 700; color: var(--kd-teal); font-size: .95rem; margin-bottom: .25rem; }
.kd-cite { color: var(--kd-muted); font-size: .75rem; white-space: nowrap; }
.kd-timing { color: var(--kd-muted); font-size: .78rem; margin: .3rem 0 .2rem; }
.kd-src { display: inline-block; font-size: .72rem; font-weight: 600; padding: .05rem .45rem; border-radius: 999px;
          margin-right: .35rem; border: 1px solid currentColor; white-space: nowrap; }
.kd-src-meta { color: #f2b84b; } .kd-src-wiki { color: #7cc4ff; }
.kd-src-public_api { color: #7ee0a8; } .kd-src-public_stats { color: #d59bff; }
.st-key-chat_summary .stMarkdown p, .st-key-chat_summary li { font-size: .92rem; line-height: 1.55; }
.st-key-detail_chat { max-width: 980px; }
[data-testid="InputInstructions"] { display: none !important; }   /* 'Press Enter to submit form' 숨김 */
.st-key-big_search [data-testid="stTextInputRootElement"], .st-key-home_search [data-testid="stTextInputRootElement"] {
  border-radius: 999px !important; overflow: hidden; }
.st-key-big_search input { border-radius: 999px !important; height: 66px; padding-left: 28px !important;
  font-size: 1.15rem !important; background: #1b2733 !important; }
.st-key-big_search [data-testid="stTextInputRootElement"], .st-key-big_search [data-baseweb="input"],
.st-key-big_search [data-baseweb="base-input"] { height: 66px !important; min-height: 66px; }
.st-key-home_search [data-testid="stTextInputRootElement"], .st-key-home_search [data-baseweb="input"],
.st-key-home_search [data-baseweb="base-input"] { height: 54px !important; min-height: 54px; }
.st-key-big_search input, .st-key-home_search input { height: 100% !important; }
.st-key-big_search [data-baseweb="input"], .st-key-big_search [data-baseweb="base-input"] {
  border-radius: 999px !important; background: #1b2733 !important; border-color: #33475a !important;
  box-shadow: 0 8px 26px rgba(0,0,0,.4); }
.st-key-big_search [data-baseweb="input"]:focus-within { border-color: var(--kd-teal) !important; }
.st-key-big_search button { border-radius: 999px !important; height: 66px; font-size: 1.3rem; }

/* ---- 연령별 페이지 ---------------------------------------------------------------- */
[class*="st-key-age_panel"] { border: 1px solid #355066; border-radius: 22px; padding: 16px 16px 8px;
  background: var(--kd-surface-2); min-height: 300px; }
.st-key-age_card .stSlider { padding-right: 12px; }

/* ---- 상세보기 인포그래픽 ------------------------------------------------------------ */
.kd-detail-head { margin: .4rem 0 1.2rem; }
.kd-detail-name { font-size: 2.3rem; font-weight: 800; color: var(--kd-ink); letter-spacing: -.02em; margin-bottom: .4rem; }
.kd-rule { margin-top: .8rem; padding: 10px 14px; border-left: 3px solid var(--kd-teal-dim); background: rgba(31,158,143,.08);
  border-radius: 0 12px 12px 0; color: var(--kd-ink); font-size: .9rem; }
.kd-kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 1.1rem; }
.kd-kpi { background: var(--kd-surface); border: 1px solid var(--kd-line); border-radius: 18px; padding: 16px 18px; }
.kd-kpi-num { font-size: 1.9rem; font-weight: 800; color: var(--kd-teal); line-height: 1.15; }
.kd-kpi-num small { font-size: .9rem; color: var(--kd-muted); margin-left: 4px; font-weight: 600; }
.kd-kpi-label { color: var(--kd-muted); font-size: .85rem; margin-top: 4px; }
.kd-split-label { display: flex; justify-content: space-between; font-weight: 700; margin-bottom: 6px; }
.kd-split-label span:first-child { color: var(--kd-male); } .kd-split-label span:last-child { color: var(--kd-female); }
.kd-split { display: flex; height: 18px; border-radius: 999px; overflow: hidden; background: #22303d; gap: 2px; }
.kd-split-m { background: var(--kd-male); width: var(--w); }
.kd-split-f { background: var(--kd-female); width: var(--w); margin-left: auto; }
.kd-age-strip { display: grid; grid-template-columns: repeat(14, minmax(0, 1fr)); gap: 6px; }
.kd-age-cell { border-radius: 12px; padding: 10px 2px 8px; text-align: center; background: rgba(69,220,200, var(--a));
  border: 1px solid rgba(69,220,200,.18); }
.kd-age-cell b { display: block; font-size: .78rem; color: var(--kd-ink); }
.kd-age-cell span { display: block; font-size: .72rem; color: var(--kd-ink); opacity: .85; }
/* 진한(비중이 큰) 칸은 글자를 어둡게 */
.kd-age-cell[style*="--a:0.6"] *, .kd-age-cell[style*="--a:0.7"] *, .kd-age-cell[style*="--a:0.8"] *,
.kd-age-cell[style*="--a:0.9"] *, .kd-age-cell[style*="--a:1.0"] * { color: #0b1a20; }
.kd-age-cell[style*="--a:0.6"], .kd-age-cell[style*="--a:0.7"], .kd-age-cell[style*="--a:0.8"],
.kd-age-cell[style*="--a:0.9"], .kd-age-cell[style*="--a:1.0"] { box-shadow: 0 0 18px rgba(69,220,200,.35); }
.kd-insight { margin: 1rem 0 .2rem; font-size: 1rem; color: var(--kd-ink); }
.kd-insight b { color: var(--kd-teal); }
@media (max-width: 760px) {
  .kd-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .kd-age-strip { grid-template-columns: repeat(7, minmax(0, 1fr)); }
}

/* ---- 애니메이션 -------------------------------------------------------------------- */
@keyframes kdPanelIn { 0% { opacity: 0; transform: translateX(48px) scale(.96); filter: blur(6px); }
                       70% { opacity: 1; filter: blur(0); } 100% { opacity: 1; transform: none; } }
@keyframes kdGlobeSide { from { transform: translateX(24%); } to { transform: none; } }
@keyframes kdRowIn { from { opacity: 0; transform: translateX(-16px); } to { opacity: 1; transform: none; } }
@keyframes kdFade { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
@keyframes kdUp { from { opacity: 0; transform: translateY(22px); } to { opacity: 1; transform: none; } }
@keyframes kdPop { from { opacity: 0; transform: scale(.6); } to { opacity: 1; transform: none; } }
@keyframes kdW { from { width: 0; } to { width: var(--w); } }
@keyframes kdGrowX { from { transform: scaleX(0); } to { transform: scaleX(1); } }
@keyframes kdGrowY { from { transform: scaleY(0); } to { transform: scaleY(1); } }
@keyframes kdWipe { from { clip-path: inset(0 100% 0 0); } to { clip-path: inset(0 0 0 0); } }

/* 나라를 누르면 지구본이 왼쪽으로 비키고, 팝업이 오른쪽에서 천천히 떠오르며 감염병 줄이 하나씩 나타남 */
.st-key-globe_side { animation: kdGlobeSide 1.2s cubic-bezier(.2,.7,.2,1) both; }
[class*="st-key-country_panel"] { animation: kdPanelIn 1.8s cubic-bezier(.16,.8,.24,1) .15s both; }
[class*="st-key-cwrow"] { animation: kdRowIn .9s ease-out both; }
__ROW_DELAYS__
[class*="st-key-age_panel"] { animation: kdFade .6s ease-out both; }

/* 상세보기: 숫자 카드가 차례로 떠오르고, 남녀 막대가 차오르고, 나이 칸이 하나씩 켜짐 */
[class*="st-key-detail_info"] .kd-kpi { animation: kdUp .8s cubic-bezier(.2,.7,.2,1) both; animation-delay: calc(var(--i) * .15s); }
[class*="st-key-detail_info"] .kd-split > div { animation: kdW 1.4s .5s cubic-bezier(.2,.7,.2,1) both; }
[class*="st-key-detail_info"] .kd-age-cell { animation: kdPop .5s ease-out both; animation-delay: calc(.8s + var(--i) * .07s); }
[class*="st-key-detail_info"] .kd-insight { animation: kdFade .8s 1.8s ease-out both; }
.st-key-detail_about, .st-key-detail_world { animation: kdUp .8s ease-out both; }
.st-key-detail_world { animation-delay: .15s; }
/* 인구 피라미드: 가운데 선에서 남(왼쪽)·여(오른쪽)로 뻗어 나감 */
.st-key-pyramid .barlayer .trace:nth-child(1) .point path { transform-box: fill-box; transform-origin: right center;
  animation: kdGrowX 1.2s .4s cubic-bezier(.2,.7,.2,1) both; }
.st-key-pyramid .barlayer .trace:nth-child(2) .point path { transform-box: fill-box; transform-origin: left center;
  animation: kdGrowX 1.2s .4s cubic-bezier(.2,.7,.2,1) both; }

/* 연령별 막대: 가로 막대는 왼쪽에서, 세로 막대는 바닥에서 자라남. 유행 시기 선은 왼쪽부터 드러남 */
.st-key-agebar_h .barlayer .point path { transform-box: fill-box; transform-origin: left center;
  animation: kdGrowX 1s cubic-bezier(.2,.7,.2,1) both; }
.st-key-agebar_v .barlayer { transform-box: fill-box; transform-origin: center bottom;   /* 쌓은 막대 전체가 함께 */
  animation: kdGrowY 1.1s cubic-bezier(.2,.7,.2,1) both; }
.st-key-agetrend .scatterlayer { animation: kdWipe 2.2s cubic-bezier(.3,.6,.3,1) both; }
.st-key-agebar_h .bartext, .st-key-agebar_v .bartext { animation: kdFade .6s .7s ease-out both; }

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
}
</style>
""".replace("__ROW_DELAYS__", _ROW_DELAYS)

# 홈 인트로 (세션당 한 번): 제목이 왼쪽에 크게 → 왼쪽 위로, 지구본이 오른쪽 절반 → 가운데로,
# 그다음 안내 문구·검색창이 나타난다.
INTRO_CSS = """
<style>
@keyframes kdHeroTitle { 0% { opacity: 0; transform: translateY(30vh) scale(1.7); }
                         12%, 42% { opacity: 1; transform: translateY(30vh) scale(1.7); } 100% { opacity: 1; transform: none; } }
@keyframes kdHeroGlobe { 0% { opacity: 0; transform: translate(39%, -250px) scale(.84); }
                         15%, 42% { opacity: 1; transform: translate(39%, -250px) scale(.84); } 100% { opacity: 1; transform: none; } }
@keyframes kdLate { 0%, 60% { opacity: 0; transform: translateY(14px); } 100% { opacity: 1; transform: none; } }
.kd-hero-title { transform-origin: left top; animation: kdHeroTitle 2.8s cubic-bezier(.6,0,.2,1) both; }
.st-key-globe_center { animation: kdHeroGlobe 2.8s cubic-bezier(.6,0,.2,1) both; }
.kd-hero-note, .st-key-home_search, .kd-globe-sub, .st-key-globe_center .kd-legend,
.st-key-globe_center [data-testid="stSelectbox"] { animation: kdLate 3.2s ease-out both; }
@media (prefers-reduced-motion: reduce) {
  .kd-hero-title, .st-key-globe_center, .kd-hero-note, .st-key-home_search, .kd-globe-sub { animation: none !important; }
}
</style>
"""
