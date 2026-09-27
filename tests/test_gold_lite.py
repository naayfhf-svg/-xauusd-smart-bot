from pathlib import Path
from unittest.mock import patch
import json

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "app.py"


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
        at = AppTest.from_file(str(APP), default_timeout=60).run()

    assert not at.exception, [x.message for x in at.exception]
    assert not at.error, [x.value for x in at.error]


def test_gold_lite_missing_key_does_not_crash(monkeypatch):
    monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
    monkeypatch.delenv("GOLDAPI_KEY", raising=False)
    st.cache_data.clear()
    st.cache_resource.clear()

    at = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not at.exception
    assert any("TWELVE_DATA_API_KEY" in x.value for x in at.error)
