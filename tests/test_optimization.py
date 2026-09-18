"""公平性ポリシーの評価関数（設計書 §5）のテスト。"""

from __future__ import annotations

from app.domain import optimization
from app.domain.models import CandidateArea, FairnessPolicy, RouteLeg


def leg(uid: str, minutes: int, fare: int, transfers: int = 1) -> RouteLeg:
    return RouteLeg(
        uid=uid,
        from_station="A",
        to_station="B",
        duration_minutes=minutes,
        fare_yen=fare,
        transfers=transfers,
    )


def candidate(name: str, legs: list[RouteLeg]) -> CandidateArea:
    return optimization.summarize(name, name, legs)


# 合計は小さいが1人だけ極端に遠い
LOPSIDED = candidate("片寄り", [leg("a", 10, 300), leg("b", 10, 300), leg("c", 70, 900)])
# 合計は大きいが全員そこそこ
BALANCED = candidate("均等", [leg("a", 32, 700), leg("b", 32, 700), leg("c", 33, 700)])
# 時間はかかるが安い
CHEAP = candidate("格安", [leg("a", 40, 200), leg("b", 40, 200), leg("c", 40, 200)])

ALL = [LOPSIDED, BALANCED, CHEAP]


def test_sum_prefers_smallest_total_time():
    ranked = optimization.rank_candidates(ALL, FairnessPolicy.SUM)
    assert ranked[0].area == "片寄り"  # 合計90分が最小


def test_minimax_prefers_smallest_worst_case():
    ranked = optimization.rank_candidates(ALL, FairnessPolicy.MINIMAX)
    assert ranked[0].area == "均等"  # 最長33分
    assert ranked[-1].area == "片寄り"  # 最長70分は最下位


def test_cost_prefers_cheapest():
    ranked = optimization.rank_candidates(ALL, FairnessPolicy.COST)
    assert ranked[0].area == "格安"


def test_policies_disagree_which_is_the_point():
    """3つの基準で結論が割れることが「宣言する意味」を生む。"""
    comparison = optimization.compare_policies(ALL)
    stations = {v["station"] for v in comparison.values()}
    assert len(stations) == 3


def test_unfairness_is_max_minus_min():
    assert optimization.unfairness(LOPSIDED) == 60
    assert optimization.unfairness(BALANCED) == 1


def test_summarize_aggregates_breakdown():
    assert LOPSIDED.total_minutes == 90
    assert LOPSIDED.max_minutes == 70
    assert LOPSIDED.total_fare_yen == 1500
    assert LOPSIDED.total_transfers == 3


def test_ranking_is_stable_for_ties():
    a = candidate("あ", [leg("a", 20, 500)])
    b = candidate("い", [leg("a", 20, 500)])
    ranked = optimization.rank_candidates([b, a], FairnessPolicy.SUM)
    assert [c.area for c in ranked] == ["あ", "い"]


def test_breakdown_names_each_participant_heaviest_first():
    """誰がどれだけ負担しているかを、名前と出発駅で示す。"""
    names = {leg.uid: f"{leg.uid}さん" for leg in LOPSIDED.legs}
    rows = LOPSIDED.breakdown(names)
    assert rows[0]["duration_minutes"] == 70  # 所要時間の降順
    assert rows[0]["participant"] == "cさん"  # 70分かかっている人
    assert all(r["origin_station"] for r in rows)


def test_breakdown_falls_back_to_symbols_without_names():
    rows = LOPSIDED.breakdown()
    assert [r["participant"] for r in rows] == ["参加者A", "参加者B", "参加者C"]
