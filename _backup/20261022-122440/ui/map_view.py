"""세계 지도 (plotly).

- 검역관리지역으로 지정된 나라: 지정된 검역감염병 수만큼 진하게 (클릭 가능)
- 주요 여행지·대한민국: 점 마커 (작은 나라도 누를 수 있게, 지정이 없어도 클릭 가능)
- 선택한 나라: 빨간색
"""
from __future__ import annotations

import plotly.graph_objects as go

from service.countries import Country, WatchData

LAND = "#24425f"      # 지정되지 않은 나라
BORDER = "#3d6080"
OCEAN = "#e9eef2"
LOW, HIGH = "#cfe9e5", "#1b7a6f"   # 검역감염병 수 1종 → 많을수록
TARGET = "#4fb3a9"    # 주요 여행지 점
HOME = "#8fb8d6"      # 대한민국
SELECTED = "#e4572e"  # 선택한 나라
HOVER = "<b>%{customdata[1]}</b><br>%{customdata[2]}<extra></extra>"
_KEEP = {"marker": {"opacity": 1}}  # 선택 시 다른 나라가 흐려지지 않게


def _count_text(n: int, loaded: bool) -> str:
    if not loaded:
        return "클릭해서 보기"
    return f"검역감염병 {n}종 지정" if n else "현재 지정 없음"


def build_world_map(markers: list[Country], watch: WatchData, selected: str | None, height: int = 370) -> go.Figure:
    counts = {c.iso3: len(c.diseases) for c in watch.countries.values() if c.iso3}
    names = {c.iso3: c.name_ko for c in watch.countries.values() if c.iso3}
    names.update({m.iso3: m.name_ko for m in markers})
    fig = go.Figure()

    # 1) 지정 국가: 검역감염병 수로 색칠
    shaded = sorted(counts)
    if shaded:
        top = max(counts.values())
        fig.add_trace(go.Choropleth(
            locations=shaded, z=[counts[i] for i in shaded], locationmode="ISO-3",
            customdata=[[i, names[i], _count_text(counts[i], True)] for i in shaded],
            colorscale=[[0, LOW], [1, HIGH]], zmin=1, zmax=max(top, 2), showscale=False,
            marker_line_color="#ffffff", marker_line_width=0.6, hovertemplate=HOVER,
            selected=_KEEP, unselected=_KEEP, name="검역관리지역"))

    # 2) 지정은 없지만 점으로 표시한 나라도 영역을 눌러 고를 수 있게 (옅은 색)
    plain = [m for m in markers if m.iso3 not in counts and m.iso3 != selected]
    if plain:
        fig.add_trace(go.Choropleth(
            locations=[m.iso3 for m in plain], z=[0] * len(plain), locationmode="ISO-3",
            customdata=[[m.iso3, m.name_ko, _count_text(0, watch.loaded)] for m in plain],
            colorscale=[[0, "#5d7f9c"], [1, "#5d7f9c"]], showscale=False,
            marker_line_color="#ffffff", marker_line_width=0.6, hovertemplate=HOVER,
            selected=_KEEP, unselected=_KEEP, name="주요 여행지"))

    # 3) 선택한 나라 강조
    if selected and len(selected) == 3:
        fig.add_trace(go.Choropleth(
            locations=[selected], z=[1], locationmode="ISO-3",
            customdata=[[selected, names.get(selected, selected), _count_text(counts.get(selected, 0), watch.loaded)]],
            colorscale=[[0, SELECTED], [1, SELECTED]], showscale=False, marker_line_color="#ffffff",
            marker_line_width=1.2, hovertemplate=HOVER, selected=_KEEP, unselected=_KEEP, name="선택"))

    # 4) 점 마커
    def color(m: Country) -> str:
        return SELECTED if m.iso3 == selected else (HOME if m.home else TARGET)
    fig.add_trace(go.Scattergeo(
        lat=[m.lat for m in markers], lon=[m.lon for m in markers], mode="markers",
        customdata=[[m.iso3, m.name_ko, _count_text(counts.get(m.iso3, 0), watch.loaded)] for m in markers],
        marker={"size": 10, "color": [color(m) for m in markers], "line": {"color": "#ffffff", "width": 1.5}},
        hovertemplate=HOVER, selected=_KEEP, unselected=_KEEP, name="주요 여행지 점"))

    fig.update_geos(
        projection_type="natural earth", showframe=False, showcoastlines=False,
        showland=True, landcolor=LAND, showcountries=True, countrycolor=BORDER, countrywidth=0.5,
        showocean=True, oceancolor=OCEAN, showlakes=False, bgcolor="rgba(0,0,0,0)", lataxis_range=[-56, 84])
    fig.update_layout(height=height, margin={"l": 0, "r": 0, "t": 0, "b": 0}, showlegend=False,
                      clickmode="event+select", dragmode=False, paper_bgcolor="rgba(0,0,0,0)",
                      hoverlabel={"bgcolor": "#ffffff", "font_size": 13})
    return fig
