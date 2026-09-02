"""LLM なしの依頼解釈（StubLlmAdapter）のテスト。"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.adapters.mock.llm import StubLlmAdapter, parse_datetime

# 2026-08-29 は土曜日
NOW = datetime(2026, 8, 29, 10, 0)


@pytest.fixture
def llm() -> StubLlmAdapter:
    return StubLlmAdapter(now_factory=lambda: NOW)


async def test_parses_relative_weekday_and_time(llm):
    out = await llm.parse_request("来週火曜14時、この6人で")
    assert out["starts_at"] == "2026-09-01T14:00:00"
    assert out["headcount"] == 6


async def test_parses_duration_and_equipment(llm):
    out = await llm.parse_request("2時間、ホワイトボードとプロジェクタのある部屋で")
    assert out["duration_minutes"] == 120
    assert set(out["equipment"]) == {"ホワイトボード", "プロジェクタ"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("いちばん遠い人の負担を減らしたい", "minimax"),
        ("なるべく安く済ませたい", "cost"),
        ("全員の移動を最短にしたい", "sum"),
    ],
)
async def test_infers_fairness_policy(llm, text, expected):
    assert (await llm.parse_request(text))["policy"] == expected


async def test_parses_absolute_date():
    dt = parse_datetime("9月10日 15時30分に集合", now=NOW)
    assert dt == datetime(2026, 9, 10, 15, 30)


async def test_tomorrow(llm):
    out = await llm.parse_request("明日16時から")
    assert out["starts_at"] == "2026-08-30T16:00:00"


async def test_kanji_headcount(llm):
    assert (await llm.parse_request("六人で打合せ"))["headcount"] == 6


async def test_participant_mentions(llm):
    out = await llm.parse_request("@tanaka @sato で来週水曜10時")
    assert out["participant_names"] == ["tanaka", "sato"]


async def test_unparseable_text_returns_empty(llm):
    assert await llm.parse_request("よろしくお願いします") == {}
