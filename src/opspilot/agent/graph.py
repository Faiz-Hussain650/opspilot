"""Wire the nodes into a LangGraph state machine.

    START
      |
    guard_input --blocked--> END
      |
    retrieve <-----------------+
      |                        |
    grade_documents --rewrite--> rewrite_query
      |          \
      |           give_up --> no_context --> END
    generate
      |
    check_grounding --> END

This is "corrective RAG": instead of blindly trusting the first search, the
agent checks its own retrieval and retries with a better query.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from opspilot.agent.nodes import AgentNodes
from opspilot.agent.routing import route_after_grading, route_after_guard
from opspilot.agent.state import AgentState


def build_agent_graph(nodes: AgentNodes, max_rewrites: int = 2):
    graph = StateGraph(AgentState)

    graph.add_node("guard_input", nodes.guard_input)
    graph.add_node("retrieve", nodes.retrieve)
    graph.add_node("grade_documents", nodes.grade_documents)
    graph.add_node("rewrite_query", nodes.rewrite_query)
    graph.add_node("generate", nodes.generate)
    graph.add_node("no_context", nodes.no_context)
    graph.add_node("check_grounding", nodes.check_grounding)

    def _after_grading(state: AgentState) -> str:
        return route_after_grading(state, max_rewrites)

    graph.add_edge(START, "guard_input")
    graph.add_conditional_edges(
        "guard_input", route_after_guard, {"blocked": END, "continue": "retrieve"}
    )
    graph.add_edge("retrieve", "grade_documents")
    graph.add_conditional_edges(
        "grade_documents",
        _after_grading,
        {"generate": "generate", "rewrite": "rewrite_query", "give_up": "no_context"},
    )
    graph.add_edge("rewrite_query", "retrieve")
    graph.add_edge("generate", "check_grounding")
    graph.add_edge("check_grounding", END)
    graph.add_edge("no_context", END)

    return graph.compile()
