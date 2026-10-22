"""화면 스타일 (다크 테마): 상단 내비게이션, 카드, 지구본·나라 패널, 애니메이션.

Streamlit의 st-key-<key> 클래스로 컨테이너를 꾸민다. 키가 바뀌면 새 요소로 그려지므로
나라 패널(country_panel_<나라>)과 차트(차트 key에 조건 포함)는 바뀔 때마다 애니메이션이 다시 재생된다.
움직임을 줄이도록 설정한 사용자(prefers-reduced-motion)에게는 애니메이션을 끈다.
"""

NAV_HTML = """
<div class="kd-nav">
  <a class="kd-logo" href="#top" target="_self"><span class="kd-logo-mark">+</span>법정감염병 안내</a>
  <nav>
    <a href="#country" target="_self">나라별 알아보기</a>
    <a href="#chat" target="_self">감염병 질문하기</a>
    <a href="#age" target="_self">연령별 알아보기</a>
  </nav>
</div>
"""

# 나라 패널의 감염병 줄: 0.6초 뒤부터 한 줄씩 0.18초 간격으로 (최대 12줄)
_ROW_DELAYS = "\n".join(
    f'[class*="st-key-cwrow{i}_"] {{ animation-delay: {0.6 + 0.18 * i:.2f}s; }}' for i in range(12))

