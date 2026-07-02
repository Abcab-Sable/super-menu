"""Core plugin model.

A single ``Plugin`` definition is the only thing a contributor writes. The TUI,
the headless CLI, and the MCP server all enumerate ``plugin.commands()`` and
render/dispatch from the same metadata, so a new integration lights up in every
surface at once.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

# A handler receives the resolved, type-coerced params as keyword args and
# returns a CommandResult.
Handler = Callable[..., "CommandResult"]

ParamType = Literal["str", "int", "float", "bool"]
ResultKind = Literal["table", "list", "text", "json"]


@dataclass
class Param:
    """One input to a command. Used to build CLI flags, TUI form fields, and
    the MCP JSON schema."""

    name: str
    type: ParamType = "str"
    required: bool = False
    default: Any = None
    help: str = ""
    choices: Optional[list[str]] = None

    def coerce(self, raw: Any) -> Any:
        """Turn a raw string/value (CLI arg, form field) into the typed value."""
        if raw is None:
            return self.default
        if self.type == "int":
            return int(raw)
        if self.type == "float":
            return float(raw)
        if self.type == "bool":
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in {"1", "true", "yes", "on", "y"}
        return str(raw)


@dataclass
class CommandResult:
    """The structured output of a command. ``data`` must be JSON-serializable so
    the same payload flows to CLI ``--json``, the MCP response, and the TUI."""

    ok: bool
    data: Any = None
    summary: str = ""
    kind: ResultKind = "text"
    # For ``kind == "table"``: column order. If omitted, inferred from the first row.
    columns: Optional[list[str]] = None

    @classmethod
    def ok_(cls, data: Any, summary: str = "", kind: ResultKind = "text",
            columns: Optional[list[str]] = None) -> "CommandResult":
        return cls(ok=True, data=data, summary=summary, kind=kind, columns=columns)

    @classmethod
    def err(cls, message: str) -> "CommandResult":
        return cls(ok=False, data=None, summary=message, kind="text")

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "summary": self.summary,
            "kind": self.kind,
            "columns": self.columns,
            "data": self.data,
        }


@dataclass
class Command:
    name: str
    help: str
    handler: Handler
    params: list[Param] = field(default_factory=list)

    def param(self, name: str) -> Optional[Param]:
        return next((p for p in self.params if p.name == name), None)

    def run(self, raw_params: dict[str, Any]) -> CommandResult:
        """Coerce ``raw_params`` against this command's spec and invoke the handler."""
        kwargs: dict[str, Any] = {}
        for p in self.params:
            if p.name in raw_params and raw_params[p.name] is not None:
                kwargs[p.name] = p.coerce(raw_params[p.name])
            elif p.required:
                return CommandResult.err(f"missing required parameter: {p.name}")
            else:
                kwargs[p.name] = p.default
        try:
            return self.handler(**kwargs)
        except Exception as exc:  # surface plugin errors uniformly across surfaces
            return CommandResult.err(f"{type(exc).__name__}: {exc}")


class Plugin:
    """Base class for every integration. Subclass it, set the identity fields,
    and return a list of ``Command`` from :meth:`commands`.

    ``id`` must be a stable, lowercase ``[a-z0-9-]`` token — it is used in CLI
    invocation (``super-menu <id> <command>``) and MCP tool names
    (``<id>__<command>``). Hyphens are allowed (``free-for-dev``).
    """

    id: str = ""
    name: str = ""
    description: str = ""
    icon: str = "•"  # bullet, shown in the TUI sidebar

    def commands(self) -> list[Command]:  # pragma: no cover - abstract
        raise NotImplementedError

    def command(self, name: str) -> Optional[Command]:
        return next((c for c in self.commands() if c.name == name), None)
