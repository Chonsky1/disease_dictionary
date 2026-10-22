"""화면 스타일: 상단 내비게이션, 카드, 지도 패널. Streamlit의 st-key-<key> 클래스로 컨테이너를 꾸민다."""

NAV_HTML = """
<div class="kd-nav">
  <a class="kd-logo" href="#top" target="_self"><span class="kd-logo-mark">+</span>법정감염병 안내</a>
  <nav>
    <a href="#chat" target="_self">감염병 질문하기</a>
    <a href="#country" target="_self">나라별 알아보기</a>
    <a href="#age" target="_self">연령별 알아보기</a>
  </nav>
</div>
"""

CSS = """
<style>
:root { --kd-navy:#1d3557; --kd-teal:#2a9d8f; --kd-accent:#e4572e; --kd-line:#d9e1e8; --kd-soft:#f5f8fa; }
html { scroll-behavior: smooth; }
/* Streamlit 기본 헤더(툴바)가 내비게이션 위를 덮어 클릭을 가로채므로 숨긴다. 다시 실행은 키보드 R. */
[data-testid="stHeader"] { display: none; }
.block-container { padding-top: 4.6rem; max-width: 1180px; }

/* 상단 내비게이션: 스크롤해도 화면 위에 고정 */
.kd-nav { position: fixed; top: 0; left: 0; right: 0; z-index: 1000000; display:flex; align-items:center; gap: 28px;
  background: rgba(255,255,255,.96); backdrop-filter: blur(6px); border-bottom: 1px solid var(--kd-line);
  padding: 10px max(16px, calc((100vw - 1180px) / 2 + 16px)); }
.kd-logo { display:flex; align-items:center; gap:8px; font-weight:700; color: var(--kd-navy) !important;
  text-decoration:none !important; font-size: 1.02rem; white-space: nowrap; }
.kd-logo-mark { display:inline-flex; align-items:center; justify-content:center; width:28px; height:28px;
  border-radius:8px; background: var(--kd-accent); color:#fff; font-weight:800; }
.kd-nav nav { display:flex; gap: 10px; flex-wrap: wrap; }
.kd-nav nav a { padding: 7px 16px; border: 1.5px solid var(--kd-navy); border-radius: 999px; color: var(--kd-navy) !important;
  text-decoration: none !important; font-size: 0.92rem; font-weight: 600; transition: all .15s; }
.kd-nav nav a:hover { background: var(--kd-navy); color:#fff !important; }
.kd-anchor { position: relative; top: -80px; }

/* 섹션 제목 */
.kd-section-title { font-size: 1.25rem; font-weight: 700; color: var(--kd-navy); margin: 1.6rem 0 0.6rem; }
.kd-sub { color:#5b6b7a; font-size: 0.9rem; margin-top:-0.3rem; }

/* 카드 */
.st-key-chat_card, .st-key-age_card { border: 1.5px solid var(--kd-line); border-radius: 22px; padding: 18px 20px 14px;
  background: #ffffff; box-shadow: 0 1px 2px rgba(16,24,40,.04); }
.st-key-map_card { border: 1.5px solid var(--kd-line); border-radius: 16px; overflow: hidden; background: var(--kd-soft); }
.st-key-country_panel { border: 2px solid var(--kd-navy); border-radius: 22px; padding: 18px 18px 10px;
  background:#ffffff; min-height: 372px; }
.kd-country-name { font-size: 1.35rem; font-weight: 700; color: var(--kd-navy); margin-bottom: .2rem; }
.kd-chip { display:inline-block; margin: 0 6px 6px 0; padding: 3px 10px; border-radius: 999px;
  background: #e8f4f3; color: #1f6f66; font-size: .85rem; }
.kd-muted { color:#6b7b8b; font-size:.85rem; }
.kd-legend { display:flex; gap:16px; font-size:.82rem; color:#5b6b7a; margin:6px 2px 0; }
.kd-dot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:5px; vertical-align:middle; }
</style>
"""
