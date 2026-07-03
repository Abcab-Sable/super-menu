# Security Policy

## Reporting a Vulnerability

If you discover a security issue in **super-menu**, please report it privately
rather than opening a public issue.

- Preferred: use GitHub's [private vulnerability reporting](https://github.com/Abcab-Sable/super-menu/security/advisories/new)
  (Security tab → "Report a vulnerability").
- We aim to acknowledge reports within **7 days** and to provide a fix or
  mitigation timeline after triage.

Please include:
- a description of the issue and its impact,
- steps to reproduce (a minimal proof of concept if possible),
- affected version or commit.

## Scope

super-menu can run plugins that shell out to external tools (e.g. the `git`
plugin). Reports about command injection, unsafe subprocess handling, path
traversal in cache/index locations, or secret leakage through command output
are in scope and especially welcome.

## Supported Versions

This project is under active development; only the latest `main` is supported
with security fixes.
