#!/bin/sh
# ANTI-PATTERN: a stub that prints status prose and exits clean.
# The loud-failure contract check (tests/test_stub_skill_contract.py)
# must reject this behavior.
echo "stories-stub-clean-exit: implementation deferred; nothing to do."
exit 0
