"""Regression tests run real engine functions without UI or network calls."""
import ast
import copy
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest

APP = Path(__file__).resolve().parents[1] / 'app.py'

class State(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__

@pytest.fixture
def engine(tmp_path, monkeypatch):
    tree = ast.parse(APP.read_text())
    nodes = []
    for n in tree.body:
        if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef)):
            nodes.append(n)
        elif isinstance(n, ast.Assign) and all(isinstance(t, ast.Name) and t.id.isupper() for t in n.targets):
            nodes.append(n)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id.isupper():
            nodes.append(n)
    spec = importlib.util.spec_from_loader('tested_engine', loader=None)
    m = importlib.util.module_from_spec(spec)
    sys.modules[m.__name__] = m
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(APP), 'exec'), m.__dict__)
    m.state_db.clear()
    monkeypatch.setenv('GOLD_AI_STATE_DB_PATH', str(tmp_path / 'state.sqlite3'))
    m.st = SimpleNamespace(session_state=State(), secrets={})
    m.init_state()
    yield m
    m.state_db()['conn'].close()
    m.state_db.clear()


def plan(m, **kw):
    args = dict(signal='BUY', entry=100., atr_value=2., equity=100000., risk_pct=.1,
                spec=m.PRESETS['Apple — AAPL'], stop_atr=1., tp1_r=1., tp2_r=2.)
    args.update(kw)
    return m.build_trade_plan(**args)


def test_original_engine_suite(engine):
    assert engine.self_test() == (True, 'OK')

@pytest.mark.parametrize('value', [float('nan'), float('inf'), -1., 0.])
def test_invalid_plan(engine, value):
    with pytest.raises(ValueError):
        plan(engine, entry=value)


def test_no_short_and_cash_cap(engine):
    with pytest.raises(ValueError):
        plan(engine, signal='SELL')
    p = plan(engine, equity=1000., atr_value=.01, risk_pct=.5)
    assert p['notional'] <= 1000.


def test_partial_manual_close(engine):
    m=engine
    p=plan(m)
    assert m.open_paper(p, m.PRESETS['Apple — AAPL'], 'one')
    m.manage_paper(102.)
    pos=m.st.session_state.paper_position
    assert pos['remaining'] == .5
    r=m.paper_unrealized_r(pos, 101.)
    assert r == pytest.approx(.75)
    m.close_paper(pos, 101., 'MANUAL', r)
    assert m.st.session_state.paper_balance == pytest.approx(100075.)
    m.close_paper(pos, 101., 'MANUAL', r)
    assert m.st.session_state.paper_balance == pytest.approx(100075.)
    m.restore_persistent_state()
    assert m.st.session_state.paper_position is None
    assert not m.open_paper(p, m.PRESETS['Apple — AAPL'], 'one')


def test_stop_gap_uses_observed_price(engine):
    m=engine
    m.open_paper(plan(m), m.PRESETS['Apple — AAPL'], 'gap')
    m.manage_paper(95.)
    assert m.st.session_state.paper_history[0]['R'] == pytest.approx(-2.5)
    assert m.st.session_state.paper_history[0]['exit'] == 95.


def test_custom_first_target(engine):
    m=engine
    m.open_paper(plan(m, tp1_r=1.5, tp2_r=3.),m.PRESETS['Apple — AAPL'],'target')
    m.manage_paper(103.)
    assert m.st.session_state.paper_position['realized_r'] == pytest.approx(.75)


def test_stale_session_cannot_overwrite(engine):
    m=engine
    assert m.persist_paper_state()
    old=State(copy.deepcopy(dict(m.st.session_state)))
    m.open_paper(plan(m),m.PRESETS['Apple — AAPL'],'other-tab')
    position_id=m.st.session_state.paper_position['id']
    m.st.session_state=old
    assert not m.persist_paper_state()
    assert m.st.session_state.paper_position['id'] == position_id
    assert m.st.session_state._persistence_error


def test_atomic_rollback(engine):
    m=engine
    assert m.persist_paper_state()
    m.st.session_state.paper_balance=77777.
    m.st.session_state.paper_history=[{'bad':float('nan')}]
    assert not m.persist_paper_state()
    assert m.state_read('paper_balance') == 100000.
    assert m.state_read('paper_history') == []


def test_adaptive_brake(engine):
    m=engine
    m.st.session_state.paper_balance=99600.
    assert not m.adaptive_entry_brake('متوازن','STOCK')
    m.st.session_state.paper_balance=100000.
    m.st.session_state.paper_history=[{'PnL':-1}]*3
    assert not m.adaptive_entry_brake('صارم','STOCK')


def test_day_rollover(engine):
    m=engine
    m.st.session_state.paper_day='2000-01-01'
    m.st.session_state.paper_trades_today=6
    m.st.session_state.paper_balance=99900.
    m.roll_paper_day()
    assert m.st.session_state.paper_trades_today == 0
    assert m.st.session_state.paper_day_start_balance == 99900.


def test_quote_guard(engine):
    m=engine
    assert not m.stock_quote_guard({'connected':True,'bid':102.,'ask':100.},100.)[0]
    q={'connected':True,'last':100.,'market_open':True,
       'raw':{'last_update_at':int((m.now_utc()+m.pd.Timedelta(days=1)).timestamp())}}
    assert not m.quote_execution_ready(q)


