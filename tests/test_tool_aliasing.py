from __future__ import annotations

from pydantic import BaseModel

from penguiflow.catalog import NodeSpec
from penguiflow.node import Node
from penguiflow.planner.tool_aliasing import build_aliased_tool_catalog


class _Args(BaseModel):
    value: int = 0


class _Out(BaseModel):
    ok: bool = True


async def _noop_tool(_: _Args, __: object) -> dict[str, object]:
    return {"ok": True}


def test_build_aliased_tool_catalog_aliases_reserved_names() -> None:
    specs = [
        NodeSpec(node=Node(_noop_tool, name="parallel"), name="parallel", desc="x", args_model=_Args, out_model=_Out),
        NodeSpec(node=Node(_noop_tool, name="search"), name="search", desc="y", args_model=_Args, out_model=_Out),
    ]

    spec_by_name, records, alias_to_real = build_aliased_tool_catalog(specs)

    assert "parallel" not in spec_by_name
    assert "search" in spec_by_name
    assert alias_to_real["parallel__tool"] == "parallel"
    assert any(record["name"] == "parallel__tool" for record in records)


def test_build_aliased_tool_catalog_avoids_alias_collisions() -> None:
    specs = [
        NodeSpec(node=Node(_noop_tool, name="parallel"), name="parallel", desc="x", args_model=_Args, out_model=_Out),
        NodeSpec(
            node=Node(_noop_tool, name="parallel__tool"),
            name="parallel__tool",
            desc="y",
            args_model=_Args,
            out_model=_Out,
        ),
    ]

    spec_by_name, _, alias_to_real = build_aliased_tool_catalog(specs)

    assert "parallel__tool" in spec_by_name  # existing tool keeps name
    assert "parallel__tool_2" in spec_by_name  # colliding reserved tool gets a unique alias
    assert alias_to_real["parallel__tool_2"] == "parallel"

