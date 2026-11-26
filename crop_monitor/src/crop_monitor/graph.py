"""LangGraph 그래프 구성.

    preprocess ─┬─(작물 등록됨)─┬─ growth_stage ─┐   생장 단계 (작물별 모듈)
                │               ├─ disease ──────┼─ merge ─ report ─ END
                │               └─ height ───────┘   이상 상태 (작물별 모듈) / 높이 (공통)
                └─(미등록 작물)── unknown_crop ─ END
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from crop_monitor import nodes
from crop_monitor.nodes.route import PARALLEL_NODES
from crop_monitor.state import PipelineState


def build_graph():
    g = StateGraph(PipelineState)

    g.add_node("preprocess", nodes.preprocess)
    g.add_node("growth_stage", nodes.growth_stage)
    g.add_node("disease", nodes.disease)
    g.add_node("height", nodes.height)
    g.add_node("merge", nodes.merge_results)
    g.add_node("report", nodes.make_report)
    g.add_node("unknown_crop", nodes.unknown_crop)

    g.add_edge(START, "preprocess")
    g.add_conditional_edges("preprocess", nodes.route_by_crop, [*PARALLEL_NODES, "unknown_crop"])
    g.add_edge(PARALLEL_NODES, "merge")  # 세 모듈이 모두 끝나면 병합
    g.add_edge("merge", "report")
    g.add_edge("report", END)
    g.add_edge("unknown_crop", END)

    return g.compile()
