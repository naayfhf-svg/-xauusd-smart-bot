import requests
import pytest

from capital_demo import CapitalDemoClient, CapitalDemoError, DEMO_BASE_URL


class Response:
    def __init__(self, payload=None, *, headers=None, status=200):
        self.payload = payload
        self.headers = headers or {}
        self.status_code = status
        self.text = "provider-body-with-secret-password"

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def close(self):
        pass


def auth():
    return Response(headers={"CST": "private-cst", "X-SECURITY-TOKEN": "private-session-token"})


def market(bid=3000, offer=3001):
    return Response({"instrument": {"epic": "GOLD"}, "snapshot": {
        "bid": bid, "offer": offer, "updateTime": "13:14:15",
        "updateTimeUTC": "2026-01-02T10:14:15", "marketStatus": "TRADEABLE"}})


def client(session):
    return CapitalDemoClient("private-key", "private-login", "private-password", session=session)


def test_fixed_demo_session_and_read_operations():
    session = Session(auth(), Response({"accounts": [{"accountId": "demo-id"}]}), market())
    c = client(session)
    c.authenticate()
    assert c.accounts() == [{"accountId": "demo-id"}]
    quote = c.gold_quote()
    assert quote["bid"] == 3000 and quote["offer"] == 3001
    assert quote["updateTimeUTC"] == "2026-01-02T10:14:15"
    assert quote["updateTime"] == "13:14:15"
    assert quote["timestamp"] is None  # Never manufacture a fresh timestamp.
    assert c.base_url == DEMO_BASE_URL
    assert [(method, url) for method, url, _ in session.calls] == [
        ("POST", DEMO_BASE_URL + "/api/v1/session"),
        ("GET", DEMO_BASE_URL + "/api/v1/accounts"),
        ("GET", DEMO_BASE_URL + "/api/v1/markets/GOLD"),
    ]
    post = session.calls[0][2]
    assert post["json"] == {"identifier": "private-login", "password": "private-password", "encryptedPassword": False}
    assert post["headers"]["X-CAP-API-KEY"] == "private-key"
    assert session.calls[1][2]["headers"]["CST"] == "private-cst"
    assert session.calls[1][2]["headers"]["X-SECURITY-TOKEN"] == "private-session-token"
    assert all(call[2]["allow_redirects"] is False for call in session.calls)
    assert all(call[2]["timeout"] == (5.0, 15.0) for call in session.calls)


@pytest.mark.parametrize("missing", ["", " ", None])
def test_missing_credentials_fail_without_network(missing):
    session = Session()
    with pytest.raises(CapitalDemoError):
        CapitalDemoClient("key", "login", missing, session=session)
    assert not session.calls


def test_from_env_uses_only_demo_names(monkeypatch):
    for name in ("CAPITAL_DEMO_API_KEY", "CAPITAL_DEMO_IDENTIFIER", "CAPITAL_DEMO_API_PASSWORD"):
        monkeypatch.setenv(name, "demo-value")
    monkeypatch.setenv("CAPITAL_BASE_URL", "https://arbitrary.invalid")
    c = CapitalDemoClient.from_env(session=Session())
    assert c.base_url == DEMO_BASE_URL
    assert "demo-value" not in repr(c)


@pytest.mark.parametrize("headers", [{}, {"CST": "cst"}, {"CST": "cst", "X-SECURITY-TOKEN": " "}])
def test_missing_auth_headers(headers):
    c = client(Session(Response(headers=headers)))
    with pytest.raises(CapitalDemoError, match="session headers"):
        c.authenticate()
    assert not c.authenticated


def test_auth_is_required_and_no_order_or_arbitrary_path_is_available():
    session = Session()
    c = client(session)
    with pytest.raises(CapitalDemoError, match="first"):
        c.accounts()
    for method, path in [("POST", "/api/v1/positions"), ("GET", "https://live.invalid"),
                         ("POST", "/api/v1/accounts/topUp"), ("DELETE", "/api/v1/session")]:
        with pytest.raises(CapitalDemoError, match="Unsupported"):
            c._request(method, path, headers={})
    assert not session.calls
    assert not hasattr(c, "submit_order")


@pytest.mark.parametrize("bid,offer", [(0, 1), (-1, 1), (2, 1), (float("nan"), 1),
                                      (1, float("inf")), (None, 1), (True, 1), (1, "bad")])
def test_malformed_prices_rejected(bid, offer):
    c = client(Session(auth(), market(bid, offer)))
    c.authenticate()
    with pytest.raises(CapitalDemoError, match="prices"):
        c.gold_quote()


@pytest.mark.parametrize("failure", [Response(status=403), Response(status=302),
    requests.RequestException("private-key private-password private-cst private-session-token")])
def test_provider_and_network_errors_do_not_expose_secrets(failure):
    c = client(Session(auth(), failure))
    c.authenticate()
    with pytest.raises(CapitalDemoError) as exc:
        c.accounts()
    rendered = str(exc.value)
    assert "private" not in rendered
    assert "provider-body" not in rendered
    assert "private" not in repr(c)
    if isinstance(failure, Response) and failure.status_code == 403:
        assert not c.authenticated


def test_malformed_json_is_sanitized():
    c = client(Session(auth(), Response(ValueError("private-password"))))
    c.authenticate()
    with pytest.raises(CapitalDemoError, match="JSON") as exc:
        c.accounts()
    assert "private" not in str(exc.value)


def test_gold_market_epic_must_match():
    c = client(Session(auth(), Response({"instrument": {"epic": "OTHER"}, "snapshot": {}})))
    c.authenticate()
    with pytest.raises(CapitalDemoError, match="market response"):
        c.gold_quote()
