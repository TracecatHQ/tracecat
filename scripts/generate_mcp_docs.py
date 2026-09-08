from __future__ import annotations

import ast
from pathlib import Path

from tracecat.cases.enums import CaseEventType

_DESCRIPTION_PLACEHOLDERS = {
    "{_CASE_EVENT_TYPE_VALUES_CSV}": ", ".join(
        event_type.value for event_type in CaseEventType
    )
}


def _render_description_placeholders(template: str) -> str:
    """Render the placeholders supported by client-facing tool descriptions."""
    for placeholder, value in _DESCRIPTION_PLACEHOLDERS.items():
        template = template.replace(placeholder, value)
    return template


def _clean_docstring(text: str | None) -> str:
    if not text:
        return "No description provided."
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped in {"Args:", "Returns:", "Raises:"}:
            break
        lines.append(line)

    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return "No description provided."

    normalized: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if normalized and normalized[-1]:
                normalized.append("")
            continue

        if stripped.startswith("- "):
            normalized.append(stripped)
            continue

        if (
            normalized
            and normalized[-1]
            and (line.startswith((" ", "\t")) or not normalized[-1].startswith("- "))
        ):
            normalized[-1] = f"{normalized[-1]} {stripped}"
        else:
            normalized.append(stripped)

    expanded: list[str] = []
    for line in normalized:
        if not line:
            expanded.append("")
            continue
        if " - " not in line:
            expanded.append(line)
            continue
        lead, *tail = line.split(" - ")
        if not tail:
            expanded.append(line)
            continue
        if lead.strip():
            expanded.append(lead.strip())
        expanded.extend(f"- {chunk.strip()}" for chunk in tail if chunk.strip())

    while expanded and not expanded[-1]:
        expanded.pop()
    return "\n".join(expanded) if expanded else "No description provided."


def _is_mcp_tool(node: ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        func = decorator.func
        if not isinstance(func, ast.Attribute) or func.attr != "tool":
            continue
        value = func.value
        if isinstance(value, ast.Name) and value.id == "mcp":
            return True
    return False


def _module_string_constants(module: ast.Module) -> dict[str, str]:
    """Map module-level names to their plain string-literal values."""
    constants: dict[str, str] = {}
    for node in module.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                constants[target.id] = node.value.value
    return constants


def _decorator_description(
    node: ast.AsyncFunctionDef, constants: dict[str, str]
) -> str | None:
    """Return the tool's declared description when it resolves statically.

    A tool may pass `description=` to move its client-facing text off the
    docstring. Prefer that text, so the published docs match what MCP clients
    actually receive instead of drifting from it. ``_render_prompt_text`` calls
    are resolved with the same enum-derived placeholder value used at runtime.
    """
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        for keyword in decorator.keywords:
            if keyword.arg != "description":
                continue
            value = keyword.value
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "_render_prompt_text"
                and len(value.args) == 1
                and not value.keywords
            ):
                value = value.args[0]
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return _render_description_placeholders(value.value)
            if isinstance(value, ast.Name):
                if text := constants.get(value.id):
                    return _render_description_placeholders(text)
    return None


def main() -> None:
    root_dir = Path(__file__).resolve().parent.parent
    server_file = root_dir / "tracecat" / "mcp" / "server.py"
    output_file = root_dir / "docs" / "snippets" / "mcp-tools.mdx"

    source = server_file.read_text(encoding="utf-8")
    module = ast.parse(source)
    constants = _module_string_constants(module)

    tools: list[tuple[str, str]] = []
    for node in module.body:
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if not _is_mcp_tool(node):
            continue
        text = _decorator_description(node, constants) or ast.get_docstring(node)
        tools.append((node.name, _clean_docstring(text)))

    lines = [
        "{/* Auto-generated by scripts/generate_mcp_docs.py; do not edit by hand. */}",
        "",
    ]
    for tool_name, docstring in tools:
        lines.append(f'<ResponseField name="{tool_name}" type="tool">')
        for doc_line in docstring.splitlines():
            if doc_line:
                rendered_line = (
                    f"-> {doc_line[2:]}" if doc_line.startswith("- ") else doc_line
                )
                lines.append(f"  {rendered_line}")
            else:
                lines.append("")
        lines.append("</ResponseField>")
        lines.append("")

    output_file.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
