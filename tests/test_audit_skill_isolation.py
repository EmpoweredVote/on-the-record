"""Loading the audit-quotes skill in a test must not hijack the repo's `scripts` namespace.

The skill ships a regular package named `scripts/`; the repo root's `scripts/` has no __init__.py,
so it is only a namespace portion and loses the lookup to any regular package on sys.path. When
test_audit_checks / test_audit_bundle left the skill package bound to `scripts`, every later
`import scripts.<repo module>` in the same run (evidence_slice, judge_ab, commit_evidence) failed
with ModuleNotFoundError — but only in a full-suite run, never when a file ran on its own.
"""
import importlib
import sys

from tests._audit_skill import SKILL_ROOT, audit_skill_imports


def test_audit_skill_tests_leave_repo_scripts_importable():
    importlib.import_module("tests.test_audit_checks")
    importlib.import_module("tests.test_audit_bundle")

    assert str(SKILL_ROOT) not in sys.path
    bound = getattr(sys.modules.get("scripts"), "__file__", None) or ""
    assert "audit-quotes" not in bound
    importlib.import_module("scripts.evidence_slice")
    importlib.import_module("scripts.judge_ab")
    importlib.import_module("scripts.commit_evidence")


def test_audit_skill_loads_when_repo_scripts_is_bound_first():
    """The other order: a repo `scripts.*` import that runs first must not hide the skill's package.

    Once the repo's namespace `scripts` is in sys.modules, a plain import resolves `scripts.checks`
    against it and fails, so the skill tests would break whenever a repo-scripts test loads first.
    """
    importlib.import_module("scripts.evidence_slice")
    repo_scripts = sys.modules["scripts"]

    with audit_skill_imports():
        checks = importlib.import_module("scripts.checks")

    assert checks.__file__.startswith(str(SKILL_ROOT))
    assert sys.modules["scripts"] is repo_scripts
