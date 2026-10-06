import ast
import threading
import time
from urllib.error import HTTPError
from unittest.mock import Mock, patch
from test_paper_journal import ns, source

endpoint = next(n.value for n in ast.parse(source).body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'GOLDAPI_URL' for t in n.targets))
ns.update(threading=threading, time=time, HTTPError=HTTPError, GOLDAPI_URL=ast.literal_eval(endpoint))
node = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == 'GoldAPIClient')
exec(compile(ast.Module(body=[node], type_ignores=[]), 'app.py', 'exec'), ns)


def test_rate_limit_backoff_and_safe_error():
    client = ns['GoldAPIClient']()
    call = Mock(side_effect=HTTPError('https://example.invalid/private', 429, 'SECRET', {}, None))
    with patch.dict(ns, http_json=call), patch.object(time, 'monotonic', return_value=100):
        first = client.snapshot('private-key', request=True)
        second = client.snapshot('private-key', request=True)
    assert call.call_count == 1

    call.assert_called_once_with('https://www.goldapi.io/api/price/XAU/USD',
                                 headers={'x-access-token': 'private-key', 'Content-Type': 'application/json'}, timeout=4)
    assert first['http_status'] == 429 and second['retry_after_seconds'] == 300
    assert 'private' not in str(first) and 'SECRET' not in str(first)
    assert first['source'] == 'GoldAPI'


def test_success_preserves_source_time_and_shares_cached_request():
    payload = dict(metal='XAU', currency='USD', price=4200, bid=4199, ask=4201, timestamp=1000)
    call = Mock(return_value=(200, payload))
    client = ns['GoldAPIClient']()
    with patch.dict(ns, http_json=call), patch.object(time, 'monotonic', return_value=100):
        a = client.snapshot('private-key', request=True)
        b = client.snapshot('private-key', request=True)
    assert a['ok'] and b['updated_at'] == 1000
    assert call.call_count == 1


def test_retry_resumes_and_invalid_payload_never_passes():
    call = Mock(return_value=(200, dict(error='private-secret')))
    client = ns['GoldAPIClient']()
    with patch.dict(ns, http_json=call), patch.object(time, 'monotonic', return_value=100):
        assert not client.snapshot('private-key', request=True)['ok']
    with patch.dict(ns, http_json=call), patch.object(time, 'monotonic', return_value=161):
        result = client.snapshot('private-key', request=True)
    assert call.call_count == 2 and 'private-secret' not in str(result)


def test_passive_reruns_never_request_even_after_cache_expires():
    call = Mock(return_value=(200, dict(metal='XAU', currency='USD', price=4200,
                                      bid=4199.9, ask=4200.1, timestamp=1000)))
    client = ns['GoldAPIClient']()
    with patch.dict(ns, http_json=call), patch.object(time, 'monotonic', return_value=100):
        assert client.snapshot('private-key')['pending']
        call.assert_not_called()
        assert client.snapshot('private-key', request=True)['http_status'] == 200
    with patch.dict(ns, http_json=call), patch.object(time, 'monotonic', return_value=10000):
        for _ in range(100):
            cached = client.snapshot('private-key')
        assert cached['updated_at'] == 1000
        quote = dict(cached, last=cached['price'], market_open=True)
        assert not ns['entry_gate'](quote, [dict(ts=9700)], 10000)[0]
    assert call.call_count == 1


def test_new_client_does_not_reuse_previous_key_result():
    call = Mock()
    with patch.dict(ns, http_json=call):
        assert ns['GoldAPIClient']().snapshot('new-key')['pending']
    call.assert_not_called()