CSS = """
<style>
:root {
  --kd-bg:#0e141b; --kd-surface:#16202a; --kd-surface-2:#1b2733; --kd-line:#2a3846;
  --kd-ink:#e6edf3; --kd-muted:#93a3b3; --kd-teal:#45dcc8; --kd-teal-dim:#1f9e8f; --kd-accent:#e5603f;
}
html { scroll-behavior: smooth; }
/* Streamlit 기본 헤더(툴바)가 내비게이션 위를 덮어 클릭을 가로채므로 숨긴다. 다시 실행은 키보드 R. */
[data-testid="stHeader"] { display: none; }
.block-container { padding-top: 4.6rem; max-width: 1180px; }

/* 상단 내비게이션: 스크롤해도 화면 위에 고정 */
.kd-nav { position: fixed; top: 0; left: 0; right: 0; z-index: 1000000; display:flex; align-items:center; gap: 28px;
  background: rgba(14,20,27,.92); backdrop-filter: blur(8px); border-bottom: 1px solid var(--kd-line);
  padding: 10px max(16px, calc((100vw - 1180px) / 2 + 16px)); }
.kd-logo { display:flex; align-items:center; gap:8px; font-weight:700; color: var(--kd-ink) !important;
  text-decoration:none !important; font-size: 1.02rem; white-space: nowrap; }
.kd-logo-mark { display:inline-flex; align-items:center; justify-content:center; width:28px; height:28px;
  border-radius:8px; background: var(--kd-accent); color:#fff; font-weight:800; }
.kd-nav nav { display:flex; gap: 10px; flex-wrap: wrap; }
.kd-nav nav a { padding: 7px 16px; border: 1.5px solid #3b4d5f; border-radius: 999px; color: var(--kd-ink) !important;
  text-decoration: none !important; font-size: 0.92rem; font-weight: 600; transition: all .15s; }
.kd-nav nav a:hover { background: var(--kd-teal-dim); border-color: var(--kd-teal-dim); color:#fff !important; }
.kd-anchor { position: relative; top: -80px; }

/* 섹션 제목 */
.kd-section-title { font-size: 1.25rem; font-weight: 700; color: var(--kd-ink); margin: 1.6rem 0 0.6rem; }
.kd-sub { color: var(--kd-muted); font-size: 0.9rem; margin-top:-0.3rem; }

/* 카드 */
.st-key-chat_card, .st-key-age_card { border: 1px solid var(--kd-line); border-radius: 22px; padding: 18px 20px 14px;
  background: var(--kd-surface); box-shadow: 0 8px 24px rgba(0,0,0,.25); }
.st-key-map_card { border: 1px solid var(--kd-line); border-radius: 22px; overflow: hidden;
  background: radial-gradient(circle at 50% 45%, #13293b 0%, #0c1620 62%, #0a1118 100%); cursor: grab; }
.st-key-map_card:active { cursor: grabbing; }
[class*="st-key-country_panel"] { border: 1px solid #355066; border-radius: 22px; padding: 18px 18px 10px;
  background: linear-gradient(180deg, #1a2733 0%, #16202a 100%); min-height: 520px;
  box-shadow: 0 12px 32px rgba(0,0,0,.35); }
[class*="st-key-age_panel"] { border: 1px solid #355066; border-radius: 22px; padding: 16px 16px 8px;
  background: var(--kd-surface-2); min-height: 300px; }
.st-key-age_card .stSlider { padding-right: 12px; }
.kd-country-name { font-size: 1.45rem; font-weight: 700; color: var(--kd-ink); margin-bottom: .2rem; }
.kd-alert { display:inline-block; margin: .3rem 0 .6rem; padding: 4px 12px; border-radius: 999px;
  background: rgba(229,96,63,.16); color: #ff9a80; font-weight: 700; font-size: .9rem; }
.kd-chip { display:inline-block; margin: 0 6px 6px 0; padding: 3px 10px; border-radius: 999px;
  background: rgba(69,220,200,.12); color: var(--kd-teal); font-size: .85rem; }
.kd-muted { color: var(--kd-muted); font-size:.85rem; }
.kd-legend { display:flex; gap:16px; flex-wrap: wrap; font-size:.82rem; color: var(--kd-muted); margin:8px 2px 0; }
.kd-dot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:5px; vertical-align:middle; }

/* ---- 애니메이션 -------------------------------------------------------------------- */
@keyframes kdPanelIn { 0% { opacity: 0; transform: translateY(28px) scale(.95); filter: blur(6px); }
                       70% { opacity: 1; filter: blur(0); } 100% { opacity: 1; transform: none; } }
@keyframes kdRowIn { from { opacity: 0; transform: translateX(-14px); } to { opacity: 1; transform: none; } }
@keyframes kdFade { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
@keyframes kdGrowX { from { transform: scaleX(0); } to { transform: scaleX(1); } }
@keyframes kdGrowY { from { transform: scaleY(0); } to { transform: scaleY(1); } }
@keyframes kdWipe { from { clip-path: inset(0 100% 0 0); } to { clip-path: inset(0 0 0 0); } }

/* 나라를 누르면 패널이 천천히 떠오르고, 감염병 줄이 하나씩 나타남 */
[class*="st-key-country_panel"] { animation: kdPanelIn 1.8s cubic-bezier(.16,.8,.24,1) both; }
[class*="st-key-cwrow"] { animation: kdRowIn .9s ease-out both; }
__ROW_DELAYS__
[class*="st-key-age_panel"] { animation: kdFade .6s ease-out both; }

/* 연령별 막대: 가로 막대는 왼쪽에서, 세로 막대는 바닥에서 자라남. 유행 시기 선은 왼쪽부터 드러남 */
.st-key-agebar_h .barlayer .point path { transform-box: fill-box; transform-origin: left center;
  animation: kdGrowX 1s cubic-bezier(.2,.7,.2,1) both; }
.st-key-agebar_v .barlayer { transform-box: fill-box; transform-origin: center bottom;   /* 쌓은 막대 전체가 함께 */
  animation: kdGrowY 1.1s cubic-bezier(.2,.7,.2,1) both; }
.st-key-agetrend .scatterlayer { animation: kdWipe 2.2s cubic-bezier(.3,.6,.3,1) both; }
.st-key-agebar_h .bartext, .st-key-agebar_v .bartext { animation: kdFade .6s .7s ease-out both; }

@media (prefers-reduced-motion: reduce) {
  [class*="st-key-country_panel"], [class*="st-key-cwrow"], [class*="st-key-age_panel"],
  .st-key-agebar_h .barlayer .point path, .st-key-agebar_v .barlayer,
  .st-key-agebar_h .bartext, .st-key-agebar_v .bartext { animation: none !important; }
  .st-key-agetrend .scatterlayer { animation: none !important; }
}
</style>
""".replace("__ROW_DELAYS__", _ROW_DELAYS)
