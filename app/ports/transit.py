"""経路・運賃・運行実況のポート。

実装は adapters/mock（既定）と adapters/ekispert（駅すぱあとAPI / MCP）を
差し替えられる。呼び出し側はこのインターフェースだけに依存する。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from app.domain.models import RouteLeg, ServiceDisruption


class TransitPort(ABC):
    """N人分の経路探索と当日実況。"""

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

    @abstractmethod
    async def disruptions(self, lines: list[str]) -> list[ServiceDisruption]:
        """当日の運行障害を返す。"""

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
