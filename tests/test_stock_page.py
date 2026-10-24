from pathlib import Path

import streamlit as st
from streamlit.testing.v1 import AppTest


def test_stock_page_opens_and_reaches_options_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("GOLD_AI_STATE_DB_PATH", str(tmp_path / "stocks.sqlite3"))
    monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
    st.cache_data.clear()
    st.cache_resource.clear()
    page = Path(__file__).resolve().parents[1] / "pages" / "1_الأسهم.py"
    app = AppTest.from_file(str(page), default_timeout=60).run()
    assert not app.exception
    assert app.radio(key="market_section").value == "الأسهم"
    app.radio(key="market_section").set_value("خيارات سهم").run()
    assert not app.exception
    assert app.text_input(key="opt_symbol") is not None
