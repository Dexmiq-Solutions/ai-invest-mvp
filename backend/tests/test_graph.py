from app.graph.graph import build_graph


def test_initial_graph():
    graph = build_graph()

    result = graph.invoke({
        "problem": "Test investigation",
    })

    assert result["problem"] == "Test investigation"
    assert result["current_step"] == "validation_failed"