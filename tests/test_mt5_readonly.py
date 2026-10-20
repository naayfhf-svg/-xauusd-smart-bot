import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

spec = importlib.util.spec_from_file_location("mt5_readonly", Path(__file__).resolve().parents[1] / "mt5_readonly.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
NOW = 1000000


class API:
    ACCOUNT_TRADE_MODE_DEMO = 0
    TIMEFRAME_M5 = 5

    def __init__(self):
        self.account = NS(login=123, server="DemoServer", trade_mode=0)
        self.tick = NS(bid=4000, ask=4000.5, time_msc=NOW*1000)
        self.connected = True
        self.stopped = False
        self.info = NS(visible=True, currency_base="XAU", currency_profit="USD")
        self.rates = [dict(time=NOW-300*i, open=4000, high=4002, low=3999, close=4001)
                      for i in range(200, 0, -1)]

    def initialize(self, **kwargs):
        return True

    def terminal_info(self):
        return NS(connected=self.connected)

    def account_info(self):
        return self.account

    def symbol_info(self, symbol):
        return self.info

    def symbol_info_tick(self, symbol):
        return self.tick

    def copy_rates_from_pos(self, symbol, frame, start, count):
        assert start == 1
        return self.rates

    def shutdown(self):
        self.stopped = True

    def order_send(self, *args, **kwargs):
        raise AssertionError("An order must never be sent")


def read(api):
    return m.read_snapshot(api, "123", "DemoServer", clock=NOW)


def test_demo_read():
    api = API()
    result = read(api)
    assert not result["execution_enabled"]
    assert len(result["bars"]) == 200
    assert result["quote"]["spread"] == .5
    assert api.stopped


@pytest.mark.parametrize("field,value", [
    ("trade_mode", 2), ("trade_mode", 1), ("login", 999), ("server", "Other"),
])
def test_account_rejected(field, value):
    api = API()
    setattr(api.account, field, value)
    with pytest.raises(m.ReadError):
        read(api)
    assert api.stopped


@pytest.mark.parametrize("offset", [-6, 1])
def test_stale_future_ticks(offset):
    api = API()
    api.tick.time_msc = (NOW+offset)*1000
    with pytest.raises(m.ReadError):
        read(api)


@pytest.mark.parametrize("value", [0, float("nan"), float("inf"), -1])
def test_invalid_bid(value):
    api = API()
    api.tick.bid = value
    with pytest.raises(m.ReadError):
        read(api)


def test_disconnect():
    api = API()
    api.connected = False
    with pytest.raises(m.ReadError):
        read(api)


def test_missing_tick():
    api = API()
    api.tick = None
    with pytest.raises(m.ReadError):
        read(api)


def test_wrong_instrument():
    api = API()
    api.info.currency_base = "EUR"
    with pytest.raises(m.ReadError):
        read(api)


def test_crossed_spread():
    api = API()
    api.tick.ask = 3999
    with pytest.raises(m.ReadError):
        read(api)


@pytest.mark.parametrize("kind", ["missing", "short", "stale", "reversed", "bad_ohlc"])
def test_bad_history(kind):
    api = API()
    if kind == "missing":
        api.rates = None
    elif kind == "short":
        api.rates = api.rates[:10]
    elif kind == "stale":
        api.rates = api.rates[:-3]
    elif kind == "reversed":
        api.rates.reverse()
    else:
        api.rates[0]["high"] = 1
    with pytest.raises(m.ReadError):
        read(api)


def test_initialization_failure():
    api = API()
    api.initialize = lambda **kwargs: False
    with pytest.raises(m.ReadError):
        read(api)
    assert api.stopped


def test_switch_account_mid_read():
    api = API()
    calls = []
    def account():
        calls.append(1)
        return api.account if len(calls) == 1 else NS(login=999, server="DemoServer", trade_mode=0)
    api.account_info = account
    with pytest.raises(m.ReadError):
        read(api)


def test_open_bar_excluded():
    api = API()
    api.rates.append(dict(time=NOW-60, open=4000, high=4002, low=3999, close=4001))
    assert len(read(api)["bars"]) == 200
