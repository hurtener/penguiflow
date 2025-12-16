"""Tests for penguiflow/planner/hints.py."""


from penguiflow.planner.hints import _PlanningHints


def test_planning_hints_from_mapping_empty():
    """Empty payload should return empty hints."""
    hints = _PlanningHints.from_mapping(None)
    assert hints.ordering_hints == ()
    assert hints.parallel_groups == ()
    assert hints.sequential_only == set()
    assert hints.disallow_nodes == set()
    assert hints.prefer_nodes == ()
    assert hints.max_parallel is None
    assert hints.budget_hints == {}


def test_planning_hints_from_mapping_empty_dict():
    """Empty dict should return empty hints."""
    hints = _PlanningHints.from_mapping({})
    assert hints.empty() is True


def test_planning_hints_from_mapping_full():
    """Full payload should populate all fields."""
    payload = {
        "ordering_hints": ["tool_a", "tool_b"],
        "parallel_groups": [["tool_c", "tool_d"], ["tool_e"]],
        "sequential_only": ["tool_f"],
        "disallow_nodes": ["tool_g"],
        "prefer_nodes": ["tool_h", "tool_i"],
        "max_parallel": 3,
        "budget_hints": {"max_hops": 10},
    }
    hints = _PlanningHints.from_mapping(payload)

    assert hints.ordering_hints == ("tool_a", "tool_b")
    assert hints.parallel_groups == (("tool_c", "tool_d"), ("tool_e",))
    assert hints.sequential_only == {"tool_f"}
    assert hints.disallow_nodes == {"tool_g"}
    assert hints.prefer_nodes == ("tool_h", "tool_i")
    assert hints.max_parallel == 3
    assert hints.budget_hints == {"max_hops": 10}


def test_planning_hints_max_parallel_from_budget():
    """max_parallel can be extracted from budget_hints."""
    payload = {
        "budget_hints": {"max_parallel": 5},
    }
    hints = _PlanningHints.from_mapping(payload)
    assert hints.max_parallel == 5


def test_planning_hints_max_parallel_invalid_type():
    """Non-int max_parallel should be None."""
    payload = {
        "max_parallel": "not_an_int",
        "budget_hints": {"max_parallel": "also_not_int"},
    }
    hints = _PlanningHints.from_mapping(payload)
    assert hints.max_parallel is None


def test_planning_hints_to_prompt_payload_empty():
    """Empty hints should return empty payload."""
    hints = _PlanningHints((), (), set(), set(), (), None, {})
    payload = hints.to_prompt_payload()
    assert payload == {}


def test_planning_hints_to_prompt_payload_max_parallel():
    """max_parallel should add constraints."""
    hints = _PlanningHints((), (), set(), set(), (), 4, {})
    payload = hints.to_prompt_payload()
    assert "constraints" in payload
    assert "max_parallel=4" in payload["constraints"]


def test_planning_hints_to_prompt_payload_sequential_only():
    """sequential_only should add constraints."""
    hints = _PlanningHints((), (), {"tool_a", "tool_b"}, set(), (), None, {})
    payload = hints.to_prompt_payload()
    assert "constraints" in payload
    assert "sequential_only=" in payload["constraints"]


def test_planning_hints_to_prompt_payload_both_constraints():
    """Both max_parallel and sequential_only should be in constraints."""
    hints = _PlanningHints((), (), {"seq_tool"}, set(), (), 2, {})
    payload = hints.to_prompt_payload()
    assert "constraints" in payload
    assert "max_parallel=2" in payload["constraints"]
    assert "sequential_only=" in payload["constraints"]


def test_planning_hints_to_prompt_payload_ordering():
    """ordering_hints should add preferred_order."""
    hints = _PlanningHints(("a", "b", "c"), (), set(), set(), (), None, {})
    payload = hints.to_prompt_payload()
    assert payload["preferred_order"] == ["a", "b", "c"]


def test_planning_hints_to_prompt_payload_parallel_groups():
    """parallel_groups should be converted to lists."""
    hints = _PlanningHints((), (("x", "y"), ("z",)), set(), set(), (), None, {})
    payload = hints.to_prompt_payload()
    assert payload["parallel_groups"] == [["x", "y"], ["z"]]


def test_planning_hints_to_prompt_payload_disallow():
    """disallow_nodes should be sorted list."""
    hints = _PlanningHints((), (), set(), {"z_node", "a_node"}, (), None, {})
    payload = hints.to_prompt_payload()
    assert payload["disallow_nodes"] == ["a_node", "z_node"]


def test_planning_hints_to_prompt_payload_prefer():
    """prefer_nodes should add preferred_nodes."""
    hints = _PlanningHints((), (), set(), set(), ("first", "second"), None, {})
    payload = hints.to_prompt_payload()
    assert payload["preferred_nodes"] == ["first", "second"]


def test_planning_hints_to_prompt_payload_budget():
    """budget_hints should add budget dict."""
    hints = _PlanningHints((), (), set(), set(), (), None, {"max_hops": 5})
    payload = hints.to_prompt_payload()
    assert payload["budget"] == {"max_hops": 5}


def test_planning_hints_empty_true():
    """empty() should return True for empty hints."""
    hints = _PlanningHints((), (), set(), set(), (), None, {})
    assert hints.empty() is True


def test_planning_hints_empty_false_ordering():
    """empty() should return False when ordering_hints populated."""
    hints = _PlanningHints(("a",), (), set(), set(), (), None, {})
    assert hints.empty() is False


def test_planning_hints_empty_false_parallel():
    """empty() should return False when parallel_groups populated."""
    hints = _PlanningHints((), (("a",),), set(), set(), (), None, {})
    assert hints.empty() is False


def test_planning_hints_empty_false_sequential():
    """empty() should return False when sequential_only populated."""
    hints = _PlanningHints((), (), {"a"}, set(), (), None, {})
    assert hints.empty() is False


def test_planning_hints_empty_false_disallow():
    """empty() should return False when disallow_nodes populated."""
    hints = _PlanningHints((), (), set(), {"a"}, (), None, {})
    assert hints.empty() is False


def test_planning_hints_empty_false_prefer():
    """empty() should return False when prefer_nodes populated."""
    hints = _PlanningHints((), (), set(), set(), ("a",), None, {})
    assert hints.empty() is False


def test_planning_hints_empty_false_max_parallel():
    """empty() should return False when max_parallel set."""
    hints = _PlanningHints((), (), set(), set(), (), 1, {})
    assert hints.empty() is False


def test_planning_hints_empty_false_budget():
    """empty() should return False when budget_hints populated."""
    hints = _PlanningHints((), (), set(), set(), (), None, {"key": "value"})
    assert hints.empty() is False
