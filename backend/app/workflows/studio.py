"""LangGraph Studio entrypoint for the reusable career-analysis graph."""

from langgraph.graph.state import CompiledStateGraph


def build_studio_graph() -> CompiledStateGraph:
    """Construct the compiled graph through the same service wiring as FastAPI."""
    from app.api.deps import (
        get_career_analysis_graph,
        get_career_assessment_service,
        get_job_analysis_service,
        get_requirement_matching_service,
    )

    return get_career_analysis_graph(
        job_analysis_service=get_job_analysis_service(),
        requirement_matching_service=get_requirement_matching_service(),
        career_assessment_service=get_career_assessment_service(),
    ).compiled_graph


graph = build_studio_graph()
