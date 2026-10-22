"""지구본 지도 (plotly orthographic, 다크 테마).

- 검역관리지역으로 지정된 나라: 지정된 검역감염병 수만큼 밝게 (어두운 바탕에서 밝을수록 많음)
- 지정되지 않은 나라: 차분한 회청색 (눌러서 '지정 없음'을 확인할 수 있음)
- 지도 경계에 모양이 없는 작은 지정 국가(섬): 같은 색의 작은 점
- 선택한 나라: 주황색, 지구본이 그 나라 쪽으로 돌아감
- 드래그하면 지구본이 돌아간다 (dragmode='pan' = orthographic 회전)
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import plotly.graph_objects as go

from service.countries import WatchData

CONFIG = Path(__file__).resolve().parents[1] / "config"
POINTS_PATH = CONFIG / "country_points.json"
CENTROIDS_PATH = CONFIG / "country_centroids.json"

OCEAN = "#0b1d2c"
LAND = "#1c2733"          # 코드가 없는 땅 (남극 등)
NEUTRAL = "#2c3b4a"       # 지정 없는 나라
BORDER = "#0e151d"
GRATICULE = "rgba(160, 190, 215, 0.10)"
LOW, HIGH = "#1d5550", "#45dcc8"   # 검역감염병 1종 → 많을수록 밝게
SELECTED = "#e5603f"
HOME_VIEW = (127.0, 25.0)  # 처음 보이는 경도·위도 (한국 근처)
HOVER = "<b>%{customdata[1]}</b><br>%{customdata[2]}<extra></extra>"
_KEEP = {"marker": {"opacity": 1}}  # 선택 시 다른 나라가 흐려지지 않게


def _load(path: Path) -> dict[str, tuple[float, float]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return {k: (float(v[0]), float(v[1])) for k, v in data.items() if not k.startswith("_")}


@lru_cache(maxsize=1)
def load_points(path: Path = POINTS_PATH) -> dict[str, tuple[float, float]]:
    """ISO-3 → (위도, 경도). 110m 지도 경계에 모양이 없는 작은 나라용."""
    return _load(path)


@lru_cache(maxsize=1)
def load_centroids(path: Path = CENTROIDS_PATH) -> dict[str, tuple[float, float]]:
    """ISO-3 → (위도, 경도). 지구본을 고른 나라 쪽으로 돌릴 때 쓴다."""
    return _load(path)


def _mix(a: str, b: str, t: float) -> str:
    ca, cb = [int(a[i:i + 2], 16) for i in (1, 3, 5)], [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb))


def _count_text(n: int, loaded: bool) -> str:
    if not loaded:
        return "눌러서 보기"
    return f"검역감염병 {n}종 지정" if n else "검역관리지역 지정 없음"


def view_center(selected: str | None, centroids: dict[str, tuple[float, float]] | None = None) -> tuple[float, float]:
    """(경도, 위도). 고른 나라가 있으면 그 나라, 없으면 한국 근처."""
    cs = load_centroids() if centroids is None else centroids
    if selected in cs:
        lat, lon = cs[selected]
        return lon, max(-60.0, min(60.0, lat))
    return HOME_VIEW


def build_world_map(watch: WatchData, selected: str | None, names: dict[str, str], height: int = 520,
                    points: dict[str, tuple[float, float]] | None = None,
                    centroids: dict[str, tuple[float, float]] | None = None) -> go.Figure:
    """names: 눌러서 고를 수 있는 모든 나라 ISO-3 → 한글 이름."""
    counts = {c.iso3: len(c.diseases) for c in watch.countries.values() if c.iso3}
    label = dict(names)
    label.update({c.iso3: c.name_ko for c in watch.countries.values() if c.iso3})
    fig = go.Figure()

    # 1) 지정 없는 나라: 눌러서 '지정 없음'을 볼 수 있게 (주요 여행지 표시는 하지 않음)
    plain = sorted(i for i in label if i not in counts and len(i) == 3)
    if plain:
        fig.add_trace(go.Choropleth(
            locations=plain, z=[0] * len(plain), locationmode="ISO-3",
            customdata=[[i, label[i], _count_text(0, watch.loaded)] for i in plain],
            colorscale=[[0, NEUTRAL], [1, NEUTRAL]], showscale=False, marker_line_color=BORDER,
            marker_line_width=0.5, hovertemplate=HOVER, selected=_KEEP, unselected=_KEEP, name="지정 없음"))

    # 2) 지정 국가: 검역감염병 수로 색칠
    shaded = sorted(counts)
    top = max(counts.values(), default=1)
    if shaded:
        fig.add_trace(go.Choropleth(
            locations=shaded, z=[counts[i] for i in shaded], locationmode="ISO-3",
            customdata=[[i, label.get(i, i), _count_text(counts[i], True)] for i in shaded],
            colorscale=[[0, LOW], [1, HIGH]], zmin=1, zmax=max(top, 2), showscale=False,
            marker_line_color=BORDER, marker_line_width=0.5, hovertemplate=HOVER,
            selected=_KEEP, unselected=_KEEP, name="검역관리지역"))

    # 3) 선택한 나라 강조
    if selected and len(selected) == 3:
        fig.add_trace(go.Choropleth(
            locations=[selected], z=[1], locationmode="ISO-3",
            customdata=[[selected, label.get(selected, selected), _count_text(counts.get(selected, 0), watch.loaded)]],
            colorscale=[[0, SELECTED], [1, SELECTED]], showscale=False, marker_line_color="#ffffff",
            marker_line_width=1.2, hovertemplate=HOVER, selected=_KEEP, unselected=_KEEP, name="선택"))

    # 4) 지도 경계에 모양이 없는 작은 지정 국가(섬 등): 감염병 수 색의 작은 점
    pts = load_points() if points is None else points
    small = [i for i in shaded if i in pts]
    if small:
        def shade(i: str) -> str:
            return SELECTED if i == selected else _mix(LOW, HIGH, (counts[i] - 1) / max(top - 1, 1))
        fig.add_trace(go.Scattergeo(
            lat=[pts[i][0] for i in small], lon=[pts[i][1] for i in small], mode="markers",
            customdata=[[i, label.get(i, i), _count_text(counts[i], True)] for i in small],
            marker={"size": 8, "color": [shade(i) for i in small], "line": {"color": "#e8f6f4", "width": 1}},
            hovertemplate=HOVER, selected=_KEEP, unselected=_KEEP, name="작은 지정 국가 점"))

    lon, lat = view_center(selected, centroids)
    fig.update_geos(
        projection_type="orthographic", projection_rotation={"lon": lon, "lat": lat, "roll": 0},
        showframe=False, showcoastlines=False, showland=True, landcolor=LAND,
        showcountries=True, countrycolor=BORDER, countrywidth=0.5,
        showocean=True, oceancolor=OCEAN, showlakes=False, bgcolor="rgba(0,0,0,0)",
        lataxis={"showgrid": True, "gridcolor": GRATICULE, "dtick": 20},
        lonaxis={"showgrid": True, "gridcolor": GRATICULE, "dtick": 30})
    fig.update_layout(height=height, margin={"l": 0, "r": 0, "t": 0, "b": 0}, showlegend=False,
                      clickmode="event+select", dragmode="pan",   # 지구본 끌어서 돌리기
                      uirevision=selected or "globe",             # 나라를 새로 고르면 그 나라로 돌아감
                      paper_bgcolor="rgba(0,0,0,0)",
                      hoverlabel={"bgcolor": "#16202a", "bordercolor": "#2f3e4c", "font": {"size": 13, "color": "#e6edf3"}})
    return fig
