"""駅すぱあとAPI の代役。首都圏の簡易グラフから決定的に所要・運賃を計算する。

実APIに差し替えるときは adapters/ekispert/transit.py を使う。
このモックは「同じ入力なら常に同じ出力」になるよう乱数を使わない。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from app.ports.transit import TransitPort
from app.domain.models import RouteLeg, ServiceDisruption

# 駅 -> (エリア名, 主要路線, 都心中心からの相対座標km)
# 座標は東京駅を原点としたごく粗い近似。所要時間の単調性を担保するために使う。
STATIONS: dict[str, tuple[str, list[str], float, float]] = {
    "東京": ("丸の内", ["JR中央線", "JR山手線"], 0.0, 0.0),
    "新宿": ("新宿", ["JR中央線", "JR山手線", "小田急線"], -6.4, 1.2),
    "渋谷": ("渋谷", ["JR山手線", "東急東横線", "東京メトロ半蔵門線"], -6.0, -3.4),
    "品川": ("品川", ["JR東海道線", "JR山手線", "京急線"], -1.6, -6.4),
    "池袋": ("池袋", ["JR山手線", "東武東上線", "西武池袋線"], -5.6, 4.8),
    "上野": ("上野", ["JR山手線", "JR常磐線"], 1.4, 4.0),
    "秋葉原": ("秋葉原", ["JR山手線", "つくばエクスプレス"], 0.9, 1.9),
    "大手町": ("大手町", ["東京メトロ東西線", "東京メトロ丸ノ内線"], -0.4, 0.6),
    "北千住": ("北千住", ["JR常磐線", "東武スカイツリーライン"], 3.2, 8.2),
    "横浜": ("横浜", ["JR東海道線", "東急東横線", "京急線"], -6.0, -25.0),
    "武蔵小杉": ("武蔵小杉", ["JR南武線", "東急東横線"], -6.5, -13.0),
    "立川": ("立川", ["JR中央線", "JR南武線"], -32.0, 3.0),
    "大宮": ("大宮", ["JR京浜東北線", "JR埼京線"], -3.0, 27.0),
    "千葉": ("千葉", ["JR総武線"], 32.0, 3.0),
    "船橋": ("船橋", ["JR総武線", "東武野田線"], 20.0, 3.5),
    "町田": ("町田", ["JR横浜線", "小田急線"], -28.0, -14.0),
    "川崎": ("川崎", ["JR東海道線", "JR南武線"], -4.0, -16.0),
    "三鷹": ("三鷹", ["JR中央線"], -17.0, 2.0),
    "赤羽": ("赤羽", ["JR京浜東北線", "JR埼京線"], -2.0, 9.0),
    "錦糸町": ("錦糸町", ["JR総武線", "東京メトロ半蔵門線"], 4.6, 1.6),
}

# 乗換の起きやすさ: ハブ度が高いほど乗換が少なく済む
HUB_SCORE: dict[str, int] = {
    "東京": 5,
    "新宿": 5,
    "品川": 4,
    "渋谷": 4,
    "池袋": 4,
    "横浜": 4,
    "大手町": 3,
    "秋葉原": 3,
    "上野": 3,
    "武蔵小杉": 3,
    "北千住": 3,
    "大宮": 3,
    "川崎": 2,
    "船橋": 2,
    "錦糸町": 2,
    "赤羽": 2,
    "立川": 2,
    "町田": 2,
    "千葉": 1,
    "三鷹": 1,
}

# 速達性と運賃水準は駅によって異なる。都心ターミナルは速いが高く、
# 郊外の結節点は遅いが安い。この非相関があるからこそ、sum / minimax / cost で
# 結論が割れる（＝公平性ポリシーを宣言する意味が生まれる）。
EXPRESS_FACTOR: dict[str, float] = {
    "東京": 1.30, "新宿": 1.30, "品川": 1.28, "渋谷": 1.22, "池袋": 1.22,
    "大手町": 1.18, "上野": 1.15, "秋葉原": 1.12, "横浜": 1.15, "大宮": 1.12,
    "武蔵小杉": 1.02, "北千住": 1.02, "川崎": 1.0, "赤羽": 1.0, "錦糸町": 0.98,
    "船橋": 0.95, "立川": 0.95, "町田": 0.92, "三鷹": 0.92, "千葉": 0.9,
}

FARE_FACTOR: dict[str, float] = {
    "東京": 1.22, "大手町": 1.20, "新宿": 1.18, "品川": 1.16, "渋谷": 1.15,
    "池袋": 1.12, "上野": 1.08, "秋葉原": 1.06, "横浜": 1.02, "大宮": 1.0,
    "武蔵小杉": 0.90, "川崎": 0.88, "北千住": 0.86, "赤羽": 0.85, "錦糸町": 0.84,
    "三鷹": 0.86, "船橋": 0.82, "町田": 0.82, "立川": 0.80, "千葉": 0.78,
}

# 当日実況のモック。ここを実APIに差し替えると本物の運行情報になる。
MOCK_DISRUPTIONS: dict[str, ServiceDisruption] = {}


def _distance_km(a: str, b: str) -> float:
    ax, ay = STATIONS[a][2], STATIONS[a][3]
    bx, by = STATIONS[b][2], STATIONS[b][3]
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _known(station: str) -> str:
    """未知の駅名は最も近いハブとして扱い、モックでも落ちないようにする。"""
    if station in STATIONS:
        return station
    return "東京"


class MockTransitAdapter(TransitPort):
    name = "mock"

    async def candidate_hubs(
        self, origin_stations: list[str], *, limit: int = 6
    ) -> list[tuple[str, str]]:
        origins = [_known(s) for s in origin_stations]

        def hub_cost(station: str) -> tuple[float, int]:
            total = sum(_distance_km(station, o) for o in origins)
            return (total - HUB_SCORE.get(station, 0) * 3.0, -HUB_SCORE.get(station, 0))

        ranked = sorted(STATIONS.keys(), key=hub_cost)
        return [(STATIONS[s][0], s) for s in ranked[:limit]]

    async def route(
        self,
        *,
        uid: str,
        from_station: str,
        to_station: str,
        arrive_by: datetime,
    ) -> RouteLeg:
        await asyncio.sleep(0)  # 実APIと同じく待ちが入る前提のシグネチャ
        src, dst = _known(from_station), _known(to_station)
        km = _distance_km(src, dst)

        if src == dst:
            duration, fare, transfers = 0, 0, 0
        else:
            # 表定速度 ~32km/h 相当 + 乗換・待ちの固定費。
            # 速達性と運賃水準は発着駅の平均で効かせる。
            transfers = max(0, 2 - (HUB_SCORE.get(src, 0) + HUB_SCORE.get(dst, 0)) // 4)
            express = (EXPRESS_FACTOR.get(src, 1.0) + EXPRESS_FACTOR.get(dst, 1.0)) / 2
            fare_factor = (FARE_FACTOR.get(src, 1.0) + FARE_FACTOR.get(dst, 1.0)) / 2
            duration = int(round(km * 1.9 / express + 8 + transfers * 5))
            fare = int(round((140 + km * 13.5) * fare_factor / 10) * 10)

        # 遅延中の路線を使う場合は所要時間に反映
        lines = sorted(set(STATIONS[src][1]) | set(STATIONS[dst][1]))
        delay = sum(
            d.delay_minutes for line, d in MOCK_DISRUPTIONS.items() if line in lines
        )
        duration += delay

        depart_at = arrive_by - timedelta(minutes=duration)
        return RouteLeg(
            uid=uid,
            from_station=from_station,
            to_station=to_station,
            duration_minutes=duration,
            fare_yen=fare,
            transfers=transfers,
            depart_at=depart_at,
            arrive_at=arrive_by,
            lines=lines[:3],
        )

    async def disruptions(self, lines: list[str]) -> list[ServiceDisruption]:
        return [MOCK_DISRUPTIONS[line] for line in lines if line in MOCK_DISRUPTIONS]

    # -- デモ用の操作（実APIでは存在しない） ------------------------------

    @staticmethod
    def inject_disruption(disruption: ServiceDisruption) -> None:
        MOCK_DISRUPTIONS[disruption.line] = disruption

    @staticmethod
    def clear_disruptions() -> None:
        MOCK_DISRUPTIONS.clear()
