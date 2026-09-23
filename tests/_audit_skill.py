"""Import audit-quotes skill modules without leaving the skill's `scripts` package behind.

The skill's modules import each other as `scripts.<name>`, and the skill's `scripts/` is a regular
package (it has an __init__.py). The repo root also holds a `scripts/` dir, but with no
__init__.py it is only a namespace portion, so the skill's package wins the lookup regardless of
sys.path ordering. That is what the skill needs while it loads — and exactly what breaks every
later `import scripts.<repo module>` in the same pytest run if it is left in place.

Test modules load the skill at import time, and pytest imports every test module before any test
runs, so the cleanup has to happen right after the load, not in a fixture.
"""
import contextlib
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1] / ".claude/skills/audit-quotes"


def _scripts_modules() -> dict:
    return {name: mod for name, mod in sys.modules.items()
            if name == "scripts" or name.startswith("scripts.")}


@contextlib.contextmanager
def audit_skill_imports():
    """Bind `scripts` to the skill's package inside the block; restore sys.path and sys.modules after.

    Modules loaded inside the block keep working afterwards: the skill only imports `scripts.*` at
    module top level, so their names are already bound when the block exits.
    """
    saved_path = list(sys.path)
    saved_modules = _scripts_modules()
    for name in saved_modules:
        del sys.modules[name]
    sys.path.insert(0, str(SKILL_ROOT))
    try:
        yield
    finally:
        for name in _scripts_modules():
            del sys.modules[name]
        sys.modules.update(saved_modules)
        sys.path[:] = saved_path
