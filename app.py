from __future__ import annotations

import os
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

from broker_bridge import BrokerBridge, BrokerBridgeError
from engine import PRESETS, InstrumentSpec, analyze_mtf, build_trade_plan, closed_bars, finite, live_risk_gate, normalize_ohlcv, self_test

VERSION = "3.0.0"
TZ = ZoneInfo("Asia/Riyadh")
DATA_URL = "https://api.twelvedata.com/time_series"
QUOTE_URL = "https://api.twelvedata.com/quote"

st.set_page_config(page_title="GOLD AI v3.0", page_icon="⚡", layout="wide", initial_sidebar_state="expanded")

st.markdown(
    """
<style>
:root{--bg:#070a10;--panel:#101722;--line:#253248;--gold:#d4af37;--text:#f7f8fb;--muted:#91a0b5;--green:#25c77a;--red:#ef5b67}
.stApp{background:var(--bg);color:var(--text)}
.block-container{max-width:1500px;padding:1.2rem 1rem 4rem}
.hero{padding:24px;border-radius:22px;border:1px solid #5b4a17;background:radial-gradient(circle at 85% 10%,rgba(212,175,55,.16),transparent 30%),var(--panel)}
.card{padding:16px;border-radius:16px;border:1px solid var(--line);background:var(--panel);margin-bottom:12px}
.kicker{font-size:.74rem;letter-spacing:.13em;color:var(--muted)}.gold{color:var(--gold)}.muted{color:var(--muted)}
.big{font-size:clamp(2.1rem,6vw,4.5rem);font-weight:900;line-height:1.05}.buy{color:var(--green)}.sell{color:var(--red)}
</style>
""",
    unsafe_allow_html=True,
)


def secret(name: str, default=None):
    try:
        return st.secrets[name]
    except Exception:
        return os.getenv(name, default)


def truthy(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def init_state() -> None:
    defaults = {
        "kill_switch": True,
        "auto_execute": False,
        "last_exec_candle": {},
        "orders": [],
        "refresh": False,
    }
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)


@st.cache_data(ttl=20, show_spinner=False)
def fetch_market(symbol: str, interval: str = "5min", outputsize: int = 5000) -> tuple[pd.DataFrame, str]:
    key = secret("TWELVE_DATA_API_KEY")
    if not key:
        return pd.DataFrame(), "TWELVE_DATA_API_KEY missing"
    params = {"symbol": symbol, "interval": interval, "outputsize": outputsize, "timezone": "UTC", "apikey": key}
    try:
        r = requests.get(DATA_URL, params=params, timeout=20)
        payload = r.json()
    except Exception as exc:
        return pd.DataFrame(), f"Data connection error: {exc}"
    if "values" not in payload:
        return pd.DataFrame(), str(payload.get("message") or payload)
    df = normalize_ohlcv(payload["values"])
    df = closed_bars(df, minutes=5)
    return df, "OK"


@st.cache_data(ttl=5, show_spinner=False)
def fetch_quote(symbol: str) -> dict:
    key = secret("TWELVE_DATA_API_KEY")
    if not key:
        return {"connected": False, "price": None, "error": "TWELVE_DATA_API_KEY missing"}
    try:
        r = requests.get(QUOTE_URL, params={"symbol": symbol, "apikey": key}, timeout=10)
        p = r.json()
    except Exception as exc:
        return {"connected": False, "price": None, "error": str(exc)}
    candidates = [p.get("close"), p.get("price"), p.get("last"), p.get("ask"), p.get("bid")]
    for value in candidates:
        if finite(value):
            return {"connected": True, "price": float(value), "raw": p}
    return {"connected": False, "price": None, "error": str(p.get("message") or "No live quote")}