def test_risk_nan_fails_closed(engine):
    p=plan(engine)
    p['qty']=float('nan')
    assert not engine.risk_gate(100000.,0.,0,p,1.,1,.5)[0]


def test_bad_candles_do_not_crash(engine):
    assert engine.normalize_ohlcv([{'datetime':'2026-09-22'}]).empty
    assert engine.normalize_ohlcv([dict(datetime='2026-09-22',open=100,high=99,low=98,close=100)]).empty


def test_storage_failure_restores_memory(engine,monkeypatch):
    m=engine
    assert m.persist_paper_state()
    def fail(): raise OSError('unavailable disk')
    with monkeypatch.context() as patcher:
        patcher.setattr(m,'state_db',fail)
        assert not m.open_paper(plan(m),m.PRESETS['Apple — AAPL'],'failed')
    assert m.st.session_state.paper_position is None
    assert m.st.session_state.paper_trades_today == 0
    assert 'failed' not in m.st.session_state.paper_order_keys


@pytest.mark.parametrize("values", [
    {"last": 0}, {"last": -1}, {"last": float("inf")},
    {"bid": -1, "ask": 2}, {"bid": 101, "ask": 100, "last": 100},
    {"bid": 100, "last": 100},
])
def test_broker_invalid_prices_fail_closed(engine, values):
    quote = {"timestamp": engine.now_utc().isoformat(), **values}
    assert not engine.broker_quote_state(quote)["ok"]


def test_broker_timestamp_guards(engine):
    now = engine.now_utc()
    for minutes in [-10, 10]:
        quote = {"last": 100, "timestamp": (now + engine.pd.Timedelta(minutes=minutes)).isoformat()}
        assert not engine.broker_quote_state(quote)["ok"]
    assert engine.broker_quote_state({"bid": 100, "ask": 101, "timestamp": now.isoformat()})["ok"]


def option_plan(m, kind='Call'):
    return m.create_option_watch('AAPL', kind, '2030-02-01', 100, 2, 2.2,
                                 1.5, 3, 4, 1, 100, '2030-01-01')


def option_quote(w, bid=2, ask=2.1, timestamp='2030-01-02T16:00:00Z'):
    return dict(watch_id=w['id'], bid=bid, ask=ask, timestamp=timestamp, source='manual')


@pytest.mark.parametrize('kind', ['Call', 'Put'])
def test_option_entry_then_exit_dedup(engine, kind):
    m = engine
    w = option_plan(m, kind)
    w, events, _ = m.evaluate_option_watch(w, option_quote(w), '2030-01-02T16:00:01Z')
    assert [e['code'] for e in events] == ['ENTRY']
    assert not w['entered']  # A price alert must never claim a fill.
    w, events, _ = m.evaluate_option_watch(w, option_quote(w, timestamp='2030-01-02T16:00:02Z'), '2030-01-02T16:00:03Z')
    assert not events
    w['entered'] = True
    w, events, _ = m.evaluate_option_watch(w, option_quote(w, 3.1, 3.2, '2030-01-02T16:00:04Z'), '2030-01-02T16:00:05Z')
    assert [e['code'] for e in events] == ['TP1']
    w, events, _ = m.evaluate_option_watch(w, option_quote(w, 0, .1, '2030-01-02T16:00:06Z'), '2030-01-02T16:00:07Z')
    assert [e['code'] for e in events] == ['STOP']
    assert not w['closed']  # No automated execution implied.


@pytest.mark.parametrize('patch', [
    {'watch_id':'other'}, {'timestamp':'2030-01-02T15:00:00Z'},
    {'timestamp':'2030-01-02T17:00:00Z'}, {'timestamp':'2030-01-02T16:00:00'},
    {'bid':float('nan')}, {'ask':float('inf')}, {'bid':3, 'ask':2},
    {'source':'stock-price'}, {'ask':0},
])
def test_option_reject_bad_quote(engine, patch):
    w = option_plan(engine)
    q = option_quote(w)
    q.update(patch)
    updated, events, _ = engine.evaluate_option_watch(w, q, '2030-01-02T16:00:01Z')
    assert not events
    assert updated == w


def test_option_expiry_and_jump(engine):
    w = option_plan(engine)
    expired, events, _ = engine.evaluate_option_watch(w, option_quote(w), '2030-02-02T16:00:01Z')
    assert not events and expired == w
    w['entered'] = True
    w, events, _ = engine.evaluate_option_watch(w, option_quote(w, 4.1, 4.2), '2030-01-02T16:00:01Z')
    assert [e['code'] for e in events] == ['TP2']
    w, events, _ = engine.evaluate_option_watch(w, option_quote(w, 3.1, 3.2, '2030-01-02T16:00:02Z'), '2030-01-02T16:00:03Z')
    assert not events


def test_option_invalid_plan(engine):
    with pytest.raises(ValueError):
        engine.create_option_watch('AAPL','Put','2030-01-01',100,2,2.2,1.5,3,4,1,100,'2030-01-01')
    with pytest.raises(ValueError):
        engine.create_option_watch('AAPL','Put','2030-02-01',100,2,2.2,3,3,4,1,100,'2030-01-01')
