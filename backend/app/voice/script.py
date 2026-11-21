"""Script tree. Mandatory lines are spoken as stored. A model reply cannot replace them."""

from __future__ import annotations

from dataclasses import dataclass

from app.compliance.guardian import guardian_line


@dataclass(frozen=True)
class ScriptNode:
    key: str
    node_type: str
    text: str
    routes: dict[str, str]
    action: str
    guardian: bool = False


def parse_nodes(raw: list[dict[str, object]] | None) -> list[ScriptNode]:
    parsed: list[ScriptNode] = []
    for item in raw or []:
        raw_content = item.get("content")
        content: dict[str, object] = raw_content if isinstance(raw_content, dict) else {}
        text_value = content.get("text")
        text = text_value if isinstance(text_value, str) else ""
        must_say = content.get("must_say")
        if not text and isinstance(must_say, str):
            text = must_say
        routes: dict[str, str] = {}
        raw_routes = content.get("routes")
        if isinstance(raw_routes, dict):
            routes = {str(key): str(value) for key, value in raw_routes.items() if isinstance(value, str)}
        nxt = content.get("next")
        if isinstance(nxt, str):
            routes.setdefault("next", nxt)
        raw_action = content.get("action")
        action = raw_action if isinstance(raw_action, str) else ""
        key = item.get("node_key") or item.get("id") or ""
        node_type = item.get("node_type") or item.get("type") or ""
        if isinstance(key, str) and key and isinstance(node_type, str):
            parsed.append(
                ScriptNode(
                    key,
                    node_type,
                    text,
                    routes,
                    action,
                    guardian_line(key, node_type, content) is not None,
                )
            )
    return parsed


def _start(nodes: list[ScriptNode]) -> ScriptNode:
    for node in nodes:
        if node.key == "n_open":
            return node
    referenced = {target for node in nodes for target in node.routes.values()}
    for node in nodes:
        if node.key not in referenced:
            return node
    return nodes[0]


def render_script(
    nodes: list[ScriptNode], *, intent: str, require_guardian: bool = False
) -> tuple[str, str, list[str]]:
    """Return spoken text, action, and the mandatory lines that were included."""
    if not nodes:
        return "", "speak", []
    by_key = {node.key: node for node in nodes}
    current: ScriptNode | None = _start(nodes)
    spoken: list[str] = []
    mandatory: list[str] = []
    action = "speak"
    seen: set[str] = set()
    while current is not None and current.key not in seen:
        seen.add(current.key)
        if current.node_type == "mandatory" and current.text:
            spoken.append(current.text)
            mandatory.append(current.text)
        if current.action == "transfer" or current.node_type == "transfer":
            action = "transfer"
            if current.text and current.text not in spoken:
                spoken.append(current.text)
            break
        if current.node_type == "gate":
            target = current.routes.get(intent) or current.routes.get("invalid") or current.routes.get("next")
        else:
            target = current.routes.get(intent) or current.routes.get("next")
        current = by_key.get(target) if target else None
    if require_guardian:
        for node in nodes:
            if node.guardian and node.text and node.text not in spoken:
                spoken.append(node.text)
                mandatory.append(node.text)
    if intent in {"refuse", "voicemail"} and action != "transfer":
        action = "hangup"
    return "".join(spoken), action, mandatory
