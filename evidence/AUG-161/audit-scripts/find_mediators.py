"""Find spawn mediators: variables built with sys.executable and later spawned (any cwd)."""
import ast
from pathlib import Path

TESTS = Path('/home/kara/worktrees/auger-AUG-161/tests')

for path in sorted(TESTS.glob('*.py')):
    src = path.read_text()
    tree = ast.parse(src)
    # map: variable name -> line of assignment containing sys.executable
    var_lines = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) for t in node.targets
        ):
            if any(
                isinstance(n, ast.Attribute)
                and n.attr == 'executable'
                and isinstance(n.value, ast.Name)
                and n.value.id == 'sys'
                for n in ast.walk(node)
            ):
                name = next(t.id for t in node.targets if isinstance(t, ast.Name))
                var_lines[name] = node.lineno
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {'run', 'Popen', 'check_output', 'check_call', 'call'}
        ):
            for a in node.args:
                if isinstance(a, ast.Name) and a.id in var_lines:
                    cwd = next(
                        (ast.unparse(kw.value) for kw in node.keywords if kw.arg == 'cwd'),
                        None,
                    )
                    print(f"{path.name}: var={a.id} (assigned line {var_lines[a.id]}) -> spawn line {node.lineno} cwd={cwd}")
