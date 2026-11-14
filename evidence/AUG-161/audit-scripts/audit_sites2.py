#!/usr/bin/env python3
"""AUG-161 audit reader v2: classify every sys.executable occurrence in tests/.

For each occurrence (grep-equivalent, via AST Name/Attribute nodes resolving to sys.executable):
  class = direct-spawn   (an argument of a subprocess.run/Popen/check_output/check_call/call node)
        | helper-mediated (passed into a local function that itself spawns a subprocess)
        | non-spawn      (display/provenance/set-membership use)
Reports json rows: file, line, class, spawncall, cwd, abs_resolved.
"""
import ast, json, sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent.parent / "tests"
SPAWN_FUNCS = {"run", "Popen", "check_output", "check_call", "call"}

def is_subprocess_call(node):
    f = node.func
    return (isinstance(f, ast.Attribute) and f.attr in SPAWN_FUNCS
            and (isinstance(f.value, ast.Name) and f.value.id in ("subprocess", "sp", "proc")
                 or isinstance(f.value, ast.Attribute)))

def mentions_sys_exec(node):
    return any(isinstance(n, (ast.Attribute, ast.Name))
               and (isinstance(n, ast.Attribute) and n.attr == "executable"
                    and isinstance(n.value, ast.Name) and n.value.id == "sys"
                    or isinstance(n, ast.Name) and n.id == "executable")
               for n in ast.walk(node))

def call_name(call):
    f = call.func
    if isinstance(f, ast.Attribute):
        return f"{ast.unparse(f.value)}.{f.attr}"
    if isinstance(f, ast.Name):
        return f.id
    return type(f).__name__

rows = []
for path in sorted(TESTS.glob("*.py")):
    src = path.read_text()
    tree = ast.parse(src)
    # collect spawn call nodes and helper-call nodes across the module
    spawns = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and is_subprocess_call(n)]
    helpers = {}  # func name -> does it spawn?
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef):
            helpers[n.name] = any(
                isinstance(c, ast.Call) and is_subprocess_call(c) for c in ast.walk(n))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and node.attr == "executable"
                and isinstance(node.value, ast.Name) and node.value.id == "sys"):
            continue
        # climb to the enclosing Call (if any)
        enc = None
        for c in spawns:
            if node in set(ast.walk(c)):
                enc = c
                break
        klass, sp, cwd = "non-spawn", "", None
        if enc is not None:
            klass, sp = "direct-spawn", call_name(enc)
            for kw in enc.keywords:
                if kw.arg == "cwd":
                    cwd = ast.unparse(kw.value)
        else:
            # enclosing any-call?
            best = None
            for c in ast.walk(tree):
                if isinstance(c, ast.Call) and node in set(ast.walk(c)):
                    best = c
                    break
            if best is not None:
                name = call_name(best)
                base = name.split(".")[-1]
                if base in helpers and helpers[base]:
                    klass, sp = "helper-mediated", name
                    for kw in best.keywords:
                        if kw.arg == "cwd":
                            cwd = ast.unparse(kw.value)
                else:
                    klass, sp = "non-spawn", name
            elif best is None:
                # module-level default arg or similar
                klass, sp = "non-spawn", "<module-scope>"
        argv0 = ""
        if enc is not None and enc.args:
            argv0 = ast.unparse(enc.args[0])[:100]
        abs_res = "abspath" in src[node.lineno - 5:node.lineno + 3] or "resolve()" in src[max(0, node.lineno - 6):node.lineno + 2]
        rows.append({
            "file": path.name, "line": node.lineno, "class": klass, "spawncall": sp,
            "cwd": cwd, "abs_resolved_hint": abs_res, "argv0": argv0,
        })

print(json.dumps(rows, indent=1))
print(f"TOTAL_OCCURRENCES={len(rows)}", file=sys.stderr)
