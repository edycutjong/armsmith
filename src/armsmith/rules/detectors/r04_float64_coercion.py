"""R4 — silent float64 coercion (static AST scan, fully real).

Walks every ``*.py`` file's AST looking for numpy array-constructor calls
without an explicit ``dtype=`` keyword: ``np.array/zeros/ones/empty/full/
linspace``.  Handles ``import numpy``, ``import numpy as np`` and
``from numpy import array as arr`` alias forms.

A missing ``dtype=`` is only reported where float64 is actually the outcome.
numpy infers dtype from the data, so ``np.array([0, 2, 4])`` is int64 and
``np.full(n, 0)`` is int64 — reporting those would be a false positive, and
the patch this rule suggests (pin ``dtype=np.float32``) would silently corrupt
an integer index array. ``zeros``/``ones``/``empty``/``linspace`` return
float64 regardless of their arguments and are always reported.
"""

from __future__ import annotations

import ast
from pathlib import Path

from ..base import Finding, FindingStatus, Fix, RuleSpec, clean, register

#: numpy constructors whose default dtype is float64 for float input.
_CTORS = {"array", "zeros", "ones", "empty", "full", "linspace"}

#: Constructors that return float64 by default no matter what you pass them:
#: ``np.zeros(3)`` is float64, and so is ``np.linspace(0, 1)``. For these,
#: a missing ``dtype=`` is always worth flagging.
_ALWAYS_FLOAT64 = {"zeros", "ones", "empty", "linspace"}


def _is_integer_literal(node: ast.AST) -> bool:
    """True if ``node`` is an int/bool literal, or a list/tuple literal of them.

    numpy infers dtype from the data: ``np.array([0, 2, 4])`` is **int64**, not
    float64, and ``np.full(n, 0)`` is int64 too. Flagging those as "silent
    float64 coercion" is simply wrong, and the suggested fix — pinning
    ``dtype=np.float32`` — would corrupt an integer index or permutation array.
    Recurses so nested literals like ``[[1, 2], [3, 4]]`` are recognised, and
    accepts unary +/- so ``[-1, 2]`` still counts.
    """
    if isinstance(node, ast.Constant):
        # bool is a subclass of int; neither yields float64.
        return isinstance(node.value, int)
    if isinstance(node, (ast.List, ast.Tuple)):
        return bool(node.elts) and all(_is_integer_literal(e) for e in node.elts)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return _is_integer_literal(node.operand)
    return False


def _yields_float64(ctor: str, call: ast.Call) -> bool:
    """Whether this call actually risks a float64 result without ``dtype=``.

    Anything we cannot prove is integer stays flagged: a non-literal argument
    (``np.array(x)``) is unknown, and under-reporting a real float64 coercion
    is the more expensive mistake on an Arm inference path.
    """
    if ctor in _ALWAYS_FLOAT64:
        return True
    if ctor == "array":
        # dtype is inferred from the payload: np.array([0, 2, 4]) is int64.
        return not (call.args and _is_integer_literal(call.args[0]))
    # ``full`` is the only constructor left in _CTORS, and its dtype follows the
    # fill value: np.full(n, 0) is int64, np.full(n, 0.5) is float64.
    return not (len(call.args) >= 2 and _is_integer_literal(call.args[1]))


class _NumpyAliasVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.module_aliases: set[str] = set()     # numpy, np, ...
        self.func_aliases: dict[str, str] = {}    # local name -> ctor name
        self.hits: list[tuple[int, int, str]] = []  # (line, col, call repr)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "numpy" or alias.name.startswith("numpy."):
                self.module_aliases.add(alias.asname or alias.name.split(".")[0])
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "numpy":
            for alias in node.names:
                if alias.name in _CTORS:
                    self.func_aliases[alias.asname or alias.name] = alias.name
        self.generic_visit(node)

    def _ctor_name(self, call: ast.Call) -> tuple[str, str] | None:
        """Return ``(display_name, canonical_ctor)``, or None if not a numpy ctor.

        The two differ under ``from numpy import array as arr``: the message
        should say ``arr(...)`` but the dtype reasoning needs ``array``.
        """
        fn = call.func
        if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
            if fn.value.id in self.module_aliases and fn.attr in _CTORS:
                return f"{fn.value.id}.{fn.attr}", fn.attr
        if isinstance(fn, ast.Name) and fn.id in self.func_aliases:
            return fn.id, self.func_aliases[fn.id]
        return None

    def visit_Call(self, node: ast.Call) -> None:
        found = self._ctor_name(node)
        if found is not None:
            display, ctor = found
            has_dtype = any(kw.arg == "dtype" for kw in node.keywords)
            if not has_dtype and _yields_float64(ctor, node):
                self.hits.append((node.lineno, node.col_offset, display))
        self.generic_visit(node)


@register("R4")
def detect(repo: Path | None, probe, spec: RuleSpec) -> Finding:
    assert repo is not None
    evidence: list[str] = []
    locations: list[str] = []
    suggestions: list[str] = []

    for py in sorted(repo.rglob("*.py")):
        if ".git" in py.parts or ".venv" in py.parts:
            continue
        rel = py.relative_to(repo)
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:
            evidence.append(f"{rel}: skipped (syntax error at line {exc.lineno})")
            continue
        visitor = _NumpyAliasVisitor()
        visitor.visit(tree)
        for lineno, _col, name in visitor.hits:
            evidence.append(
                f"{rel}:{lineno}: {name}(...) without dtype= — floats default to float64"
            )
            locations.append(f"{rel}:{lineno}")
            suggestions.append(f"{rel}:{lineno}: add dtype=np.float32 to {name}(...)")

    if not locations:
        return clean(spec, ["all numpy constructor calls pin an explicit dtype"])

    fix = Fix(
        rule_id=spec.id,
        kind="code_suggestion",
        description=(
            "Pin dtype=np.float32 at each flagged numpy constructor call site; "
            "keep a single explicit cast at the model boundary if float64 is "
            "genuinely required somewhere."
        ),
        patch="\n".join(suggestions),
        commands=(),
    )
    return Finding(
        rule_id=spec.id,
        status=FindingStatus.MATCHED,
        evidence=tuple(evidence),
        locations=tuple(locations),
        fix=fix,
    )
