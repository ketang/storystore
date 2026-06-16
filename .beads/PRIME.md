# Beads Workflow Context

This repo uses **bd (Beads)** for ALL task tracking. Create the issue
*before* writing code; mark it in_progress when you start.

## Session close
Before saying "done", run:

1. `git status` — see what changed
2. `git add <files>` — stage code changes
3. `bd dolt pull` — pull beads updates from main
4. `git commit -m "..."` — commit (ephemeral branch; merge to main
   locally, no push)

## Core commands
- `bd ready` — issues ready to work (no blockers)
- `bd show <id>` — issue detail with dependencies
- `bd create --title="..." --description="..." --type=task|bug|feature --priority=2` — new issue (priority 0–4, not high/med/low)
- `bd update <id> --claim` — claim work
- `bd close <id> [<id> ...]` — mark complete
- `bd dep add <issue> <depends-on>` — add dependency

## Rules
- Do NOT use TodoWrite, TaskCreate, or markdown files for task tracking.
- Use `bd remember "insight"` for persistent knowledge; search with
  `bd memories <keyword>`. Do NOT use MEMORY.md files.

---
This trimmed override replaces the full prime output entirely. Run
`bd memories` to load persistent memories (suppressed by this override),
and `bd prime --full` for the complete command reference.
