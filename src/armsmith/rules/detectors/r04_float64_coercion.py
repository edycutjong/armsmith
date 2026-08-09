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


def _is_integer_comprehension(node: ast.AST) -> bool:
    """True for a comprehension that provably yields ints, e.g. ``[i for i in range(n)]``.

    numpy infers int64 from such a payload exactly as it does from a literal
    list, so these must not be reported either. Only the narrow, provable shape
    is recognised: the element expression is the loop variable itself (or an
    integer literal), and the iterable is a ``range(...)`` call.
    """
    if not isinstance(node, (ast.ListComp, ast.SetComp)):
        return False
    # A comprehension always carries at least one generator — the grammar
    # requires it — so there is no empty case to guard.
    targets: set[str] = set()
    for gen in node.generators:
        iter_ = gen.iter
        is_range = (
            isinstance(iter_, ast.Call)
            and isinstance(iter_.func, ast.Name)
            and iter_.func.id == "range"
        )
        if not is_range:
            return False
        if isinstance(gen.target, ast.Name):
            targets.add(gen.target.id)
    elt = node.elt
    if isinstance(elt, ast.Name) and elt.id in targets:
        return True
    return _is_integer_literal(elt)


#: How confident we are that a call really produces float64.
PROVEN = "proven"          # the constructor or its literal payload guarantees it
UNPROVABLE = "unprovable"  # a non-literal payload we cannot type statically
CLEAN = "clean"            # provably NOT float64 — never reported


def _classify(ctor: str, call: ast.Call) -> str:
    """Classify a dtype-less call as PROVEN float64, UNPROVABLE, or CLEAN.

    The distinction is not cosmetic. ``np.zeros(n)`` is float64 whatever you
    pass it, so pinning ``dtype=np.float32`` there is a safe, mechanical fix.
    ``np.array(x)`` where ``x`` is a name is *unknown* — and if it happens to
    hold integers (an index or permutation array, which is common in quantised
    inference code) then pinning float32 silently corrupts it. Reporting both
    is right; suggesting the same patch for both is not.
    """
    if ctor in _ALWAYS_FLOAT64:
        return PROVEN
    payload = None
    if ctor == "array" and call.args:
        payload = call.args[0]
    elif ctor == "full" and len(call.args) >= 2:
        payload = call.args[1]
    if payload is None:
        return UNPROVABLE
    if _is_integer_literal(payload) or _is_integer_comprehension(payload):
        return CLEAN
    # A float literal payload proves float64; anything else is a name, call or
    # expression whose element type we cannot see.
    if isinstance(payload, (ast.Constant, ast.List, ast.Tuple, ast.UnaryOp)):
        return PROVEN
    return UNPROVABLE


class _NumpyAliasVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.module_aliases: set[str] = set()     # numpy, np, ...
        self.func_aliases: dict[str, str] = {}    # local name -> ctor name
        # (line, col, call repr, PROVEN|UNPROVABLE)
        self.hits: list[tuple[int, int, str, str]] = []

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
            if not has_dtype:
                verdict = _classify(ctor, node)
                if verdict != CLEAN:
                    self.hits.append((node.lineno, node.col_offset, display, verdict))
        self.generic_visit(node)


@register("R4")
def detect(repo: Path | None, probe, spec: RuleSpec) -> Finding:
    assert repo is not None
    evidence: list[str] = []
    locations: list[str] = []
    proven: list[str] = []
    unprovable: list[str] = []

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
        for lineno, _col, name, verdict in visitor.hits:
            site = f"{rel}:{lineno}"
            locations.append(site)
            if verdict == PROVEN:
                evidence.append(
                    f"{site}: {name}(...) without dtype= — this call returns float64"
                )
                proven.append(f"{site}: add dtype=np.float32 to {name}(...)")
            else:
                evidence.append(
                    f"{site}: {name}(...) without dtype= — payload type is not statically "
                    f"provable; float64 only if its elements are floats"
                )
                unprovable.append(
                    f"{site}: CONFIRM the payload is float before pinning dtype on "
                    f"{name}(...). If it holds integers (an index or permutation array), "
                    f"pinning float32 CORRUPTS it — leave this call alone."
                )

    if not locations:
        return clean(spec, ["all numpy constructor calls pin an explicit dtype"])

    # A patch is only offered for the calls we can prove. The rest are reported
    # as questions, because the whole point of this rule is not to hand someone
    # an edit that silently breaks their indexing.
    patch_lines = []
    if proven:
        patch_lines.append("# safe to pin — these return float64 regardless of payload")
        patch_lines.extend(proven)
    if unprovable:
        if patch_lines:
            patch_lines.append("")
        patch_lines.append("# NOT auto-patchable — verify the element type first")
        patch_lines.extend(unprovable)

    if proven:
        description = (
            f"Pin dtype=np.float32 at the {len(proven)} call site(s) proven to return "
            f"float64. The other {len(unprovable)} site(s) are reported for review only: "
            "their payload type cannot be determined statically, and pinning float32 on "
            "an integer array corrupts it."
        ) if unprovable else (
            "Pin dtype=np.float32 at each flagged numpy constructor call site; keep a "
            "single explicit cast at the model boundary if float64 is genuinely required."
        )
        kind = "code_suggestion"
    else:
        description = (
            f"{len(unprovable)} call site(s) omit dtype= on a payload whose type cannot "
            "be determined statically. Review each one: pin float32 only where the data "
            "is genuinely float. No automatic patch is offered, because pinning float32 "
            "on an integer array silently corrupts it."
        )
        kind = "advisory"

    fix = Fix(
        rule_id=spec.id,
        kind=kind,
        description=description,
        patch="\n".join(patch_lines),
        commands=(),
    )
    return Finding(
        rule_id=spec.id,
        status=FindingStatus.MATCHED,
        evidence=tuple(evidence),
        locations=tuple(locations),
        fix=fix,
    )
