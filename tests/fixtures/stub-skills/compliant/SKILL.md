---
name: stories-stub-compliant
description: |
  Planned capability — NOT yet implemented. Invoking this skill fails
  loudly and names the shipped plugin version.
stub: true
---

# stories-stub-compliant

This skill is a **stub**. The capability is planned but not implemented in
this release. Your first and only step is to run the loud-failure guard,
which exits non-zero and names the shipped plugin version:

```bash
python3 scripts/stub-skill-guard.py --skill stories-stub-compliant
```

Surface the guard's error to the user. Do not proceed as if the skill ran.
