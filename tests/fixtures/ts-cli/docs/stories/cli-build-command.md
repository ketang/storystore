---
schema_version: 1
title: CLI build command
slug: cli-build-command
status: active
authority: observed
change_resistance: low
tests_applicable: true
---

# CLI build command

## Intent
A user runs `cli build` to compile the project.

## Story
After editing source, the developer wants a single command that produces
a built artifact.

## Expected Behavior
Running `cli build` exits 0 and prints `built`.

## Boundaries
Watch mode and incremental builds are out of scope.

## Auditable Claims
- `cli build` prints `built` on stdout.

## Evidence
### Surface
- `cli: cli build`
