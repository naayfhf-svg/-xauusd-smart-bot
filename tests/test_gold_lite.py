from pathlib import Path
from unittest.mock import patch
import json
import pytest

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "app.py"


@pytest.mark.parametrize('verified', [True, False])
def test_gld_closed_market_is_distinct_from_unknown_market(verified):
    def reply(request, **kwargs):
        assert request.get_method() == 'GET'
        if '/v2/clock' in request.full_url and verified:
            return Response(dict(is_open=False, timestamp=pd.Timestamp.now(tz='UTC').isoformat(),
                                 next_open='2026-09-28T13:30:00Z'))
        return Response({})

    st.cache_data.clear()
    st.cache_resource.clear()
    with patch('urllib.request.urlopen', side_effect=reply), patch(
        'websockets.sync.client.connect', side_effect=OSError('offline test')
    ):
        app = AppTest.from_file(str(APP))
        app.secrets['ALPACA_API_KEY'] = 'test-market-state'
        app.secrets['ALPACA_SECRET_KEY'] = 'test-market-state'
        app.secrets['ALPACA_DATA_FEED'] = 'iex'
        app.run(timeout=20)
    assert not app.exception
    cards = next(m.value for m in app.markdown if "class='grid'" in m.value)
    assert '%' not in cards
    assert 'شراء تجريبي' not in cards
    if verified:
        assert 'السوق مغلق — انتظار الافتتاح' in cards
        assert not app.error
        assert any('2026-09-28 16:30' in c.value for c in app.caption)
    else:
        assert 'البيانات غير جاهزة' in cards
        assert app.error


class Response:
    status = 200

    def __init__(self, data):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return json.dumps(self._data).encode()


def _fixture_get(request, **kwargs):
    assert request.get_method() == 'GET'
    url = request.full_url
    now = pd.Timestamp.now(tz="UTC").floor("5min")
    if "time_series" in url:
        rows = []
        price = 4200.0
        for i in range(1200):
            ts = now - pd.Timedelta(minutes=5 * (1199 - i))
            price += 0.03
            rows.append(
                {
                    "datetime": ts.isoformat(),
                    "open": price - 0.1,
                    "high": price + 0.4,
                    "low": price - 0.4,
                    "close": price,
                }
            )
        return Response({"values": rows})
    if "goldapi.io" in url:
        return Response({"price": 4236.0, "bid": 4235.8, "ask": 4236.2,
                         "metal": "XAU", "currency": "USD",
                         "timestamp": pd.Timestamp.now(tz="UTC").timestamp()})
    assert '/quote?' in url, f'Unexpected test request: {url.split("?")[0]}'
    return Response(
        {
            "close": 4236.0,
            "bid": 4235.8,
            "ask": 4236.2,
            "is_market_open": True,
            "last_update_at": int(pd.Timestamp.now(tz="UTC").timestamp()),
        }
    )


def test_gold_lite_app_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("TWELVE_DATA_API_KEY", "fixture")
    monkeypatch.setenv("GOLDAPI_KEY", "fixture")
    st.cache_data.clear()
    st.cache_resource.clear()

    with patch("urllib.request.urlopen", side_effect=_fixture_get), patch(
        "websockets.sync.client.connect", side_effect=OSError('offline test')
    ):
        with patch("urllib.request.urlopen", side_effect=_fixture_get) as calls:
            at = AppTest.from_file(str(APP), default_timeout=60).run()
            assert not at.exception, [x.message for x in at.exception]
            assert not any("goldapi.io" in c.args[0].full_url for c in calls.call_args_list)
            assert any("لم يُختبر" in x.value for x in at.info)
            at.button(key="goldapi_manual_check").click().run()
            assert not at.exception, [x.message for x in at.exception]
            assert any("HTTP 200" in x.value for x in at.success)
            assert sum("goldapi.io" in c.args[0].full_url for c in calls.call_args_list) == 1
            at.run()
            assert sum("goldapi.io" in c.args[0].full_url for c in calls.call_args_list) == 1


def test_gold_lite_missing_key_does_not_crash(monkeypatch):
    monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
    monkeypatch.delenv("GOLDAPI_KEY", raising=False)
    st.cache_data.clear()
    st.cache_resource.clear()

    at = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not at.exception
    assert any("TWELVE_DATA_API_KEY" in x.value for x in at.error)
