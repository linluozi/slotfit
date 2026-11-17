"""Static analysis: find classes whose instances can safely move to __slots__.

The rules are deliberately conservative.  A class is only reported ``safe`` when
the source shows a fixed attribute set and no facility that requires a per
instance ``__dict__``.  Everything else is reported as ``review`` or ``unsafe``
with an explicit reason, so the tool never recommends a change it cannot
justify from the code it read.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

SAFE = "safe"
REVIEW = "review"
UNSAFE = "unsafe"
ALREADY = "already-slotted"

_DYNAMIC_DUNDERS = {
    "__getattr__",
    "__getattribute__",
    "__setattr__",
    "__delattr__",
    "__init_subclass__",
}


@dataclass
class ClassReport:
    path: str
    lineno: int
    name: str
    bases: list[str]
    attrs: list[str]
    status: str
    reasons: list[str] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "lineno": self.lineno,
            "name": self.name,
            "bases": self.bases,
            "attrs": self.attrs,
            "status": self.status,
            "reasons": self.reasons,
            "decorators": self.decorators,
        }


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return _dotted(node.func) + "()"
    return type(node).__name__


def _self_attrs(fn: ast.AST) -> tuple[set[str], list[str]]:
    """Return (assigned attribute names, dynamic-attribute reasons)."""
    names: set[str] = set()
    problems: list[str] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            func = _dotted(node.func)
            if func in {"setattr", "delattr", "vars"}:
                problems.append(f"calls {func}()")
            if func in {"globals", "locals"}:
                problems.append(f"calls {func}()")
        if isinstance(node, ast.Attribute) and node.attr == "__dict__":
            problems.append("touches instance/class __dict__")
        if isinstance(node, ast.Assign):
            for target in node.targets:
                for sub in ast.walk(target):
                    if (
                        isinstance(sub, ast.Attribute)
                        and isinstance(sub.value, ast.Name)
                        and sub.value.id == "self"
                    ):
                        names.add(sub.attr)
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                names.add(target.attr)
        if isinstance(node, ast.AugAssign):
            target = node.target
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                names.add(target.attr)
    return names, problems


def _init_unsafe_reasons(init: ast.FunctionDef) -> list[str]:
    args = init.args
    reasons: list[str] = []
    if args.kwarg is not None:
        reasons.append(f"__init__ accepts **{args.kwarg.arg}")
    if args.vararg is not None:
        reasons.append(f"__init__ accepts *{args.vararg.arg}")
    for node in ast.walk(init):
        if isinstance(node, ast.Call):
            func = _dotted(node.func)
            if func in {"setattr", "delattr"}:
                reasons.append(f"__init__ calls {func}()")
            if func == "super" and node.args == []:
                # super().__init__ is fine, but a base may add a dict.
                pass
    return reasons


def analyze_class(node: ast.ClassDef, path: str) -> ClassReport | None:
    body_names = {
        target.id
        for stmt in node.body
        if isinstance(stmt, ast.Assign)
        for target in stmt.targets
        if isinstance(target, ast.Name)
    }
    decorators = [_dotted(d) for d in node.decorator_list]
    bases = [_dotted(b) for b in node.bases]
    if "__slots__" in body_names:
        return ClassReport(path, node.lineno, node.name, bases, [], ALREADY,
                           ["class already declares __slots__"], decorators)

    methods: dict[str, ast.AST] = {}
    for stmt in node.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods[stmt.name] = stmt
        elif isinstance(stmt, ast.ClassDef):
            continue

    reasons: list[str] = []
    for d in decorators:
        if d.endswith("dataclass") or d.endswith("dataclass()"):
            reasons.append("dataclass: needs slots=True, not a plain __slots__ edit")
        elif d not in {"final", "staticmethod", "classmethod", "property"}:
            reasons.append(f"decorator @{d} may rely on a per-instance dict")

    for base in bases:
        if base not in {"object", "ABC", "abc.ABC", "Protocol"}:
            reasons.append(f"inherits from {base}: base layout must be checked")

    for stmt in node.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in stmt.decorator_list:
                if _dotted(dec).endswith("cached_property"):
                    reasons.append(f"{stmt.name} uses cached_property, which needs __dict__")

    for name in sorted(_DYNAMIC_DUNDERS & methods.keys()):
        reasons.append(f"defines {name}(), which requires dynamic attribute lookup")

    init = methods.get("__init__")
    attrs: set[str] = set()
    if init is None:
        reasons.append("no __init__() to derive a fixed attribute set from")
    else:
        attrs, problems = _self_attrs(init)
        reasons.extend(problems)
        reasons.extend(_init_unsafe_reasons(init))
        if not attrs:
            reasons.append("__init__ assigns no self attributes")

    for name, fn in methods.items():
        if name == "__init__":
            continue
        extra, problems = _self_attrs(fn)
        if extra:
            reasons.append(
                f"{name}() assigns self.{sorted(extra)[0]} outside __init__"
            )
        reasons.extend(f"{name}(): {p}" for p in problems)

    for stmt in node.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == "__weakref__":
                    reasons.append("declares __weakref__")

    status = SAFE if not reasons else UNSAFE
    if reasons and all(
        r.startswith(("inherits from", "decorator @", "no __init__", "dataclass"))
        or "base layout must be checked" in r
        for r in reasons
    ):
        status = REVIEW
    return ClassReport(path, node.lineno, node.name, bases, sorted(attrs), status,
                       reasons, decorators)


def analyze_source(text: str, path: str = "<string>") -> list[ClassReport]:
    try:
        tree = ast.parse(text, filename=path)
    except SyntaxError:
        return []
    out: list[ClassReport] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            report = analyze_class(node, path)
            if report is not None:
                out.append(report)
    return out


def analyze_file(path: Path) -> list[ClassReport]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    try:
        return analyze_source(text, str(path))
    except SyntaxError:
        return []


def iter_python_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        if root.suffix == ".py":
            yield root
        return
    for path in sorted(root.rglob("*.py")):
        if any(part in {".git", ".venv", "venv", "build", "dist", "__pycache__"}
               for part in path.parts):
            continue
        yield path


def analyze_paths(paths: Iterable[str]) -> list[ClassReport]:
    reports: list[ClassReport] = []
    for raw in paths:
        root = Path(raw)
        if not root.exists():
            continue
        for path in iter_python_files(root):
            reports.extend(analyze_file(path))
    return reports
