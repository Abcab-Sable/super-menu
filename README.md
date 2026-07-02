# super-menu

An extensible **super terminal menu**. One plugin definition drives three surfaces:

| Surface | How you use it |
| --- | --- |
| **TUI** (Textual) | `super-menu` — full-screen interactive menu |
| **CLI** | `super-menu <plugin> <command> [--flags] [--json]` — headless, scriptable |
| **MCP server** | `super-menu mcp` — every command becomes a tool Claude Code can call |

The idea: spot a useful project on [GitHub Trending](https://github.com/trending), wrap it
as a plugin, and it instantly appears in all three surfaces. The reference plugin,
**free-for-dev**, turns the [ripienaar/free-for-dev](https://github.com/ripienaar/free-for-dev)
catalog into a searchable index — usable by you *and* by Claude Code when designing app
architectures.

## Quick start

```bash
uv venv --python 3.13
uv pip install -e .            # add  --extra mcp  for the MCP server

# launch the TUI
uv run super-menu

# headless usage
uv run super-menu list
uv run super-menu free-for-dev update                  # build the index
uv run super-menu free-for-dev categories
uv run super-menu free-for-dev search --query postgres --json

uv run super-menu git status                           # read-only git state
uv run super-menu git log --limit 5 --json
```

## Architecture

```
src/super_menu/
  core/
    plugin.py     # Plugin / Command / Param / CommandResult — the single source of truth
    registry.py   # auto-discovers plugins under super_menu.plugins
    config.py     # per-user data dirs for caches/indexes
  cli.py          # entry point: no args -> TUI; args -> headless dispatch; `mcp` subcommand
  tui/app.py      # Textual app — auto-generates forms + result tables from Command metadata
  mcp_server.py   # exposes every command as an MCP tool (low-level mcp.server API)
  plugins/
    free_for_dev/ # reference plugin: fetch + parse + search the free-for-dev catalog
    git_tools/    # read-only git state (status, log, branches, diffs) via subprocess
```

A plugin never writes UI code. It declares `Command`s with typed `Param`s and a handler
returning a `CommandResult`; the TUI, CLI, and MCP layers render and dispatch from that.

## Writing a plugin

Drop a package under `src/super_menu/plugins/<name>/` exposing a module-level `PLUGIN`:

```python
from super_menu.core.plugin import Plugin, Command, Param, CommandResult

def cmd_hello(name: str = "world") -> CommandResult:
    return CommandResult.ok_(data=f"hello, {name}!", summary="greeting", kind="text")

class HelloPlugin(Plugin):
    id = "hello"
    name = "Hello"
    description = "A minimal example plugin."
    icon = "👋"

    def commands(self):
        return [Command("greet", "Say hello.", cmd_hello,
                        params=[Param("name", help="Who to greet.")])]

PLUGIN = HelloPlugin()
```

It now appears in `super-menu list`, the TUI sidebar, and (if installed) as the MCP tool
`hello__greet`.

## Claude Code integration

`super-menu` is built to be a control surface for Claude Code. See
[`.claude/super-menu.md`](.claude/super-menu.md) for how Claude Code should query plugins,
and register the MCP server with:

```bash
claude mcp add super-menu -- super-menu mcp
```

## Roadmap

- `simplex-chat` plugin: read chats / relay messages to Claude Code via the simplex CLI.
- Richer TUI result views (open URLs, copy to clipboard).
- Plugin manifest metadata (source repo, version, enable/disable).
