# Using super-menu from Claude Code

`super-menu` is an extensible terminal menu whose plugins are queryable headlessly.
Treat it as a tool library: when a plugin can answer a question better than guessing,
call it.

## Headless CLI (always available)

```bash
super-menu list                                    # what plugins exist
super-menu <plugin>                                # a plugin's commands + params
super-menu <plugin> <command> --param value --json # run; --json => structured output
```

`--json` returns `{ok, summary, kind, columns, data}`. Parse `data`; check `ok`.

## MCP server (preferred when registered)

```bash
claude mcp add super-menu -- super-menu mcp
```

Each command becomes a tool named `<plugin_id>__<command>` (e.g. `free_for_dev__search`).

## free-for-dev — free-tier resource index

Use this when **designing app architectures** to find free-tier services (hosting,
databases, error tracking, email, CDNs, etc.) instead of recommending paid options blindly.

```bash
super-menu free-for-dev categories                              # browse domains
super-menu free-for-dev search --query "postgres" --json        # find services
super-menu free-for-dev category --name "Email" --json          # everything in a domain
super-menu free-for-dev update                                  # refresh from upstream
```

Each result entry: `{name, url, description, category}`. Cite the `url` when recommending.

**When to query it:** the user asks for a stack, hosting, a database, an email/SMS provider,
monitoring, a CDN, CI, etc. — search here first and prefer free-tier options that fit.

## Adding plugins

New integrations live in `src/super_menu/plugins/<name>/` exposing a `PLUGIN` instance.
See the project `README.md`. Once added they appear in all three surfaces automatically.