def make_bridge() -> BrokerBridge | None:
    base = secret("BROKER_BRIDGE_URL")
    token = secret("BROKER_BRIDGE_TOKEN")
    if not base or not token:
        return None
    return BrokerBridge(
        base_url=str(base),
        token=str(token),
        account_path=str(secret("BROKER_ACCOUNT_PATH", "/account")),
        order_path=str(secret("BROKER_ORDER_PATH", "/orders")),
        cancel_all_path=str(secret("BROKER_CANCEL_ALL_PATH", "/orders/cancel-all")),
        hmac_secret=secret("BROKER_HMAC_SECRET"),
    )


def instrument_ui() -> InstrumentSpec:
    preset_name = st.sidebar.selectbox("Market preset", list(PRESETS), index=0)
    base = PRESETS[preset_name]
    with st.sidebar.expander("Instrument settings", expanded=False):
        symbol = st.text_input("Data / broker symbol", value=base.symbol)
        point_value = st.number_input("P&L value for 1.0 price move / qty", min_value=0.000001, value=float(base.point_value), format="%.6f")
        qty_step = st.number_input("Quantity step", min_value=0.000001, value=float(base.qty_step), format="%.6f")
        min_qty = st.number_input("Minimum quantity", min_value=0.0, value=float(base.min_qty), format="%.6f")
        max_qty = st.number_input("Maximum quantity", min_value=min_qty, value=float(base.max_qty), format="%.6f")
    return InstrumentSpec(base.asset_class, symbol.strip(), base.display_name, float(point_value), float(qty_step), float(min_qty), float(max_qty))


def account_panel(bridge: BrokerBridge | None) -> tuple[dict | None, str]:
    if not bridge:
        return None, "Broker bridge not configured"
    try:
        account = bridge.get_account()
        return account, "CONNECTED"
    except Exception as exc:
        return None, str(exc)


def submit_live(bridge: BrokerBridge, plan: dict, analysis: dict, instrument: InstrumentSpec, candle_id: str) -> dict:
    client_order_id = f"goldai-{instrument.asset_class.lower()}-{uuid.uuid4().hex[:12]}"
    payload = {
        "client_order_id": client_order_id,
        "strategy": "GOLD_AI_V3_MTF_B2",
        "version": VERSION,
        "symbol": instrument.symbol,
        "asset_class": instrument.asset_class,
        "side": plan["side"],
        "quantity": plan["qty"],
        "order_type": "MARKET",
        "stop_loss": round(plan["stop_loss"], 8),
        "take_profit_1": round(plan["take_profit_1"], 8),
        "take_profit_2": round(plan["take_profit_2"], 8),
        "entry_reference": round(plan["entry_reference"], 8),
        "risk_pct": plan["risk_pct"],
        "estimated_risk": round(plan["estimated_risk"], 2),
        "signal_strength": int(analysis["strength"]),
        "signal_candle": candle_id,
        "requested_at": datetime.now(TZ).isoformat(),
    }
    result = bridge.submit_order(payload)
    return {"request": payload, "response": result}


init_state()
self_test()

st.sidebar.markdown(f"## ⚡ GOLD AI v{VERSION}")
st.sidebar.caption("Multi-asset trading terminal • Gold • Stocks • Futures/Contracts")
instrument = instrument_ui()

st.sidebar.divider()
risk_pct = st.sidebar.number_input("Risk per trade %", min_value=0.05, max_value=2.0, value=0.50, step=0.05)
max_daily_loss_pct = st.sidebar.number_input("Daily loss limit %", min_value=0.5, max_value=10.0, value=2.0, step=0.5)
max_open_positions = st.sidebar.number_input("Max open positions", min_value=1, max_value=20, value=3, step=1)
max_order_risk_pct = st.sidebar.number_input("Hard max order risk %", min_value=0.05, max_value=2.0, value=1.0, step=0.05)
st.session_state.kill_switch = st.sidebar.toggle("KILL SWITCH", value=st.session_state.kill_switch, help="ON blocks every new live order")
st.session_state.refresh = st.sidebar.toggle("Auto refresh", value=st.session_state.refresh)
refresh_seconds = st.sidebar.slider("Refresh seconds", 10, 120, 30)

