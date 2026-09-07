"""Guard against the dependency drifting from requirements.txt.

pyyaml was added to the local virtualenv when the YAML knowledge base landed but
never written into requirements.txt, so CI went red on two commits while local
tests stayed green. This catches that class of mistake at test time instead.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: import name -> distribution name on PyPI, where they differ.
_IMPORT_TO_PACKAGE = {
    "yaml": "pyyaml",
    "PIL": "pillow",
    "google": "google-genai",
}

#: Modules that are part of this project, not dependencies. Derived from the
#: files on disk so a new module cannot silently look like an undeclared
#: third-party package.
_LOCAL_MODULES = {p.stem for p in ROOT.glob("*.py")} | {"tools", "tests"}


def source_files() -> list[Path]:
    return sorted(
        [p for p in ROOT.glob("*.py")]
        + [p for p in (ROOT / "tools").glob("*.py")]
        + [p for p in (ROOT / "tests").glob("*.py")]
    )


def top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # Skip relative imports; they are local by definition.
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


def declared_packages() -> set[str]:
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    packages = set()
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if line:
            packages.add(re.split(r"[<>=!~\[]", line)[0].strip().lower())
    return packages


def third_party_imports() -> set[str]:
    found: set[str] = set()
    for path in source_files():
        for name in top_level_imports(path):
            if name in sys.stdlib_module_names or name in _LOCAL_MODULES:
                continue
            found.add(_IMPORT_TO_PACKAGE.get(name, name).lower())
    return found


class TestRequirementsCoverImports:
    def test_every_imported_package_is_declared(self):
        missing = third_party_imports() - declared_packages()
        assert not missing, (
            f"imported but absent from requirements.txt: {sorted(missing)}. "
            "CI installs only from that file, so this would pass locally and fail there."
        )

    @pytest.mark.parametrize("package", ["pydantic", "pyyaml", "pytest"])
    def test_hard_dependencies_are_declared(self, package):
        # These are imported unconditionally - the package will not start without them.
        assert package in declared_packages()

    def test_requirements_file_parses(self):
        assert declared_packages(), "requirements.txt yielded no packages"
