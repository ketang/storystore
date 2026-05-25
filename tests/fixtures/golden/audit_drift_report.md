# stories-audit report

Generated: <TIMESTAMP>
Stories scanned: 1
Findings: 2

## Language Coverage

Detected: (none)
Extracted: (none)

## Finding 1: surface `cli: report` does not resolve

- kind: surface-missing
- story_slug: orphaned-evidence
- severity: low
- suggested_action: fix-code

Surface reference `cli: report` does not match any extracted user-facing surface in the repository.

## Finding 2: test evidence `tests/test_missing.py` did not resolve

- kind: test-evidence-missing
- story_slug: orphaned-evidence
- severity: low
- suggested_action: add-evidence

Test evidence ref `tests/test_missing.py` did not resolve to any files. Either add the test, fix the path/glob, or update the story.
