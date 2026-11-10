"""The dashboard password (app.py), the help note on every module page, units on every input, and Home."""
import ast
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from marketlens import config
from marketlens.ui import password_ok

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "app.py")
MODULE_PAGES = ["market_overview", "option_chain", "options_downloader", "options_backtester", "option_simulator",
                "forward_test", "signals_builder", "momentum_engine", "etf_momentum", "analytics_library",
                "live_trading", "telegram_panel"]
NUMBER_WIDGETS = ("number_input", "slider", "select_slider", "time_input")
UNIT_WORDS = ("(", "%", "Rs", "IST", "days")


# ---------------------------------------------------------------- password

# The comparison is exact (and constant-time inside hmac.compare_digest).
def test_password_ok():
    assert password_ok("s3cret pass", "s3cret pass")
    assert not password_ok("s3cret", "s3cret pass") and not password_ok("", "x") and not password_ok(None, "x")
    assert password_ok("पासवर्ड", "पासवर्ड")                     # any language works


# With DASHBOARD_PASSWORD set: only the password box until it is right; then the app and a Logout button.
def test_login_and_logout(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "open sesame")
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert at.title[0].value == "🔒 MarketLens" and len(at.title) == 1        # nothing else is shown
    at.text_input(key="login_password").input("wrong")
    at.button(key="FormSubmitter:login-Log in").click().run()
    assert at.error[0].value == "Wrong password." and "logged_in" not in at.session_state
    at.text_input(key="login_password").input("open sesame")
    at.button(key="FormSubmitter:login-Log in").click().run()
    assert not at.exception and at.session_state["logged_in"] is True
    assert any(t.value == "📈 MarketLens" for t in at.title)                  # the Home page
    at.sidebar.button(key="logout").click().run()
    assert "logged_in" not in at.session_state and at.title[0].value == "🔒 MarketLens"


# Five wrong passwords in a row: a 30-second pause.
def test_too_many_tries(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "open sesame")
    at = AppTest.from_file(APP, default_timeout=60).run()
    for _ in range(5):
        at.text_input(key="login_password").input("nope")
        at.button(key="FormSubmitter:login-Log in").click().run()
    at.run()
    assert "Too many wrong passwords" in at.error[0].value


# Without a password the app opens at once, and Settings warns about it.
def test_no_password(monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "")
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception and at.title[0].value == "📈 MarketLens"
    settings = AppTest.from_file(str(ROOT / "dashboards" / "settings.py"), default_timeout=60)
    monkeypatch.setattr("marketlens.db.connection_status", lambda url=None: (False, "not running"))
    settings.run()
    assert any("DASHBOARD_PASSWORD is empty" in w.value for w in settings.warning)
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "x")
    settings.run()
    assert not any("DASHBOARD_PASSWORD is empty" in w.value for w in settings.warning)


# ---------------------------------------------------------------- help notes and units

# The page's code as a syntax tree.
def tree(page: str) -> ast.Module:
    return ast.parse((ROOT / "dashboards" / f"{page}.py").read_text(encoding="utf-8"))


# Every module page calls help_note right under its title, with both lines.
@pytest.mark.parametrize("page", MODULE_PAGES)
def test_help_note_under_the_title(page):
    body = tree(page).body
    title = next(i for i, node in enumerate(body) if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                 and getattr(node.value.func, "attr", "") == "title")
    call = body[title + 1].value
    assert getattr(call.func, "id", "") == "help_note", f"{page}: help_note must follow st.title"
    text = ast.literal_eval(call.args[0])
    what, how = text.split("\n")
    assert what.startswith("What this page does:") and how.startswith("How to read it:")


# The label of a widget call, as text ("{...}" for the f-string parts), or None when it is not literal.
def label_of(call: ast.Call) -> str | None:
    return text_of(call.args[0]) if call.args else None


# A label expression as text: a string, an f-string, or "a if ... else b".
def text_of(arg: ast.expr) -> str | None:
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{...}" for v in arg.values)
    if isinstance(arg, ast.IfExp):                       # "Slippage (% of price)" if ... else "Slippage (Rs per unit)"
        both = (text_of(arg.body), text_of(arg.orelse))
        return " / ".join(both) if all(both) else None
    return None


# Every number box, slider and time box on every page says its unit (Rs, %, days, minutes, IST, strikes ...).
def test_every_number_input_has_a_unit():
    missing = []
    for path in [*(ROOT / "dashboards").glob("*.py"), ROOT / "marketlens" / "ui.py"]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in NUMBER_WIDGETS:
                label = label_of(node)
                if label is not None and not any(word in label for word in UNIT_WORDS):
                    missing.append(f"{path.name}:{node.lineno} {label!r}")
    assert missing == [], "inputs without a unit: " + ", ".join(missing)


# ---------------------------------------------------------------- Home

# Every one of the 12 modules shows Ready.
def test_home_all_ready():
    at = AppTest.from_file(str(ROOT / "dashboards" / "home.py"), default_timeout=60).run()
    table = next(m.value for m in at.markdown if "| Group | # | Module |" in m.value)
    rows = [line for line in table.splitlines() if line.startswith("| ") and "✅ Ready" in line]
    assert len(rows) == 12 and "🛠️" not in table and "📋" not in table
    assert at.info[0].value.startswith("What this app does:") and at.info[0].icon == "ℹ️"
