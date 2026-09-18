"""アーキテクチャ図を生成する（mingrammer/diagrams）。

    docker compose --profile docs run --rm docs

docs/mannaka_architecture.png に、使っている技術スタックが一目で分かる図を出力する。
"""

from __future__ import annotations

from pathlib import Path

from diagrams import Cluster, Diagram, Edge
from diagrams.gcp.compute import Run
from diagrams.gcp.database import Firestore
from diagrams.gcp.ml import AIPlatform
from diagrams.gcp.operations import Logging
from diagrams.onprem.client import Users
from diagrams.programming.flowchart import Action

OUT = Path(__file__).resolve().parent

# 日本語ラベルを graphviz で描くためのフォント指定
FONT = "Noto Sans CJK JP"


def main() -> None:
    graph_attr = {
        "fontname": FONT,
        "fontsize": "18",
        "bgcolor": "white",
        "pad": "0.5",
        "ranksep": "1.2",
        "nodesep": "0.8",
    }
    node_attr = {"fontname": FONT, "fontsize": "12"}
    edge_attr = {"fontname": FONT, "fontsize": "11"}

    with Diagram(
        "マンナカ 技術スタック",
        filename=str(OUT / "mannaka_architecture"),
        direction="TB",
        show=False,
        outformat="png",
        graph_attr=graph_attr,
        node_attr=node_attr,
        edge_attr=edge_attr,
    ):
        users = Users("参加者・主催者")

        with Cluster("Cloud Run", graph_attr={"fontname": FONT, "bgcolor": "#eaf1f8"}):
            app = Run("FastAPI + ADK\nOrchestrator / Optimizer")

        with Cluster("外部API", graph_attr={"fontname": FONT, "bgcolor": "#fdf3ec"}):
            ekispert = Action("駅すぱあと API\nMCPサーバー")
            gemini = AIPlatform("Gemini API")

        with Cluster("データ", graph_attr={"fontname": FONT, "bgcolor": "#eef7ef"}):
            firestore = Firestore("Firestore")
            logging = Logging("Cloud Logging\n監査ログ")

        users >> Edge(label="参加者と出発駅", **edge_attr) >> app
        app >> Edge(label="候補地と負担の内訳", **edge_attr) >> users
        app >> Edge(label="結節駅・経路・運賃", **edge_attr) >> ekispert
        app >> Edge(label="依頼解釈・説明文", **edge_attr) >> gemini
        app >> firestore
        app >> logging


if __name__ == "__main__":
    main()
    for png in sorted(OUT.glob("*.png")):
        print(f"生成: {png.relative_to(OUT.parent)}")
