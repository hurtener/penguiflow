"""Tests for penguiflow/planner/reflection_prompts.py."""

from pydantic import BaseModel

from penguiflow.planner.reflection_prompts import _format_answer

# ─── _format_answer tests ────────────────────────────────────────────────────


def test_format_answer_basemodel():
    """_format_answer should JSON serialize BaseModel."""

    class MyModel(BaseModel):
        result: str
        value: int

    result = _format_answer(MyModel(result="test", value=42))
    assert '"result": "test"' in result
    assert '"value": 42' in result


def test_format_answer_dict_with_answer():
    """_format_answer should extract 'answer' key from dict."""
    result = _format_answer({"answer": "the answer", "other": "data"})
    assert result == "the answer"


def test_format_answer_dict_with_result():
    """_format_answer should extract 'result' key from dict."""
    result = _format_answer({"result": "the result", "other": "data"})
    assert result == "the result"


def test_format_answer_dict_with_response():
    """_format_answer should extract 'response' key from dict."""
    result = _format_answer({"response": "the response", "other": "data"})
    assert result == "the response"


def test_format_answer_dict_with_output():
    """_format_answer should extract 'output' key from dict."""
    result = _format_answer({"output": "the output", "other": "data"})
    assert result == "the output"


def test_format_answer_dict_without_special_key():
    """_format_answer should JSON serialize dict without special keys."""
    result = _format_answer({"key1": "value1", "key2": "value2"})
    assert "key1" in result
    assert "value1" in result


def test_format_answer_list():
    """_format_answer should JSON serialize list."""
    result = _format_answer(["item1", "item2", "item3"])
    assert "item1" in result
    assert "item2" in result


def test_format_answer_tuple():
    """_format_answer should JSON serialize tuple."""
    result = _format_answer(("a", "b", "c"))
    assert '"a"' in result
    assert '"b"' in result


def test_format_answer_string():
    """_format_answer should return string as-is."""
    result = _format_answer("simple string answer")
    assert result == "simple string answer"


def test_format_answer_number():
    """_format_answer should stringify number."""
    result = _format_answer(42)
    assert result == "42"
