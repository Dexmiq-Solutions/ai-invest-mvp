from unittest.mock import MagicMock, patch
from app.graph.graph import (
    build_graph,
    create_investigation_plan_node,
    execute_plan_and_aggregate_node,
)
from app.agents.investigation_orchestrator import InvestigationOrchestrator


def test_initial_graph():
    graph = build_graph()

    result = graph.invoke({
        "problem": "Test investigation",
    })

    assert result["problem"] == "Test investigation"
    assert result["current_step"] == "validation_failed"


def test_create_investigation_plan_node_uses_state_factory():
    """Verify that create_investigation_plan_node passes state['llm_factory'] to InvestigationOrchestrator."""
    mock_factory = MagicMock()
    mock_orch_instance = MagicMock()
    mock_orch_instance.create_initial_plan.return_value = [
        {"task_id": "task-01", "assigned_agent": "code_investigator"}
    ]

    with patch("app.graph.graph.InvestigationOrchestrator", return_value=mock_orch_instance) as mock_orch_cls:
        state = {
            "session_id": "test-session",
            "problem": "Fix null pointer in auth module",
            "llm_factory": mock_factory,
        }
        result = create_investigation_plan_node(state)

        mock_orch_cls.assert_called_once_with(llm_factory=mock_factory)
        assert result["investigation_plan"] == [{"task_id": "task-01", "assigned_agent": "code_investigator"}]
        assert result["selected_investigators"] == ["code_investigator"]


def test_create_investigation_plan_node_uses_default_factory_when_omitted():
    """Verify that create_investigation_plan_node falls back to get_llm_factory() when not in state."""
    mock_default_factory = MagicMock()
    mock_orch_instance = MagicMock()
    mock_orch_instance.create_initial_plan.return_value = []

    with patch("app.graph.graph.get_llm_factory", return_value=mock_default_factory) as mock_get_factory:
        with patch("app.graph.graph.InvestigationOrchestrator", return_value=mock_orch_instance) as mock_orch_cls:
            state = {
                "session_id": "test-session-default",
                "problem": "Investigate timeout error",
            }
            create_investigation_plan_node(state)

            mock_get_factory.assert_called_once()
            mock_orch_cls.assert_called_once_with(llm_factory=mock_default_factory)


def test_execute_plan_and_aggregate_node_uses_state_factory():
    """Verify that execute_plan_and_aggregate_node passes state['llm_factory'] to InvestigationOrchestrator."""
    mock_factory = MagicMock()
    mock_orch_instance = MagicMock()
    mock_orch_instance.execute_plan_single_pass.return_value = {
        "execution_status": "completed",
        "workflow_status": "completed",
        "evidence": [],
        "completed_tasks": [],
        "failed_tasks": [],
    }

    with patch("app.graph.graph.InvestigationOrchestrator", return_value=mock_orch_instance) as mock_orch_cls:
        state = {
            "session_id": "test-session",
            "problem": "Memory leak on reload",
            "llm_factory": mock_factory,
            "investigation_plan": [{"task_id": "task-01", "assigned_agent": "code_investigator"}],
        }
        result = execute_plan_and_aggregate_node(state)

        mock_orch_cls.assert_called_once_with(llm_factory=mock_factory)
        assert result["current_step"] == "investigation_completed"


def test_specialist_agents_receive_llm_factory():
    """Verify that InvestigationOrchestrator propagates llm_factory to planner, subcomponents, and specialist agents."""
    mock_factory = MagicMock()
    orch = InvestigationOrchestrator(llm_factory=mock_factory)

    assert orch.llm_factory is mock_factory
    assert orch.planner.llm_factory is mock_factory
    assert orch.reviewer.llm_factory is mock_factory
    assert orch.synthesizer.llm_factory is mock_factory

    with patch("app.agents.investigation_orchestrator.run_code_investigator") as mock_code:
        mock_code.return_value = {"status": "completed"}
        orch.code_agent({"task_id": "t1"}, {}, {})
        mock_code.assert_called_once_with({"task_id": "t1"}, {}, {}, llm_factory=mock_factory)

    with patch("app.agents.investigation_orchestrator.run_dependency_impact_investigator") as mock_dep:
        mock_dep.return_value = {"status": "completed"}
        orch.dependency_agent({"task_id": "t2"}, {}, {})
        mock_dep.assert_called_once_with({"task_id": "t2"}, {}, {}, llm_factory=mock_factory)

    with patch("app.agents.investigation_orchestrator.run_runtime_error_investigator") as mock_rt:
        mock_rt.return_value = {"status": "completed"}
        orch.runtime_agent({"task_id": "t3"}, {}, {})
        mock_rt.assert_called_once_with({"task_id": "t3"}, {}, {}, llm_factory=mock_factory)