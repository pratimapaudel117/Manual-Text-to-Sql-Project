import json
import math
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import sqlglot

from generate_sql import MODEL, classify, generate_sql, get_schema
from run_query import run_query
from test_cases import TEST_CASES
from validate_sql import validate_sql

REPORT_DIR = Path("reports")
BASELINE_FILE = Path("baseline.json")


# ---------------------------------------------------------------------------
# Step 5: compare returned results with expected outputs
# ---------------------------------------------------------------------------

def values_match(actual, expected, tolerance=0.01):
    """Numbers match within a tolerance; everything else must be equal."""
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return math.isclose(actual, expected, abs_tol=tolerance)
    return actual == expected


def rows_match(actual_row, expected_row):
    if len(actual_row) != len(expected_row):
        return False
    return all(values_match(a, e) for a, e in zip(actual_row, expected_row))


def sort_key(row):
    # Sort rows by their text form, so rows with mixed types can be sorted
    return tuple(str(value) for value in row)


def compare_results(actual, expected, ordered=True):
    """Compare result rows. If order doesn't matter, sort both sides first."""
    if len(actual) != len(expected):
        return False
    if not ordered:
        actual = sorted(actual, key=sort_key)
        expected = sorted(expected, key=sort_key)
    return all(rows_match(a, e) for a, e in zip(actual, expected))


def evaluate_query(sql, expected, ordered=True, check=None):
    """Run the SQL read-only and compare its rows with the expected rows."""
    try:
        actual = run_query(sql)  # read-only, allowed tables, timeout, row limit, logging
    except PermissionError as exc:
        return {"outcome": "security_rejection", "actual": None, "error": str(exc)}
    except sqlite3.OperationalError as exc:
        outcome = "timeout" if "interrupted" in str(exc) else "execution_error"
        return {"outcome": outcome, "actual": None, "error": str(exc)}
    except Exception as exc:
        return {"outcome": "execution_error", "actual": None, "error": str(exc)}

    passed = compare_results(actual, expected, ordered)

    # Business rule assertion, if the test case has one
    if passed and check is not None and not check(actual):
        passed = False

    return {
        "outcome": "passed" if passed else "incorrect_result",
        "actual": actual,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Full pipeline for one test case: generate -> validate -> execute -> compare
# ---------------------------------------------------------------------------

def run_case(case, schema):
    start = time.monotonic()
    result = {
        "id": case["id"],
        "category": case["category"],
        "difficulty": case.get("difficulty", "unknown"),
        "question": case["question"],
        "expected_behavior": case["expected_behavior"],
        "expected": case["expected_result"],
        "actual_behavior": None,
        "sql": None,
        "actual": None,
        "error": None,
    }

    # 1. Generate
    try:
        output = generate_sql(case["question"], schema)
    except Exception as exc:
        result.update(outcome="generation_error", error=str(exc))
        return finish(result, start)

    behavior = classify(output)
    result["actual_behavior"] = behavior
    result["sql"] = output

    # 2. Did the model do the right kind of thing?
    if behavior != case["expected_behavior"]:
        if case["expected_behavior"] == "refuse" and behavior == "answer":
            result["outcome"] = "unauthorized_attempt"
        else:
            result["outcome"] = "wrong_behavior"
        return finish(result, start)

    if behavior in ("clarify", "refuse"):
        result["outcome"] = "passed"
        return finish(result, start)

    # 3. Validate the SQL
    try:
        sqlglot.parse(output, read="sqlite")
    except sqlglot.errors.SqlglotError as exc:
        result.update(outcome="syntax_failure", error=str(exc))
        return finish(result, start)

    if not validate_sql(output):
        result.update(outcome="security_rejection", error="Not a single read-only SELECT")
        return finish(result, start)

    # 4. Execute and compare
    evaluation = evaluate_query(
        output,
        case["expected_result"],
        ordered=case.get("ordered", True),
        check=case.get("check"),
    )
    result.update(evaluation)
    return finish(result, start)


def finish(result, start):
    result["passed"] = result["outcome"] == "passed"
    result["seconds"] = round(time.monotonic() - start, 2)
    return result


# ---------------------------------------------------------------------------
# Step 6: measure execution accuracy and failure rates
# ---------------------------------------------------------------------------

def count_by(results, field):
    groups = {}
    for r in results:
        group = groups.setdefault(r[field], {"total": 0, "passed": 0})
        group["total"] += 1
        group["passed"] += r["passed"]
    return groups


def summarize_results(results):
    total = len(results)

    if total == 0:
        return {
            "total": 0,
            "execution_accuracy": 0.0,
            "failure_rate": 0.0,
        }

    passed = sum(r["passed"] for r in results)
    failed = total - passed

    outcomes = {}
    for r in results:
        outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "execution_accuracy": round(passed / total * 100, 2),
        "failure_rate": round(failed / total * 100, 2),
        "outcomes": outcomes,
        "unauthorized_attempts": outcomes.get("unauthorized_attempt", 0),
        "by_category": count_by(results, "category"),
        "by_difficulty": count_by(results, "difficulty"),
        "average_seconds": round(sum(r["seconds"] for r in results) / total, 2),
    }


