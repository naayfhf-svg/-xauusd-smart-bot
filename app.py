from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import streamlit as st

# ============================================================
# GOLD AI v3.0 — X10 Build
# Single-file multi-asset Streamlit terminal.
# Gold • Stocks • Futures/Contracts • Paper • Optional live bridge
# ============================================================

VERSION = "3.0.0-x10"
TZ = ZoneInfo("Asia/Riyadh")
DATA_URL = "https://api.twelvedata.com/time_series"
QUOTE_URL = "https://api.twelvedata.com/quote"
TIMEFRAMES = ("M5", "M15", "H1", "H4")

st.set_page_config(
    page_title="GOLD AI X10",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ----------------------------- UI -----------------------------
st.markdown(
    """
<style>
:root{
  --bg:#070a10;--panel:#0e1622;--panel2:#111b2a;--line:#26364f;
  --gold:#d4af37;--text:#f5f7fb;--muted:#8fa2ba;--green:#24c97d;
  --red:#f05d68;--blue:#62a8ff;
}
.stApp{background:var(--bg);color:var(--text)}
.block-container{max-width:1500px;padding:1rem 1rem 4rem}
.hero{border:1px solid #5f4d18;border-radius:22px;padding:24px;background:
 radial-gradient(circle at 90% 10%,rgba(212,175,55,.15),transparent 30%),var(--panel)}
.card{border:1px solid var(--line);border-radius:16px;padding:16px;background:var(--panel);margin-bottom:12px}
.kicker{font-size:.72rem;letter-spacing:.13em;color:var(--muted)}
.big{font-size:clamp(2.1rem,6vw,4.5rem);font-weight:900;line-height:1.05}
.gold{color:var(--gold)}.buy{color:var(--green)}.sell{color:var(--red)}.muted{color:var(--muted)}
.badge{display:inline-block;padding:4px 9px;border:1px solid var(--line);border-radius:999px;font-size:.78rem;color:var(--muted)}
div[data-testid="stMetric"]{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:8px}
</style>
""",
    unsafe_allow_html=True,
)

# --------------------------- config ---------------------------
def secret(name: str, default: Any = None) -> Any:
    try:
        return st.secrets[name]
    except Exception:
        return os.getenv(name, default)


def truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class InstrumentSpec:
    label: str
    asset_class: str
    symbol: str
    point_value: float
    qty_step: float
    min_qty: float
    max_qty: float


PRESETS: dict[str, InstrumentSpec] = {
    "Gold — XAU/USD": InstrumentSpec("Gold — XAU/USD", "GOLD", "XAU/USD", 1.0, 0.01, 0.01, 100.0),
    "Apple — AAPL": InstrumentSpec("Apple — AAPL", "STOCK", "AAPL", 1.0, 1.0, 1.0, 100000.0),
    "Microsoft — MSFT": InstrumentSpec("Microsoft — MSFT", "STOCK", "MSFT", 1.0, 1.0, 1.0, 100000.0),
    "SPY ETF": InstrumentSpec("SPY ETF", "STOCK", "SPY", 1.0, 1.0, 1.0, 100000.0),
    "Nasdaq Futures — NQ": InstrumentSpec("Nasdaq Futures — NQ", "FUTURES", "NQ", 20.0, 1.0, 1.0, 1000.0),
    "S&P Futures — ES": InstrumentSpec("S&P Futures — ES", "FUTURES", "ES", 50.0, 1.0, 1.0, 1000.0),
}

# ------------------------- utilities --------------------------
def now_utc() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def now_riyadh() -> datetime:
    return datetime.now(TZ)


def finite(value: Any) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except Exception:
        return False


def fmt(value: Any, digits: int = 2) -> str:
    return f"{float(value):,.{digits}f}" if finite(value) else "—"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def floor_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    precision = max(0, int(round(-math.log10(step))) + 2) if step < 1 else 6
    return round(math.floor((value + 1e-12) / step) * step, precision)


def init_state() -> None:
    defaults = {
        "kill_switch": True,
        "auto_refresh": False,
        "auto_paper": False,
        "auto_live": False,
        "paper_balance": 100_000.0,
        "paper_position": None,
        "paper_history": [],
        "orders": [],
        "decisions": [],
        "last_signal_candle": {},
        "last_auto_paper_candle": {},
        "last_auto_live_candle": {},
        "paper_day": now_riyadh().date().isoformat(),
        "paper_day_start_balance": 100_000.0,
        "paper_trades_today": 0,
        "backtest": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)

    today = now_riyadh().date().isoformat()
    if st.session_state.paper_day != today:
        st.session_state.paper_day = today
        st.session_state.paper_day_start_balance = float(st.session_state.paper_balance)
        st.session_state.paper_trades_today = 0


init_state()

# -------------------------- data feed -------------------------
def normalize_ohlcv(values: Any) -> pd.DataFrame:
    if not isinstance(values, list) or not values:
        return pd.DataFrame()
    df = pd.DataFrame(values)
    if "datetime" not in df.columns:
        return pd.DataFrame()
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    required = ["datetime", "open", "high", "low", "close"]
    df = df.dropna(subset=required)
    df = df.drop_duplicates("datetime").sort_values("datetime").reset_index(drop=True)
    return df


def closed_m5(frame: pd.DataFrame, as_of: pd.Timestamp | None = None) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    cutoff = now_utc() if as_of is None else pd.Timestamp(as_of)
    if cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize("UTC")
    else:
        cutoff = cutoff.tz_convert("UTC")
    boundary = cutoff.floor("5min")
    return frame[frame["datetime"] + pd.Timedelta(minutes=5) <= boundary].copy().reset_index(drop=True)


def resample_closed(frame: pd.DataFrame, rule: str, as_of: pd.Timestamp | None = None) -> pd.DataFrame:
    frame = closed_m5(frame, as_of)
    if frame.empty:
        return frame.copy()
    out = (
        frame.set_index("datetime")[["open", "high", "low", "close"]]
        .resample(rule, label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
        .reset_index()
    )
    cutoff = now_utc() if as_of is None else pd.Timestamp(as_of)
    if cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize("UTC")
    else:
        cutoff = cutoff.tz_convert("UTC")
    return out[out["datetime"] + pd.Timedelta(rule) <= cutoff].reset_index(drop=True)


@st.cache_data(ttl=30, show_spinner=False)
def fetch_market(symbol: str, outputsize: int = 5000) -> tuple[pd.DataFrame, str]:
    key = secret("TWELVE_DATA_API_KEY")
    if not key:
        return pd.DataFrame(), "أضف TWELVE_DATA_API_KEY في Secrets"
    params = {
        "symbol": symbol,
        "interval": "5min",
        "outputsize": int(outputsize),
        "timezone": "UTC",
        "apikey": key,
    }
    try:
        response = requests.get(DATA_URL, params=params, timeout=20)
        payload = response.json()
    except Exception as exc:
        return pd.DataFrame(), f"خطأ اتصال بمصدر البيانات: {exc}"
    if "values" not in payload:
        return pd.DataFrame(), str(payload.get("message") or payload)
    frame = closed_m5(normalize_ohlcv(payload["values"]))
    if frame.empty:
        return pd.DataFrame(), "تم الاتصال بالمصدر لكن لم تصل شموع صالحة"
    return frame, "OK"


@st.cache_data(ttl=8, show_spinner=False)
def fetch_quote(symbol: str) -> dict[str, Any]:
    key = secret("TWELVE_DATA_API_KEY")
    if not key:
        return {"connected": False, "error": "TWELVE_DATA_API_KEY missing"}
    try:
        response = requests.get(QUOTE_URL, params={"symbol": symbol, "apikey": key}, timeout=10)
        payload = response.json()
    except Exception as exc:
        return {"connected": False, "error": str(exc)}

    bid = payload.get("bid")
    ask = payload.get("ask")
    last = payload.get("close", payload.get("price", payload.get("last")))
    if finite(bid) and finite(ask) and float(ask) >= float(bid):
        return {
            "connected": True,
            "bid": float(bid),
            "ask": float(ask),
            "last": float(last) if finite(last) else (float(bid) + float(ask)) / 2,
            "spread": float(ask) - float(bid),
            "raw": payload,
        }
    if finite(last):
        return {"connected": True, "bid": None, "ask": None, "last": float(last), "spread": None, "raw": payload}
    return {"connected": False, "error": str(payload.get("message") or "No quote")}


def data_quality(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"ok": False, "label": "لا توجد بيانات"}
    duplicate_count = int(frame["datetime"].duplicated().sum())
    monotonic = bool(frame["datetime"].is_monotonic_increasing)
    age_min = max(0.0, (now_utc() - frame["datetime"].iloc[-1]).total_seconds() / 60)
    return {
        "ok": monotonic and duplicate_count == 0,
        "age_min": age_min,
        "duplicates": duplicate_count,
        "label": f"آخر شمعة منذ {age_min:.0f} دقيقة • تكرار {duplicate_count}",
    }

# ------------------------- indicators -------------------------
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = losses.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    pc = frame["close"].shift(1)
    tr = pd.concat(
        [frame["high"] - frame["low"], (frame["high"] - pc).abs(), (frame["low"] - pc).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def macd(series: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    line = ema(series, 12) - ema(series, 26)
    signal = line.ewm(span=9, adjust=False, min_periods=9).mean()
    return line, signal, line - signal


def adx(frame: pd.DataFrame, period: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    up = frame["high"].diff()
    down = -frame["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=frame.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=frame.index)
    pc = frame["close"].shift(1)
    tr = pd.concat(
        [frame["high"] - frame["low"], (frame["high"] - pc).abs(), (frame["low"] - pc).abs()],
        axis=1,
    ).max(axis=1)
    smoothed_tr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / smoothed_tr.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / smoothed_tr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_line = dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return adx_line, plus_di, minus_di


def add_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["ema20"] = ema(out["close"], 20)
    out["ema50"] = ema(out["close"], 50)
    out["ema100"] = ema(out["close"], 100)
    out["rsi"] = rsi(out["close"])
    out["atr"] = atr(out)
    out["macd"], out["macd_signal"], out["macd_hist"] = macd(out["close"])
    out["momentum"] = out["close"].pct_change(5) * 100
    out["adx"], out["plus_di"], out["minus_di"] = adx(out)
    return out


def trend(row: pd.Series) -> str:
    # EMA20/50 are the primary trend structure. EMA100, when available,
    # acts as an extra confirmation without making higher timeframes unusable.
    ema100_ok_up = (not finite(row.get("ema100"))) or row["close"] > row["ema100"]
    ema100_ok_down = (not finite(row.get("ema100"))) or row["close"] < row["ema100"]
    if row["close"] > row["ema20"] > row["ema50"] and ema100_ok_up:
        return "UP"
    if row["close"] < row["ema20"] < row["ema50"] and ema100_ok_down:
        return "DOWN"
    return "MIXED"


def snapshot(frame: pd.DataFrame) -> dict[str, Any] | None:
    if len(frame) < 65:
        return None
    calc = add_indicators(frame)
    required = ["ema20", "ema50", "rsi", "atr", "macd_hist", "momentum", "adx"]
    calc = calc.dropna(subset=required).reset_index(drop=True)
    if calc.empty:
        return None
    row = calc.iloc[-1]
    return {
        "trend": trend(row),
        "rsi": float(row["rsi"]),
        "adx": float(row["adx"]),
        "atr": float(row["atr"]),
        "momentum": float(row["momentum"]),
        "macd_hist": float(row["macd_hist"]),
        "close": float(row["close"]),
        "candle": row["datetime"],
        "frame": calc,
    }


def b2_signal(frame: pd.DataFrame, lookback: int = 20, retest_atr: float = 0.35) -> dict[str, Any]:
    if len(frame) < lookback + 3:
        return {"valid": False, "side": None, "level": None, "breakout": False, "retest": False}
    current = frame.iloc[-1]
    breakout = frame.iloc[-2]
    history = frame.iloc[-(lookback + 2):-2]
    resistance = float(history["high"].max())
    support = float(history["low"].min())
    current_atr = float(current["atr"])
    if not finite(current_atr) or current_atr <= 0:
        return {"valid": False, "side": None, "level": None, "breakout": False, "retest": False}

    bullish_break = float(breakout["close"]) > resistance
    bearish_break = float(breakout["close"]) < support
    bullish_retest = bullish_break and float(current["low"]) <= resistance + current_atr * retest_atr and float(current["close"]) > resistance
    bearish_retest = bearish_break and float(current["high"]) >= support - current_atr * retest_atr and float(current["close"]) < support
    if bullish_retest:
        return {"valid": True, "side": "BUY", "level": resistance, "breakout": True, "retest": True}
    if bearish_retest:
        return {"valid": True, "side": "SELL", "level": support, "breakout": True, "retest": True}
    return {
        "valid": False,
        "side": None,
        "level": resistance if bullish_break else support if bearish_break else None,
        "breakout": bool(bullish_break or bearish_break),
        "retest": False,
    }


def analyze_mtf(raw: pd.DataFrame) -> dict[str, Any]:
    frames = {
        "M5": raw,
        "M15": resample_closed(raw, "15min"),
        "H1": resample_closed(raw, "1h"),
        "H4": resample_closed(raw, "4h"),
    }
    snaps = {name: snapshot(frame) for name, frame in frames.items()}
    if any(v is None for v in snaps.values()):
        return {"signal": "WAIT", "strength": 0, "reason": "بيانات غير كافية لكل الأطر", "snapshots": snaps, "b2": {}}

    m5 = snaps["M5"]
    b2 = b2_signal(m5["frame"])
    trends = [snaps[x]["trend"] for x in TIMEFRAMES]

    # Weighted score keeps the engine useful while still requiring strong alignment.
    buy_score = 0
    sell_score = 0
    buy_score += 25 if trends[3] == "UP" else 0
    sell_score += 25 if trends[3] == "DOWN" else 0
    buy_score += 20 if trends[2] == "UP" else 0
    sell_score += 20 if trends[2] == "DOWN" else 0
    buy_score += 15 if trends[1] == "UP" else 0
    sell_score += 15 if trends[1] == "DOWN" else 0
    buy_score += 10 if trends[0] == "UP" else 0
    sell_score += 10 if trends[0] == "DOWN" else 0
    buy_score += 10 if m5["rsi"] >= 52 else 0
    sell_score += 10 if m5["rsi"] <= 48 else 0
    buy_score += 8 if m5["momentum"] > 0 and m5["macd_hist"] > 0 else 0
    sell_score += 8 if m5["momentum"] < 0 and m5["macd_hist"] < 0 else 0
    buy_score += 5 if m5["adx"] >= 20 else 0
    sell_score += 5 if m5["adx"] >= 20 else 0
    buy_score += 7 if b2.get("valid") and b2.get("side") == "BUY" else 0
    sell_score += 7 if b2.get("valid") and b2.get("side") == "SELL" else 0

    if buy_score >= 75 and buy_score >= sell_score + 20:
        return {"signal": "BUY", "strength": min(100, buy_score), "reason": "اتجاه متعدد الأطر + زخم صاعد متوافق", "snapshots": snaps, "b2": b2, "buy_score": buy_score, "sell_score": sell_score}
    if sell_score >= 75 and sell_score >= buy_score + 20:
        return {"signal": "SELL", "strength": min(100, sell_score), "reason": "اتجاه متعدد الأطر + زخم هابط متوافق", "snapshots": snaps, "b2": b2, "buy_score": buy_score, "sell_score": sell_score}
    return {"signal": "WAIT", "strength": max(buy_score, sell_score), "reason": "شروط الدخول غير مكتملة", "snapshots": snaps, "b2": b2, "buy_score": buy_score, "sell_score": sell_score}

# ----------------------- risk / trade plan --------------------
def build_trade_plan(
    signal: str,
    entry: float,
    atr_value: float,
    equity: float,
    risk_pct: float,
    spec: InstrumentSpec,
    stop_atr: float = 1.5,
    tp1_r: float = 1.0,
    tp2_r: float = 2.2,
) -> dict[str, Any]:
    if signal not in {"BUY", "SELL"}:
        raise ValueError("Signal must be BUY or SELL")
    if equity <= 0 or risk_pct <= 0 or atr_value <= 0 or spec.point_value <= 0:
        raise ValueError("Invalid risk inputs")

    stop_distance = max(atr_value * stop_atr, 1e-9)
    risk_budget = equity * risk_pct / 100.0
    raw_qty = risk_budget / (stop_distance * spec.point_value)
    qty = floor_step(raw_qty, spec.qty_step)
    qty = clamp(qty, spec.min_qty, spec.max_qty)

    if signal == "BUY":
        stop = entry - stop_distance
        tp1 = entry + stop_distance * tp1_r
        tp2 = entry + stop_distance * tp2_r
    else:
        stop = entry + stop_distance
        tp1 = entry - stop_distance * tp1_r
        tp2 = entry - stop_distance * tp2_r

    estimated_risk = abs(entry - stop) * spec.point_value * qty
    return {
        "side": signal,
        "symbol": spec.symbol,
        "qty": float(qty),
        "entry_reference": float(entry),
        "stop_loss": float(stop),
        "take_profit_1": float(tp1),
        "take_profit_2": float(tp2),
        "risk_budget": float(risk_budget),
        "estimated_risk": float(estimated_risk),
        "risk_pct": float(risk_pct),
        "point_value": float(spec.point_value),
        "asset_class": spec.asset_class,
    }


def risk_gate(
    equity: float,
    day_pnl: float,
    open_positions: int,
    plan: dict[str, Any],
    max_daily_loss_pct: float,
    max_open_positions: int,
    max_order_risk_pct: float,
    trading_enabled: bool = True,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if equity <= 0:
        reasons.append("Equity غير صالح")
    if not trading_enabled:
        reasons.append("الوسيط عطّل التداول")
    if open_positions >= max_open_positions:
        reasons.append("وصلت الحد الأقصى للمراكز المفتوحة")
    if equity > 0 and day_pnl <= -(equity * max_daily_loss_pct / 100.0):
        reasons.append("وصلت حد الخسارة اليومية")
    if float(plan.get("risk_pct", 999)) > max_order_risk_pct:
        reasons.append("مخاطرة الصفقة أعلى من الحد")
    if float(plan.get("qty", 0)) <= 0:
        reasons.append("حجم الصفقة غير صالح")
    return not reasons, reasons

# ------------------------- paper broker -----------------------
def paper_mark_price(position: dict[str, Any], quote: dict[str, Any], fallback: float) -> float:
    if quote.get("connected"):
        if position["side"] == "BUY" and finite(quote.get("bid")):
            return float(quote["bid"])
        if position["side"] == "SELL" and finite(quote.get("ask")):
            return float(quote["ask"])
        if finite(quote.get("last")):
            return float(quote["last"])
    return fallback


def paper_unrealized_r(position: dict[str, Any], price: float) -> float:
    distance = abs(position["entry"] - position["stop_initial"])
    if distance <= 0:
        return 0.0
    direction = 1 if position["side"] == "BUY" else -1
    return direction * (price - position["entry"]) / distance


def open_paper(plan: dict[str, Any], instrument: InstrumentSpec) -> None:
    if st.session_state.paper_position:
        return
    st.session_state.paper_position = {
        "id": uuid.uuid4().hex[:12],
        "opened_at": now_riyadh().isoformat(),
        "symbol": instrument.symbol,
        "asset_class": instrument.asset_class,
        "side": plan["side"],
        "qty": plan["qty"],
        "entry": plan["entry_reference"],
        "stop": plan["stop_loss"],
        "stop_initial": plan["stop_loss"],
        "tp1": plan["take_profit_1"],
        "tp2": plan["take_profit_2"],
        "risk_money": plan["estimated_risk"],
        "point_value": instrument.point_value,
        "tp1_hit": False,
        "remaining": 1.0,
        "realized_r": 0.0,
    }
    st.session_state.paper_trades_today += 1


def close_paper(position: dict[str, Any], exit_price: float, reason: str, r_value: float) -> None:
    pnl = r_value * position["risk_money"]
    st.session_state.paper_balance += pnl
    row = {
        "id": position["id"],
        "symbol": position["symbol"],
        "side": position["side"],
        "opened_at": position["opened_at"],
        "closed_at": now_riyadh().isoformat(),
        "entry": position["entry"],
        "exit": exit_price,
        "R": round(r_value, 4),
        "PnL": round(pnl, 2),
        "reason": reason,
    }
    st.session_state.paper_history.insert(0, row)
    st.session_state.paper_position = None


def manage_paper(price: float) -> None:
    p = st.session_state.paper_position
    if not p:
        return
    d = abs(p["entry"] - p["stop_initial"])
    if d <= 0:
        return

    if p["side"] == "BUY":
        if price <= p["stop"]:
            r = p["realized_r"] + p["remaining"] * ((p["stop"] - p["entry"]) / d)
            close_paper(p, p["stop"], "STOP", r)
            return
        if not p["tp1_hit"] and price >= p["tp1"]:
            p["tp1_hit"] = True
            p["remaining"] = 0.5
            p["realized_r"] = 0.5
            p["stop"] = p["entry"]
        if p["tp1_hit"] and price >= p["tp2"]:
            r = p["realized_r"] + 0.5 * ((p["tp2"] - p["entry"]) / d)
            close_paper(p, p["tp2"], "TP2", r)
            return
    else:
        if price >= p["stop"]:
            r = p["realized_r"] + p["remaining"] * ((p["entry"] - p["stop"]) / d)
            close_paper(p, p["stop"], "STOP", r)
            return
        if not p["tp1_hit"] and price <= p["tp1"]:
            p["tp1_hit"] = True
            p["remaining"] = 0.5
            p["realized_r"] = 0.5
            p["stop"] = p["entry"]
        if p["tp1_hit"] and price <= p["tp2"]:
            r = p["realized_r"] + 0.5 * ((p["entry"] - p["tp2"]) / d)
            close_paper(p, p["tp2"], "TP2", r)
            return

# ------------------------- live bridge ------------------------
class BrokerBridgeError(RuntimeError):
    pass


@dataclass
class BrokerBridge:
    base_url: str
    token: str
    account_path: str = "/account"
    order_path: str = "/orders"
    cancel_all_path: str = "/orders/cancel-all"
    hmac_secret: str | None = None
    timeout: int = 15

    def _url(self, path: str) -> str:
        return urljoin(self.base_url.rstrip("/") + "/", path.lstrip("/"))

    def _headers(self, body: bytes = b"") -> dict[str, str]:
        ts = str(int(time.time()))
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-GoldAI-Timestamp": ts,
        }
        if self.hmac_secret:
            signature = hmac.new(
                str(self.hmac_secret).encode(),
                ts.encode() + b"." + body,
                hashlib.sha256,
            ).hexdigest()
            headers["X-GoldAI-Signature"] = signature
        return headers

    def get_account(self) -> dict[str, Any]:
        try:
            response = requests.get(self._url(self.account_path), headers=self._headers(), timeout=self.timeout)
        except Exception as exc:
            raise BrokerBridgeError(f"تعذر الاتصال بالوسيط: {exc}") from exc
        if response.status_code >= 400:
            raise BrokerBridgeError(f"Broker HTTP {response.status_code}: {response.text[:250]}")
        try:
            payload = response.json()
        except Exception as exc:
            raise BrokerBridgeError("رد /account ليس JSON") from exc
        if not isinstance(payload, dict):
            raise BrokerBridgeError("رد /account غير صالح")
        return payload

    def submit_order(self, order: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(order, separators=(",", ":"), sort_keys=True).encode()
        try:
            response = requests.post(self._url(self.order_path), data=body, headers=self._headers(body), timeout=self.timeout)
        except Exception as exc:
            raise BrokerBridgeError(f"فشل إرسال الأمر: {exc}") from exc
        if response.status_code >= 400:
            raise BrokerBridgeError(f"رفض الوسيط الأمر HTTP {response.status_code}: {response.text[:500]}")
        try:
            payload = response.json()
        except Exception as exc:
            raise BrokerBridgeError("رد الأمر ليس JSON") from exc
        if not isinstance(payload, dict):
            raise BrokerBridgeError("رد الأمر غير صالح")
        return payload

    def cancel_all(self) -> dict[str, Any]:
        body = b"{}"
        try:
            response = requests.post(self._url(self.cancel_all_path), data=body, headers=self._headers(body), timeout=self.timeout)
        except Exception as exc:
            raise BrokerBridgeError(f"فشل إلغاء الأوامر: {exc}") from exc
        if response.status_code >= 400:
            raise BrokerBridgeError(f"فشل الإلغاء HTTP {response.status_code}: {response.text[:500]}")
        try:
            return response.json()
        except Exception:
            return {"ok": True, "raw": response.text[:300]}


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


def live_payload(plan: dict[str, Any], analysis: dict[str, Any], instrument: InstrumentSpec, candle_id: str) -> dict[str, Any]:
    return {
        "client_order_id": f"goldai-{instrument.asset_class.lower()}-{uuid.uuid4().hex[:12]}",
        "strategy": "GOLD_AI_V3_X10_MTF",
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
        "requested_at": now_riyadh().isoformat(),
    }

# --------------------------- backtest -------------------------
def backtest_quick(raw: pd.DataFrame, spec: InstrumentSpec, risk_pct: float = 0.5) -> tuple[pd.DataFrame, dict[str, Any]]:
    # Simple walk-through using M5 only, with no future data used for a decision.
    # This is diagnostic, not a profitability guarantee.
    if len(raw) < 500:
        return pd.DataFrame(), {"trades": 0, "warning": "بيانات غير كافية للاختبار"}

    calc = add_indicators(raw).dropna().reset_index(drop=True)
    trades: list[dict[str, Any]] = []
    position: dict[str, Any] | None = None
    equity = 100_000.0

    for i in range(120, len(calc) - 1):
        history = calc.iloc[: i + 1].copy()
        row = history.iloc[-1]
        b2 = b2_signal(history)
        tr = trend(row)
        side = None
        if tr == "UP" and row["rsi"] >= 52 and row["macd_hist"] > 0 and row["adx"] >= 20 and b2.get("side") == "BUY":
            side = "BUY"
        elif tr == "DOWN" and row["rsi"] <= 48 and row["macd_hist"] < 0 and row["adx"] >= 20 and b2.get("side") == "SELL":
            side = "SELL"

        nxt = calc.iloc[i + 1]
        if position is None and side:
            plan = build_trade_plan(side, float(nxt["open"]), float(row["atr"]), equity, risk_pct, spec)
            position = {
                "side": side,
                "entry": plan["entry_reference"],
                "stop": plan["stop_loss"],
                "tp": plan["take_profit_2"],
                "risk": plan["estimated_risk"],
                "opened": nxt["datetime"],
            }
            continue

        if position is not None:
            hi = float(nxt["high"])
            lo = float(nxt["low"])
            side = position["side"]
            stop_hit = lo <= position["stop"] if side == "BUY" else hi >= position["stop"]
            tp_hit = hi >= position["tp"] if side == "BUY" else lo <= position["tp"]
            if stop_hit or tp_hit:
                # Conservative rule: if both touched in same candle, count stop first.
                if stop_hit:
                    r = -1.0
                    exit_price = position["stop"]
                    reason = "STOP"
                else:
                    r = 2.2
                    exit_price = position["tp"]
                    reason = "TP2"
                pnl = r * position["risk"]
                equity += pnl
                trades.append({
                    "opened": position["opened"], "closed": nxt["datetime"], "side": side,
                    "entry": position["entry"], "exit": exit_price, "R": r, "PnL": pnl,
                    "equity": equity, "reason": reason,
                })
                position = None

    if not trades:
        return pd.DataFrame(), {"trades": 0, "warning": "لم ينتج الاختبار صفقات"}
    df = pd.DataFrame(trades)
    gross_win = df.loc[df["PnL"] > 0, "PnL"].sum()
    gross_loss = abs(df.loc[df["PnL"] < 0, "PnL"].sum())
    pf = gross_win / gross_loss if gross_loss else math.inf
    equity_curve = df["equity"]
    dd = equity_curve.cummax() - equity_curve
    stats = {
        "trades": len(df),
        "win_rate": float((df["PnL"] > 0).mean() * 100),
        "profit_factor": float(pf),
        "net_pnl": float(df["PnL"].sum()),
        "max_dd": float(dd.max()),
        "ending_equity": float(equity_curve.iloc[-1]),
    }
    return df, stats

# --------------------------- self test ------------------------
def self_test() -> tuple[bool, str]:
    try:
        idx = pd.date_range("2026-01-01", periods=220, freq="5min", tz="UTC")
        close = np.linspace(100, 130, len(idx))
        df = pd.DataFrame({
            "datetime": idx,
            "open": close - 0.1,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
        })
        calc = add_indicators(df)
        assert len(calc) == len(df)
        spec = PRESETS["Gold — XAU/USD"]
        plan = build_trade_plan("BUY", 2000.0, 10.0, 100000.0, 0.5, spec)
        assert plan["qty"] > 0 and plan["stop_loss"] < plan["entry_reference"] < plan["take_profit_1"]
        ok, reasons = risk_gate(100000.0, 0.0, 0, plan, 2.0, 3, 1.0)
        assert ok and not reasons
        return True, "OK"
    except Exception as exc:
        return False, str(exc)

# ----------------------------- app ----------------------------
engine_ok, engine_error = self_test()
if not engine_ok:
    st.error(f"فشل اختبار المحرك الداخلي: {engine_error}")
    st.stop()

st.sidebar.markdown(f"## ⚡ GOLD AI X10")
st.sidebar.caption(f"v{VERSION} • Gold • Stocks • Futures/Contracts")

preset_name = st.sidebar.selectbox("السوق", list(PRESETS.keys()), index=0)
base_spec = PRESETS[preset_name]
with st.sidebar.expander("إعدادات الأصل", expanded=False):
    custom_symbol = st.text_input("رمز البيانات/الوسيط", value=base_spec.symbol)
    point_value = st.number_input("قيمة حركة سعر 1 لكل وحدة", min_value=0.000001, value=float(base_spec.point_value), format="%.6f")
    qty_step = st.number_input("خطوة الكمية", min_value=0.000001, value=float(base_spec.qty_step), format="%.6f")
    min_qty = st.number_input("أقل كمية", min_value=0.0, value=float(base_spec.min_qty), format="%.6f")
    max_qty = st.number_input("أعلى كمية", min_value=min_qty, value=float(base_spec.max_qty), format="%.6f")

instrument = InstrumentSpec(
    label=base_spec.label,
    asset_class=base_spec.asset_class,
    symbol=custom_symbol.strip(),
    point_value=float(point_value),
    qty_step=float(qty_step),
    min_qty=float(min_qty),
    max_qty=float(max_qty),
)

st.sidebar.divider()
mode = st.sidebar.radio("وضع التشغيل", ["تحليل", "Paper", "Live"], index=1)
risk_pct = st.sidebar.number_input("مخاطرة الصفقة %", min_value=0.05, max_value=1.0, value=0.50, step=0.05)
max_daily_loss_pct = st.sidebar.number_input("حد الخسارة اليومية %", min_value=0.5, max_value=5.0, value=2.0, step=0.5)
max_open_positions = st.sidebar.number_input("أقصى مراكز مفتوحة", min_value=1, max_value=10, value=3, step=1)
max_order_risk_pct = st.sidebar.number_input("الحد الصلب لمخاطرة الأمر %", min_value=0.05, max_value=1.0, value=1.0, step=0.05)
st.session_state.kill_switch = st.sidebar.toggle("KILL SWITCH", value=st.session_state.kill_switch, help="ON يمنع أي أمر Live جديد")
st.session_state.auto_refresh = st.sidebar.toggle("تحديث تلقائي", value=st.session_state.auto_refresh)
refresh_seconds = st.sidebar.slider("ثواني التحديث", 10, 120, 30)

live_unlocked = truthy(secret("LIVE_TRADING_ENABLED", "false"))
auto_live_unlocked = truthy(secret("AUTO_EXECUTION_ALLOWED", "false"))
bridge = make_bridge()

account: dict[str, Any] | None = None
broker_error = None
if bridge:
    try:
        account = bridge.get_account()
    except Exception as exc:
        broker_error = str(exc)

st.markdown(
    f"<div class='hero'><div class='kicker'>GOLD AI • X10 MULTI-ASSET TERMINAL</div>"
    f"<div class='big gold'>{instrument.symbol}</div>"
    f"<p class='muted'>{instrument.asset_class} • Decision Engine • Risk Engine • Paper/Live Bridge</p>"
    f"<span class='badge'>v{VERSION}</span></div>",
    unsafe_allow_html=True,
)

raw, data_status = fetch_market(instrument.symbol)
if raw.empty:
    st.error("مصدر البيانات غير جاهز")
    st.info(data_status)
    st.code('TWELVE_DATA_API_KEY = "ضع_المفتاح_هنا"', language="toml")
    st.stop()

quality = data_quality(raw)
quote = fetch_quote(instrument.symbol)
reference_price = float(quote["last"]) if quote.get("connected") and finite(quote.get("last")) else float(raw["close"].iloc[-1])
analysis = analyze_mtf(raw)
candle_id = str(raw["datetime"].iloc[-1])

# Manage paper position before rendering.
if st.session_state.paper_position:
    mark = paper_mark_price(st.session_state.paper_position, quote, reference_price)
    manage_paper(mark)

# Decision log once per candle/instrument.
log_key = f"{instrument.symbol}:{candle_id}"
if st.session_state.last_signal_candle.get(instrument.symbol) != candle_id:
    st.session_state.last_signal_candle[instrument.symbol] = candle_id
    st.session_state.decisions.insert(0, {
        "time": now_riyadh().isoformat(),
        "symbol": instrument.symbol,
        "candle": candle_id,
        "signal": analysis["signal"],
        "strength": analysis["strength"],
        "reason": analysis["reason"],
    })
    st.session_state.decisions = st.session_state.decisions[:500]

m = st.columns(6)
m[0].metric("السعر", fmt(reference_price, 4))
m[1].metric("القرار", analysis["signal"])
m[2].metric("القوة", f"{analysis['strength']}%")
m[3].metric("جودة البيانات", "OK" if quality["ok"] else "CHECK")
m[4].metric("Live", "UNLOCKED" if live_unlocked else "LOCKED")
m[5].metric("Kill Switch", "ON" if st.session_state.kill_switch else "OFF")

if quality.get("age_min", 0) > 240:
    st.warning("البيانات الحالية قد تكون قديمة لأن السوق مغلق أو المصدر متأخر. لا تعتمد على السعر كتنفيذ حي قبل التأكد من Quote مباشر.")

st.subheader("Multi-Timeframe Command Center")
tf_cols = st.columns(4)
for col, tf in zip(tf_cols, TIMEFRAMES):
    snap = analysis["snapshots"].get(tf)
    if snap:
        cls = "buy" if snap["trend"] == "UP" else "sell" if snap["trend"] == "DOWN" else ""
        col.markdown(
            f"<div class='card'><div class='kicker'>{tf}</div><h2 class='{cls}'>{snap['trend']}</h2>"
            f"<div class='muted'>RSI {snap['rsi']:.1f} • ADX {snap['adx']:.1f}</div></div>",
            unsafe_allow_html=True,
        )
    else:
        col.markdown(f"<div class='card'><div class='kicker'>{tf}</div><h2>WAIT</h2><div class='muted'>Insufficient data</div></div>", unsafe_allow_html=True)

signal_class = "buy" if analysis["signal"] == "BUY" else "sell" if analysis["signal"] == "SELL" else ""
st.markdown(
    f"<div class='card'><div class='kicker'>FINAL DECISION</div>"
    f"<div class='big {signal_class}'>{analysis['signal']}</div>"
    f"<p>{analysis['reason']}</p>"
    f"<div class='muted'>BUY score {analysis.get('buy_score',0)} • SELL score {analysis.get('sell_score',0)}</div></div>",
    unsafe_allow_html=True,
)

chart = raw.tail(400).set_index("datetime")[["close"]]
st.line_chart(chart, use_container_width=True)

# Prepare plan for BUY/SELL.
plan: dict[str, Any] | None = None
m5_snap = analysis["snapshots"].get("M5") if analysis.get("snapshots") else None
if analysis["signal"] in {"BUY", "SELL"} and m5_snap:
    if mode == "Live" and account:
        plan_equity = float(account.get("equity") or 0)
    else:
        plan_equity = float(st.session_state.paper_balance)
    if plan_equity > 0:
        try:
            plan = build_trade_plan(analysis["signal"], reference_price, float(m5_snap["atr"]), plan_equity, float(risk_pct), instrument)
        except Exception as exc:
            st.error(f"تعذر بناء خطة الصفقة: {exc}")

if plan:
    st.subheader("Trade Plan")
    pc = st.columns(7)
    pc[0].metric("Side", plan["side"])
    pc[1].metric("Qty", fmt(plan["qty"], 6))
    pc[2].metric("Entry", fmt(plan["entry_reference"], 4))
    pc[3].metric("SL", fmt(plan["stop_loss"], 4))
    pc[4].metric("TP1", fmt(plan["take_profit_1"], 4))
    pc[5].metric("TP2", fmt(plan["take_profit_2"], 4))
    pc[6].metric("Est. Risk", fmt(plan["estimated_risk"], 2))

# --------------------------- Paper ----------------------------
if mode == "Paper":
    st.subheader("Paper Trading")
    day_pnl = float(st.session_state.paper_balance) - float(st.session_state.paper_day_start_balance)
    p_open = 1 if st.session_state.paper_position else 0
    p_gate_ok = False
    p_gate_reasons: list[str] = []
    if plan:
        p_gate_ok, p_gate_reasons = risk_gate(
            float(st.session_state.paper_balance), day_pnl, p_open, plan,
            float(max_daily_loss_pct), int(max_open_positions), float(max_order_risk_pct), True,
        )

    pc = st.columns(5)
    pc[0].metric("Paper Balance", f"${st.session_state.paper_balance:,.2f}")
    pc[1].metric("Day P&L", f"${day_pnl:,.2f}")
    pc[2].metric("Trades Today", st.session_state.paper_trades_today)
    pc[3].metric("Open Position", "YES" if st.session_state.paper_position else "NO")
    pc[4].metric("Risk Gate", "PASS" if p_gate_ok else "BLOCK")

    st.session_state.auto_paper = st.toggle("Auto Paper", value=st.session_state.auto_paper, help="ينفذ Paper فقط عند وجود إشارة وخطة واجتياز المخاطر")

    if plan and not st.session_state.paper_position:
        if st.button("فتح صفقة Paper الآن", type="primary", use_container_width=True, disabled=not p_gate_ok):
            open_paper(plan, instrument)
            st.rerun()

    if st.session_state.auto_paper and plan and p_gate_ok and not st.session_state.paper_position:
        if st.session_state.last_auto_paper_candle.get(instrument.symbol) != candle_id:
            st.session_state.last_auto_paper_candle[instrument.symbol] = candle_id
            open_paper(plan, instrument)
            st.rerun()

    if p_gate_reasons:
        for reason in p_gate_reasons:
            st.warning(reason)

    p = st.session_state.paper_position
    if p:
        mark = paper_mark_price(p, quote, reference_price)
        ur = paper_unrealized_r(p, mark)
        upnl = ur * p["risk_money"]
        st.info(f"{p['side']} {p['symbol']} • Entry {fmt(p['entry'],4)} • Mark {fmt(mark,4)} • Unrealized {ur:.2f}R / ${upnl:,.2f}")
        if st.button("إغلاق Paper يدوي", use_container_width=True):
            close_paper(p, mark, "MANUAL", ur)
            st.rerun()

    if st.session_state.paper_history:
        st.dataframe(pd.DataFrame(st.session_state.paper_history), hide_index=True, use_container_width=True)

# ---------------------------- Live ----------------------------
if mode == "Live":
    st.subheader("Live Execution")
    if not live_unlocked:
        st.warning("LIVE_TRADING_ENABLED=false — التنفيذ الحقيقي مقفول من Secrets.")
    if not bridge:
        st.warning("Broker Bridge غير مضبوط. أضف BROKER_BRIDGE_URL و BROKER_BRIDGE_TOKEN.")
    if broker_error:
        st.error(broker_error)

    if account:
        equity = float(account.get("equity") or 0)
        day_pnl = float(account.get("day_pnl") or 0)
        open_positions = int(account.get("open_positions") or 0)
        trading_enabled = bool(account.get("trading_enabled", True))
        ac = st.columns(4)
        ac[0].metric("Equity", f"${equity:,.2f}")
        ac[1].metric("Day P&L", f"${day_pnl:,.2f}")
        ac[2].metric("Open Positions", open_positions)
        ac[3].metric("Broker Trading", "ENABLED" if trading_enabled else "DISABLED")

        live_gate_ok = False
        live_reasons: list[str] = []
        if plan:
            live_gate_ok, live_reasons = risk_gate(
                equity, day_pnl, open_positions, plan,
                float(max_daily_loss_pct), int(max_open_positions), float(max_order_risk_pct), trading_enabled,
            )
        if st.session_state.kill_switch:
            live_gate_ok = False
            live_reasons.append("KILL SWITCH مفعّل")
        if not live_unlocked:
            live_gate_ok = False
            live_reasons.append("Live غير مفتوح من Secrets")

        if live_reasons:
            for reason in dict.fromkeys(live_reasons):
                st.warning(reason)

        confirm_text = st.text_input("للتنفيذ اليدوي اكتب LIVE", value="", type="default")
        can_submit = bool(plan and live_gate_ok and confirm_text.strip().upper() == "LIVE")
        if st.button("إرسال أمر Live", type="primary", use_container_width=True, disabled=not can_submit):
            payload = live_payload(plan, analysis, instrument, candle_id)
            try:
                result = bridge.submit_order(payload)
                st.session_state.orders.insert(0, {"time": now_riyadh().isoformat(), "request": payload, "response": result})
                st.success("تم إرسال الأمر للـBroker Bridge")
                st.json(result)
            except Exception as exc:
                st.error(str(exc))

        st.session_state.auto_live = st.toggle(
            "Auto Live",
            value=st.session_state.auto_live,
            disabled=not auto_live_unlocked,
            help="يحتاج AUTO_EXECUTION_ALLOWED=true بالإضافة لباقي بوابات المخاطر",
        )
        if st.session_state.auto_live and auto_live_unlocked and plan and live_gate_ok:
            if st.session_state.last_auto_live_candle.get(instrument.symbol) != candle_id:
                st.session_state.last_auto_live_candle[instrument.symbol] = candle_id
                payload = live_payload(plan, analysis, instrument, candle_id)
                try:
                    result = bridge.submit_order(payload)
                    st.session_state.orders.insert(0, {"time": now_riyadh().isoformat(), "request": payload, "response": result})
                    st.success("Auto Live order sent")
                except Exception as exc:
                    st.error(f"Auto Live failed: {exc}")

        if st.button("Emergency: Cancel All Orders", use_container_width=True):
            if st.session_state.kill_switch:
                try:
                    result = bridge.cancel_all()
                    st.success("تم إرسال طلب إلغاء جميع الأوامر")
                    st.json(result)
                except Exception as exc:
                    st.error(str(exc))
            else:
                st.warning("فعّل KILL SWITCH أولاً قبل Cancel All")

    if st.session_state.orders:
        st.markdown("### Live Order Log")
        safe_rows = []
        for item in st.session_state.orders[:50]:
            req = item.get("request", {})
            res = item.get("response", {})
            safe_rows.append({
                "time": item.get("time"), "symbol": req.get("symbol"), "side": req.get("side"),
                "qty": req.get("quantity"), "client_order_id": req.get("client_order_id"),
                "status": res.get("status", res.get("state", "submitted")),
                "broker_order_id": res.get("order_id", res.get("id")),
            })
        st.dataframe(pd.DataFrame(safe_rows), hide_index=True, use_container_width=True)

# -------------------------- backtest --------------------------
st.subheader("Backtest Lab")
st.caption("اختبار تشخيصي على البيانات المتاحة، وليس ضمانًا للربحية المستقبلية.")
if st.button("تشغيل Backtest سريع", use_container_width=True):
    with st.spinner("تشغيل الاختبار..."):
        bt_trades, bt_stats = backtest_quick(raw, instrument, risk_pct=float(risk_pct))
        st.session_state.backtest = {"trades": bt_trades, "stats": bt_stats}

if st.session_state.backtest:
    bt = st.session_state.backtest
    stats = bt["stats"]
    if stats.get("trades", 0):
        bc = st.columns(6)
        bc[0].metric("Trades", stats["trades"])
        bc[1].metric("Win Rate", f"{stats['win_rate']:.1f}%")
        bc[2].metric("Profit Factor", "∞" if not math.isfinite(stats["profit_factor"]) else f"{stats['profit_factor']:.2f}")
        bc[3].metric("Net P&L", f"${stats['net_pnl']:,.2f}")
        bc[4].metric("Max DD", f"${stats['max_dd']:,.2f}")
        bc[5].metric("Ending Equity", f"${stats['ending_equity']:,.2f}")
        st.dataframe(bt["trades"].tail(100), hide_index=True, use_container_width=True)
    else:
        st.info(stats.get("warning", "لا توجد نتائج"))

# --------------------------- health ---------------------------
st.subheader("System Health")
health_rows = [
    {"Component": "Engine self-test", "Status": "ONLINE"},
    {"Component": "Market data", "Status": "ONLINE" if not raw.empty else "BLOCKED"},
    {"Component": "Quote", "Status": "ONLINE" if quote.get("connected") else "CHECK"},
    {"Component": "Data quality", "Status": "ONLINE" if quality["ok"] else "CHECK"},
    {"Component": "Paper engine", "Status": "ONLINE"},
    {"Component": "Broker bridge", "Status": "ONLINE" if account else "BLOCKED"},
    {"Component": "Live trading", "Status": "UNLOCKED" if live_unlocked else "LOCKED"},
    {"Component": "Auto live", "Status": "UNLOCKED" if auto_live_unlocked else "LOCKED"},
]
st.dataframe(pd.DataFrame(health_rows), hide_index=True, use_container_width=True)
st.caption(f"{quality['label']} • Last closed M5: {raw['datetime'].iloc[-1]} UTC • {len(raw):,} bars")

with st.expander("Decision Log", expanded=False):
    if st.session_state.decisions:
        st.dataframe(pd.DataFrame(st.session_state.decisions), hide_index=True, use_container_width=True)
    else:
        st.info("لا يوجد سجل بعد")

st.info("إعدادات Live مقفولة افتراضيًا. أي تنفيذ حقيقي يحتاج Broker Bridge فعلي وحالة حساب موثقة وفتح LIVE_TRADING_ENABLED في Secrets.")

if st.session_state.auto_refresh:
    time.sleep(refresh_seconds)
    st.rerun()
