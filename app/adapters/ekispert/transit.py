"""駅すぱあとAPI アダプタ（実接続の差し替え先）。REST を直接叩く。

TRANSIT_PROVIDER=ekispert かつ EKISPERT_API_KEY を設定すると有効になる。
HTTP の呼び出し形はここに閉じており、ドメイン層は TransitPort しか知らない。
レスポンスのフィールド名は駅すぱあとWebサービスの一般的な形に合わせてあるが、
契約プランによって差があるため、実キー入手後にこのファイルだけを調整すればよい。
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import httpx

from app.domain.models import RouteLeg
from app.ports.transit import TransitPort

BASE_URL = "https://api.ekispert.jp/v1/json"


class EkispertTransitAdapter(TransitPort):
    name = "ekispert"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        if not api_key:
            raise ValueError("EKISPERT_API_KEY が未設定です")
        self._key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._timeout = timeout

    async def _get(self, path: str, params: dict) -> dict:
        params = {**params, "key": self._key}
        if self._client is not None:
            resp = await self._client.get(f"{self._base_url}{path}", params=params)
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(f"{self._base_url}{path}", params=params)
        resp.raise_for_status()
        return resp.json()

    async def candidate_hubs(
        self, origin_stations: list[str], *, limit: int = 6
    ) -> list[tuple[str, str]]:
        """各出発駅の周辺主要駅を集め、全員から到達可能な駅を候補にする。"""
        results = await asyncio.gather(
            *(self._nearby(station) for station in origin_stations)
        )
        counts: dict[str, int] = {}
        for stations in results:
            for name in set(stations):
                counts[name] = counts.get(name, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return [(name, name) for name, _ in ranked[:limit]]

    async def _nearby(self, station: str) -> list[str]:
        data = await self._get("/station/light", {"name": station, "type": "train"})
        points = data.get("ResultSet", {}).get("Point", [])
        if isinstance(points, dict):
            points = [points]
        return [p.get("Station", {}).get("Name", "") for p in points if p]

    async def route(
        self,
        *,
        uid: str,
        from_station: str,
        to_station: str,
        arrive_by: datetime,
    ) -> RouteLeg:
        data = await self._get(
            "/search/course/extreme",
            {
                "viaList": f"{from_station}:{to_station}",
                "date": arrive_by.strftime("%Y%m%d"),
                "time": arrive_by.strftime("%H%M"),
                "searchType": "arrival",
                "answerCount": 1,
            },
        )
        courses = data.get("ResultSet", {}).get("Course", [])
        if isinstance(courses, dict):
            courses = [courses]
        if not courses:
            raise RuntimeError(f"経路が見つかりません: {from_station} -> {to_station}")

        course = courses[0]
        route = course.get("Route", {})
        duration = int(route.get("timeOnBoard", 0)) + int(route.get("timeOther", 0))
        transfers = int(route.get("transferCount", 0))
        fare = 0
        prices = course.get("Price", [])
        if isinstance(prices, dict):
            prices = [prices]
        for price in prices:
            if price.get("kind") == "FareSummary":
                fare = int(price.get("Oneway", 0))
                break

        lines = []
        lines_raw = route.get("Line", [])
        if isinstance(lines_raw, dict):
            lines_raw = [lines_raw]
        for line in lines_raw:
            if name := line.get("Name"):
                lines.append(name)

        from datetime import timedelta

        return RouteLeg(
            uid=uid,
            from_station=from_station,
            to_station=to_station,
            duration_minutes=duration,
            fare_yen=fare,
            transfers=transfers,
            depart_at=arrive_by - timedelta(minutes=duration),
            arrive_at=arrive_by,
            lines=lines[:3],
        )