# ---------------------------------------------------------------------------
# Step 7: regression check and release gate
# ---------------------------------------------------------------------------

def check_release_gate(summary, results):
    """Return a list of reasons the run fails. An empty list means it passes."""
    problems = []

    # Security gate: zero unauthorized-query attempts
    if summary["unauthorized_attempts"] > 0:
        problems.append(f"{summary['unauthorized_attempts']} unauthorized query attempt(s)")

    # Accuracy gate: must not drop below the saved baseline
    if BASELINE_FILE.exists():
        baseline = json.loads(BASELINE_FILE.read_text())
        old_accuracy = baseline["summary"]["execution_accuracy"]
        if summary["execution_accuracy"] < old_accuracy:
            problems.append(
                f"accuracy dropped from {old_accuracy}% to {summary['execution_accuracy']}%"
            )

        # Regressions: tests that passed before but fail now
        previously_passed = {r["id"] for r in baseline["results"] if r["passed"]}
        for r in results:
            if r["id"] in previously_passed and not r["passed"]:
                problems.append(f"regression: {r['id']} passed before, now {r['outcome']}")

    return problems


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    schema = get_schema()
    results = []

    print(f"Model: {MODEL}\n")
    for case in TEST_CASES:
        r = run_case(case, schema)
        results.append(r)
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"{mark}  {r['id']:<28} {r['outcome']:<20} {r['seconds']}s")
        if not r["passed"]:
            print(f"      output:   {r['sql']}")
            if r["actual"] is not None:
                print(f"      got:      {r['actual']}")
                print(f"      expected: {r['expected']}")
            if r["error"]:
                print(f"      error:    {r['error']}")

    summary = summarize_results(results)

    print("\n=== Summary ===")
    print(f"Execution accuracy: {summary['execution_accuracy']}%  "
          f"({summary['passed']}/{summary['total']})")
    print(f"Failure rate:       {summary['failure_rate']}%")
    print(f"Outcomes:           {summary['outcomes']}")
    print("By difficulty:")
    for name, g in summary["by_difficulty"].items():
        print(f"  {name:<10} {g['passed']}/{g['total']}")
    print("By category:")
    for name, g in summary["by_category"].items():
        print(f"  {name:<26} {g['passed']}/{g['total']}")

    # Save the full report
    report = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "model": MODEL,
        "summary": summary,
        "results": results,
    }
    REPORT_DIR.mkdir(exist_ok=True)
    report_file = REPORT_DIR / f"report_{datetime.now():%Y%m%d_%H%M%S}.json"
    report_file.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nReport saved to {report_file}")

    if "--save-baseline" in sys.argv:
        BASELINE_FILE.write_text(json.dumps(report, indent=2, default=str))
        print(f"Baseline saved to {BASELINE_FILE}")
        return 0

    problems = check_release_gate(summary, results)
    if problems:
        print("\nRELEASE GATE: FAIL")
        for p in problems:
            print(f"  - {p}")
        return 1

    print("\nRELEASE GATE: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())