import ast
import math
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
import pytest

source = (Path(__file__).parents[1] / 'app.py').read_text()
body = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and not n.decorator_list]
ns = dict(Any=Any, math=math, datetime=datetime, timezone=timezone,
          PAPER_SLIPPAGE_USD=0.1, PAPER_SAR_PER_USD=3.75)
exec(compile(ast.Module(body=body, type_ignores=[]), 'app.py', 'exec'), ns)


def test_win_rate_does_not_hide_net_loss_and_drawdown():
    wallet = ns['new_wallet']()
    wallet['trades'] = [dict(pnl_sar=10) for _ in range(89)] + [dict(pnl_sar=-100) for _ in range(11)]
    stats = ns['paper_statistics'](wallet)
    assert stats['win_rate'] == 89
    assert stats['net'] == -210
    assert stats['realized_drawdown'] == 1100
    assert stats['average_win'] == 10 and stats['average_loss'] == 100
    assert ns['paper_statistics'](ns['new_wallet']())['win_rate'] is None


def test_gld_complete_trade_and_same_signal_cannot_reenter():
    wallet = ns['new_wallet']('GLD')
    q = dict(ok=True, updated_at=1000, market_open=True, bid=400, ask=400.02)
    plan = dict(stop=398, tp1=402)
    assert ns['paper_open'](wallet, 'BUY', plan, q, 1000, 'bar1')
    p = wallet['position'].copy()
    assert p['ounces'] == 3
    assert p['entry'] == pytest.approx(400.03)
    assert p['risk_sar'] <= 25
    assert ns['paper_close'](wallet, q | dict(bid=402, ask=402.02), 1000) == 'هدف'
    assert wallet['trades'][0]['exit'] == pytest.approx(401.99)
    assert wallet['trades'][0]['pnl_sar'] == pytest.approx((401.99-400.03)*3*3.75)
    assert not ns['paper_open'](wallet, 'BUY', plan, q, 1000, 'bar1')
    assert ns['paper_open'](wallet, 'BUY', plan, q, 1000, 'bar2')


def test_gld_rejects_short_stale_and_insufficient_cash():
    q = dict(ok=True, updated_at=1000, market_open=True, bid=400, ask=400.02)
    w = ns['new_wallet']('GLD')
    assert not ns['paper_open'](w, 'SELL', dict(stop=402, tp1=398), q, 1000)
    assert not ns['paper_open'](w, 'BUY', dict(stop=398, tp1=402), q, 1006)
    w['balance'] = 100
    assert not ns['paper_open'](w, 'BUY', dict(stop=398, tp1=402), q, 1000)
    assert w['position'] is None


def test_stale_quotes_do_not_fabricate_exit_and_gap_uses_observed_price():
    w = ns['new_wallet']('GLD')
    q = dict(ok=True, updated_at=1000, market_open=True, bid=400, ask=400.02)
    assert ns['paper_open'](w, 'BUY', dict(stop=398, tp1=402), q, 1000)
    assert ns['paper_close'](w, q | dict(bid=395, ask=395.02), 1010) is None
    assert ns['paper_close'](w, q | dict(bid=395, ask=395.02, updated_at=1010), 1010) == 'وقف'
    assert w['trades'][0]['exit'] == pytest.approx(394.99)
    assert -w['trades'][0]['pnl_sar'] > w['trades'][0]['risk_sar']
