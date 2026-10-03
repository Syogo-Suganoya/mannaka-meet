"""経路が引けないときの振る舞い。

実APIで起きた 500 の再発防止。駅すぱあとは「三鷹 → 三鷹」のような同じ駅どうしの
経路を返さず、それが候補算出ごと落としていた。
"""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from app.adapters.ekispert.transit import EkispertTransitAdapter
from app.adapters.mock.transit import MockTransitAdapter
from app.deps import Container
from tests.factories import SEED_PARTICIPANTS

WHEN = datetime(2026, 10, 6, 14, 0)


async def test_ekispert_same_station_is_zero_without_asking():
    """出発駅＝候補駅なら、問い合わせずに 0分・0円・乗換0回 とする。"""

    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"同じ駅なのに問い合わせた: {request.url}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    adapter = EkispertTransitAdapter("dummy-key", client=client)

    leg = await adapter.route(uid="p1", from_station="三鷹", to_station="三鷹", arrive_by=WHEN)

    assert (leg.duration_minutes, leg.fare_yen, leg.transfers) == (0, 0, 0)
    await client.aclose()


class OneHubUnreachable(MockTransitAdapter):
    """ある1駅にだけ、誰の経路も引けないモック。"""

    def __init__(self, broken: str) -> None:
        super().__init__()
        self.broken = broken

    async def route(self, *, uid, from_station, to_station, arrive_by):
        if to_station == self.broken:
            raise RuntimeError(f"経路が見つかりません: {from_station} -> {to_station}")
        return await super().route(
            uid=uid, from_station=from_station, to_station=to_station, arrive_by=arrive_by
        )


async def test_unreachable_candidate_is_dropped_not_fatal(settings):
    """1候補で経路が引けなくても、残りの候補で結果を出す。外したことは記録する。"""
    hubs = await MockTransitAdapter().candidate_hubs(
        [s for _, s in SEED_PARTICIPANTS], limit=settings.candidate_limit
    )
    broken = hubs[0][1]
    container = Container(settings, transit=OneHubUnreachable(broken))

    meeting = await container.orchestrator.create_meeting(
        participants=SEED_PARTICIPANTS, starts_at=WHEN
    )

    stations = [c.station for c in meeting.optimization.candidates]
    assert broken not in stations
    assert len(stations) == len(hubs) - 1

    logs = await container.audit.list(meeting.meeting_id)
    dropped = [log for log in logs if log.action == "candidates.dropped"]
    assert dropped and dropped[0].payload["dropped"][0]["station"] == broken


class NothingReachable(MockTransitAdapter):
    async def route(self, **kwargs):
        raise RuntimeError("経路が見つかりません")


async def test_no_reachable_candidate_is_a_readable_error(settings):
    """全候補で経路が引けないときは、500 ではなく理由の分かるエラーにする（API では 400）。"""
    container = Container(settings, transit=NothingReachable())

    with pytest.raises(ValueError, match="経路が見つかりませんでした"):
        await container.orchestrator.create_meeting(
            participants=SEED_PARTICIPANTS, starts_at=WHEN
        )


# -- 駅すぱあとの駅名解決 ------------------------------------------------------
# 応答の形は実APIで確かめたもの（/station/light・/station・/search/course/extreme）。

LIGHT = {
    "大宮": [
        ("大宮(埼玉県)", "21987", "埼玉県"),
        ("大宮(京都府)", "25616", "京都府"),
        ("大宮公園", "21988", "埼玉県"),
    ],
    "三鷹": [("三鷹", "22986", "東京都"), ("三鷹台", "22987", "東京都")],
}


def fake_ekispert(seen: list[str]):
    def handle(request: httpx.Request) -> httpx.Response:
        path, q = request.url.path, request.url.params
        if path.endswith("/station/light"):
            points = [
                {"Station": {"Name": n, "code": c}, "Prefecture": {"Name": p}}
                for n, c, p in LIGHT.get(q["name"], [])
            ]
            return httpx.Response(200, json={"ResultSet": {"Point": points}})
        if path.endswith("/search/course/extreme"):
            seen.append(q["viaList"])
            course = {
                "Route": {"timeOnBoard": "40", "timeOther": "8", "transferCount": "1"},
                "Price": [{"kind": "FareSummary", "Oneway": "660"}],
            }
            return httpx.Response(200, json={"ResultSet": {"Course": course}})
        raise AssertionError(f"想定外の問い合わせ: {request.url}")

    return handle


async def test_ekispert_resolves_ambiguous_names_to_metro_codes():
    """「大宮」は同名駅があり名前のままだと E102 になる。首都圏のコードで引く。"""
    seen: list[str] = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake_ekispert(seen)))
    adapter = EkispertTransitAdapter("dummy-key", client=client)

    leg = await adapter.route(uid="p1", from_station="大宮", to_station="三鷹", arrive_by=WHEN)

    assert seen == ["21987:22986"]
    assert (leg.from_station, leg.duration_minutes, leg.fare_yen) == ("大宮", 48, 660)
    await client.aclose()


async def test_ekispert_rejects_unknown_names_before_routing():
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake_ekispert([])))
    adapter = EkispertTransitAdapter("dummy-key", client=client)

    assert await adapter.unknown_stations(["大宮", "どこでもない駅", "三鷹"]) == ["どこでもない駅"]
    await client.aclose()


def test_gemini_timeout_is_not_below_the_api_minimum():
    """Gemini API は10秒未満の締切を 400 で拒否する。下回ると全呼び出しが失敗する。"""
    from app.adapters.google.llm import TIMEOUT_MS

    assert TIMEOUT_MS >= 10_000
