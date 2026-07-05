"""R4 — silent float64 coercion (static AST scan, fully real).

Walks every ``*.py`` file's AST looking for numpy array-constructor calls
without an explicit ``dtype=`` keyword: ``np.array/zeros/ones/empty/full/
linspace``.  Handles ``import numpy``, ``import numpy as np`` and
``from numpy import array as arr`` alias forms.
"""

from __future__ import annotations

import ast
from pathlib import Path

from ..base import Finding, FindingStatus, Fix, RuleSpec, clean, register

#: numpy constructors whose default dtype is float64 for float input.
_CTORS = {"array", "zeros", "ones", "empty", "full", "linspace"}


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

    def _ctor_name(self, call: ast.Call) -> str | None:
        fn = call.func
        if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
            if fn.value.id in self.module_aliases and fn.attr in _CTORS:
                return f"{fn.value.id}.{fn.attr}"
        if isinstance(fn, ast.Name) and fn.id in self.func_aliases:
            return fn.id
        return None

    def visit_Call(self, node: ast.Call) -> None:
        name = self._ctor_name(node)
        if name is not None:
            has_dtype = any(kw.arg == "dtype" for kw in node.keywords)
            if not has_dtype:
                self.hits.append((node.lineno, node.col_offset, name))
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
