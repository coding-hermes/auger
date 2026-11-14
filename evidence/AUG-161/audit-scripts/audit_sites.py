#!/usr/bin/env python3
"""AUG-161 mechanical audit: enumerate every sys.executable subprocess spawn in tests/.

Walks each tests/*.py with ast, finds Call nodes whose argument list mentions
sys.executable, and reports: file:line, enclosing function, callable, cwd= kwarg
(presence + static value), whether the executable expression is absolute-resolved
(wrapped in Path(...).resolve()/os.path.abspath/str(Path...)) and the disposition key.
"""
import ast, json, sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent.parent / "tests"

def enc_func(tree, node):
    for parent in ast.walk(tree):
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(c is node for c in ast.walk(parent)):
                return parent.name
    return "<module>"

def call_name(call):
    f = call.func
    if isinstance(f, ast.Attribute):
        base = ast.unparse(f.value) if hasattr(ast, "unparse") else "?"
        return f"{base}.{f.attr}"
    if isinstance(f, ast.Name):
        return f.id
    return type(f).__name__

rows = []
for path in sorted(TESTS.glob("*.py")):
    src = path.read_text()
    tree = ast.parse(src)
    lines = src.splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        args_src = [ast.unparse(a) for a in node.args] + [ast.unparse(k.value) for k in node.keywords]
        if not any("sys.executable" in a for a in args_src):
            continue
        # find the outermost call if this one is nested inside another call that also
        # mentions sys.executable (e.g. helper(  subprocess.run([...])  )) -- keep all,
        # the reader dedupes by file:line of the sys.executable occurrence.
        cwd = None
        for kw in node.keywords:
            if kw.arg == "cwd":
                cwd = ast.unparse(kw.value)
        exec_src = ast.unparse(node.args[0]) if node.args else "?"
        abs_resolved = bool(
            {"Path(sys.executable).resolve()", "os.path.abspath(sys.executable)",
             "Path(sys.executable).resolve().as_posix()"} & set(args_src)
        ) or "resolve()" in exec_src or "abspath" in exec_src
        for i, a in enumerate(node.args + [k.value for k in node.keywords]):
            if "sys.executable" in ast.unparse(a):
                ln = a.lineno
                break
        rows.append({
            "file": path.name, "line": ln, "func": enc_func(tree, node),
            "call": call_name(node), "cwd": cwd,
            "exec_expr": exec_src[:80], "abs_resolved": abs_resolved,
            "argv0": ast.unparse(node.args[0])[:60] if node.args else "",
        })

print(json.dumps(rows, indent=1))
print(f"TOTAL_SITES={len(rows)}", file=sys.stderr)
