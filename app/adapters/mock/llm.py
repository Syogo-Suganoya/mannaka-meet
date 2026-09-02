"""LLM を使わない決定的な依頼解釈。

「来週火曜14時、この6人で」のような日本語の依頼文から日時・人数・設備・
公平性ポリシーを取り出す。Gemini が使えない環境でも全機能が動くようにする。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from app.ports.llm import LlmPort

WEEKDAYS = {"月": 0, "火": 1, "水": 2, "木": 3, "金": 4, "土": 5, "日": 6}

_KANJI_NUM = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
    "七": 7, "八": 8, "九": 9, "十": 10,
}

EQUIPMENT_KEYWORDS = {
    "プロジェクタ": ["プロジェクタ", "プロジェクター", "投影"],
    "ホワイトボード": ["ホワイトボード", "WB"],
    "モニタ": ["モニタ", "モニター", "ディスプレイ"],
    "Web会議設備": ["web会議", "ウェブ会議", "オンライン接続", "ハイブリッド"],
    "電源": ["電源", "コンセント"],
}

POLICY_KEYWORDS = {
    "minimax": ["公平", "遠い人", "平準", "負担をならす", "いちばん遠い"],
    "cost": ["安く", "経費", "運賃", "コスト", "予算重視"],
    "sum": ["最短", "合計時間", "時短", "早く"],
}


def _to_int(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    if all(c in _KANJI_NUM for c in text):
        if text == "十":
            return 10
        if len(text) == 2 and text[0] == "十":
            return 10 + _KANJI_NUM[text[1]]
        if len(text) == 2 and text[1] == "十":
            return _KANJI_NUM[text[0]] * 10
        return _KANJI_NUM.get(text)
    return None


def parse_datetime(text: str, *, now: datetime) -> datetime | None:
    """相対表現（来週火曜/明日/今週金曜）と時刻を解釈する。"""
    hour, minute = 14, 0
    m = re.search(r"(\d{1,2})\s*(?:時|:)\s*(\d{1,2})?", text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        if "午後" in text and hour < 12:
            hour += 12

    base: datetime | None = None
    md = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if md:
        month, day = int(md.group(1)), int(md.group(2))
        year = now.year + (1 if month < now.month else 0)
        base = now.replace(year=year, month=month, day=day)
    elif "明後日" in text:
        base = now + timedelta(days=2)
    elif "明日" in text:
        base = now + timedelta(days=1)
    else:
        wd = re.search(r"(来週|今週|再来週)?\s*(?:次の)?\s*([月火水木金土日])曜", text)
        if wd:
            offset_weeks = {"来週": 1, "再来週": 2, "今週": 0, None: 0}[wd.group(1)]
            target = WEEKDAYS[wd.group(2)]
            if wd.group(1) is None:
                # 週の指定がなければ「次に来るその曜日」
                base = now + timedelta(days=(target - now.weekday()) % 7 or 7)
            else:
                # 月曜始まりの週を基準に「来週/再来週の◯曜」を解決する
                week_start = now - timedelta(days=now.weekday())
                base = week_start + timedelta(days=7 * offset_weeks + target)
                if base.date() <= now.date():
                    base += timedelta(days=7)

    if base is None:
        if not m:
            return None
        base = now + timedelta(days=1)  # 時刻だけの指定は翌日とみなす

    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def parse_headcount(text: str) -> int | None:
    m = re.search(r"([0-9]+|[一二三四五六七八九十]+)\s*(?:人|名)", text)
    return _to_int(m.group(1)) if m else None


def parse_duration(text: str) -> int | None:
    m = re.search(r"([0-9]+)\s*時間", text)
    if m:
        return int(m.group(1)) * 60
    m = re.search(r"([0-9]+)\s*分(?:間|の)", text)
    if m:
        return int(m.group(1))
    return None


def parse_equipment(text: str) -> list[str]:
    lowered = text.lower()
    return [
        name
        for name, keys in EQUIPMENT_KEYWORDS.items()
        if any(k.lower() in lowered for k in keys)
    ]


def parse_policy(text: str) -> str | None:
    for policy, keys in POLICY_KEYWORDS.items():
        if any(k in text for k in keys):
            return policy
    return None


def parse_participants(text: str) -> list[str]:
    """「@田中 @佐藤」「田中さん、佐藤さん」形式の氏名を拾う。"""
    names = re.findall(r"@([\w一-龥ぁ-んァ-ヶ]+)", text)
    if names:
        return names
    return re.findall(r"([一-龥ァ-ヶ]{2,4})\s*さん", text)


class StubLlmAdapter(LlmPort):
    """ルールベースの解釈器。Gemini 不在時の既定実装。"""

    name = "stub"

    def __init__(self, now_factory=None) -> None:
        self._now = now_factory or datetime.now

    async def parse_request(self, text: str) -> dict:
        now = self._now()
        out: dict = {}
        if (dt := parse_datetime(text, now=now)) is not None:
            out["starts_at"] = dt.isoformat()
        if (n := parse_headcount(text)) is not None:
            out["headcount"] = n
        if (d := parse_duration(text)) is not None:
            out["duration_minutes"] = d
        if equipment := parse_equipment(text):
            out["equipment"] = equipment
        if policy := parse_policy(text):
            out["policy"] = policy
        if names := parse_participants(text):
            out["participant_names"] = names
        return out

    async def explain(self, prompt: str, *, fallback: str) -> str:
        return fallback
