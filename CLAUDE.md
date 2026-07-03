# super-menu — project guide for Claude Code

An extensible terminal menu. **One `Plugin` definition drives three surfaces** (TUI, CLI,
MCP). Never add UI code to a plugin — declare `Command`s with typed `Param`s and a handler
returning a `CommandResult`; the surfaces render from that metadata.

## Layout
- `src/super_menu/core/plugin.py` — `Plugin` / `Command` / `Param` / `CommandResult` (the contract).
- `src/super_menu/core/registry.py` — auto-discovers any `super_menu.plugins.<name>` exposing `PLUGIN`.
- `src/super_menu/cli.py` — entry point: no args → TUI; `<plugin> <cmd>` → headless; `mcp` → MCP server.
- `src/super_menu/tui/app.py` — Textual app; auto-builds forms + result tables.
- `src/super_menu/mcp_server.py` — low-level `mcp.server` exposing each command as a tool.
- `src/super_menu/plugins/free_for_dev/` — reference plugin (fetch + parse + search).
- `src/super_menu/plugins/git_tools/` — read-only git plugin (`id = "git"`); the *reference
  example* for subprocess-backed sources (`gitio.py` data layer + `plugin.py`). **Feature-frozen —
  do not add commands.** Every further git command duplicates strictly better tools (Claude Code
  runs `git` directly, other MCP clients have the official `mcp-server-git`, humans have the git
  CLI / lazygit / IDE integrations) and only bloats the MCP tool list. Its value is proving the
  contract generalizes beyond fetch-and-index, not shipping git features.

## Conventions
- A plugin handler must return `CommandResult` (use `CommandResult.ok_` / `.err`); `data` must be
  JSON-serializable so it flows to CLI `--json`, MCP, and the TUI unchanged.
- `Plugin.id` is a stable lowercase token used in CLI and MCP tool names (`<id>__<command>`).
- Runtime caches/indexes go under `core.config.plugin_data_dir(<id>)`, never in the repo
  (except an optional packaged seed in the plugin's `data/`).

## Dev commands
```bash
uv pip install -e ".[mcp]"        # install with MCP support
uv run super-menu                  # TUI
uv run python tests/test_smoke.py  # smoke tests (discovery, parse, CLI, TUI boot)
```

## Notes
- Output is forced to UTF-8 in `cli.py` (`_force_utf8`) because Windows consoles are cp1252
  and crash on emoji icons.
- See `.claude/super-menu.md` for how to *use* plugins (esp. free-for-dev) while assisting the user.