live_unlock = truthy(secret("LIVE_TRADING_ENABLED", "false"))
auto_unlock = truthy(secret("AUTO_EXECUTION_ALLOWED", "false"))
bridge = make_bridge()
account, broker_status = account_panel(bridge)

st.markdown(
    f"<div class='hero'><div class='kicker'>GOLD AI • MULTI-ASSET EXECUTION TERMINAL</div><div class='big gold'>{instrument.symbol}</div><p class='muted'>{instrument.asset_class} • Strategy + Risk + Broker Bridge • v{VERSION}</p></div>",
    unsafe_allow_html=True,
)

raw, data_status = fetch_market(instrument.symbol)
if raw.empty:
    st.error(f"Market data unavailable: {data_status}")
    st.stop()

analysis = analyze_mtf(raw)
quote = fetch_quote(instrument.symbol)
reference = float(quote["price"]) if quote.get("connected") else float(raw["close"].iloc[-1])
candle_id = str(raw["datetime"].iloc[-1])

cols = st.columns(6)
cols[0].metric("Price", f"{reference:,.4f}")
cols[1].metric("Signal", analysis["signal"])
cols[2].metric("Strength", f"{analysis['strength']}%")
cols[3].metric("Broker", broker_status if account else "BLOCKED")
cols[4].metric("Live unlock", "YES" if live_unlock else "NO")
cols[5].metric("Kill switch", "ON" if st.session_state.kill_switch else "OFF")

if account:
    a = st.columns(4)
    a[0].metric("Equity", f"{float(account.get('equity', 0)):,.2f}")
    a[1].metric("Day P&L", f"{float(account.get('day_pnl', 0)):,.2f}")
    a[2].metric("Open positions", int(account.get("open_positions", 0)))
    a[3].metric("Trading", "ENABLED" if account.get("trading_enabled", True) else "DISABLED")
else:
    st.warning("Live execution is blocked until the authenticated broker bridge returns real account state.")

st.subheader("Multi-timeframe engine")
tf_cols = st.columns(4)
for c, tf in zip(tf_cols, ("M5", "M15", "H1", "H4")):
    snap = analysis["snapshots"].get(tf)
    if snap:
        c.markdown(f"<div class='card'><div class='kicker'>{tf}</div><h3>{snap['trend']}</h3><div class='muted'>RSI {snap['rsi']:.1f} • ADX {snap['adx']:.1f}</div></div>", unsafe_allow_html=True)
    else:
        c.markdown(f"<div class='card'><div class='kicker'>{tf}</div><h3>WAIT</h3><div class='muted'>Insufficient data</div></div>", unsafe_allow_html=True)

signal_class = "buy" if analysis["signal"] == "BUY" else "sell" if analysis["signal"] == "SELL" else ""
st.markdown(f"<div class='card'><div class='kicker'>FINAL DECISION</div><div class='big {signal_class}'>{analysis['signal']}</div><p>{analysis['reason']}</p></div>", unsafe_allow_html=True)

st.line_chart(raw.tail(300).set_index("datetime")[["close"]], use_container_width=True)

plan = None
risk_ok = False
risk_reasons: list[str] = []
if analysis["signal"] in {"BUY", "SELL"} and account:
    m5 = analysis["snapshots"]["M5"]
    try:
        plan = build_trade_plan(
            analysis["signal"], reference, m5["atr"], float(account.get("equity", 0)), float(risk_pct), instrument
        )
        risk_ok, risk_reasons = live_risk_gate(account, plan, float(max_daily_loss_pct), int(max_open_positions), float(max_order_risk_pct))
    except Exception as exc:
        risk_reasons = [str(exc)]

