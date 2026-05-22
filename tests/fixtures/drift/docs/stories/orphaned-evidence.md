---
schema_version: 1
title: Orphaned evidence story
slug: orphaned-evidence
status: active
authority: observed
change_resistance: low
tests_applicable: true
---

# Orphaned evidence story

## Intent
A user runs the `report` command to view aggregated stats.

## Expected Behavior
Running the command exits 0 and prints a non-empty report.

## Auditable Claims
- The report includes a total count line.

## Evidence
### Tests
- `tests/test_missing.py`
### Surface
- `cli: report`
