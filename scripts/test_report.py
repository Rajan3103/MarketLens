"""Run every test and save the results as CSV files (for reports and the research paper).

Run it from the MarketLens folder:
    python scripts/test_report.py
    python scripts/test_report.py --out reports/tests      # choose the folder

It writes three files into reports/tests/ (the date and time are in each name, so old runs are kept):
    test_results_<when>.csv   one row per test: module, test, what it checks, status, seconds, message
    test_summary_<when>.csv   one row per test file: total, passed, failed, errors, skipped, seconds
    test_run_<when>.csv       the run itself: date, Python, operating system, git commit, totals
It also keeps pytest's own XML (test_results_<when>.xml).

Database tests use TEST_DATABASE_URL only (never your real database), as always.
"""
import argparse
import ast
import csv
import datetime as dt
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULT_COLUMNS = ["module", "test", "what_it_checks", "status", "seconds", "message"]
SUMMARY_COLUMNS = ["module", "total", "passed", "failed", "errors", "skipped", "seconds"]


# Read the --out option.
def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run all tests and save the results as CSV.")
    parser.add_argument("--out", default="reports/tests", help="folder for the CSV files (default: reports/tests)")
    return parser.parse_args(argv)


# Run pytest on the whole tests/ folder, saving its XML report; returns pytest's exit code.
def run_pytest(xml_path: Path) -> int:
    command = [sys.executable, "-m", "pytest", "tests", "-rs", "-q", f"--junitxml={xml_path}",
               "-o", "junit_family=xunit1"]
    print("Running:", " ".join(command))
    return subprocess.call(command, cwd=ROOT)


# What each test checks: the comment above it, else its docstring, else its name in words.
def test_descriptions(tests_dir: Path) -> dict:
    found = {}
    for path in tests_dir.glob("test_*.py"):
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
                continue
            first = min([node.lineno] + [d.lineno for d in node.decorator_list]) - 1   # 0-based line of def/@
            comments, j = [], first - 1
            while j >= 0 and lines[j].startswith("#"):
                comments.insert(0, lines[j].lstrip("# ").strip())
                j -= 1
            doc = (ast.get_docstring(node) or "").strip().splitlines()
            words = node.name[len("test_"):].replace("_", " ")
            found[(path.stem, node.name)] = " ".join(comments) or (doc[0] if doc else words.capitalize())
    return found


# One row per test from pytest's XML report.
def read_results(xml_path: Path, descriptions: dict) -> list[dict]:
    rows = []
    for case in ET.parse(xml_path).getroot().iter("testcase"):
        module = case.get("classname", "").split(".")[-1] or case.get("file", "")
        name = case.get("name", "")
        status, message = "passed", ""
        for kind in ("failure", "error", "skipped"):
            child = case.find(kind)
            if child is not None:
                status = {"failure": "failed", "error": "error", "skipped": "skipped"}[kind]
                message = (child.get("message") or child.text or "").strip().replace("\n", " ")[:500]
                break
        rows.append({"module": module, "test": name,
                     "what_it_checks": descriptions.get((module, name.split("[")[0]), ""),
                     "status": status, "seconds": round(float(case.get("time") or 0), 3), "message": message})
    return rows


# Totals per test file, plus an ALL row at the end.
def summarise(rows: list[dict]) -> list[dict]:
    per = defaultdict(lambda: {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "seconds": 0.0})
    for r in rows:
        for key in (r["module"], "ALL"):
            s = per[key]
            s["total"] += 1
            s[{"passed": "passed", "failed": "failed", "error": "errors", "skipped": "skipped"}[r["status"]]] += 1
            s["seconds"] = round(s["seconds"] + r["seconds"], 3)
    names = sorted(k for k in per if k != "ALL") + ["ALL"]
    return [{"module": k, **per[k]} for k in names if k in per]


# The current git commit (short), or "" when git is not available.
def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


# Write a list of dicts as a CSV file (utf-8 with BOM, so Excel shows it correctly).
def write_csv(path: Path, rows: list[dict], columns: list[str]):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


# Run the tests and write the CSV files; returns pytest's exit code (0 = all passed).
def main(argv=None) -> int:
    args = parse_args(argv)
    out = (ROOT / args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    when = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    xml_path = out / f"test_results_{when}.xml"
    started = dt.datetime.now()
    code = run_pytest(xml_path)
    if not xml_path.exists():
        print("pytest did not write a report; see the messages above.")
        return code or 1
    rows = read_results(xml_path, test_descriptions(ROOT / "tests"))
    summary = summarise(rows)
    total = summary[-1]
    run = [{"date": started.strftime("%Y-%m-%d %H:%M"), "python": platform.python_version(),
            "os": f"{platform.system()} {platform.release()}", "git_commit": git_commit(),
            "total": total["total"], "passed": total["passed"], "failed": total["failed"],
            "errors": total["errors"], "skipped": total["skipped"], "seconds": total["seconds"],
            "pytest_exit_code": code}]
    files = {f"test_results_{when}.csv": (rows, RESULT_COLUMNS),
             f"test_summary_{when}.csv": (summary, SUMMARY_COLUMNS),
             f"test_run_{when}.csv": (run, list(run[0]))}
    for name, (data, columns) in files.items():
        write_csv(out / name, data, columns)
        print("Saved", out / name)
    print(f"{total['passed']} passed, {total['failed']} failed, {total['errors']} errors, "
          f"{total['skipped']} skipped in {total['seconds']:.0f} s")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
