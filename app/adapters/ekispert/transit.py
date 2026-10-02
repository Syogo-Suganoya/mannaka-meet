"""駅すぱあとAPI アダプタ（実接続の差し替え先）。REST を直接叩く。

TRANSIT_PROVIDER=ekispert かつ EKISPERT_API_KEY を設定すると有効になる。
HTTP の呼び出し形はここに閉じており、ドメイン層は TransitPort しか知らない。

駅名はそのまま経路検索に渡さず、必ず駅コードに解決してから引く。
「大宮」のように同名の駅が複数ある名前は、駅すぱあとが E102（駅名が見つかりません）
で弾くため。同名が複数あるときは首都圏の駅を選ぶ。
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta

import httpx

from app.domain.models import RouteLeg
from app.ports.transit import TransitPort

BASE_URL = "https://api.ekispert.jp/v1/json"

# 同名の駅があるときに優先する都県。利用者の想定が首都圏のため。
METRO_PREFECTURES = ("東京都", "神奈川県", "埼玉県", "千葉県")

# 集合場所の候補にする主要ターミナル。
# 名前の前方一致で周辺駅を拾うと「三鷹台」「千葉ニュータウン中央」のような
# 集まる場所として不自然な駅が並ぶので、候補はここから選ぶ。
# 参加者の出発駅も候補に加え、全員から近い順に絞る（モックと同じ考え方）。
HUBS = (
    "東京", "大手町", "新橋", "品川", "秋葉原", "上野", "新宿", "渋谷", "池袋",
    "四ツ谷", "飯田橋", "中野", "吉祥寺", "赤羽", "北千住", "錦糸町",
    "武蔵小杉", "川崎", "横浜", "大宮",
)


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
        # 駅名 → 駅コード、駅コード → 座標。駅は動かないのでプロセス内で使い回す
        self._codes: dict[str, str | None] = {}
        self._geo: dict[str, tuple[float, float] | None] = {}

    async def _get(self, path: str, params: dict) -> dict:
        params = {**params, "key": self._key}
        if self._client is not None:
            resp = await self._client.get(f"{self._base_url}{path}", params=params)
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(f"{self._base_url}{path}", params=params)
        resp.raise_for_status()
        return resp.json()

    # -- 駅の解決 ------------------------------------------------------------

    async def _code(self, name: str) -> str | None:
        """駅名を駅コードにする。見つからなければ None。"""
        if name in self._codes:
            return self._codes[name]
        data = await self._get("/station/light", {"name": name, "type": "train"})
        points = data.get("ResultSet", {}).get("Point", [])
        if isinstance(points, dict):
            points = [points]

        def station_name(p: dict) -> str:
            return p.get("Station", {}).get("Name", "")

        exact = [p for p in points if station_name(p) == name]
        # 「大宮(埼玉県)」「大宮(京都府)」のように括弧で区別される同名駅
        same = [p for p in points if station_name(p).startswith(f"{name}(")]
        metro = [p for p in same if p.get("Prefecture", {}).get("Name") in METRO_PREFECTURES]
        chosen = (exact or metro or same or [None])[0]
        code = chosen.get("Station", {}).get("code") if chosen else None
        self._codes[name] = code
        return code

    async def _point(self, name: str) -> tuple[float, float] | None:
        """駅の (緯度, 経度)。地図タイルと揃えるため世界測地系で取る。"""
        code = await self._code(name)
        if code is None:
            return None
        if code not in self._geo:
            data = await self._get("/station", {"code": code, "gcs": "wgs84"})
            point = data.get("ResultSet", {}).get("Point", {})
            if isinstance(point, list):
                point = point[0] if point else {}
            geo = point.get("GeoPoint", {})
            try:
                self._geo[code] = (float(geo["lati_d"]), float(geo["longi_d"]))
            except (KeyError, ValueError):
                self._geo[code] = None
        return self._geo[code]

    async def unknown_stations(self, stations: list[str]) -> list[str]:
        codes = await asyncio.gather(*(self._code(s) for s in stations))
        return [s for s, code in zip(stations, codes) if code is None]

    async def coordinates(self, stations: list[str]) -> dict[str, tuple[float, float]]:
        points = await asyncio.gather(*(self._point(s) for s in stations))
        return {s: p for s, p in zip(stations, points) if p is not None}

    # -- 候補と経路 ----------------------------------------------------------

    async def candidate_hubs(
        self, origin_stations: list[str], *, limit: int = 6
    ) -> list[tuple[str, str]]:
        """主要ターミナルと出発駅の中から、全員の出発駅に近い順に選ぶ。"""
        origins = list(dict.fromkeys(origin_stations))
        pool = list(dict.fromkeys([*HUBS, *origins]))
        points = await self.coordinates([*pool, *origins])
        here = [points[o] for o in origins if o in points]
        if not here:
            return [(name, name) for name in HUBS[:limit]]

        def total_km(station: str) -> float:
            return sum(_km(points[station], p) for p in here)

        ranked = sorted((s for s in pool if s in points), key=total_km)
        return [(name, name) for name in ranked[:limit]]

    async def route(
        self,
        *,
        uid: str,
        from_station: str,
        to_station: str,
        arrive_by: datetime,
    ) -> RouteLeg:
        # 出発駅がそのまま候補駅になることはよくある（三鷹の人に「三鷹集合」）。
        # 駅すぱあとは同じ駅どうしの経路を返さないので、問い合わせずに0とする。
        # モックと同じ扱い。
        if from_station == to_station:
            return _stay(uid, from_station, to_station, arrive_by)

        src, dst = await asyncio.gather(self._code(from_station), self._code(to_station))
        if src is None or dst is None:
            missing = from_station if src is None else to_station
            raise RuntimeError(f"駅が見つかりません: {missing}")
        if src == dst:
            return _stay(uid, from_station, to_station, arrive_by)

        data = await self._get(
            "/search/course/extreme",
            {
                "viaList": f"{src}:{dst}",
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


def _stay(uid: str, from_station: str, to_station: str, arrive_by: datetime) -> RouteLeg:
    """移動しない人の経路。0分・0円・乗換0回。"""
    return RouteLeg(
        uid=uid,
        from_station=from_station,
        to_station=to_station,
        duration_minutes=0,
        fare_yen=0,
        transfers=0,
        depart_at=arrive_by,
        arrive_at=arrive_by,
    )


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """2点間のおおよその距離。候補の並べ替えにしか使わないので平面近似で足りる。"""
    lat = math.radians((a[0] + b[0]) / 2)
    dx = (a[1] - b[1]) * 111.32 * math.cos(lat)
    dy = (a[0] - b[0]) * 110.57
    return math.hypot(dx, dy)
