"""Capture baseline metrics for modularized backend services.

This script avoids external coverage dependencies by using stdlib `trace`.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from trace import Trace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
REPORT_PATH = REPO_ROOT / "local-docs" / "MODULAR_SERVICES_BASELINE.json"

TARGET_MODULES = {
    "inference.py": REPO_ROOT / "api" / "services" / "inference.py",
    "messages.py": REPO_ROOT / "api" / "routers" / "messages.py",
    "llm_client.py": REPO_ROOT / "api" / "services" / "llm_client.py",
}

TARGET_TESTS = [
    str(REPO_ROOT / "api" / "tests" / "test_inference.py"),
    str(REPO_ROOT / "api" / "tests" / "test_messages.py"),
    str(REPO_ROOT / "api" / "tests" / "test_llm_client_fallback.py"),
]


def _executable_lines(path: Path) -> set[int]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()
    executable: set[int] = set()

    for node in ast.walk(tree):
        lineno = getattr(node, "lineno", None)
        if isinstance(lineno, int) and 1 <= lineno <= len(lines):
            raw = lines[lineno - 1].strip()
            if raw and not raw.startswith("#"):
                executable.add(lineno)
    return executable


def main() -> int:
    tracer = Trace(count=True, trace=False)

    def _run_pytest() -> int:
        return pytest.main([*TARGET_TESTS, "-q"])

    exit_code = tracer.runfunc(_run_pytest)
    results = tracer.results()

    report: dict[str, object] = {
        "phase": 0,
        "date": "2026-04-17",
        "test_exit_code": int(exit_code),
        "modules": {},
    }

    counts = results.counts
    for name, module_path in TARGET_MODULES.items():
        executable = _executable_lines(module_path)
        covered = {
            line
            for (filename, line), hit_count in counts.items()
            if Path(filename).resolve() == module_path.resolve() and hit_count > 0
        }
        total = len(executable)
        covered_count = len(executable & covered)
        coverage_pct = round((covered_count / total * 100.0), 2) if total else 0.0

        report["modules"][name] = {
            "path": str(module_path.relative_to(REPO_ROOT)),
            "executable_lines": total,
            "covered_lines": covered_count,
            "coverage_pct": coverage_pct,
        }

    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[baseline] report written: {REPORT_PATH}")
    print(json.dumps(report, indent=2))
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
