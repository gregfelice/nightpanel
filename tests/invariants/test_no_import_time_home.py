# SPDX-License-Identifier: GPL-3.0-or-later
"""No module under src/nightpanel binds ``Path.home()`` at import time.

The adapter battery polices ``adapters/`` only. This covers the rest of the package:
a module-level ``Path.home()`` is fixed when the module is imported, so it ignores
``XDG_CONFIG_HOME`` and ``NP_CONFIG_DIR`` and ties tests to the developer's real home.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "nightpanel"
_DEFERRED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _import_time_home_calls(tree: ast.AST) -> list[int]:
    """Line numbers of ``Path.home()`` calls that run when the module is imported."""
    found: list[int] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _DEFERRED):
                # A def body runs later; its defaults and decorators run now.
                args = [] if isinstance(child, ast.Lambda) else child.decorator_list
                for sub in [*args, *child.args.defaults, *child.args.kw_defaults]:
                    if sub is not None:
                        visit_expr(sub)
                continue
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "home"
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == "Path"
            ):
                found.append(child.lineno)
            visit(child)

    def visit_expr(expr: ast.AST) -> None:
        wrapper = ast.Module(body=[ast.Expr(value=expr)], type_ignores=[])
        visit(wrapper)

    visit(tree)
    return found


def _modules() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def test_no_module_binds_path_home_at_import() -> None:
    offenders = {
        str(p.relative_to(SRC)): lines
        for p in _modules()
        if (lines := _import_time_home_calls(ast.parse(p.read_text())))
    }
    assert not offenders, f"Path.home() runs at import time: {offenders}"


def test_the_rule_bites() -> None:
    bad = ast.parse("from pathlib import Path\nX = Path.home() / 'a'\n")
    ok = ast.parse("from pathlib import Path\ndef f():\n    return Path.home()\n")
    assert _import_time_home_calls(bad) == [2]
    assert _import_time_home_calls(ok) == []
