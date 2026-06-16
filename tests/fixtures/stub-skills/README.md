# stub-skills fixture

Fixtures for the stub-skill loud-failure contract (`ss-c2v`).

- `compliant/SKILL.md` — a stub skill that obeys the contract: its first
  step invokes `scripts/stub-skill-guard.py`, so invoking it exits
  non-zero and names the shipped plugin version.
- `clean-exit/SKILL.md` + `clean-exit/run.sh` — the anti-pattern a stub
  must never be: documents itself as deferred and exits clean. Used to
  prove the contract check rejects a silent stub.

See `docs/contributing/stub-skills.md` for the contract and
`tests/test_stub_skill_contract.py` for the enforcement.
