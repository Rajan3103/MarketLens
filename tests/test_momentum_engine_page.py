"""Streamlit page 8 (dashboards/momentum_engine.py): it links to the web page and opens a library strategy there."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

from marketlens import config

PAGE = str(Path(__file__).resolve().parent.parent / "dashboards" / "momentum_engine.py")


# Without a hand-off: a link to the web page and the historical note.
def test_page_links_to_the_web_app():
    at = AppTest.from_file(PAGE).run()
    assert not at.exception
    assert any("not a live trading recommendation" in c.value for c in [*at.caption, *at.markdown, *at.info])
    assert "/momentum-engine" in str(at.get("link_button")[0].proto.url)


# A strategy sent from the Strategy Library opens on the web page with ?strategy=<id>.
def test_library_handoff_opens_the_strategy():
    at = AppTest.from_file(PAGE)
    at.session_state["handoff"] = {"to": "momentum", "strategy_id": 7, "name": "Top 20", "params": {}}
    at.run()
    assert not at.exception and "Top 20" in at.success[0].value
    assert str(at.get("link_button")[0].proto.url) == f"{config.FRONTEND_URL}/momentum-engine?strategy=7"
    assert "handoff" not in at.session_state
