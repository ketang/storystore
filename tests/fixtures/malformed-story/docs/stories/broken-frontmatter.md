---
schema_version: 1
title: Broken frontmatter story
slug: broken-frontmatter
status: active
authority: observed
change_resistance: low
tests_applicable: true
nested:
  not: allowed
---

# Broken frontmatter story

## Intent
This story has a nested mapping in its frontmatter, which the storystore
YAML dialect rejects.

## Expected Behavior
Loaders should surface a parse error with line/column.
