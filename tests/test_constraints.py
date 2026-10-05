"""
Automated checks for the framework's structural rules, so a violation
fails the tests (and the Docker build and CI) before an assessor finds it:

  C1  100% procedural -- no class definitions anywhere (instant fail)
  2.1 print() and input() only in io_manager.py
  2.2 every record passes through ai_manager -- main.py's pipeline calls it
  4   the four manager files exist, and managers don't import each other

Uses Python's ast module, which is more precise than grep (it ignores the
words 'class' or 'print(' inside comments and strings).
"""
import ast
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANAGERS = ("io_manager", "ai_manager", "logic_manager", "data_manager")


def python_files() -> list[str]:
    found = []
    for folder, subfolders, files in os.walk(PROJECT_ROOT):
        subfolders[:] = [d for d in subfolders if not d.startswith(".") and d not in ("__pycache__", "venv", ".venv")]
        found += [os.path.join(folder, f) for f in files if f.endswith(".py")]
    return found


def parse(path: str) -> ast.AST:
    with open(path, encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def relative(path: str) -> str:
    return os.path.relpath(path, PROJECT_ROOT)


def test_all_four_manager_files_exist():
    for name in MANAGERS + ("main",):
        assert os.path.isfile(os.path.join(PROJECT_ROOT, f"{name}.py")), f"{name}.py is missing"


def test_no_class_definitions_anywhere():
    offenders = [
        f"{relative(path)}:{node.lineno}"
        for path in python_files()
        for node in ast.walk(parse(path))
        if isinstance(node, ast.ClassDef)
    ]
    assert offenders == [], f"Class definitions found (C1 instant fail): {offenders}"


def test_print_and_input_only_in_io_manager():
    offenders = [
        f"{relative(path)}:{node.lineno} {node.func.id}()"
        for path in python_files()
        if os.path.basename(path) != "io_manager.py"
        for node in ast.walk(parse(path))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("print", "input")
    ]
    assert offenders == [], f"print()/input() outside io_manager.py: {offenders}"


def test_managers_do_not_import_each_other():
    for name in MANAGERS:
        tree = parse(os.path.join(PROJECT_ROOT, f"{name}.py"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        others = imported & (set(MANAGERS) - {name})
        assert not others, f"{name}.py imports {others}; only main.py should connect managers"


def test_pipeline_sends_every_record_through_ai_manager():
    tree = parse(os.path.join(PROJECT_ROOT, "main.py"))
    build = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "build_record")
    calls = [ast.unparse(n.func) for n in ast.walk(build) if isinstance(n, ast.Call)]
    assert "ai_manager.process" in calls
    assert {"logic_manager.evaluate", "logic_manager.score", "logic_manager.route"} <= set(calls)
