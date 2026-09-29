from test_paper_journal import ns


def test_live_tick_replaces_untimed_rest_without_borrowing_bid_ask():
    rest = dict(ok=True, last=4000, bid=3999, ask=4001, updated_at=None, market_open=True)
    tick = dict(price=4100, timestamp=999, received=1000)
    q, confirmation = ns['select_gold_sources'](rest, dict(ok=False, configured=False), tick, 1000)
    assert q['last'] == 4100 and q['updated_at'] == 999
    assert q['bid'] is None and q['ask'] is None
    assert not confirmation['ok']
    assert not ns['entry_gate'](q, [dict(ts=700)], 1000)[0]


def test_stale_and_future_ticks_are_not_retimestamped():
    rest = dict(ok=True, last=4000, updated_at=None)
    for stamp in [990, 1001]:
        q, _ = ns['select_gold_sources'](rest, dict(configured=False), dict(price=4100, timestamp=stamp), 1000)
        assert q == rest


def test_newer_rest_quote_is_preserved():
    rest = dict(ok=True, last=4000, updated_at=1000, bid=3999, ask=4001)
    q, _ = ns['select_gold_sources'](rest, dict(configured=False), dict(price=4100, timestamp=999), 1000)
    assert q == rest


def test_goldapi_retains_its_own_timestamp_and_stream_is_confirmation():
    gold = dict(ok=True, configured=True, price=4100, bid=4099, ask=4101, updated_at=998)
    q, confirmation = ns['select_gold_sources'](dict(market_open=True), gold, dict(price=4100, timestamp=999), 1000)
    assert q['updated_at'] == 998 and q['bid'] == 4099
    assert confirmation['updated_at'] == 999


def test_readiness_distinguishes_missing_bid_ask_from_staleness():
    q = dict(ok=True, last=4100, updated_at=999, market_open=True, bid=None, ask=None)
    reasons = ns['xau_readiness_issues'](q, [dict(ts=700)], dict(ok=False, reason='يلزم مصدر ثانٍ'), 1000)
    assert len(reasons) == 2
    assert 'Bid/Ask' in reasons[0]
    assert 'التأكيد المستقل' in reasons[1]
