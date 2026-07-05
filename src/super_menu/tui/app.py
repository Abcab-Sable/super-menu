"""The interactive Textual TUI for super-menu.

Left: a tree of plugins → commands. Right: an auto-generated parameter form for
the selected command and a results panel. The form and rendering are driven
entirely by the plugin's ``Command``/``Param`` metadata, so new plugins appear
here with zero TUI code: choices become dropdowns, bools become checkboxes,
int/float params get numeric inputs, and defaults are pre-filled.

Commands run in a worker thread so slow handlers (network fetches, link checks)
never freeze the UI. Ctrl+P / ``/`` opens a fuzzy palette over every command.
"""
from __future__ import annotations

import asyncio
import re
import time
from functools import partial
from typing import Any, Iterator, Optional, Union

from rich.markup import escape
from rich.text import Text

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Input,
    Select,
    Static,
    Tree,
)
from textual.widgets.tree import TreeNode

from super_menu.core import braille, roads
from super_menu.core.registry import default_registry
from super_menu.core.plugin import Command, CommandResult, Param, Plugin

FieldWidget = Union[Input, Checkbox, Select]


class GeoMap(Widget):
    """Interactive braille map of a ``kind="geojson"`` payload. Focus it to zoom
    (``+``/``-``), pan (arrows), toggle route waypoints (``w``) and reset (``0``).
    Generic — it knows nothing about routes, only GeoJSON."""

    can_focus = True

    BINDINGS = [
        Binding("plus,equals_sign", "zoom(1.4)", "Zoom in"),
        Binding("minus,underscore", "zoom(0.714)", "Zoom out"),
        Binding("w", "toggle_waypoints", "Waypoints"),
        Binding("0", "reset", "Reset view"),
        Binding("up", "pan(0, -1)", "Pan", show=False),
        Binding("down", "pan(0, 1)", "Pan", show=False),
        Binding("left", "pan(-1, 0)", "Pan", show=False),
        Binding("right", "pan(1, 0)", "Pan", show=False),
    ]

    zoom: reactive[float] = reactive(1.0)
    show_waypoints: reactive[bool] = reactive(False)

    def __init__(self, geojson: dict, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._geojson = geojson
        self._bbox = braille.data_bbox(geojson)
        self._cx, self._cy = self._center()
        self._roads: list = []
        self.border_title = "🗺  map"

    def on_mount(self) -> None:
        self._request_roads()

    def _center(self) -> tuple[float, float]:
        if not self._bbox:
            return 0.0, 0.0
        minlng, minlat, maxlng, maxlat = self._bbox
        return (minlng + maxlng) / 2, (minlat + maxlat) / 2

    def _view(self) -> Optional[tuple[float, float, float, float]]:
        """Current zoom/pan window, or None to auto-frame the data."""
        if not self._bbox or self.zoom <= 1.0:
            return None
        minlng, minlat, maxlng, maxlat = self._bbox
        half_x = (maxlng - minlng) / self.zoom / 2
        half_y = (maxlat - minlat) / self.zoom / 2
        return (self._cx - half_x, self._cy - half_y,
                self._cx + half_x, self._cy + half_y)

    def render(self) -> Text:
        w = self.size.width or 48
        h = self.size.height or 16
        wp = " · waypoints" if self.show_waypoints else ""
        self.border_subtitle = f"{self.zoom:.1f}×  +/− · ↑↓←→ · w · 0{wp}"
        return braille.render_geojson(self._geojson, w, h, view=self._view(),
                                      waypoints=self.show_waypoints,
                                      roads=self._roads)

    def _request_roads(self) -> None:
        """Fetch the OSM road underlay for the current view off the UI thread.

        ``exclusive`` per group: a rapid zoom/pan burst keeps only the newest
        fetch. Cache hits inside ``roads_for_view`` make repeats instant."""
        frame = self._view() or self._bbox
        if frame is None:
            return
        self.run_worker(partial(self._load_roads, frame), thread=True,
                        exclusive=True, group="roads")

    def _load_roads(self, frame: tuple[float, float, float, float]) -> None:
        lines = roads.roads_for_view(frame)  # never raises; [] on any failure
        if lines:
            self._roads = lines
            self.app.call_from_thread(self.refresh)

    def watch_zoom(self) -> None:
        self._request_roads()
        self.refresh()

    def watch_show_waypoints(self) -> None:
        self.refresh()

    def on_resize(self) -> None:
        self.refresh()

    def action_zoom(self, factor: float) -> None:
        self.zoom = max(1.0, min(64.0, self.zoom * factor))
        if self.zoom == 1.0:              # fully zoomed out ⇒ recentre on the data
            self._cx, self._cy = self._center()

    def action_pan(self, dx: int, dy: int) -> None:
        if not self._bbox:
            return
        minlng, minlat, maxlng, maxlat = self._bbox
        self._cx += dx * (maxlng - minlng) / self.zoom * 0.2
        self._cy -= dy * (maxlat - minlat) / self.zoom * 0.2   # screen y grows downward
        self._request_roads()
        self.refresh()

    def action_toggle_waypoints(self) -> None:
        self.show_waypoints = not self.show_waypoints

    def action_reset(self) -> None:
        self._cx, self._cy = self._center()
        self.show_waypoints = False
        self.zoom = 1.0
        self.refresh()

# Curated Textual themes cycled with the ``t`` binding.
THEMES = [
    "tokyo-night",
    "catppuccin-mocha",
    "dracula",
    "nord",
    "gruvbox",
    "monokai",
    "flexoki",
    "textual-dark",
    "textual-light",
]

# Welcome banner, wide (one line per row, 39 cells) and narrow (stacked words,
# 18 cells) variants; picked in ``_show_welcome`` from the terminal width.
BANNER_WIDE_WIDTH = 39
BANNER_WIDE = (
    "[b $primary]█▀ █░█ █▀█ █▀▀ █▀█[/]   [b $accent]█▀▄▀█ █▀▀ █▄░█ █░█[/]\n"
    "[b $primary]▄█ █▄█ █▀▀ ██▄ █▀▄[/]   [b $accent]█░▀░█ ██▄ █░▀█ █▄█[/]"
)
BANNER_NARROW = (
    "[b $primary]█▀ █░█ █▀█ █▀▀ █▀█[/]\n"
    "[b $primary]▄█ █▄█ █▀▀ ██▄ █▀▄[/]\n"
    "[b $accent]█▀▄▀█ █▀▀ █▄░█ █░█[/]\n"
    "[b $accent]█░▀░█ ██▄ █░▀█ █▄█[/]"
)
# Horizontal cells around the form's content: sidebar (32) + its margin (2) +
# #body padding (4) + form border (2) + form padding (4).
FORM_CHROME_WIDTH = 44

_SAFE_ACTION_ARG = re.compile(r"^[\w.-]+$")


def action_link(label: str, action: str, *args: str) -> str:
    """Wrap ``label`` (already markup-escaped) in an ``@click`` action link.

    Arguments are embedded with ``repr`` and only when they are plain tokens;
    anything else falls back to unlinked text rather than risk producing
    markup that Textual's action parser rejects into a silent no-op link.
    """
    if all(_SAFE_ACTION_ARG.match(a) for a in args):
        call = f"{action}({', '.join(repr(a) for a in args)})"
        return f"[@click={call}]{label}[/]"
    return label


class PluginCommandProvider(Provider):
    """Fuzzy command-palette source: every plugin command, jump on select."""

    def _entries(self) -> Iterator[tuple[Plugin, Command]]:
        app = self.app
        assert isinstance(app, SuperMenuApp)
        for plugin in app.registry.plugins:
            for cmd in plugin.commands():
                yield plugin, cmd

    async def discover(self) -> Hits:
        for plugin, cmd in self._entries():
            yield DiscoveryHit(
                f"{plugin.icon} {plugin.name} › {cmd.name}",
                partial(self.app.select_command, plugin.id, cmd.name),  # type: ignore[attr-defined]
                help=cmd.help,
            )

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for plugin, cmd in self._entries():
            display = f"{plugin.icon} {plugin.name} › {cmd.name}"
            score = matcher.match(display)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(display),
                    partial(self.app.select_command, plugin.id, cmd.name),  # type: ignore[attr-defined]
                    help=cmd.help,
                )