st.subheader("Execution center")
if plan:
    p = st.columns(7)
    p[0].metric("Side", plan["side"])
    p[1].metric("Qty", f"{plan['qty']:,.6f}")
    p[2].metric("SL", f"{plan['stop_loss']:,.4f}")
    p[3].metric("TP1", f"{plan['take_profit_1']:,.4f}")
    p[4].metric("TP2", f"{plan['take_profit_2']:,.4f}")
    p[5].metric("Risk budget", f"{plan['risk_budget']:,.2f}")
    p[6].metric("Est. risk", f"{plan['estimated_risk']:,.2f}")
else:
    st.info("No executable BUY/SELL plan on the current closed candle.")

hard_blocks = []
if not live_unlock:
    hard_blocks.append("LIVE_TRADING_ENABLED is false")
if not bridge:
    hard_blocks.append("Broker bridge secrets are missing")
if not account:
    hard_blocks.append("Real broker account state is unavailable")
if st.session_state.kill_switch:
    hard_blocks.append("Kill switch is ON")
if not risk_ok and plan:
    hard_blocks.extend(risk_reasons)
if analysis["signal"] not in {"BUY", "SELL"}:
    hard_blocks.append("No live entry signal")

ready = len(hard_blocks) == 0 and plan is not None and bridge is not None
if hard_blocks:
    st.warning("Execution blocked: " + " • ".join(dict.fromkeys(hard_blocks)))
else:
    st.success("All live execution gates passed.")

manual_confirm = st.checkbox("I confirm this live order uses real money", value=False)
if st.button("EXECUTE LIVE ORDER", type="primary", use_container_width=True, disabled=not (ready and manual_confirm)):
    try:
        result = submit_live(bridge, plan, analysis, instrument, candle_id)
        st.session_state.orders.insert(0, result)
        st.success(f"Order submitted: {result['response']}")
    except BrokerBridgeError as exc:
        st.error(str(exc))

st.markdown("### Automatic live execution")
if not auto_unlock:
    st.caption("AUTO_EXECUTION_ALLOWED=false — automatic order submission is hard-locked in server secrets.")
    st.session_state.auto_execute = False
else:
    st.session_state.auto_execute = st.toggle("Enable automatic live execution", value=st.session_state.auto_execute)

if auto_unlock and st.session_state.auto_execute and ready:
    last = st.session_state.last_exec_candle.get(instrument.symbol)
    if last != candle_id:
        try:
            result = submit_live(bridge, plan, analysis, instrument, candle_id)
            st.session_state.last_exec_candle[instrument.symbol] = candle_id
            st.session_state.orders.insert(0, result)
            st.success(f"AUTO order submitted for closed candle {candle_id}")
        except BrokerBridgeError as exc:
            st.error(f"AUTO execution failed: {exc}")

if bridge and account and st.button("EMERGENCY: CANCEL ALL OPEN ORDERS", use_container_width=True):
    try:
        st.write(bridge.cancel_all())
    except BrokerBridgeError as exc:
        st.error(str(exc))

st.subheader("Order audit log")
if st.session_state.orders:
    rows = []
    for item in st.session_state.orders[:100]:
        req = item.get("request", {})
        resp = item.get("response", {})
        rows.append({
            "time": req.get("requested_at"),
            "client_order_id": req.get("client_order_id"),
            "symbol": req.get("symbol"),
            "side": req.get("side"),
            "qty": req.get("quantity"),
            "risk_pct": req.get("risk_pct"),
            "broker_status": resp.get("status") or resp.get("state") or "submitted",
            "broker_order_id": resp.get("order_id") or resp.get("id"),
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
else:
    st.caption("No orders submitted from this session.")

st.caption(
    "Production safeguard: this UI will not submit a live order unless market data is valid, a real broker account state is returned, live trading is unlocked in server secrets, risk gates pass, and the kill switch is OFF."
)

if st.session_state.refresh:
    time.sleep(refresh_seconds)
    st.rerun()
