import ast
import threading
import time
from urllib.error import HTTPError
from unittest.mock import Mock, patch
from test_paper_journal import ns, source

ns.update(threading=threading, time=time, HTTPError=HTTPError, GOLDAPI_URL='https://www.goldapi.io/api/XAU/USD')
node = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == 'GoldAPIClient')
exec(compile(ast.Module(body=[node], type_ignores=[]), 'app.py', 'exec'), ns)


def test_rate_limit_backoff_and_safe_error():
    client = ns['GoldAPIClient']()
    call = Mock(side_effect=HTTPError('https://example.invalid/private', 429, 'SECRET', {}, None))
    with patch.dict(ns, http_json=call), patch.object(time, 'monotonic', return_value=100):
        first = client.snapshot('private-key')
        second = client.snapshot('private-key')
    assert call.call_count == 1
    assert first['http_status'] == 429 and second['retry_after_seconds'] == 300
    assert 'private' not in str(first) and 'SECRET' not in str(first)
    assert first['source'] == 'GoldAPI'


def test_success_preserves_source_time_and_shares_cached_request():
    payload = dict(metal='XAU', currency='USD', price=4200, bid=4199, ask=4201, timestamp=1000)
    call = Mock(return_value=(200, payload))
    client = ns['GoldAPIClient']()
    with patch.dict(ns, http_json=call), patch.object(time, 'monotonic', return_value=100):
        a = client.snapshot('private-key')
        b = client.snapshot('private-key')
    assert a['ok'] and b['updated_at'] == 1000
    assert call.call_count == 1


def test_retry_resumes_and_invalid_payload_never_passes():
    call = Mock(return_value=(200, dict(error='private-secret')))
    client = ns['GoldAPIClient']()
    with patch.dict(ns, http_json=call), patch.object(time, 'monotonic', return_value=100):
        assert not client.snapshot('private-key')['ok']
    with patch.dict(ns, http_json=call), patch.object(time, 'monotonic', return_value=161):
        result = client.snapshot('private-key')
    assert call.call_count == 2 and 'private-secret' not in str(result)
