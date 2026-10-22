"""나라별 주의 감염병 (지도·패널 데이터 계층).

- config/countries.json             : 지도에 점으로 찍는 주요 여행지 (작은 나라도 누를 수 있게)
- data/processed/country_watch.json : scripts/fetch_quarantine.py가 질병관리청 검역관리지역정보로 만든 파일

country_watch.json 형식:
{
  "as_of": "2026-10-22", "source": "질병관리청 검역관리지역정보", "source_url": "https://…",
  "countries": {
    "VNM": {"name_ko": "베트남", "iso3": "VNM",
            "diseases": [{"name": "콜레라", "api_name": "콜레라", "disease_id": "inf_0022",
                          "designated": "2024-09-02", "released": "", "note": "검역관리지역 지정 2024-09-02"}]},
    "NAME:어떤나라": {"name_ko": "어떤나라", "iso3": "", "diseases": [...]}     ← 지도 코드를 못 찾은 나라
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
    key: str
    name_ko: str
    iso3: str
    diseases: list[dict] = field(default_factory=list)


@dataclass
class WatchData:
    loaded: bool = False
    as_of: str = ""
    source: str = ""
    source_url: str = ""
    countries: dict[str, CountryWatch] = field(default_factory=dict)


def load_countries(path: Path) -> list[Country]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Country(c["iso3"], c["name_ko"], float(c["lat"]), float(c["lon"]), bool(c.get("home", False)))
            for c in data["countries"]]


def load_watch(path: Path) -> WatchData:
    """파일이 없으면 loaded=False (아직 수집 전)."""
    p = Path(path)
    if not p.exists():
        return WatchData()
    data = json.loads(p.read_text(encoding="utf-8"))
    countries = {}
    for key, v in data.get("countries", {}).items():
        iso3 = v.get("iso3", key if len(key) == 3 and key.isupper() else "")
        countries[key] = CountryWatch(key, v.get("name_ko", key), iso3, list(v.get("diseases", [])))
    return WatchData(True, data.get("as_of", ""), data.get("source", ""), data.get("source_url", ""), countries)


def country_from_event(event, valid: set[str]) -> str | None:
    """st.plotly_chart(on_select="rerun")의 선택 이벤트에서 클릭한 나라 키를 꺼낸다.

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
