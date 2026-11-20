"""Read and rewrite an install rule's query to add or remove exact clusters.

A Nebius rule's coverage is its query alone: events syntax over the
cluster's labels and `region`, plus `@_key`, the cluster's MCI resource id
(`nebius-cloud:mk8s:<region>:<tenant>:cluster/<id>`). Adding or removing a
cluster keeps the label query and layers exact exceptions on it:

    (<label query> OR @_key:"<added>") AND -@_key:"<removed>"

Datadog stores the normalized form of every query (`@_key:("a" OR "b")`
becomes `(@_key:"a" OR @_key:"b")`, nested groups keep their parentheses,
exclusions are a top-level `AND -...` clause), so the parser reads that
form back. Only top-level clauses count: an `@_key` term nested inside the
label query is part of the label query.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

KEY_ATTRIBUTE = "@_key"
# MCI resource ids never contain quotes, and Datadog stores quoted text
# unescaped, so key values are matched and written verbatim.
_QUOTED = r'"([^"]*)"'
_KEY_TERM = re.compile(re.escape(KEY_ATTRIBUTE) + ":" + _QUOTED)
_KEY_LIST = re.compile(re.escape(KEY_ATTRIBUTE) + r":\((.*)\)")
_QUOTED_VALUE = re.compile(_QUOTED)
_OPERATORS = (" AND ", " OR ")


@dataclass(frozen=True)
class RuleQuery:
    """A rule query split into its label query and exact exceptions.

    `base` is the label query: "" matches every cluster, and None means the
    rule has no label query, only explicitly included clusters.
    """

    base: str | None
    includes: tuple[str, ...]
    excludes: tuple[str, ...]


class MixedOperators(ValueError):
    """AND and OR side by side at one level: not a normalized query."""


def _split_top(text: str) -> tuple[str | None, list[str]]:
    """Split at the top level's AND/OR, outside quotes and brackets."""
    parts: list[str] = []
    operators: set[str] = set()
    depth = 0
    quoted = escaped = False
    start = index = 0
    while index < len(text):
        char = text[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif quoted:
            quoted = char != '"'
        elif char == '"':
            quoted = True
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif depth == 0:
            operator = next(
                (item for item in _OPERATORS if text.startswith(item, index)), None
            )
            if operator:
                parts.append(text[start:index].strip())
                operators.add(operator.strip())
                index += len(operator)
                start = index
                continue
        index += 1
    parts.append(text[start:].strip())
    if len(operators) > 1:
        raise MixedOperators(text)
    return (operators.pop() if operators else None), parts


def _unwrap(text: str) -> str:
    """Drop one pair of parentheses that encloses the whole text."""
    if not (text.startswith("(") and text.endswith(")")):
        return text
    depth = 0
    quoted = escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif quoted:
            quoted = char != '"'
        elif char == '"':
            quoted = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0 and index != len(text) - 1:
                return text
    return text[1:-1].strip()


def _key_values(clause: str) -> list[str] | None:
    """The ids a clause names when it is only `@_key` terms joined by OR."""
    try:
        operator, parts = _split_top(_unwrap(clause.strip()))
    except MixedOperators:
        return None
    if operator not in (None, "OR"):
        return None
    values: list[str] = []
    for part in parts:
        term = _KEY_TERM.fullmatch(part)
        if term:
            values.append(term.group(1))
            continue
        listed = _KEY_LIST.fullmatch(part)
        if not listed:
            return None
        try:
            inner_operator, items = _split_top(listed.group(1))
        except MixedOperators:
            return None
        if inner_operator not in (None, "OR"):
            return None
        for item in items:
            value = _QUOTED_VALUE.fullmatch(item)
            if not value:
                return None
            values.append(value.group(1))
    return values


def parse_rule_query(query: str | None) -> RuleQuery:
    """Split a stored rule query into label query, includes and excludes."""
    text = (query or "").strip()
    if not text:
        return RuleQuery("", (), ())
    try:
        operator, parts = _split_top(text)
    except MixedOperators:
        return RuleQuery(text, (), ())
    excludes: list[str] = []
    if operator == "OR":
        core = text
    else:
        rest: list[str] = []
        for part in parts:
            if part.startswith("-"):
                values = _key_values(part[1:])
                if values is not None:
                    excludes.extend(values)
                    continue
            rest.append(part)
        if not rest:
            return RuleQuery("", (), tuple(excludes))
        core = " AND ".join(rest)
    try:
        core_operator, core_parts = _split_top(_unwrap(core))
    except MixedOperators:
        return RuleQuery(core, (), tuple(excludes))
    if core_operator == "OR":
        includes: list[str] = []
        base_parts: list[str] = []
        for part in core_parts:
            values = _key_values(part)
            if values is None:
                base_parts.append(part)
            else:
                includes.extend(values)
        if includes:
            base = " OR ".join(base_parts) if base_parts else None
            return RuleQuery(base, tuple(includes), tuple(excludes))
        return RuleQuery(core, (), tuple(excludes))
    values = _key_values(core)
    if values is not None:
        return RuleQuery(None, tuple(values), tuple(excludes))
    return RuleQuery(core, (), tuple(excludes))


def _key_clause(values: tuple[str, ...]) -> str:
    terms = [f'{KEY_ATTRIBUTE}:"{value}"' for value in sorted(set(values))]
    return terms[0] if len(terms) == 1 else "(" + " OR ".join(terms) + ")"


def _grouped(text: str) -> str:
    """Parenthesize text whose top level is an OR (or unparseable)."""
    try:
        operator, _ = _split_top(text)
    except MixedOperators:
        operator = "OR"
    return f"({text})" if operator == "OR" else text


def compose_rule_query(rule_query: RuleQuery) -> str:
    """The query for a label query plus exact includes and excludes.

    Raises ValueError when the result would cover no cluster at all: a
    rule without a label query whose last included cluster is removed.
    """
    if rule_query.base == "":
        # Every cluster already matches; an explicit include adds nothing.
        core = ""
    else:
        pieces = []
        if rule_query.base is not None:
            pieces.append(rule_query.base)
        if rule_query.includes:
            pieces.append(_key_clause(rule_query.includes))
        if not pieces:
            raise ValueError("the rule would cover no cluster")
        core = (
            pieces[0]
            if len(pieces) == 1
            else " OR ".join(_grouped(piece) for piece in pieces)
        )
    if not rule_query.excludes:
        return core
    exclusion = "-" + _key_clause(rule_query.excludes)
    return f"{_grouped(core)} AND {exclusion}" if core else exclusion
