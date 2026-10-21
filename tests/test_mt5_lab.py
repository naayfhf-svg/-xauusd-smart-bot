import ast
import math
import time
from pathlib import Path
from typing import Any

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mt5_lab import close_virtual, open_virtual, prepare
from mt5_readonly import ReadError


def snapshot(clock=1000000):
    return dict(symbol="GOLD", quote=dict(bid=4000, ask=4000.5, midpoint=4000.25,
                                         updated_at=clock),
                bars=[dict(ts=clock-300)])


def analyzer(rows):
    return dict(signal="BUY", atr=2)


def planner(signal, quote, analysis):
    return dict(entry=quote["ask"], stop=3998, tp1=4004)


def test_plan_and_duplicate_entry():
    d = prepare(snapshot(), analyzer, planner, 1000000)
    state = {}
    assert open_virtual(state, d, 1000000)
    assert not open_virtual(state, d, 1000000)
    close_virtual(state, d["quote"], 1000000, manual=True)
    assert not open_virtual(state, d, 1000000)


def test_stale_cannot_open_or_close():
    d = prepare(snapshot(), analyzer, planner, 1000000)
    state = {}
    assert not open_virtual(state, d, 1000006)
    assert open_virtual(state, d, 1000000)
    assert close_virtual(state, d["quote"], 1000006, manual=True) is None
    assert state["position"]


def test_gap_stop_records_observed_bid():
    d = prepare(snapshot(), analyzer, planner, 1000000)
    state = {}
    open_virtual(state, d, 1000000)
    q = dict(d["quote"], bid=3995, ask=3995.5)
    result = close_virtual(state, q, 1000000)
    assert result["exit"] == 3995
    assert result["change_per_ounce"] == -5.5
    assert state["position"] is None


def test_sell_uses_ask_to_exit():
    state = dict(position=dict(side="SELL", entry=4000, stop=4002, target=3998))
    result = close_virtual(state, dict(bid=4002, ask=4002.5, updated_at=1000000), 1000000)
    assert result["exit"] == 4002.5
    assert result["change_per_ounce"] == -2.5


def test_wide_spread_blocks_plan():
    s = snapshot()
    s["quote"]["ask"] = 4010
    d = prepare(s, analyzer, planner, 1000000)
    assert d["plan"] is None
    assert not open_virtual({}, d, 1000000)


@pytest.mark.parametrize("bad_plan", [
    dict(entry=4000, stop=4001, tp1=4002),
    dict(entry=float("nan"), stop=3999, tp1=4002),
])
def test_invalid_plan(bad_plan):
    d = prepare(snapshot(), analyzer, lambda *args: bad_plan, 1000000)
    assert d["plan"] is None


@pytest.mark.parametrize("offset", [6, -1])
def test_stale_future_data(offset):
    with pytest.raises(ReadError):
        prepare(snapshot(), analyzer, planner, 1000000+offset)


def test_stale_bars():
    s = snapshot()
    s["bars"][0]["ts"] -= 900
    with pytest.raises(ReadError):
        prepare(s, analyzer, planner, 1000000)


def test_actual_app_analysis_gets_enough_history():
    # Exercise actual strategy functions without starting Streamlit or any network.
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text()
    names = {"finite", "ema", "rolling_rsi", "atr", "resample", "trend", "analyze", "trade_plan"}
    tree = ast.parse(source)
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    ns = {"Any": Any, "math": math}
    exec(compile(ast.Module(body=body, type_ignores=[]), "app.py", "exec"), ns)
    clock = math.floor(time.time()/3600)*3600
    rows = [dict(ts=clock-i*300, open=4000, high=4001, low=3999, close=4000)
            for i in range(1200, 0, -1)]
    s = snapshot(clock)
    s["bars"] = rows
    result = prepare(s, ns["analyze"], ns["trade_plan"], clock)
    assert result["signal"] == "WAIT"
    assert result["analysis"]["reason"] == "الشروط لم تكتمل"


@pytest.mark.skipif(sys.platform == "win32", reason="Non-Windows UI fallback")
def test_new_mode_does_not_fall_through_to_external_feeds():
    from unittest.mock import patch
    from streamlit.testing.v1 import AppTest
    app = Path(__file__).resolve().parents[1] / "app.py"
    with patch("urllib.request.urlopen", side_effect=AssertionError("No external quote expected")):
        at = AppTest.from_file(str(app), default_timeout=30).run()
        at.selectbox[0].select("XM / MT5 — تجربة الذهب").run()
    assert not at.exception
    assert any("ويندوز" in w.value for w in at.warning)


@pytest.mark.skipif(sys.platform == "win32", reason="Non-Windows UI fallback")
def test_launcher_mode_starts_without_external_quote_requests(monkeypatch):
    from unittest.mock import patch
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("START_IN_MT5_DEMO", "true")
    app = Path(__file__).resolve().parents[1] / "app.py"
    with patch("urllib.request.urlopen", side_effect=AssertionError("Unexpected network")) as network:
        at = AppTest.from_file(str(app), default_timeout=30).run()
    assert not at.exception
    assert at.selectbox[0].value == "XM / MT5 — تجربة الذهب"
    network.assert_not_called()

