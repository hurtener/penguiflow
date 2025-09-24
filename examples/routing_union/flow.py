"""Discriminated union routing example."""

from __future__ import annotations

import asyncio
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from penguiflow import Node, NodePolicy, create, union_router


class SearchWeb(BaseModel):
    kind: Literal["web"]
    query: str


class SearchSql(BaseModel):
    kind: Literal["sql"]
    table: str


SearchTask = Annotated[SearchWeb | SearchSql, Field(discriminator="kind")]


async def handle_web(task: SearchWeb, ctx) -> str:
    return f"web::{task.query}"


async def handle_sql(task: SearchSql, ctx) -> str:
    return f"sql::{task.table}"


async def main() -> None:
    router = union_router("router", SearchTask)
    web_node = Node(handle_web, name="web", policy=NodePolicy(validate="none"))
    sql_node = Node(handle_sql, name="sql", policy=NodePolicy(validate="none"))

    flow = create(
        router.to(web_node, sql_node),
        web_node.to(),
        sql_node.to(),
    )
    flow.run()

    await flow.emit(SearchWeb(kind="web", query="penguins"))
    print(await flow.fetch())  # web::penguins

    await flow.emit(SearchSql(kind="sql", table="metrics"))
    print(await flow.fetch())  # sql::metrics

    await flow.stop()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
