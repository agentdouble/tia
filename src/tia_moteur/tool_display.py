"""Rendu terminal concis des appels de tools."""

from pydantic_ai.messages import ToolCallPart


def format_tool_call(part: ToolCallPart) -> str:
    """Affiche le nom du tool et, pour Bash, la commande exécutée."""
    if part.tool_name == "load_skill":
        name = part.args_as_dict().get("name")
        if isinstance(name, str) and name:
            return f"\n[skill] {name}"
    if part.tool_name == "run_bash":
        command = part.args_as_dict().get("command")
        if isinstance(command, str) and command:
            return f"\n[tool] {part.tool_name}: {command}"
    return f"\n[tool] {part.tool_name}"
