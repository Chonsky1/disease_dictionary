"""나라별 주의 감염병 (단계 3 화면용 데이터 계층).

- config/countries.json        : 지도에서 누를 수 있는 나라 목록
- data/processed/country_watch.json : 나라별 주의 감염병. 단계 3에서 질병관리청 검역관리지역정보 API로 만든다.

country_watch.json 형식 (단계 3 수집 스크립트가 이 형식으로 저장하면 화면이 그대로 표시한다):
{
  "as_of": "2026-10-22",
  "source": "질병관리청 검역관리지역정보",
  "source_url": "https://…",
  "countries": {
    "THA": {"diseases": [{"name": "뎅기열", "disease_id": "inf_0059", "note": "검역관리지역"}]}
  }
}
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Country:
    iso3: str
    name_ko: str
    lat: float
    lon: float
    home: bool = False


@dataclass
class CountryWatch:
    diseases: list[dict] = field(default_factory=list)
    as_of: str = ""
    source: str = ""
    source_url: str = ""


def load_countries(path: Path) -> list[Country]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Country(c["iso3"], c["name_ko"], float(c["lat"]), float(c["lon"]), bool(c.get("home", False)))
            for c in data["countries"]]


def load_watch(path: Path) -> dict[str, CountryWatch]:
    """파일이 없으면 빈 dict (단계 3 전)."""
    p = Path(path)
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    common = {k: data.get(k, "") for k in ("as_of", "source", "source_url")}
    return {iso3: CountryWatch(diseases=list(v.get("diseases", [])), **common)
            for iso3, v in data.get("countries", {}).items()}


def country_from_event(event, valid: set[str]) -> str | None:
    """st.plotly_chart(on_select="rerun")의 선택 이벤트에서 클릭한 나라 코드를 꺼낸다.

    choropleth 영역을 누르면 point에 location이, 마커를 누르면 customdata[0]이 들어온다.
    """
    try:
        points = event["selection"]["points"]
    except (KeyError, TypeError):
        return None
    for p in reversed(points or []):
        loc = p.get("location")
        if not loc:
            cd = p.get("customdata")
            loc = cd[0] if isinstance(cd, (list, tuple)) and cd else cd
        if isinstance(loc, str) and loc in valid:
            return loc
    return None
