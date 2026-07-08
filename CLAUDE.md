# super-menu — project guide for Claude Code

An extensible terminal menu. **One `Plugin` definition drives three surfaces** (TUI, CLI,
MCP). Never add UI code to a plugin — declare `Command`s with typed `Param`s and a handler
returning a `CommandResult`; the surfaces render from that metadata.

## Layout
- `src/super_menu/core/plugin.py` — `Plugin` / `Command` / `Param` / `CommandResult` (the contract).
- `src/super_menu/core/registry.py` — auto-discovers any `super_menu.plugins.<name>` exposing `PLUGIN`.
- `src/super_menu/cli.py` — entry point: no args → TUI; `<plugin> <cmd>` → headless; `mcp` → MCP
  server; `web` → the dashboard.
- `src/super_menu/web/` — **the primary surface** (user direction 2026-07: web-first): a
  Jarvis-style HUD dashboard (`server.py` stdlib server + `static/index.html`) that, like the TUI,
  builds itself from plugin metadata (`GET /api/menu` → auto-forms; `POST /api/run` →
  `CommandResult.to_dict()`, rendered per `kind`, geojson as a dark Leaflet map). No login, no
  server state. The route-avoider's dedicated planner stays mounted at `/route`.
- `src/super_menu/tui/app.py` — Textual app; auto-builds forms + result tables.
- `src/super_menu/mcp_server.py` — low-level `mcp.server` exposing each command as a tool.
- `src/super_menu/plugins/free_for_dev/` — reference plugin (fetch + parse + search).
- `src/super_menu/plugins/git_tools/` — read-only git plugin (`id = "git"`); the *reference
  example* for subprocess-backed sources (`gitio.py` data layer + `plugin.py`). **Feature-frozen —
  do not add commands.** Every further git command duplicates strictly better tools (Claude Code
  runs `git` directly, other MCP clients have the official `mcp-server-git`, humans have the git
  CLI / lazygit / IDE integrations) and only bloats the MCP tool list. Its value is proving the
  contract generalizes beyond fetch-and-index, not shipping git features.
- `src/super_menu/plugins/route_avoider/` — area-avoidance route planner (`id = "route-avoider"`);
  the *reference example* for network-backed sources swappable behind an **adapter interface**
  (`adapter.py` = `RoutingAdapter` + `ORSAdapter` + self-hosted `ValhallaAdapter` + offline
  `StubAdapter`; `geo.py` = pure geometry/parsing; `plugin.py` = commands). Engine picked by
  `active_adapter()`: `VALHALLA_URL` > `ORS_API_KEY` > offline estimator, so it installs/demos/
  tests with zero setup (the free-for-dev seed pattern). Its dedicated map planner
  (`webserver.py` + `web/index.html`) is mounted at `/route` in the web dashboard; `deploy/`
  stands up a self-hosted Valhalla via Docker (see its README for the service-limit and WSL
  memory gotchas). Pure `geo.py` + the stub keep it fully unit-testable offline.
- `src/super_menu/plugins/hazard_watch/` — live global disaster feed (`id = "hazard-watch"`);
  the *reference example* for **poll-a-public-feed** plugins. Sources sit behind a `HazardFeed`
  adapter (`feeds.py` = `EONETFeed` + `USGSFeed`, both **keyless**, + offline `SeedFeed`);
  `collect()` merges them, tolerates any one feed failing, and falls back to a disk cache then a
  packaged `data/seed.json` so it installs/demos/tests with zero setup and no network
  (`active_feeds()` returns just the seed under `SUPER_MENU_OFFLINE`). Commands (`plugin.py`):
  `active` emits a GeoJSON FeatureCollection of hazards — so it lights up as a braille map (TUI/CLI)
  and drives the web deck's dedicated **threat board** (`web/static/index.html`, activates on the
  `hazard-watch` id); `near` filters to a radius of a place/`lat,lng`; `sources` reports which feeds
  are live. Its whole point is **composition**: the deck's "avoid these" button feeds active hazards
  into route-avoider as avoid zones. Severity is stored 1/2/3, emitted as GDACS-style
  red/orange/green words; the feature-property contract is documented at the top of `feeds.py`.

## Conventions
- A plugin handler must return `CommandResult` (use `CommandResult.ok_` / `.err`); `data` must be
  JSON-serializable so it flows to CLI `--json`, MCP, and the TUI unchanged.
- `CommandResult.kind` picks the rendering: `table` / `list` / `text` / `json`, or `geojson` —
  where `data` is a GeoJSON object (usually a FeatureCollection) that the TUI and CLI rasterize to
  a braille map via `core/braille.py` (MCP/`--json` get the raw GeoJSON). Any plugin emitting
  spatial data becomes a map with no surface-specific code; route-avoider is the reference user.
  The map draws a real **OSM road underlay** (zoom-aware highway classes via the Overpass API)
  through `core/roads.py`: braille stays a pure rasterizer taking `roads=`; surfaces fetch (TUI
  in a worker, CLI synchronously) with a disk cache under `data_home()/basemap/`. Offline-safe:
  any failure renders roadless; tests set `SUPER_MENU_OFFLINE=1` (see `tests/conftest.py`).
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
