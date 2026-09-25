import ast, math, time, threading, json, csv, io, hashlib
from pathlib import Path
from typing import Any
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch

app_path = Path(__file__).resolve().parent / 'app.py'
if not app_path.exists():
    app_path = Path(__file__).resolve().parents[1] / 'app.py'
source = app_path.read_text()
tree = ast.parse(source)
ns = dict(Any=Any, math=math, time=time, threading=threading, json=json, csv=csv, io=io, hashlib=hashlib,
          datetime=datetime, timezone=timezone, ZoneInfo=ZoneInfo)
body = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and not n.decorator_list]
exec(compile(ast.Module(body=body, type_ignores=[]), 'app', 'exec'), ns)
clock = datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc).timestamp()
ns['now_ts'] = lambda: clock
settings = dict(ALPACA_API_KEY='test', ALPACA_SECRET_KEY='secret', ALPACA_DATA_FEED='iex')
ns['secret'] = lambda key, default='': settings.get(key, default)
identity = ns['alpaca_cache_identity']()
settings['ALPACA_DATA_FEED'] = 'sip'
assert ns['alpaca_cache_identity']() != identity
identity = ns['alpaca_cache_identity']()
settings['ALPACA_SECRET_KEY'] = 'rotated'
assert ns['alpaca_cache_identity']() != identity
closed = ns['gld_readiness_issues'](dict(feed='sip', market_verified=True, market_open=False), [], clock)
unknown = ns['gld_readiness_issues'](dict(feed='sip', market_verified=False), [], clock)
assert any('النظامية مغلقة' in x for x in closed)
assert not any('تعذر التحقق من ساعة' in x for x in closed)
assert any('تعذر التحقق من ساعة' in x for x in unknown)
assert any('0/120' in x for x in unknown)
q = dict(T='q', S='GLD', bp=400, ap=400.02,
         t=datetime.fromtimestamp(clock - 1, timezone.utc).isoformat())
normalize = ns['normalize_gld_quote']
assert normalize(q, 'sip')['ok']
assert not normalize(q | dict(bp=401), 'sip')['ok']
assert not normalize(q | dict(ap='NaN'), 'sip')['ok']
assert not normalize(q | dict(t=clock + 1), 'sip')['ok']
stream = ns['GLDStream']('test', 'test', 'sip')
stream.accept(q | dict(S='XAU/USD')); assert stream.latest is None
stream.accept(q); assert stream.latest['bid'] == 400
stream.accept(q | dict(bp=399, t=clock - 10)); assert stream.latest['bid'] == 400
assert not ns['entry_gate'](stream.latest | dict(market_open=True), [dict(ts=clock-300)], clock+10)[0]

bars = []
for day in range(10):
    d = datetime(2026, 9, 16, 13, 30, tzinfo=timezone.utc) + timedelta(days=day)
    if d.weekday() >= 5:
        continue
    for i in range(78):
        t = d + timedelta(minutes=5*i)
        price = 390 + len(bars)*0.01 + math.sin(i)*0.02
        bars.append(dict(t=t.isoformat(), o=price, h=price+.1, l=price-.1,
                         c=price+.03, v=1000, vw=price))
rows = ns['normalize_gld_bars'](list(reversed(bars)) + [bars[0]])
assert rows and all(r['ts'] + 300 <= clock for r in rows)
assert len({r['ts'] for r in rows}) == len(rows)
assert all(570 <= (datetime.fromtimestamp(r['ts'], ZoneInfo('America/New_York')).hour*60
                   + datetime.fromtimestamp(r['ts'], ZoneInfo('America/New_York')).minute) < 960 for r in rows)
analyze = ns['analyze_gld']
assert analyze(rows)['signal'] in ('WAIT', 'BUY')
assert len(analyze(rows)['checks']) == 6
assert analyze(rows[:-3] + rows[-2:])['signal'] == 'WAIT'  # session gap
assert analyze([])['signal'] == 'WAIT'
saved = {k: ns[k] for k in ('ema', 'rolling_rsi', 'trend')}
trial = [dict(r) for r in rows]
trial[-1]['close'] = trial[-1]['high'] + 0.5
trial[-1]['high'] = trial[-1]['close']
trial[-1]['volume'] = 5000
ns['ema'] = lambda values, period: [trial[-1]['close'] - (0.1 if period == 20 else 0.2)]
ns['rolling_rsi'] = lambda *args: 60
ns['trend'] = lambda *args: 'UP'
assert analyze(trial)['signal'] == 'BUY'
ns.update(saved)
started = time.perf_counter()
for _ in range(100):
    analyze(rows)
print(f'GLD normalization, stream isolation, stale rejection, session gaps passed; analyzer {(time.perf_counter()-started)*10:.2f} ms/run on {len(rows)} synthetic bars')

from streamlit.testing.v1 import AppTest
class Response:
    status = 200
    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return json.dumps(self.data).encode()
def reply(req, **kwargs):
    assert req.get_method() == 'GET'
    url = req.full_url
    if '/v2/clock' in url:
        return Response(dict(is_open=True, timestamp=datetime.now(timezone.utc).isoformat()))
    if '/quotes/latest' in url:
        return Response(dict(quote=q | dict(t=datetime.now(timezone.utc).isoformat())))
    if '/bars' in url:
        return Response(dict(bars=bars))
    return Response({})
with patch('urllib.request.urlopen', side_effect=reply), patch('websockets.sync.client.connect', side_effect=OSError('offline test')):
    app = AppTest.from_file(str(app_path))
    app.secrets['ALPACA_API_KEY'] = 'test-only'
    app.secrets['ALPACA_SECRET_KEY'] = 'test-only'
    app.secrets['ALPACA_DATA_FEED'] = 'iex'
    app.run(timeout=20)
    app.selectbox[0].select_index(1).run(timeout=20)
    assert not app.exception, app.exception
    assert any('SIP غير مفعل' in m.value for m in app.markdown)
    assert not any('فرصة شراء تجريبية' in m.value for m in app.markdown)
    app.secrets['ALPACA_DATA_FEED'] = 'sip'
    app.run(timeout=20)
    assert not app.exception, app.exception
    assert not any('SIP غير مفعل' in m.value for m in app.markdown)
print('GLD UI with Alpaca only (no Twelve Data or GoldAPI), IEX blocked and GET-only requests passed')
print('Credential/feed cache isolation, missing history and closed/unknown market diagnostics passed')
