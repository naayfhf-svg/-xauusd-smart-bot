from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
import sys
import pytest
from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from spx_lab import contract_info, quote_view, long_option_cost, load_chain, read_bars, historical_analysis

CLOCK = datetime(2026, 9, 29, tzinfo=timezone.utc).timestamp()


def bars_csv():
    start = datetime(2026, 9, 28, 13, 30, tzinfo=timezone.utc)
    lines = ['symbol,timestamp,open,high,low,close']
    for i in range(60):
        lines.append(f'SPX,{(start+timedelta(minutes=5*i)).isoformat()},{6000+i},{6002+i},{5999+i},{6001+i}')
    return '\n'.join(lines)


def test_contract_and_long_risk_units():
    assert contract_info('SPXW260928C06000000')['strike'] == 6000
    assert contract_info('SPX260918P06000000')['settlement'] == 'AM'
    assert long_option_cost(5, 2, 1)['maximum_loss'] == 1002
    with pytest.raises(ValueError):
        contract_info('SPY260928C00600000')
    with pytest.raises(ValueError):
        long_option_cost(float('nan'), 1, 0)
    with pytest.raises(ValueError):
        long_option_cost(5, 0.5, 0)


@pytest.mark.parametrize('bid,ask', [(6,5), (-1,5), ('nan',5), (None,5)])
def test_bad_options_quotes_never_become_executable(bid,ask):
    result = quote_view('SPXW260928C06000000', {'latestQuote':{'bp':bid,'ap':ask}}, CLOCK)
    assert result['ask'] is None
    assert result['live_entry_allowed'] is False


def test_recent_indicative_still_not_live():
    result = quote_view('SPXW260928C06000000', {'latestQuote':{'bp':4,'ap':5,'t':'2026-09-29T00:00:00Z'}}, CLOCK)
    assert result['age_seconds'] == 0
    assert result['live_entry_allowed'] is False


def test_chain_pagination_and_no_paid_fallback():
    calls = []
    def request(url, params, headers, timeout):
        assert url.endswith('/snapshots/SPX')
        assert params['feed'] == 'indicative'
        calls.append(params.copy())
        i = len(calls)
        return 200, {'snapshots':{f'SPXW260928C0600{i}000': {'latestQuote':{'bp':4,'ap':5}}}, 'next_page_token':str(i)}
    load_chain.clear()
    result = load_chain('test-pages', '2026-09-28', request, ('key','secret'))
    assert len(calls) == 3 and calls[1]['page_token'] == '1'
    assert result['partial'] and len(result['rows']) == 3
    def fail(*args, **kwargs):
        raise RuntimeError('secret-must-not-leak')
    result = load_chain('test-error', '2026-09-28', fail, ('key','secret'))
    assert 'secret' not in str(result)
    assert not result['rows']


def test_historical_analysis_is_not_option_price_or_live_signal():
    rows = read_bars(bars_csv(), CLOCK)
    result = historical_analysis(rows)
    assert result['direction'] == 'صاعد'
    assert result['support'] == 6052
    assert result['resistance'] == 6060
    assert result['atr14'] == 3
    assert not result['live_entry_allowed']
    assert 'entry' not in result and 'vwap' not in result


@pytest.mark.parametrize('change', [
    lambda s: s.replace('SPX,', 'SPY,'),
    lambda s: s.replace('+00:00',''),
    lambda s: s+'\n'+s.splitlines()[-1],
    lambda s: s.replace('6000,6002,5999,6001','6000,5998,5999,6001'),
    lambda s: s.replace('6000,6002,5999,6001','nan,6002,5999,6001'),
    lambda s: s.replace('2026-09-28','2027-09-28'),
    lambda s: '\n'.join(s.splitlines()[:30]+s.splitlines()[31:]),
])
def test_reject_wrong_or_corrupt_history(change):
    with pytest.raises(ValueError):
        read_bars(change(bars_csv()), CLOCK)


def test_spx_ui_isolated_from_gold_and_calculator_works():
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'))
    with patch('urllib.request.urlopen', side_effect=AssertionError('No network expected')):
        app.run(timeout=20)
        app.selectbox[0].select('SPX — بحث وخيارات إرشادية').run(timeout=20)
        assert not app.exception
        assert any('الدخول المباشر غير مفعّل' in w.value for w in app.warning)
        assert app.metric[0].value == '$500.00'
        app.number_input(key='spx_qty').set_value(2).run(timeout=20)
        assert app.metric[0].value == '$1,000.00'
        assert not any('محفظتي' in x.value for x in app.subheader)
        assert not any("class='grid'" in x.value for x in app.markdown)
