"""Offline compute benchmark; excludes network, browser rendering and concurrency."""
import ast
import importlib.util
import json
from pathlib import Path
import platform
import statistics
import sys
import time
import tracemalloc

APP = Path(__file__).resolve().parents[1] / 'app.py'
tree = ast.parse(APP.read_text())
nodes = []
for n in tree.body:
    if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef)):
        nodes.append(n)
    elif isinstance(n, ast.Assign) and all(isinstance(t, ast.Name) and t.id.isupper() for t in n.targets):
        nodes.append(n)
    elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id.isupper():
        nodes.append(n)
spec = importlib.util.spec_from_loader('bench_engine', loader=None)
m = importlib.util.module_from_spec(spec)
sys.modules[m.__name__] = m
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(APP), 'exec'), m.__dict__)
idx = m.pd.date_range(end=m.now_utc().floor('5min')-m.pd.Timedelta(minutes=5), periods=5000, freq='5min')
price = m.np.linspace(100, 130, len(idx)) + m.np.sin(m.np.arange(len(idx))/15.)*.3
raw = m.pd.DataFrame(dict(datetime=idx,open=price-.1,high=price+.4,low=price-.4,close=price,volume=150000))

def compute():
    snaps = {tf:m.snapshot(frame) for tf,frame in {
        'M5':m.closed_m5(raw), 'M15':m.resample_closed(raw,'15min'), 'H1':m.resample_closed(raw,'1h')}.items()}
    assert all(snaps.values()), 'Fixture must exercise all three frames'
    return m.option_direction(snaps,m.b2_signal(snaps['M5']['frame']),True)

start=time.perf_counter()
compute()
first=time.perf_counter()-start
samples=[]
for _ in range(10):
    start=time.perf_counter()
    compute()
    samples.append(time.perf_counter()-start)
tracemalloc.start()
compute()
_,peak=tracemalloc.get_traced_memory()
tracemalloc.stop()
print(json.dumps(dict(scope='offline compute only; no API latency, browser, live prices or load test',
    app_version=m.VERSION,python=platform.python_version(),bars=len(raw),repeats=len(samples),
    first_run_seconds=round(first,4),median_seconds=round(statistics.median(samples),4),
    slowest_seconds=round(max(samples),4),peak_traced_python_mb=round(peak/1024/1024,2)),indent=2))
