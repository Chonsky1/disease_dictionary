"""연령별 알아보기 차트 (plotly). 색: 남·여 2색(색각 이상 검증 통과), 유행 시기는 최근 해만 진하게."""
from __future__ import annotations

import plotly.graph_objects as go

from service.age import AgeDisease, Trend, TrendItem

TEAL, NAVY, ACCENT = "#1b7a6f", "#1d3557", "#e4572e"
MALE, FEMALE = "#2f6f9f", "#d9775a"       # validate_palette.js: CVD ΔE 16, 일반 ΔE 27 (PASS)
CONTEXT = "#c6d0d9"                         # 지난 해·절기 (배경 선)
PREV = "#7f93a8"                            # 바로 전 해·절기
INK, MUTED, GRID = "#24323f", "#6b7b8b", "#e6ebef"
FONT = {"size": 13, "color": INK}


def _layout(fig: go.Figure, height: int, **kw) -> go.Figure:
    fig.update_layout(height=height, margin={"l": 8, "r": 12, "t": 8, "b": 8}, font=FONT,
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", bargap=0.28,
                      hoverlabel={"bgcolor": "#ffffff", "font_size": 13}, dragmode=False, **kw)
    fig.update_xaxes(gridcolor=GRID, zeroline=False, tickfont={"color": MUTED})
    fig.update_yaxes(gridcolor=GRID, zeroline=False, tickfont={"color": MUTED})
    return fig


def top_bar(rows: list[dict], selected: str | None, by: str) -> go.Figure:
    """연령대별 상위 감염병: 가로 막대 (위에서부터 큰 순). 막대를 누르면 오른쪽 정보 창이 바뀐다."""
    rows = list(reversed(rows))
    x = [r["share"] * 100 if by == "share" else r["count"] for r in rows]
    fig = go.Figure(go.Bar(
        x=x, y=[r["name"] for r in rows], orientation="h",
        marker={"color": [ACCENT if r["name"] == selected else TEAL for r in rows], "cornerradius": 4},
        customdata=[[r["name"], f"{r['count']:,}", f"{r['share'] * 100:.0f}%"] for r in rows],
        text=[f"{v:.0f}%" if by == "share" else f"{v:,}" for v in x], textposition="outside",
        textfont={"color": MUTED, "size": 12}, cliponaxis=False,
        hovertemplate="<b>%{customdata[0]}</b><br>이 연령대 %{customdata[1]}건 · 전체의 %{customdata[2]}<extra></extra>"))
    _layout(fig, max(220, 34 * len(rows) + 40), showlegend=False, clickmode="event+select")
    fig.update_xaxes(showgrid=True, ticksuffix="%" if by == "share" else "", range=[0, max(x or [1]) * 1.16])
    fig.update_yaxes(showgrid=False, tickfont={"color": INK, "size": 13}, automargin=True)
    return fig


def age_sex_bar(d: AgeDisease, ages: list[str], highlight: str | None = None) -> go.Figure:
    """감염병 하나의 연령대·성별 신고 건수 (쌓은 막대)."""
    fig = go.Figure()
    for sex, color in (("남", MALE), ("여", FEMALE)):
        fig.add_trace(go.Bar(
            x=ages, y=[d.count(a, sex) for a in ages], name=sex, marker={"color": color, "cornerradius": 3},
            marker_line={"color": "#ffffff", "width": 1},
            hovertemplate=f"%{{x}} · {sex} %{{y:,}}건<extra></extra>"))
    _layout(fig, 330, barmode="stack",
            legend={"orientation": "h", "y": 1.08, "x": 0, "font": {"color": INK}, "traceorder": "normal"})
    fig.update_xaxes(showgrid=False, tickangle=0, tickfont={"size": 11})
    fig.update_yaxes(title=None, rangemode="tozero")
    if highlight in ages:
        fig.add_vrect(x0=ages.index(highlight) - 0.5, x1=ages.index(highlight) + 0.5,
                      fillcolor=ACCENT, opacity=0.08, line_width=0)
    return fig


def sari_bar(by_age: dict[str, int]) -> go.Figure:
    ages = list(by_age)
    fig = go.Figure(go.Bar(x=ages, y=[by_age[a] for a in ages], marker={"color": TEAL, "cornerradius": 4},
                           hovertemplate="%{x} · %{y:,}명<extra></extra>"))
    _layout(fig, 330, showlegend=False)
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(rangemode="tozero")
    return fig


def trend_lines(trend: Trend, item: TrendItem, show: list[str] | None = None) -> go.Figure:
    """해(절기)마다 한 줄. 가장 최근 = 진한 청록, 바로 전 = 회청색, 나머지 = 옅은 회색."""
    keys = sorted(item.series)
    if show:
        keys = [k for k in keys if k in show]
    fig = go.Figure()
    unit_short = "건" if "건수" in trend.unit else ("명" if "환자 수" in trend.unit and "1,000" not in trend.unit else "")
    for i, k in enumerate(keys):
        latest, prev = i == len(keys) - 1, i == len(keys) - 2
        color, width = (TEAL, 3) if latest else ((PREV, 2) if prev else (CONTEXT, 1.5))
        label = f"{k}절기" if trend.kind == "season" else f"{k}년"
        fig.add_trace(go.Scatter(
            x=trend.x, y=item.series[k], mode="lines", name=label, connectgaps=True,   # 53주가 없는 해의 빈칸 잇기
            line={"color": color, "width": width},
            hovertemplate=f"{label} %{{x}} · %{{y:,}}{unit_short}<extra></extra>"))
    _layout(fig, 330, hovermode="x unified",
            legend={"orientation": "h", "y": 1.1, "x": 0, "font": {"color": INK, "size": 12}, "traceorder": "reversed"})
    fig.update_xaxes(showgrid=False, tickmode="array", tickvals=trend.x[::4], tickfont={"size": 11})
    fig.update_yaxes(rangemode="tozero")
    return fig
