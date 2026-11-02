"""Internal normalization helpers for Nebius CLI responses."""

from __future__ import annotations

from typing import Any, Callable


JsonObject = dict[str, Any]
Runner = Callable[[list[str]], Any]


def _items(value: Any) -> list[JsonObject]:
    """Accept common Nebius list-response envelopes without exposing raw data."""
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    for key in (
        "items",
        "tenants",
        "projects",
        "service_accounts",
        "serviceAccounts",
        "memberships",
        "groups",
        "results",
    ):
        items = value.get(key)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    if isinstance(value.get("metadata"), dict):
        return [value]
    return []


def _nested(value: JsonObject, *path: str) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_string(value: JsonObject, paths: tuple[tuple[str, ...], ...]) -> str:
    for path in paths:
        candidate = _nested(value, *path)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _identity(value: JsonObject) -> tuple[str, str]:
    resource_id = _first_string(
        value,
        (
            ("metadata", "id"),
            ("id",),
            ("resource_id",),
            ("resourceId",),
        ),
    )
    name = _first_string(
        value,
        (
            ("metadata", "name"),
            ("name",),
            ("spec", "name"),
            ("display_name",),
            ("displayName",),
        ),
    )
    return resource_id, name or resource_id


def _paginated_items(runner: Runner, command: list[str]) -> list[JsonObject]:
    items: list[JsonObject] = []
    page_token = ""
    while True:
        page_command = [*command, "--page-size", "1000", "--format", "json"]
        if page_token:
            page_command.extend(["--page-token", page_token])
        value = runner(page_command)
        items.extend(_items(value))
        if not isinstance(value, dict):
            break
        page_token = _first_string(
            value,
            (
                ("next_page_token",),
                ("nextPageToken",),
            ),
        )
        if not page_token:
            break
    return items


def _membership(value: JsonObject) -> JsonObject | None:
    membership_id, name = _identity(value)
    group_id = _first_string(
        value,
        (
            ("metadata", "parent_id"),
            ("metadata", "parentId"),
            ("parent_id",),
            ("parentId",),
            ("spec", "group_id"),
            ("spec", "groupId"),
        ),
    )
    if membership_id.startswith("group-"):
        group_id = membership_id
    role = _first_string(value, (("role",), ("spec", "role")))
    if not any((membership_id, group_id, name, role)):
        return None
    return {
        "id": membership_id,
        "name": name,
        "group_id": group_id,
        "role": role,
    }
