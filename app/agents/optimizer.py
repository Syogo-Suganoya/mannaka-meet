"""最適化エージェント（設計書 §6）。

候補エリアの列挙 → N人分経路の並列取得 → 公平性ポリシーによる評価。
算出・比較までは自律実行し、決定は行わない。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from app.domain import optimization
from app.domain.models import (
    CandidateArea,
    FairnessPolicy,
    MeetingRequest,
    OptimizationResult,
)
from app.ports.llm import LlmPort
from app.ports.transit import TransitPort
from app.services.audit import AuditService


class OptimizerAgent:
    actor = "optimizer-agent"

    def __init__(
        self,
        transit: TransitPort,
        audit: AuditService,
        llm: LlmPort,
        *,
        candidate_limit: int = 6,
        buffer_minutes: int = 10,
    ) -> None:
        self._transit = transit
        self._audit = audit
        self._llm = llm
        self._candidate_limit = candidate_limit
        self._buffer_minutes = buffer_minutes

    async def unknown_stations(self, stations: list[str]) -> list[str]:
        """経路を引けない駅名を返す。

        黙って近くの駅に寄せると、その人だけ0分0円で来られることになり、
        どの候補が最良かという結論そのものが狂う。受け付ける前に弾く。
        列挙できないプロバイダ（実API）では常に空を返す。
        """
        known = await self._transit.known_stations()
        if known is None:
            return []
        seen: list[str] = []
        for station in stations:
            name = (station or "").strip()
            if name and name not in known and name not in seen:
                seen.append(name)
        return seen

    async def evaluate(
        self, *, meeting_id: str, request: MeetingRequest, policy: FairnessPolicy
    ) -> OptimizationResult:
        origins = [p.origin_station for p in request.participants]
        hubs = await self._transit.candidate_hubs(origins, limit=self._candidate_limit)
        arrive_by = request.starts_at - timedelta(minutes=self._buffer_minutes)

        participants = [(p.uid, p.origin_station) for p in request.participants]
        legs_per_hub = await asyncio.gather(
            *(
                self._transit.routes_for(
                    participants=participants, to_station=station, arrive_by=arrive_by
                )
                for _, station in hubs
            )
        )

        candidates: list[CandidateArea] = [
            optimization.summarize(area, station, legs)
            for (area, station), legs in zip(hubs, legs_per_hub)
        ]
        result = optimization.optimize(candidates, policy)
        result.explanation = await self._explain(result, request)

        # 地図を描くための座標。出せないプロバイダなら空のまま
        coords = await self._transit.coordinates(
            sorted({*origins, *(c.station for c in candidates)})
        )
        result.places = {name: [x, y] for name, (x, y) in coords.items()}

        await self._audit.record(
            action="candidates.evaluated",
            actor=self.actor,
            meeting_id=meeting_id,
            policy=policy.value,
            policy_label=policy.label,
            transit_provider=self._transit.name,
            participant_count=len(request.participants),
            candidates=[
                {
                    "station": c.station,
                    "rank": c.rank,
                    "score": c.score,
                    "total_minutes": c.total_minutes,
                    "max_minutes": c.max_minutes,
                    "total_fare_yen": c.total_fare_yen,
                }
                for c in result.candidates
            ],
            policy_comparison=optimization.compare_policies(candidates),
        )
        return result

    async def compare_policies(self, candidates: list[CandidateArea]) -> dict:
        """3ポリシーの結論の差を返す（どれを選ぶかは主催者の判断）。"""
        return optimization.compare_policies(candidates)

    async def _explain(self, result: OptimizationResult, request: MeetingRequest) -> str:
        best = result.best
        n = len(best.legs) or 1
        fallback = (
            f"{result.policy.label}（{result.policy.value}）で評価した結果、"
            f"{best.area}（{best.station}）が最良です。"
            f"参加者{n}名の合計移動時間は{best.total_minutes}分"
            f"（最長{best.max_minutes}分、平均{best.total_minutes // n}分）、"
            f"総運賃は{best.total_fare_yen:,}円、負担の偏りは"
            f"{optimization.unfairness(best)}分です。"
        )
        prompt = (
            "あなたは会議場所の最適化結果を説明するアシスタントです。"
            "個人名や誰がどこから来るかには触れず、集計値だけで3文以内の日本語で説明してください。\n"
            f"採用ポリシー: {result.policy.label}\n"
            f"候補: "
            + " / ".join(
                f"{c.station}(合計{c.total_minutes}分,最長{c.max_minutes}分,{c.total_fare_yen}円)"
                for c in result.candidates[:3]
            )
        )
        return await self._llm.explain(prompt, fallback=fallback)
