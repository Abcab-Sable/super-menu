"""The interactive Textual TUI for super-menu.

Left: a tree of plugins → commands. Right: an auto-generated parameter form for
the selected command and a results panel. The form and rendering are driven
entirely by the plugin's ``Command``/``Param`` metadata, so new plugins appear
here with zero TUI code.
"""
from __future__ import annotations

from typing import Any, Optional

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
    Tree,
)
from textual.widgets.tree import TreeNode

from super_menu.core.registry import default_registry
from super_menu.core.plugin import Command, CommandResult, Plugin


class SuperMenuApp(App):
    CSS = """
    Screen { layout: horizontal; }
    #sidebar { width: 34; border-right: solid $primary; }
    #main { padding: 0 1; }
    #form { height: auto; max-height: 50%; border-bottom: solid $panel; padding: 1; }
    #form-title { text-style: bold; color: $accent; }
    .field-label { color: $text-muted; margin-top: 1; }
    #results { height: 1fr; }
    #summary { color: $accent; text-style: italic; padding: 1 0; }
    Button { margin-top: 1; }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "run", "Run command"),
        ("u", "focus_tree", "Plugins"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.registry = default_registry()
        self._inputs: dict[str, Input] = {}
        self._current: Optional[tuple[Plugin, Command]] = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            tree: Tree[dict] = Tree("🗂  super-menu", id="sidebar")
            tree.root.expand()
            for plugin in self.registry.plugins:
                node = tree.root.add(
                    f"{plugin.icon} {plugin.name}",
                    data={"type": "plugin", "plugin": plugin.id},
                    expand=True,
                )
                for cmd in plugin.commands():
                    node.add_leaf(
                        f"  {cmd.name}",
                        data={"type": "command", "plugin": plugin.id, "command": cmd.name},
                    )
            yield tree
            with Vertical(id="main"):
                with VerticalScroll(id="form"):
                    yield Static("Select a command from the left.", id="form-title")
                with VerticalScroll(id="results"):
                    yield Static("", id="summary")
        yield Footer()

    # ----- navigation -------------------------------------------------------
    async def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        data = event.node.data
        if not data or data.get("type") != "command":
            return
        plugin = self.registry.get(data["plugin"])
        if plugin is None:
            return
        command = plugin.command(data["command"])
        if command is None:
            return
        self._current = (plugin, command)
        await self._build_form(plugin, command)

    def action_focus_tree(self) -> None:
        self.query_one(Tree).focus()

    # ----- dynamic form -----------------------------------------------------
    async def _build_form(self, plugin: Plugin, command: Command) -> None:
        form = self.query_one("#form", VerticalScroll)
        await form.remove_children()
        self._inputs = {}
        form.mount(Static(f"{plugin.icon} {plugin.name} › {command.name}", id="form-title"))
        form.mount(Static(command.help, classes="field-label"))
        for p in command.params:
            req = " *" if p.required else ""
            hint = f" — {p.help}" if p.help else ""
            form.mount(Label(f"{p.name}{req}{hint}", classes="field-label"))
            placeholder = "" if p.default in (None, "") else str(p.default)
            inp = Input(placeholder=placeholder or p.type, id=f"param-{p.name}")
            self._inputs[p.name] = inp
            form.mount(inp)
        form.mount(Button("Run  (r)", variant="primary", id="run-btn"))

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-btn":
            await self.action_run()

    async def action_run(self) -> None:
        if not self._current:
            return
        _plugin, command = self._current
        raw: dict[str, Any] = {}
        for name, inp in self._inputs.items():
            val = inp.value.strip()
            if val != "":
                raw[name] = val
        result = command.run(raw)
        await self._render_result(result)

    # ----- results rendering ------------------------------------------------
    async def _render_result(self, result: CommandResult) -> None:
        panel = self.query_one("#results", VerticalScroll)
        await panel.remove_children()
        if not result.ok:
            panel.mount(Static(f"[b red]error:[/] {result.summary}"))
            return
        panel.mount(Static(result.summary or "done", id="summary"))
        data = result.data
        if result.kind == "table" and isinstance(data, list) and data:
            cols = result.columns or list(data[0].keys())
            table: DataTable = DataTable(zebra_stripes=True)
            table.add_columns(*cols)
            for row in data:
                table.add_row(*[self._fmt(row.get(c, "")) for c in cols])
            panel.mount(table)
        elif isinstance(data, list):
            for item in data:
                panel.mount(Static(f"• {item}"))
        elif isinstance(data, dict):
            for k, v in data.items():
                panel.mount(Static(f"[b]{k}[/]: {v}"))
        elif data is not None:
            panel.mount(Static(str(data)))

    @staticmethod
    def _fmt(value: Any, width: int = 70) -> str:
        s = str(value)
        return s if len(s) <= width else s[: width - 1] + "…"


def run() -> None:
    SuperMenuApp().run()


if __name__ == "__main__":
    run()
