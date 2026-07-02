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
import time
from functools import partial
from typing import Any, Iterator, Optional, Union

from rich.markup import escape
from rich.text import Text

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Select,
    Static,
    Tree,
)
from textual.widgets.tree import TreeNode

from super_menu.core.registry import default_registry
from super_menu.core.plugin import Command, CommandResult, Param, Plugin

FieldWidget = Union[Input, Checkbox, Select]

# Curated Textual themes cycled with the ``t`` binding.
THEMES = ["tokyo-night", "nord", "gruvbox", "textual-dark", "textual-light"]


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
    Screen { layout: horizontal; }

    #sidebar {
        width: 34;
        min-width: 26;
        padding: 0 1;
        border: round $primary 60%;
        border-title-color: $text-muted;
        border-title-style: bold;
        scrollbar-gutter: stable;
    }
    #sidebar:focus-within {
        border: round $accent;
        border-title-color: $accent;
    }

    #form {
        height: auto;
        max-height: 60%;
        padding: 0 2 1 2;
        border: round $primary 60%;
        border-title-color: $text-muted;
        border-title-style: bold;
    }
    #form:focus-within {
        border: round $accent;
        border-title-color: $accent;
    }

    #results {
        height: 1fr;
        padding: 0 2 1 2;
        border: round $primary 60%;
        border-title-color: $text-muted;
        border-title-style: bold;
    }
    #results:focus-within {
        border: round $accent;
        border-title-color: $accent;
    }

    .field-label { margin-top: 1; text-style: bold; }
    .field-help { color: $text-muted; }
    .muted { color: $text-muted; }
    .command-help { color: $text-muted; margin-top: 1; }

    Checkbox { margin-top: 1; }

    #actions { height: auto; margin-top: 1; }
    #actions Button { margin-right: 2; }

    #summary { padding: 1 0; }
    .error-box {
        border: round $error;
        padding: 0 1;
        margin-top: 1;
    }
    #results DataTable { height: auto; }
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
        self.sub_title = f"{len(self.registry.plugins)} plugins · {n_cmds} commands"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            tree: Tree[dict] = Tree("super-menu", id="sidebar")
            tree.show_root = False
            tree.guide_depth = 3
            tree.border_title = "Plugins"
            for plugin in self.registry.plugins:
                node = tree.root.add(
                    f"{plugin.icon} [b]{escape(plugin.name)}[/]",
                    data={"type": "plugin", "plugin": plugin.id},
                    expand=True,
                )
                for cmd in plugin.commands():
                    node.add_leaf(
                        escape(cmd.name),
                        data={"type": "command", "plugin": plugin.id, "command": cmd.name},
                    )
            yield tree
            with Vertical(id="main"):
                form = VerticalScroll(id="form")
                form.border_title = "Command"
                yield form
                results = VerticalScroll(id="results")
                results.border_title = "Results"
                yield results
        yield Footer()

    async def on_mount(self) -> None:
        self.theme = THEMES[0]
        await self._show_welcome()
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

    # ----- info panels ------------------------------------------------------
    async def _show_welcome(self) -> None:
        form = self.query_one("#form", VerticalScroll)
        form.border_title = "Welcome"
        await form.remove_children()
        lines: list[Widget] = [
            Static("Pick a command from the left, fill in its parameters, and run it.",
                   classes="command-help"),
        ]
        for plugin in self.registry.plugins:
            lines.append(Static(f"{plugin.icon} [b]{escape(plugin.name)}[/]",
                                classes="field-label"))
            if plugin.description:
                lines.append(Static(escape(plugin.description), classes="field-help"))
        lines.append(Static(
            "[b]↑/↓[/] navigate · [b]enter[/] select · [b]r[/] run · "
            "[b]/[/] search commands · [b]t[/] theme · [b]q[/] quit",
            classes="command-help",
        ))
        await form.mount_all(lines)
        self._current = None
        self._inputs = {}

    async def _show_plugin(self, plugin: Plugin) -> None:
        form = self.query_one("#form", VerticalScroll)
        form.border_title = f"{plugin.icon} {plugin.name}"
        await form.remove_children()
        widgets: list[Widget] = []
        if plugin.description:
            widgets.append(Static(escape(plugin.description), classes="command-help"))
        for cmd in plugin.commands():
            widgets.append(Static(f"[b]{escape(cmd.name)}[/]", classes="field-label"))
            widgets.append(Static(escape(cmd.help), classes="field-help"))
        await form.mount_all(widgets)
        self._current = None
        self._inputs = {}

    # ----- dynamic form -----------------------------------------------------
    async def _activate(self, plugin: Plugin, command: Command) -> None:
        self._current = (plugin, command)
        form = self.query_one("#form", VerticalScroll)
        form.border_title = f"{plugin.icon} {plugin.name} › {command.name}"
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
                widgets.append(Static(f"[b]{escape(p.name)}[/]{req}", classes="field-label"))
                if p.help:
                    widgets.append(Static(escape(p.help), classes="field-help"))
                widgets.append(field)
        if not command.params:
            widgets.append(Static("No parameters.", classes="field-help"))
        widgets.append(Horizontal(Button("Run  (r)", variant="primary", id="run-btn"),
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
                value=p.default if p.default is not None else Select.BLANK,
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
                if widget.value is not Select.BLANK:
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
        if not result.ok:
            await panel.mount(
                Static(f"[b $error]✗ error[/]  {escape(result.summary)}",
                       classes="error-box")
            )
            return
        meta = f"{elapsed:.1f}s"
        if isinstance(result.data, list):
            meta = f"{len(result.data)} rows · {meta}"
        await panel.mount(Static(
            f"[b $success]✓[/] {escape(result.summary or 'done')}  [dim]· {meta}[/]",
            id="summary",
        ))
        widget = self._data_widget(result)
        if widget is not None:
            await panel.mount(widget)

    def _data_widget(self, result: CommandResult) -> Optional[Widget]:
        data = result.data
        if data is None:
            return None
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
            return Static("\n".join(f"• {escape(str(item))}" for item in data))
        if isinstance(data, dict):
            return Static("\n".join(f"[b]{escape(str(k))}[/]: {escape(str(v))}"
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
