"""経路・運賃のポート。

実装は adapters/mock（既定）と adapters/ekispert（駅すぱあとAPI）を
差し替えられる。呼び出し側はこのインターフェースだけに依存する。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from app.domain.models import RouteLeg


class TransitPort(ABC):
    """N人分の経路探索。"""

    name: str = "transit"

    @abstractmethod
    async def candidate_hubs(
        self, origin_stations: list[str], *, limit: int = 6
    ) -> list[tuple[str, str]]:
        """出発駅集合から到達性の高い結節駅を列挙し、(エリア名, 駅名) を返す。"""

    @abstractmethod
    async def route(
        self,
        *,
        uid: str,
        from_station: str,
        to_station: str,
        arrive_by: datetime,
    ) -> RouteLeg:
        """1人分の経路（所要・運賃・乗換回数・出発時刻）を返す。"""

    async def coordinates(self, stations: list[str]) -> dict[str, tuple[float, float]]:
        """駅のおおよその位置を返す。描画にしか使わない。

        (緯度, 経度) の度。地図タイルの上に重ねるので実座標である必要がある。
        位置を出せない実装は空を返し、その場合 UI は地図を描かない。
        """
        return {}

    async def known_stations(self) -> set[str] | None:
        """扱える駅名の集合。列挙できない実装は None を返す。

        列挙できるなら、依頼を受け付ける前に未知の駅を弾ける。黙って近くの駅に
        寄せると「0分・0円で来られる人」が生まれ、結論そのものが狂うため。
        """
        return None

    async def routes_for(
        self,
        *,
        participants: list[tuple[str, str]],
        to_station: str,
        arrive_by: datetime,
    ) -> list[RouteLeg]:
        """N人分を並列取得する既定実装。(uid, 出発駅) の配列を受ける。"""
        import asyncio

        tasks = [
            self.route(
                uid=uid, from_station=origin, to_station=to_station, arrive_by=arrive_by
            )
            for uid, origin in participants
        ]
        return list(await asyncio.gather(*tasks))
