"""세계 지도 (plotly). 대상 나라는 색칠·마커로 표시하고, 클릭하면 선택 이벤트를 돌려준다."""
from __future__ import annotations

import plotly.graph_objects as go

from service.countries import Country

LAND = "#24425f"      # 대상이 아닌 나라
BORDER = "#3d6080"
OCEAN = "#e9eef2"
TARGET = "#4fb3a9"    # 클릭할 수 있는 나라
HOME = "#8fb8d6"      # 대한민국
SELECTED = "#e4572e"  # 선택한 나라


def build_world_map(countries: list[Country], selected: str | None, height: int = 370) -> go.Figure:
    def color(c: Country) -> str:
        if c.iso3 == selected:
            return SELECTED
        return HOME if c.home else TARGET

    iso = [c.iso3 for c in countries]
    names = [c.name_ko for c in countries]
    custom = [[c.iso3, c.name_ko] for c in countries]
    hover = "<b>%{customdata[1]}</b><br>클릭해서 주의 감염병 보기<extra></extra>"
    keep = {"marker": {"opacity": 1}}  # 선택 시 다른 나라가 흐려지지 않게

    fig = go.Figure()
    # 나라 영역: 나라마다 색이 달라 단색 colorscale 트레이스를 나라 수만큼 겹친다 (범례·색막대 없음)
    for c, cd in zip(countries, custom):
        fig.add_trace(go.Choropleth(
            locations=[c.iso3], z=[1], locationmode="ISO-3", customdata=[cd],
            colorscale=[[0, color(c)], [1, color(c)]], showscale=False,
            marker_line_color="#ffffff", marker_line_width=0.8,
            hovertemplate=hover, selected=keep, unselected=keep, name=c.name_ko))
    # 마커: 싱가포르처럼 작은 나라도 누를 수 있게
    fig.add_trace(go.Scattergeo(
        lat=[c.lat for c in countries], lon=[c.lon for c in countries], customdata=custom,
        text=names, mode="markers",
        marker={"size": 11, "color": [color(c) for c in countries], "line": {"color": "#ffffff", "width": 1.5}},
        hovertemplate=hover, selected=keep, unselected=keep, name="markers"))
    fig.update_geos(
        projection_type="natural earth", showframe=False, showcoastlines=False,
        showland=True, landcolor=LAND, showcountries=True, countrycolor=BORDER, countrywidth=0.5,
        showocean=True, oceancolor=OCEAN, showlakes=False, bgcolor="rgba(0,0,0,0)",
        lataxis_range=[-56, 84])
    fig.update_layout(height=height, margin={"l": 0, "r": 0, "t": 0, "b": 0}, showlegend=False,
                      clickmode="event+select", dragmode=False, paper_bgcolor="rgba(0,0,0,0)",
                      hoverlabel={"bgcolor": "#ffffff", "font_size": 13})
    return fig
