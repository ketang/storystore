---
schema_version: 1
title: CLI init command
slug: cli-init-command
status: active
authority: observed
change_resistance: medium
tests_applicable: true
---

# CLI init command

## Intent
A user runs the `init` subcommand to scaffold a new project.

## Story
A developer adopting the tool needs a fast first-run experience that
creates a usable starting layout without prompting.

## Expected Behavior
Running `cli init` exits 0 and prints `initialized`.

## Boundaries
This story does not cover network access or template selection.

## Auditable Claims
- `cli init` prints the literal string `initialized` on stdout.

## Evidence
### Surface
- `cli: cli init`
### Docs
- `README.md`
