from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

APP=Path(__file__).resolve().parents[1]/'app.py'

class Response:
    status_code=200
    headers={}
    def __init__(self,data): self.data=data
    def json(self): return self.data


def test_ui_with_market_fixture(tmp_path,monkeypatch):
    monkeypatch.setenv('GOLD_AI_STATE_DB_PATH',str(tmp_path/'ui.sqlite3'))
    monkeypatch.setenv('TWELVE_DATA_API_KEY','test-fixture-only')
    st.cache_resource.clear()
    st.cache_data.clear()
    idx=pd.date_range(end=pd.Timestamp.now(tz='UTC').floor('5min')-pd.Timedelta(minutes=5),periods=5000,freq='5min')
    price=np.linspace(100,130,len(idx))+np.sin(np.arange(len(idx))/15.)*.3
    values=[dict(datetime=d.isoformat(),open=float(c-.1),high=float(c+.4),low=float(c-.4),close=float(c),volume=150000) for d,c in zip(idx,price)]
    def get(url,**kw):
        if 'time_series' in url: return Response({'values':values})
        return Response({'close':float(price[-1]),'bid':float(price[-1]-.01),'ask':float(price[-1]+.01),'is_market_open':True,'last_update_at':int(pd.Timestamp.now(tz='UTC').timestamp())})
    with patch('requests.get',side_effect=get):
        at=AppTest.from_file(str(APP),default_timeout=60).run()
        assert not at.exception, [x.message for x in at.exception]
        assert not at.error, [x.value for x in at.error]
        assert at.radio[0].value == 'الأسهم'
        for profile in ['صارم','متوازن','مرن','ذكي تلقائي']:
            next(x for x in at.selectbox if x.label=='نمط الدخول').select(profile).run()
            assert not at.exception
        at.button(key='stock_favorite_toggle').click().run()
        assert 'AAPL' in at.session_state.stock_favorites
        next(x for x in at.toggle if x.label=='إعدادات متقدمة').set_value(True).run()
        assert not at.exception
        next(x for x in at.toggle if x.label=='إظهار أدوات البحث والتشخيص').set_value(True).run()
        assert not at.exception
        for section in ['الذهب','العقود','الأسهم']:
            at.radio[0].set_value(section).run()
            assert not at.exception, [x.message for x in at.exception]


def test_missing_key_ui(tmp_path,monkeypatch):
    monkeypatch.setenv('GOLD_AI_STATE_DB_PATH',str(tmp_path/'no-key.sqlite3'))
    monkeypatch.delenv('TWELVE_DATA_API_KEY',raising=False)
    st.cache_resource.clear()
    st.cache_data.clear()
    at=AppTest.from_file(str(APP),default_timeout=60).run()
    assert not at.exception
    assert any('مصدر البيانات' in x.value for x in at.error)
