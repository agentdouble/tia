"""Rendu terminal concis des appels de tools."""

from pydantic_ai.messages import ToolCallPart


def format_tool_call(part: ToolCallPart) -> str:
    """Affiche le nom du tool et, pour Bash, la commande exécutée."""
    return format_tool_values(part.tool_name, part.args_as_dict())


def format_tool_values(tool_name: str, arguments: dict) -> str:
    """Même rendu à partir du protocole TIA, sans dépendre de Pydantic AI."""
    if tool_name == "load_skill":
        name = arguments.get("name")
        if isinstance(name, str) and name:
            return f"\n[skill] {name}"
    if tool_name == "run_bash":
        command = arguments.get("command")
        if isinstance(command, str) and command:
            return f"\n[tool] {tool_name}: {command}"
    return f"\n[tool] {tool_name}"