class SuperMenuApp(App):
    TITLE = "super-menu"

    COMMANDS = App.COMMANDS | {PluginCommandProvider}

    CSS = """
    /* ── chrome ─────────────────────────────────────────── */
    Screen {
        layout: vertical;
        background: $background;
    }
    #appbar {
        dock: top;
        height: 1;
        padding: 0 2;
        background: $panel;
    }
    #brand { width: auto; }
    #stats { width: 1fr; text-align: right; color: $text-muted; }
    #body { height: 1fr; padding: 1 2 0 2; }

    /* ── panels ─────────────────────────────────────────── */
    #sidebar, #form, #results {
        background: $surface;
        border: round $primary 45%;
        border-title-color: $text-muted;
        border-title-style: bold;
        border-subtitle-color: $text-muted;
    }
    #sidebar:focus-within, #form:focus-within, #results:focus-within {
        border: round $accent;
        border-title-color: $accent;
    }

    #sidebar {
        width: 32;
        min-width: 24;
        margin-right: 2;
        padding: 1 1;
        scrollbar-gutter: stable;
    }
    #form {
        height: auto;
        max-height: 42%;
        margin-bottom: 1;
        padding: 0 2 1 2;
    }
    #results { height: 1fr; padding: 0 2 1 2; }

    /* ── sidebar tree ───────────────────────────────────── */
    Tree { background: transparent; }
    Tree > .tree--guides { color: $primary 40%; }
    Tree > .tree--guides-hover { color: $accent 60%; }
    Tree > .tree--guides-selected { color: $accent; }
    Tree > .tree--cursor { background: $accent 30%; text-style: bold; }
    Tree > .tree--highlight-line { background: $boost; }

    /* ── welcome ────────────────────────────────────────── */
    #hero-banner { margin-top: 1; text-wrap: nowrap; }
    #hero-tagline { margin-top: 1; color: $text-muted; text-style: italic; }
    .plugin-card {
        height: auto;
        margin-top: 1;
        padding: 0 1;
        border: round $primary 35%;
        background: $boost;
    }
    .plugin-card:hover { border: round $accent 70%; }
    #hints { margin-top: 1; color: $text-muted; }

    /* clickable plugin / command names rendered as action links */
    #form, .plugin-card {
        link-color: $text;
        link-style: bold;
        link-background: transparent;
        link-color-hover: $accent;
        link-background-hover: transparent;
        link-style-hover: bold;
    }

    /* ── form ───────────────────────────────────────────── */
    .command-help { color: $text-muted; margin-top: 1; }
    .field-label { margin-top: 1; }
    .field-help { color: $text-muted; text-style: italic; }
    .muted { color: $text-muted; }

    #form Input, #form Select SelectCurrent {
        border: tall $primary 35%;
        background: $boost;
    }
    #form Input:focus { border: tall $accent; background: $surface; }
    #form Select:focus SelectCurrent { border: tall $accent; }
    #form Checkbox { margin-top: 1; background: transparent; }

    #actions { height: auto; margin-top: 1; }
    #actions Button { margin-right: 2; min-width: 16; }

    /* ── results ────────────────────────────────────────── */
    #placeholder {
        height: 1fr;
        margin-top: 1;
        color: $text-muted;
        text-style: italic;
        content-align: center middle;
        hatch: right $primary 10%;
    }
    #summary-row { height: auto; margin-top: 1; }
    .result-chip { width: auto; padding: 0 1; text-style: bold; }
    .result-chip.-ok { color: $success; background: $success 15%; }
    .result-chip.-err { color: $error; background: $error 15%; }
    #result-summary { width: 1fr; margin-left: 1; }

    GeoMap {
        width: 1fr;
        height: 1fr;
        min-height: 12;
        margin-top: 1;
        padding: 0 1;
        color: $accent;
        border: round $primary 35%;
    }

    #results DataTable {
        height: auto;
        margin-top: 1;
        background: transparent;
    }
    DataTable > .datatable--header {
        background: transparent;
        color: $accent;
        text-style: bold;
    }
    DataTable > .datatable--cursor { background: $accent 35%; }
    DataTable > .datatable--hover { background: $boost; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "run", "Run"),
        Binding("u", "focus_tree", "Plugins"),
        Binding("/", "command_palette", "Search"),
        Binding("t", "cycle_theme", "Theme"),
        Binding("escape", "focus_tree", "Back to menu", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.registry = default_registry()
        self._inputs: dict[str, FieldWidget] = {}
        self._current: Optional[tuple[Plugin, Command]] = None
        n_cmds = sum(len(p.commands()) for p in self.registry.plugins)
        self._stats = f"{len(self.registry.plugins)} plugins · {n_cmds} commands"
        self.sub_title = self._stats

    def compose(self) -> ComposeResult:
        with Horizontal(id="appbar"):
            yield Static("[b $accent]☰[/] [b]super-menu[/]", id="brand")
            yield Static(self._stats, id="stats")
        with Horizontal(id="body"):
            tree: Tree[dict] = Tree("super-menu", id="sidebar")
            tree.show_root = False
            tree.guide_depth = 3
            tree.border_title = "Plugins"
            for plugin in self.registry.plugins:
                cmds = plugin.commands()
                node = tree.root.add(
                    f"{plugin.icon} [b]{escape(plugin.name)}[/] [dim]{len(cmds)}[/]",
                    data={"type": "plugin", "plugin": plugin.id},
                    expand=True,
                )
                for cmd in cmds:
                    node.add_leaf(
                        escape(cmd.name),
                        data={"type": "command", "plugin": plugin.id, "command": cmd.name},
                    )
            yield tree
            with Vertical(id="main"):
                form = VerticalScroll(id="form")
                form.border_title = "Welcome"
                yield form
                results = VerticalScroll(id="results")
                results.border_title = "Results"
                yield results
        yield Footer()

    async def on_mount(self) -> None:
        self.theme = THEMES[0]
        await self._show_welcome()
        await self.query_one("#results", VerticalScroll).mount(
            Static("· run a command to see its output here ·", id="placeholder")
        )
        self.query_one("#sidebar", Tree).focus()

    # ----- navigation -------------------------------------------------------
    async def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        data = event.node.data
        if not data:
            return
        plugin = self.registry.get(data["plugin"])
        if plugin is None:
            return
        if data["type"] == "plugin":
            await self._show_plugin(plugin)
            return
        command = plugin.command(data["command"])
        if command is not None:
            await self._activate(plugin, command)

    async def select_command(self, plugin_id: str, command_name: str) -> None:
        """Jump to a command (used by the command palette); syncs the tree cursor."""
        plugin = self.registry.get(plugin_id)
        command = plugin.command(command_name) if plugin else None
        if plugin is None or command is None:
            return
        tree = self.query_one("#sidebar", Tree)
        for node in self._iter_nodes(tree.root):
            d = node.data or {}
            if (
                d.get("type") == "command"
                and d.get("plugin") == plugin_id
                and d.get("command") == command_name
            ):
                if node.parent is not None:
                    node.parent.expand()
                tree.move_cursor(node)
                break
        await self._activate(plugin, command)

    def _iter_nodes(self, node: TreeNode) -> Iterator[TreeNode]:
        for child in node.children:
            yield child
            yield from self._iter_nodes(child)

    def action_focus_tree(self) -> None:
        self.query_one("#sidebar", Tree).focus()

    def action_cycle_theme(self) -> None:
        current = self.theme
        idx = THEMES.index(current) if current in THEMES else -1
        self.theme = THEMES[(idx + 1) % len(THEMES)]
        self.notify(self.theme, title="theme", timeout=1.5)

    async def action_open_plugin(self, plugin_id: str) -> None:
        """Markup-link target: welcome-screen plugin cards."""
        plugin = self.registry.get(plugin_id)
        if plugin is None:
            return
        tree = self.query_one("#sidebar", Tree)
        for node in tree.root.children:
            d = node.data or {}
            if d.get("type") == "plugin" and d.get("plugin") == plugin_id:
                node.expand()
                tree.move_cursor(node)
                break
        await self._show_plugin(plugin)

    async def action_open_command(self, plugin_id: str, command_name: str) -> None:
        """Markup-link target: command names in the plugin overview."""
        await self.select_command(plugin_id, command_name)

    # ----- info panels ------------------------------------------------------
    async def _show_welcome(self) -> None:
        form = self.query_one("#form", VerticalScroll)
        form.border_title = "Welcome"
        form.border_subtitle = ""
        await form.remove_children()
        form_width = self.size.width - FORM_CHROME_WIDTH
        banner = BANNER_WIDE if form_width >= BANNER_WIDE_WIDTH else BANNER_NARROW
        widgets: list[Widget] = [
            Static(banner, id="hero-banner"),
            Static("one menu, every surface — pick a command, fill the form, run it",
                   id="hero-tagline"),
        ]
        for plugin in self.registry.plugins:
            n = len(plugin.commands())
            name_link = action_link(escape(plugin.name), "app.open_plugin", plugin.id)
            card = (
                f"[$accent]{plugin.icon}[/] {name_link} "
                f"[dim]· {n} command{'s' if n != 1 else ''}[/]"
            )
            if plugin.description:
                card += f"\n[i $text-muted]{escape(plugin.description)}[/]"
            widgets.append(Static(card, classes="plugin-card"))
        widgets.append(Static(
            "[b $accent]enter[/] select · [b $accent]r[/] run · [b $accent]/[/] search · "
            "[b $accent]t[/] theme · [b $accent]q[/] quit",
            id="hints",
        ))
        await form.mount_all(widgets)
        self._current = None
        self._inputs = {}

    async def _show_plugin(self, plugin: Plugin) -> None:
        form = self.query_one("#form", VerticalScroll)
        form.border_title = f"{plugin.icon} {plugin.name}"
        form.border_subtitle = plugin.id
        await form.remove_children()
        widgets: list[Widget] = []
        if plugin.description:
            widgets.append(Static(escape(plugin.description), classes="command-help"))
        for cmd in plugin.commands():
            cmd_link = action_link(escape(cmd.name), "app.open_command",
                                   plugin.id, cmd.name)
            widgets.append(Static(f"[$accent]▸[/] {cmd_link}", classes="field-label"))
            widgets.append(Static(escape(cmd.help), classes="field-help"))
        await form.mount_all(widgets)
        self._current = None
        self._inputs = {}

    # ----- dynamic form -----------------------------------------------------
    async def _activate(self, plugin: Plugin, command: Command) -> None:
        self._current = (plugin, command)
        form = self.query_one("#form", VerticalScroll)
        form.border_title = f"{plugin.icon} {plugin.name} › {command.name}"
        form.border_subtitle = "r · run"
        await form.remove_children()
        self._inputs = {}
        widgets: list[Widget] = [Static(escape(command.help), classes="command-help")]
        for p in command.params:
            field = self._make_field(p)
            self._inputs[p.name] = field
            if isinstance(field, Checkbox):
                widgets.append(field)
                if p.help:
                    widgets.append(Static(escape(p.help), classes="field-help"))
            else:
                req = " [$error]*[/]" if p.required else ""
                widgets.append(Static(f"[$accent]▸[/] [b]{escape(p.name)}[/]{req}",
                                      classes="field-label"))
                if p.help:
                    widgets.append(Static(escape(p.help), classes="field-help"))
                widgets.append(field)
        if not command.params:
            widgets.append(Static("No parameters.", classes="field-help"))
        widgets.append(Horizontal(Button("▶ Run", variant="primary", id="run-btn"),
                                  id="actions"))
        await form.mount_all(widgets)
        first = next(iter(self._inputs.values()), None)
        (first or self.query_one("#run-btn", Button)).focus()

    @staticmethod
    def _make_field(p: Param) -> FieldWidget:
        if p.type == "bool":
            return Checkbox(p.name, value=bool(p.default), id=f"param-{p.name}")
        if p.choices:
            return Select(
                [(c, c) for c in p.choices],
                # Select.BLANK is a dead alias equal to False in current Textual;
                # the real "no selection" sentinel is Select.NULL. Passing False
                # crashes the widget on mount for any choices param without a default.
                value=p.default if p.default is not None else Select.NULL,
                allow_blank=True,
                prompt=f"({p.type})",
                id=f"param-{p.name}",
            )
        input_type = {"int": "integer", "float": "number"}.get(p.type, "text")
        value = "" if p.default in (None, "") else str(p.default)
        placeholder = p.type + (" · required" if p.required else "")
        return Input(value=value, placeholder=placeholder, type=input_type,
                     id=f"param-{p.name}")

    def _collect(self) -> dict[str, Any]:
        raw: dict[str, Any] = {}
        for name, widget in self._inputs.items():
            if isinstance(widget, Checkbox):
                raw[name] = widget.value
            elif isinstance(widget, Select):
                if widget.value is not Select.NULL:
                    raw[name] = widget.value
            else:
                val = widget.value.strip()
                if val != "":
                    raw[name] = val
        return raw

    # ----- execution --------------------------------------------------------
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-btn":
            self.action_run()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if self._current is not None:
            self.action_run()

    def action_run(self) -> None:
        if not self._current:
            self.notify("Pick a command from the menu first.", severity="warning")
            return
        _plugin, command = self._current
        raw = self._collect()
        for p in command.params:
            if p.required and p.name not in raw:
                self.notify(f"'{p.name}' is required", severity="error")
                widget = self._inputs.get(p.name)
                if widget is not None:
                    widget.focus()
                return
        self._run_command(command, raw)

    @work(exclusive=True)
    async def _run_command(self, command: Command, raw: dict[str, Any]) -> None:
        results = self.query_one("#results", VerticalScroll)
        results.border_title = f"Results · {command.name}"
        results.border_subtitle = "running…"
        await results.remove_children()
        results.loading = True
        start = time.monotonic()
        try:
            result = await asyncio.to_thread(self._safe_run, command, raw)
        finally:
            results.loading = False
        await self._render_result(result, time.monotonic() - start)

    @staticmethod
    def _safe_run(command: Command, raw: dict[str, Any]) -> CommandResult:
        # Command.run coerces params before its own try block, so a bad value
        # (e.g. int("abc")) would otherwise escape as a raw exception.
        try:
            return command.run(raw)
        except Exception as exc:
            return CommandResult.err(f"{type(exc).__name__}: {exc}")

    # ----- results rendering ------------------------------------------------
    async def _render_result(self, result: CommandResult, elapsed: float) -> None:
        panel = self.query_one("#results", VerticalScroll)
        await panel.remove_children()
        meta = f"{elapsed:.1f}s"
        if result.ok and isinstance(result.data, list):
            meta = f"{len(result.data)} rows · {meta}"
        panel.border_subtitle = meta
        if not result.ok:
            await panel.mount(Horizontal(
                Static("✗ error", classes="result-chip -err"),
                Static(escape(result.summary), id="result-summary"),
                id="summary-row",
            ))
            return
        await panel.mount(Horizontal(
            Static("✓ ok", classes="result-chip -ok"),
            Static(escape(result.summary or "done"), id="result-summary"),
            id="summary-row",
        ))
        widget = self._data_widget(result)
        if widget is not None:
            await panel.mount(widget)
            if isinstance(widget, GeoMap):
                widget.focus()  # so zoom/pan keys work without an extra Tab

    def _data_widget(self, result: CommandResult) -> Optional[Widget]:
        data = result.data
        if data is None:
            return None
        if result.kind == "geojson" and isinstance(data, dict):
            return GeoMap(data)
        if result.kind == "table" and isinstance(data, list):
            if not data:
                return Static("no rows", classes="muted")
            cols = result.columns or list(data[0].keys())
            table: DataTable = DataTable(zebra_stripes=True, cursor_type="row")
            table.add_columns(*cols)
            for row in data:
                table.add_row(*[Text(self._fmt(row.get(c, ""))) for c in cols])
            return table
        if result.kind == "json":
            try:
                from rich.json import JSON as RichJSON
                return Static(RichJSON.from_data(data, indent=2))
            except Exception:
                pass  # non-serializable payload: fall through to plain repr
        if isinstance(data, list):
            return Static("\n".join(f"[$accent]▪[/] {escape(str(item))}" for item in data))
        if isinstance(data, dict):
            return Static("\n".join(f"[b $accent]{escape(str(k))}[/]  {escape(str(v))}"
                                    for k, v in data.items()))
        return Static(escape(str(data)))

    @staticmethod
    def _fmt(value: Any, width: int = 70) -> str:
        s = str(value)
        return s if len(s) <= width else s[: width - 1] + "…"


def run() -> None:
    SuperMenuApp().run()


if __name__ == "__main__":
    run()
