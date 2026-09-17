"""LangGraph wiring for the analyst agent.

    START → plan ─┬─ analysis ─→ generate_code → execute ─┬─ ok / give up → report → verify → END
                  │                    ↑                   │
                  │                    └──── retry ────────┘
                  └─ direct ─→ respond_direct → END

`verify` is deterministic: it audits the write-up against the executed output and
costs no model tokens.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agent.llm import StructuredLLM
from app.agent.nodes import AnalystNodes
from app.agent.state import AgentState
from app.sandbox.runner import SandboxRunner


def build_graph(llm: StructuredLLM, runner: SandboxRunner, max_repair_attempts: int = 2):
    nodes = AnalystNodes(llm, runner, max_repair_attempts)
    graph = StateGraph(AgentState)

    graph.add_node("plan", nodes.plan)
    graph.add_node("generate_code", nodes.generate_code)
    graph.add_node("execute", nodes.execute)
    graph.add_node("report", nodes.report)
    graph.add_node("verify", nodes.verify)
    graph.add_node("respond_direct", nodes.respond_direct)

    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", nodes.route_after_plan,
                                {"analysis": "generate_code", "direct": "respond_direct"})
    graph.add_edge("generate_code", "execute")
    graph.add_conditional_edges("execute", nodes.route_after_execute,
                                {"retry": "generate_code", "report": "report"})
    graph.add_edge("report", "verify")
    graph.add_edge("verify", END)
    graph.add_edge("respond_direct", END)
    return graph.compile()
