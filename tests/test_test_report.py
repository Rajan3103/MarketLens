"""Tests for scripts/test_report.py: the Markdown summary for the project report (no pytest run inside)."""
from scripts.test_report import markdown_summary, summarise

ROWS = [
    {"module": "test_costs", "test": "test_options_sell", "what_it_checks": "", "status": "passed", "seconds": 0.2,
     "message": ""},
    {"module": "test_costs", "test": "test_round_trip", "what_it_checks": "", "status": "failed", "seconds": 0.1,
     "message": "assert 41.2 == 41.3"},
    {"module": "test_pricing", "test": "test_iv[1]", "what_it_checks": "", "status": "skipped", "seconds": 0.0,
     "message": "price too close to its limit"},
    {"module": "test_pricing", "test": "test_iv[2]", "what_it_checks": "", "status": "skipped", "seconds": 0.0,
     "message": "price too close to its limit"},
]
RUN = {"date": "2026-11-19 10:00", "python": "3.13.7", "os": "Windows 11", "git_commit": "abc1234"}


# Totals, one line per file, skip reasons grouped, and every failure with its message.
def test_markdown_summary():
    text = markdown_summary(RUN, summarise(ROWS), ROWS)
    assert text.startswith("# MarketLens: test summary")
    assert "**1 passed, 1 failed, 0 errors, 2 skipped**" in text and "(4 tests in 2 files)" in text
    assert "| test_costs | 2 | 1 | 1 | 0 | 0 | 0.3 |" in text
    assert "- 2 x price too close to its limit" in text
    assert "- `test_costs::test_round_trip`: assert 41.2 == 41.3" in text
    assert "commit `abc1234`" in text


# No failures: the section says so.
def test_markdown_summary_all_green():
    rows = [r for r in ROWS if r["status"] != "failed"]
    text = markdown_summary(RUN, summarise(rows), rows)
    assert "## Failures\n\nNone." in text
