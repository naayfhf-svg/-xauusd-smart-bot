"""Exercise the production entry decision without credentials or network access."""
import ast
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest


@pytest.fixture
def engine():
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'app.py').read_text())
    names = {'finite', 'entry_gate', 'gld_source_check', 'gld_entry_ready',
             'gld_readiness_issues', 'analyze_gld', 'ema', 'rolling_rsi',
             'atr', 'trend', 'resample'}
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    ns = dict(Any=Any, math=math, time=time, datetime=datetime, timezone=timezone, ZoneInfo=ZoneInfo)
    exec(compile(ast.Module(body=body, type_ignores=[]), 'app.py', 'exec'), ns)
    return ns


@pytest.fixture
def inputs():
    clock = 1_800_000_000.0
    quote = dict(ok=True, feed='iex', updated_at=clock-1, market_open=True,
                 market_verified=True, bid=400.0, ask=400.02, last=400.01)
    rows = [dict(ts=clock-300*(120-i), close=400.0) for i in range(120)]
    return quote, rows, dict(signal='BUY', atr=1.0), clock


@pytest.mark.parametrize('feed', ['iex', 'sip'])
def test_supported_feed_can_produce_paper_entry(engine, inputs, feed):
    quote, rows, analysis, clock = inputs
    quote['feed'] = feed
    assert engine['gld_entry_ready'](quote, rows, analysis, clock)
    assert not engine['gld_readiness_issues'](quote, rows, clock)


@pytest.mark.parametrize('change', [
    {'updated_at': 1_799_999_994.0}, {'updated_at': 1_800_000_001.0},
    {'market_open': False}, {'market_verified': False}, {'ok': False},
    {'feed': 'unknown'}, {'ask': None}, {'ask': 399.0}, {'ask': 401.0},
])
def test_bad_quote_cannot_produce_entry(engine, inputs, change):
    quote, rows, analysis, clock = inputs
    assert not engine['gld_entry_ready'](quote | change, rows, analysis, clock)


def test_missing_stale_history_wait_and_chasing_blocked(engine, inputs):
    quote, rows, analysis, clock = inputs
    ready = engine['gld_entry_ready']
    assert not ready(quote, [], analysis, clock)
    assert not ready(quote, rows[-119:], analysis, clock)
    assert not ready(quote, [dict(r, ts=r['ts']-900) for r in rows], analysis, clock)
    assert not ready(quote, rows, dict(analysis, signal='WAIT'), clock)
    assert not ready(quote, rows, dict(analysis, signal='SELL'), clock)
    assert not ready(quote, rows, dict(analysis, atr=float('nan')), clock)
    assert not ready(quote, rows, dict(analysis, atr=0.01), clock)


def test_iex_disclosure_and_gapped_session(engine):
    assert 'بورصة واحدة' in engine['gld_source_check'](dict(ok=True, feed='iex'))['reason']
    start = datetime(2026, 9, 25, 13, 30, tzinfo=timezone.utc).timestamp()
    # A missing five-minute interval must not become a fabricated candle.
    rows = [dict(ts=start+i*300) for i in range(121) if i != 5]
    assert engine['analyze_gld'](rows)['signal'] == 'WAIT'
