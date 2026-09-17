"""Static safety policy for model-generated analysis code.

This is the first of three layers (static AST policy → restricted runtime in a
separate process → audit hook + resource watchdog). It rejects obviously unsafe
code early and returns messages the model can use to fix its own code.
"""

from __future__ import annotations

import ast
import re

MAX_CODE_CHARS = 30_000

ALLOWED_MODULES = frozenset({
    "pandas", "numpy",
    "plotly", "plotly.express", "plotly.graph_objects", "plotly.subplots", "plotly.colors",
    "math", "statistics", "datetime", "re", "collections", "itertools", "functools",
    "json", "decimal", "fractions", "string", "textwrap", "calendar", "typing", "warnings",
})
# Submodules of these packages are allowed unless a segment is blocked.
ALLOWED_SUBMODULE_ROOTS = frozenset({"pandas", "numpy"})
BLOCKED_SEGMENTS = frozenset({"io", "ctypeslib", "f2py", "distutils", "testing", "compat", "conftest"})

# Runtime import guard (inside the sandbox). Libraries import their own private
# submodules lazily *through the calling frame's builtins*, so at runtime we gate
# on the top-level package only; the static policy above still restricts what the
# generated source itself may import.
RUNTIME_IMPORT_ROOTS = frozenset({
    *(m.split(".")[0] for m in ALLOWED_MODULES),
    "pyarrow", "dateutil", "pytz", "tzdata", "narwhals", "packaging", "six",
    "_strptime", "time", "zoneinfo", "locale", "copy", "operator", "numbers", "unicodedata",
    "difflib", "bisect", "heapq", "weakref", "contextlib", "enum", "dataclasses", "abc", "random",
    "encodings", "_json", "_decimal", "_datetime", "_pydatetime", "_collections_abc",
})

BLOCKED_BUILTINS = frozenset({
    "open", "eval", "exec", "compile", "input", "__import__", "globals", "locals", "vars",
    "getattr", "setattr", "delattr", "breakpoint", "exit", "quit", "help", "memoryview",
})

BLOCKED_ATTRIBUTES = frozenset({
    # file / network IO
    "to_csv", "to_excel", "to_parquet", "to_pickle", "to_sql", "to_hdf", "to_feather", "to_stata",
    "to_clipboard", "to_orc", "to_xml", "to_latex", "read_pickle", "write_html", "write_image",
    "write_json", "save", "savez", "savez_compressed", "savetxt", "load", "loadtxt", "fromfile",
    "tofile", "genfromtxt", "memmap", "open_memmap", "DataSource",
    # dynamic evaluation
    "eval", "query", "system", "popen",
    # side effects / escapes
    "show", "ctypeslib", "f2py", "distutils", "testing", "lib", "io", "os", "sys", "builtins",
    "subprocess", "importlib", "set_eng_float_format",
})
DUNDER_STRING = re.compile(r"__\w+__")


class _PolicyVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.violations: list[str] = []

    def _flag(self, node: ast.AST, message: str) -> None:
        line = getattr(node, "lineno", "?")
        entry = f"line {line}: {message}"
        if entry not in self.violations:
            self.violations.append(entry)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if not is_module_allowed(alias.name):
                self._flag(node, f"import of '{alias.name}' is not allowed")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            self._flag(node, "relative imports are not allowed")
        module = node.module or ""
        if not is_module_allowed(module):
            self._flag(node, f"import from '{module}' is not allowed")
        for alias in node.names:
            if alias.name == "*":
                self._flag(node, "wildcard imports are not allowed")
            elif alias.name.startswith("_") or alias.name in BLOCKED_ATTRIBUTES:
                self._flag(node, f"importing '{alias.name}' is not allowed")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in BLOCKED_BUILTINS:
            self._flag(node, f"use of '{node.id}' is not allowed")
        elif node.id.startswith("__"):
            self._flag(node, f"dunder name '{node.id}' is not allowed")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        attr = node.attr
        if attr.startswith("_"):
            self._flag(node, f"private/dunder attribute '.{attr}' is not allowed")
        elif attr in BLOCKED_ATTRIBUTES or attr.startswith("read_"):
            hint = " (use boolean masks instead)" if attr in ("query", "eval") else ""
            hint = " (register figures with chart(fig) instead)" if attr == "show" else hint
            self._flag(node, f"'.{attr}' is not allowed in the sandbox{hint}")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and DUNDER_STRING.search(node.value):
            self._flag(node, "string literals containing dunder names are not allowed")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._flag(node, "async code is not allowed")

    def visit_Global(self, node: ast.Global) -> None:
        self._flag(node, "'global' statements are not allowed")


def is_module_allowed(name: str) -> bool:
    if name in ALLOWED_MODULES:
        return True
    parts = name.split(".")
    if parts[0] in ALLOWED_SUBMODULE_ROOTS:
        return not any(p in BLOCKED_SEGMENTS or p.startswith("_") for p in parts[1:])
    return False


def validate_code(code: str) -> list[str]:
    """Return a list of human-readable violations; empty means the code passed."""
    if not code or not code.strip():
        return ["code is empty"]
    if len(code) > MAX_CODE_CHARS:
        return [f"code is too long ({len(code):,} chars, max {MAX_CODE_CHARS:,})"]
    try:
        tree = ast.parse(code, filename="<analysis>")
    except SyntaxError as exc:
        return [f"line {exc.lineno}: syntax error — {exc.msg}"]
    visitor = _PolicyVisitor()
    visitor.visit(tree)
    return visitor.violations
