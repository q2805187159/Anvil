from __future__ import annotations

import ast
from pathlib import Path
import unittest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
HARNESS_ROOT = BACKEND_ROOT / "packages" / "harness"
GATEWAY_ROOT = BACKEND_ROOT / "app" / "gateway"

FORBIDDEN_IMPORT_PREFIXES = (
    "app",
    "backend.app",
)


def iter_python_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*.py") if "__pycache__" not in path.parts]


def is_forbidden_import(module_name: str | None) -> bool:
    if not module_name:
        return False
    return any(
        module_name == prefix or module_name.startswith(prefix + ".")
        for prefix in FORBIDDEN_IMPORT_PREFIXES
    )


def extract_dynamic_import_target(node: ast.Call) -> str | None:
    if not node.args:
        return None
    first_arg = node.args[0]
    if not isinstance(first_arg, ast.Constant) or not isinstance(first_arg.value, str):
        return None

    if isinstance(node.func, ast.Name):
        if node.func.id == "__import__":
            if first_arg.value == "backend":
                for keyword in node.keywords:
                    if keyword.arg != "fromlist":
                        continue
                    if isinstance(keyword.value, (ast.List, ast.Tuple)):
                        for element in keyword.value.elts:
                            if isinstance(element, ast.Constant) and element.value == "app":
                                return "backend.app"
            return first_arg.value
        if node.func.id == "import_module":
            return first_arg.value

    if (
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "importlib"
        and node.func.attr == "import_module"
    ):
        return first_arg.value

    return None


def collect_import_violations(path: Path, tree: ast.AST) -> list[str]:
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if is_forbidden_import(alias.name):
                    violations.append(f"{path}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0:
                continue
            if is_forbidden_import(node.module):
                violations.append(f"{path}: from {node.module} import ...")
            elif node.module == "backend":
                for alias in node.names:
                    if alias.name == "app":
                        violations.append(f"{path}: from backend import app")
        elif isinstance(node, ast.Call):
            dynamic_target = extract_dynamic_import_target(node)
            if is_forbidden_import(dynamic_target):
                violations.append(f"{path}: dynamic import {dynamic_target}")

    return violations


class HarnessBoundaryTests(unittest.TestCase):
    def test_expected_phase_two_roots_exist(self) -> None:
        self.assertTrue(HARNESS_ROOT.exists(), "harness root must exist")
        self.assertTrue((BACKEND_ROOT / "app").exists(), "app root must exist")

    def test_gateway_skeleton_files_exist(self) -> None:
        expected_files = (
            GATEWAY_ROOT / "app.py",
            GATEWAY_ROOT / "deps.py",
            GATEWAY_ROOT / "services.py",
            GATEWAY_ROOT / "routers" / "__init__.py",
        )

        missing = [str(path) for path in expected_files if not path.exists()]
        self.assertEqual([], missing, "phase two gateway skeleton files must exist")

    def test_harness_python_files_do_not_import_app(self) -> None:
        violations: list[str] = []

        for path in iter_python_files(HARNESS_ROOT):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            violations.extend(collect_import_violations(path, tree))

        self.assertEqual(
            [],
            violations,
            "harness layer must not import app adapters",
        )

    def test_collect_import_violations_rejects_from_backend_import_app(self) -> None:
        tree = ast.parse("from backend import app")
        violations = collect_import_violations(Path("<memory>"), tree)
        self.assertIn("<memory>: from backend import app", violations)

    def test_collect_import_violations_rejects_dynamic_app_imports(self) -> None:
        cases = (
            '__import__("app")',
            '__import__("backend", fromlist=["app"])',
            'import importlib\nimportlib.import_module("backend.app.gateway")',
            'from importlib import import_module\nimport_module("app")',
        )

        for snippet in cases:
            with self.subTest(snippet=snippet):
                tree = ast.parse(snippet)
                violations = collect_import_violations(Path("<memory>"), tree)
                self.assertGreater(len(violations), 0, f"expected violation for: {snippet}")


if __name__ == "__main__":
    unittest.main()
