"""공공데이터포털(data.go.kr) API 공통 수집기.

API마다 엔드포인트·파라미터 이름·응답 모양이 다르므로 config/sources.json에 적고,
여기서는 페이지 순회, 응답 해석, 원본 저장만 담당한다.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from .http_client import PoliteSession, mask_secrets

OK_CODES = {"00", "0", "000", "200", "INFO-000", "NORMAL_SERVICE"}


class ApiError(RuntimeError):
    pass


def _as_list(value) -> list:
    if value is None or value == "":
        return []
    return value if isinstance(value, list) else [value]


def _xml_to_dict(elem: ET.Element):
    children = list(elem)
    if not children:
        return (elem.text or "").strip()
    out: dict = {}
    for child in children:
        val = _xml_to_dict(child)
        if child.tag in out:
            out[child.tag] = _as_list(out[child.tag]) + [val]
        else:
            out[child.tag] = val
    return out


def parse_response(text: str) -> dict:
    """JSON 또는 XML 응답을 dict로. 인증키 오류처럼 XML로만 오는 오류도 잡는다."""
    text = text.strip()
    if text.startswith("<"):
        root = ET.fromstring(text)
        return {root.tag: _xml_to_dict(root)}
    return json.loads(text)


def extract_items(payload: dict) -> tuple[list[dict], int | None]:
    """data.go.kr에서 흔한 응답 모양 세 가지를 처리해 (항목 목록, 전체 건수)를 돌려준다.

    1) {"response": {"header": {...}, "body": {"items": {"item": [...]}, "totalCount": N}}}
    2) {"response": {"body": {"items": [...]}}}  (items가 바로 리스트)
    3) {"data": [...], "totalCount": N}           (파일데이터 자동변환 API)
    """
    if "OpenAPI_ServiceResponse" in payload:
        hdr = payload["OpenAPI_ServiceResponse"].get("cmmMsgHeader", {})
        raise ApiError(f"{hdr.get('returnAuthMsg') or hdr.get('errMsg') or '인증 오류'} "
                       f"(code {hdr.get('returnReasonCode')})")

    if "data" in payload and isinstance(payload["data"], list):
        return payload["data"], _to_int(payload.get("totalCount") or payload.get("matchCount"))

    resp = payload.get("response", payload)
    header = resp.get("header", {})
    code = str(header.get("resultCode", "00"))
    if code not in OK_CODES:
        raise ApiError(f"{header.get('resultMsg', '알 수 없는 오류')} (code {code})")

    body = resp.get("body", {})
    items = body.get("items")
    if isinstance(items, dict):
        items = items.get("item")
    return [i for i in _as_list(items) if isinstance(i, dict)], _to_int(body.get("totalCount"))


def _to_int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def fetch_all(source: dict, session: PoliteSession, service_key: str, max_pages: int = 200) -> list[dict]:
    """source 설정에 따라 모든 페이지를 받아 항목 목록을 돌려준다."""
    api = source["api"]
    page_param = api.get("page_param", "pageNo")
    size_param = api.get("size_param", "numOfRows")
    page_size = int(api.get("page_size", 100))
    key_param = api.get("key_param", "serviceKey")
    base_params = dict(api.get("params", {}))

    items: list[dict] = []
    total = None
    for page in range(1, max_pages + 1):
        params = {**base_params, key_param: service_key, page_param: page, size_param: page_size}
        resp = session.get(api["endpoint"], params=params)
        try:
            page_items, total = extract_items(parse_response(resp.text))
        except (ValueError, ET.ParseError) as exc:
            raise ApiError(f"응답 해석 실패: {mask_secrets(resp.url)} ({exc})") from exc
        items.extend(page_items)
        if not page_items or (total is not None and len(items) >= total) or len(page_items) < page_size:
            break
    return items


def save_raw(source_id: str, items: list[dict], out_dir: Path, request_info: dict) -> Path:
    """원본을 수집일과 함께 저장 (NF2002 캐시, NF2007 출처 기록의 근거)."""
    now = datetime.now(timezone.utc).astimezone()
    out_dir = out_dir / source_id
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{now:%Y%m%d}.json"
    safe_info = {k: v for k, v in request_info.items() if "key" not in k.lower()}
    path.write_text(
        json.dumps({"source_id": source_id, "fetched_at": now.isoformat(timespec="seconds"),
                    "request": safe_info, "n_items": len(items), "items": items},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
