"""公平性ポリシー付きの候補地評価。

外部APIには依存しない純粋関数群。経路の取得は ports.transit 側の責務。
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.domain.models import CandidateArea, FairnessPolicy, OptimizationResult, RouteLeg


def summarize(area: str, station: str, legs: list[RouteLeg]) -> CandidateArea:
    """1候補地について、内訳から集計値を組み立てる。"""
    return CandidateArea(
        area=area,
        station=station,
        legs=legs,
        total_minutes=sum(leg.duration_minutes for leg in legs),
        max_minutes=max((leg.duration_minutes for leg in legs), default=0),
        total_fare_yen=sum(leg.fare_yen for leg in legs),
        total_transfers=sum(leg.transfers for leg in legs),
    )


def score(candidate: CandidateArea, policy: FairnessPolicy) -> float:
    """小さいほど良いスコア。

    - sum:     合計移動時間（分）
    - minimax: いちばん遠い人の所要時間。同点は合計時間で解く
    - cost:    総運賃（円）。同点は合計時間で解く
    """
    if policy is FairnessPolicy.SUM:
        return float(candidate.total_minutes)
    if policy is FairnessPolicy.MINIMAX:
        return candidate.max_minutes * 1000.0 + candidate.total_minutes
    if policy is FairnessPolicy.COST:
        return candidate.total_fare_yen * 1000.0 + candidate.total_minutes
    raise ValueError(f"unknown policy: {policy}")


def unfairness(candidate: CandidateArea) -> int:
    """負担の偏り = 最長と最短の所要時間差（分）。提示の補助指標。"""
    durations = [leg.duration_minutes for leg in candidate.legs]
    if not durations:
        return 0
    return max(durations) - min(durations)


def rank_candidates(
    candidates: list[CandidateArea], policy: FairnessPolicy
) -> list[CandidateArea]:
    """ポリシーに従ってスコア付けし、良い順に並べて rank を振る。"""
    scored: list[CandidateArea] = []
    for candidate in candidates:
        c = candidate.model_copy(deep=True)
        c.score = score(c, policy)
        scored.append(c)

    scored.sort(key=lambda c: (c.score, c.station))
    for i, c in enumerate(scored, start=1):
        c.rank = i
        c.rationale = _rationale(c, policy)
    return scored


def _rationale(candidate: CandidateArea, policy: FairnessPolicy) -> str:
    n = len(candidate.legs) or 1
    avg = candidate.total_minutes // n
    base = (
        f"{candidate.area}（{candidate.station}）: "
        f"合計{candidate.total_minutes}分 / 最長{candidate.max_minutes}分 / "
        f"平均{avg}分 / 総額{candidate.total_fare_yen:,}円 / 偏り{unfairness(candidate)}分"
    )
    focus = {
        FairnessPolicy.SUM: "合計移動時間で評価",
        FairnessPolicy.MINIMAX: "最長移動時間の小ささで評価",
        FairnessPolicy.COST: "総運賃で評価",
    }[policy]
    return f"{base}（{focus}）"


def optimize(
    candidates: list[CandidateArea],
    policy: FairnessPolicy,
    *,
    explanation: str | None = None,
    now: datetime | None = None,
) -> OptimizationResult:
    ranked = rank_candidates(candidates, policy)
    return OptimizationResult(
        policy=policy,
        candidates=ranked,
        evaluated_at=now or datetime.now(timezone.utc),
        explanation=explanation,
    )


def compare_policies(candidates: list[CandidateArea]) -> dict[str, dict]:
    """3ポリシーそれぞれの最良候補を並べ、選択の差分を可視化する。"""
    out: dict[str, dict] = {}
    for policy in FairnessPolicy:
        ranked = rank_candidates(candidates, policy)
        best = ranked[0]
        out[policy.value] = {
            "label": policy.label,
            "station": best.station,
            "area": best.area,
            "total_minutes": best.total_minutes,
            "max_minutes": best.max_minutes,
            "total_fare_yen": best.total_fare_yen,
            "unfairness_minutes": unfairness(best),
        }
    return out
