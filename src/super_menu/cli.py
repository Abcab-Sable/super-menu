"""super-menu entry point.

    super-menu                      launch the interactive TUI
    super-menu list                 list installed plugins
    super-menu <plugin>             list a plugin's commands
    super-menu <plugin> <cmd> ...   run a command (headless)
    super-menu mcp                  run the MCP server (stdio) for Claude Code
    super-menu web                  launch the route-planner web UI (real road map)

Headless command runs accept ``--name value`` / ``--name=value`` flags matching
the command's params, plus ``--json`` to emit the raw structured result.
"""
from __future__ import annotations

import json
import shutil
import sys
from typing import Any

from super_menu.core import braille
from super_menu.core.config import load_dotenv
from super_menu.core.registry import default_registry
from super_menu.core.plugin import Command, CommandResult


def _parse_flags(args: list[str]) -> tuple[dict[str, Any], bool]:
    """Parse ``--key value`` / ``--key=value`` / ``--flag`` into a dict.

    Returns (params, want_json). ``--json`` is consumed as a meta flag.
    """
    params: dict[str, Any] = {}
    want_json = False
    i = 0
    while i < len(args):
        tok = args[i]
        if not tok.startswith("--"):
            i += 1
            continue
        key = tok[2:]
        if "=" in key:
            key, val = key.split("=", 1)
            params[key] = val
            i += 1
            continue
        if key == "json":
            want_json = True
            i += 1
            continue
        # boolean flag vs. "--key value"
        if i + 1 < len(args) and not args[i + 1].startswith("--"):
            params[key] = args[i + 1]
            i += 2
        else:
            params[key] = True
            i += 1
    return params, want_json


def _print_result(result: CommandResult) -> int:
    if not result.ok:
        print(f"error: {result.summary}", file=sys.stderr)
        return 1
    if result.summary:
        print(result.summary)
    data = result.data
    if result.kind == "geojson" and isinstance(data, dict):
        _print_map(data)
    elif result.kind == "table" and isinstance(data, list) and data:
        cols = result.columns or list(data[0].keys())
        _print_table(data, cols)
    elif result.kind in ("json",) or isinstance(data, (dict,)):
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif isinstance(data, list):
        for item in data:
            print(f"  • {item}")
    elif data is not None:
        print(data)
    return 0


def _print_table(rows: list[dict], cols: list[str], max_width: int = 60) -> None:
    def cell(r: dict, c: str) -> str:
        v = str(r.get(c, ""))
        return v if len(v) <= max_width else v[: max_width - 1] + "…"

    widths = {c: max(len(c), *(len(cell(r, c)) for r in rows)) for c in cols}
    header = "  ".join(c.ljust(widths[c]) for c in cols)
    print(header)
    print("  ".join("-" * widths[c] for c in cols))
    for r in rows:
        print("  ".join(cell(r, c).ljust(widths[c]) for c in cols))


def _print_map(geojson: dict) -> None:
    from rich.console import Console

    size = shutil.get_terminal_size((80, 24))
    cols = min(max(size.columns - 2, 40), 110)
    rows = min(max(size.lines - 8, 16), 30)
    Console().print(braille.render_geojson(geojson, width=cols, height=rows))


def _list_plugins() -> int:
    reg = default_registry()
    if not reg.plugins:
        print("no plugins installed")
        return 0
    print("Installed plugins:\n")
    for p in reg.plugins:
        print(f"  {p.icon}  {p.id:<16} {p.description}")
    print("\nRun 'super-menu <plugin>' to see its commands.")
    return 0


def _list_commands(plugin_id: str) -> int:
    reg = default_registry()
    plugin = reg.get(plugin_id)
    if plugin is None:
        print(f"unknown plugin: {plugin_id}", file=sys.stderr)
        return 1
    print(f"{plugin.icon}  {plugin.name} — {plugin.description}\n")
    print("Commands:")
    for c in plugin.commands():
        req = [p.name for p in c.params if p.required]
        opt = [p.name for p in c.params if not p.required]
        sig = " ".join([f"--{n} <v>" for n in req] + [f"[--{n} <v>]" for n in opt])
        print(f"  {c.name:<14} {c.help}")
        if sig:
            print(f"                 {sig}")
    return 0


def _run_command(plugin_id: str, command_name: str, rest: list[str]) -> int:
    reg = default_registry()
    plugin = reg.get(plugin_id)
    if plugin is None:
        print(f"unknown plugin: {plugin_id}", file=sys.stderr)
        return 1
    command: Command | None = plugin.command(command_name)
    if command is None:
        print(f"unknown command: {plugin_id} {command_name}", file=sys.stderr)
        return 1
    params, want_json = _parse_flags(rest)
    result = command.run(params)
    if want_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0 if result.ok else 1
    return _print_result(result)


def _force_utf8() -> None:
    """Windows consoles default to cp1252 and crash on emoji/Unicode output.
    Reconfigure the standard streams to UTF-8 with a safe fallback."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    load_dotenv()  # pick up ORS_API_KEY (etc.) from a .env in the working directory
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv:
        from super_menu.tui.app import run as run_tui
        run_tui()
        return 0

    head = argv[0]

    if head in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if head == "mcp":
        from super_menu.mcp_server import run as run_mcp
        run_mcp()
        return 0
    if head == "web":
        from super_menu.plugins.route_avoider.webserver import run as run_web
        port = 8765
        for tok in argv[1:]:
            if tok.startswith("--port"):
                port = int(tok.split("=", 1)[1]) if "=" in tok else port
        run_web(port=port)
        return 0
    if head == "list":
        return _list_plugins()

    # head is a plugin id
    if len(argv) == 1:
        return _list_commands(head)
    return _run_command(head, argv[1], argv[2:])


if __name__ == "__main__":
    raise SystemExit(main())
