"""Turns the client's GraphQL selection into a pg_graphql query that asks ONLY for those columns.

Client field names are camelCase (Strawberry); view columns are snake_case. Relay wrapper fields
(edges / node / cursor / pageInfo) keep their names.
"""
import re
from typing import Any

from strawberry.types.nodes import FragmentSpread, InlineFragment, SelectedField

_CAMEL = re.compile(r"(?<!^)(?=[A-Z])")


def to_snake(name: str) -> str:
    return _CAMEL.sub("_", name).lower()


def to_camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(p.title() for p in rest)


def flatten(selections) -> list[SelectedField]:
    """Resolve fragments/inline fragments into a flat list of fields (dedup by name)."""
    seen: dict[str, SelectedField] = {}
    for sel in selections or []:
        if isinstance(sel, (FragmentSpread, InlineFragment)):
            for child in flatten(sel.selections):
                seen.setdefault(child.name, child)
        elif isinstance(sel, SelectedField) and not sel.name.startswith("__"):
            seen.setdefault(sel.name, sel)
    return list(seen.values())


def columns(selections, exclude: set[str] = frozenset()) -> list[str]:
    return [to_snake(f.name) for f in flatten(selections) if f.name not in exclude]


def connection_selection(field: SelectedField) -> str:
    """`{ edges { node { <only requested columns> } cursor } pageInfo { ... } }`"""
    parts: list[str] = []
    for child in flatten(field.selections):
        if child.name == "edges":
            inner: list[str] = []
            for e in flatten(child.selections):
                if e.name == "node":
                    cols = columns(e.selections) or ["id"]
                    inner.append("node { " + " ".join(cols) + " }")
                elif e.name == "cursor":
                    inner.append("cursor")
            parts.append("edges { " + " ".join(inner or ["cursor"]) + " }")
        elif child.name == "pageInfo":
            info = [c.name for c in flatten(child.selections)]  # hasNextPage / endCursor ... (already camelCase)
            parts.append("pageInfo { " + " ".join(info or ["hasNextPage"]) + " }")
    return "{ " + " ".join(parts or ["edges { cursor }"]) + " }"


def collection_fragment(alias: str, collection: str, prefix: str, selection: str, *, filter_: dict | None,
                        order_by: list[dict] | None, first: int, after: str | None,
                        entity: str) -> tuple[str, list[str], dict[str, Any]]:
    """One aliased collection with variables (no string interpolation of user values -> no injection)."""
    defs = [f"${prefix}f: {entity}Filter", f"${prefix}o: [{entity}OrderBy!]", f"${prefix}n: Int", f"${prefix}a: Cursor"]
    variables: dict[str, Any] = {f"{prefix}n": first}
    if filter_:
        variables[f"{prefix}f"] = filter_
    if order_by:
        variables[f"{prefix}o"] = order_by
    if after:
        variables[f"{prefix}a"] = after
    fragment = (f"{alias}: {collection}(filter: ${prefix}f, orderBy: ${prefix}o, first: ${prefix}n, "
                f"after: ${prefix}a) {selection}")
    return fragment, defs, variables


class Node:
    """Wraps a pg_graphql JSON object so Strawberry's default resolvers (getattr) work on it."""

    def __init__(self, data: dict[str, Any]):
        self._data = data

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):  # never answer for dunder/protocol lookups (__await__, __iter__, ...)
            raise AttributeError(name)
        data = self._data
        value = data[name] if name in data else data.get(to_camel(name))
        return wrap(value)


def wrap(value: Any) -> Any:
    if isinstance(value, dict):
        return Node(value)
    if isinstance(value, list):
        return [wrap(v) for v in value]
    return value
