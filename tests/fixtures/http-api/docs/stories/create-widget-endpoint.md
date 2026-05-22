---
schema_version: 1
title: Create widget endpoint
slug: create-widget-endpoint
status: active
authority: accepted
change_resistance: high
tests_applicable: true
locked_sections:
  - Intent
---

# Create widget endpoint

## Intent
An API client creates a new widget by POSTing to `/widgets`.

## Story
External services integrate by sending widget definitions and receiving
the assigned id.

## Expected Behavior
A request with a `name` returns 201 and the new widget's id.
A request without a `name` returns 400.

## Boundaries
Authentication and rate limiting are handled elsewhere.

## Auditable Claims
- Missing `name` returns HTTP 400.
- Valid request returns HTTP 201 and a numeric id.

## Evidence
### Tests
- `tests/test_widgets.py`
### Surface
- `POST /widgets`
