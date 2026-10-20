"""Fixture repository and diff helpers shared by the test suite.

The fixture mirrors the shape of a small real project: a package with two
functions, a mirrored test module, and a change that adds one debug statement, one
bare ``except``, one new untested function and one new test.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping
from pathlib import Path

OLD_FILES: Mapping[str, str] = {
    "demoapp/__init__.py": '"""Demo application used by the PatchLens test suite."""\n',
    "demoapp/calculator.py": (
        '"""Small arithmetic helpers."""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "import os\n"
        "\n"
        "\n"
        "def add(a: int, b: int) -> int:\n"
        '    """Add two numbers."""\n'
        "    return a + b\n"
        "\n"
        "\n"
        "def divide(a: float, b: float) -> float:\n"
        '    """Divide a by b."""\n'
        "    return a / b\n"
    ),
    "demoapp/legacy.py": (
        '"""Legacy helpers kept for backwards compatibility."""\n'
        "\n"
        "\n"
        "def read_config(path: str) -> dict[str, str]:\n"
        '    """Read a simple key=value configuration file."""\n'
        "    values: dict[str, str] = {}\n"
        "    with open(path) as handle:\n"
        "        for line in handle:\n"
        "            if not line.strip() or line.startswith('#'):\n"
        "                continue\n"
        "            key, _, value = line.partition('=')\n"
        "            values[key.strip()] = value.strip()\n"
        "    return values\n"
    ),
    "tests/__init__.py": "",
    "tests/test_calculator.py": (
        "from demoapp.calculator import add, divide\n"
        "\n"
        "\n"
        "def test_add() -> None:\n"
        "    assert add(2, 3) == 5\n"
        "\n"
        "\n"
        "def test_divide() -> None:\n"
        "    assert divide(6, 3) == 2\n"
    ),
    "README.md": "# Demo\n\nA fixture repository for PatchLens tests.\n",
    ".env": "SECRET_TOKEN=super-secret-value\n",
    ".venv/lib/python3.11/site-packages/vendored/ignored.py": "raise RuntimeError('never index me')\n",
    "demoapp/__pycache__/calculator.cpython-311.pyc": "not really a pyc",
    "demoapp/broken.py": "def broken(:\n    pass\n",
}

NEW_FILES: Mapping[str, str] = {
    **OLD_FILES,
    "demoapp/calculator.py": (
        '"""Small arithmetic helpers."""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "\n"
        "def add(a: int, b: int) -> int:\n"
        '    """Add two numbers."""\n'
        "    print(a + b)  # TODO: remove debug output\n"
        "    return a + b\n"
        "\n"
        "\n"
        "def divide(a: float, b: float) -> float:\n"
        '    """Divide a by b."""\n'
        "    try:\n"
        "        return a / b\n"
        "    except:\n"
        "        pass\n"
        "\n"
        "\n"
        "def mean(values: list[float]) -> float:\n"
        '    """Arithmetic mean."""\n'
        "    return sum(values) / len(values)\n"
    ),
    "tests/test_calculator.py": (
        "import pytest\n"
        "\n"
        "from demoapp.calculator import add, divide\n"
        "\n"
        "\n"
        "def test_add() -> None:\n"
        "    assert add(2, 3) == 5\n"
        "\n"
        "\n"
        "def test_divide() -> None:\n"
        "    assert divide(6, 3) == 2\n"
        "\n"
        "\n"
        "def test_divide_by_zero_raises() -> None:\n"
        "    with pytest.raises(ZeroDivisionError):\n"
        "        divide(1, 0)\n"
    ),
}


def write_repo(root: Path, files: Mapping[str, str]) -> None:
    """Materialise ``files`` under ``root``."""
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def apply_new_state(root: Path) -> None:
    """Write the post-change content of the changed files."""
    for relative in ("demoapp/calculator.py", "tests/test_calculator.py"):
        (root / relative).write_text(NEW_FILES[relative], encoding="utf-8")


def make_diff(relative: str, old: str, new: str, *, context: int = 3) -> str:
    """A ``git``-style unified diff for one file."""
    body = "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
            n=context,
        )
    )
    return f"diff --git a/{relative} b/{relative}\n{body}"


def demo_diff() -> str:
    """The diff used across integration tests (calculator + its test module)."""
    return make_diff(
        "demoapp/calculator.py", OLD_FILES["demoapp/calculator.py"], NEW_FILES["demoapp/calculator.py"]
    ) + make_diff(
        "tests/test_calculator.py",
        OLD_FILES["tests/test_calculator.py"],
        NEW_FILES["tests/test_calculator.py"],
    )


__all__ = [
    "NEW_FILES",
    "OLD_FILES",
    "apply_new_state",
    "demo_diff",
    "make_diff",
    "write_repo",
]
