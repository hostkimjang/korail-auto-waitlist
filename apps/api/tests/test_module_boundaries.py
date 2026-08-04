from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"


@dataclass(frozen=True)
class BoundaryRule:
    name: str
    matches: Callable[[Path], bool]
    forbidden_import_roots: frozenset[str]


DOMAIN_FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "SRT",
        "celery",
        "fastapi",
        "httpx",
        "korail2",
        "pydantic",
        "pydoll",
        "playwright",
        "sqlalchemy",
    }
)


def _is_domain_module(relative_path: Path) -> bool:
    return relative_path.name == "domain.py"


def _is_application_module(relative_path: Path) -> bool:
    stem = relative_path.stem
    return (
        "application" in relative_path.parts[:-1]
        or stem == "application"
        or stem.startswith("application_")
        or stem.endswith("_application")
    )


BOUNDARY_RULES = (
    BoundaryRule(
        name="domain modules are framework and provider independent",
        matches=_is_domain_module,
        forbidden_import_roots=DOMAIN_FORBIDDEN_IMPORT_ROOTS,
    ),
    BoundaryRule(
        name="application modules are independent from FastAPI",
        matches=_is_application_module,
        forbidden_import_roots=frozenset({"fastapi"}),
    ),
)


def _import_roots(module_path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name.partition(".")[0]) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append((node.lineno, node.module.partition(".")[0]))
    return imports


def test_module_dependency_boundaries() -> None:
    violations: list[str] = []
    python_modules = sorted(SOURCE_ROOT.rglob("*.py"))

    for module_path in python_modules:
        relative_path = module_path.relative_to(SOURCE_ROOT)
        imports = _import_roots(module_path)
        for rule in BOUNDARY_RULES:
            if not rule.matches(relative_path):
                continue
            for line_number, import_root in imports:
                if import_root in rule.forbidden_import_roots:
                    violations.append(
                        f"{relative_path.as_posix()}:{line_number}: "
                        f"{import_root} violates '{rule.name}'"
                    )

    assert violations == [], "\n".join(violations)
