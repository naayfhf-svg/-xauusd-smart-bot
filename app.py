from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import altair as alt
import numpy as np
import pandas as pd
import requests
import streamlit as st

# ============================================================
# GOLD AI v3.4 — X10 Build
# Single-file multi-asset Streamlit terminal.
# Analysis • Paper • MTF backtest • broker-safe Live bridge
# ============================================================

VERSION = "3.4.0-x10"
TZ = ZoneInfo("Asia/Riyadh")
DATA_URL = "https://api.twelvedata.com/time_series"
QUOTE_URL = "https://api.twelvedata.com/quote"
TIMEFRAMES = ("M5", "M15", "H1", "H4")
TF_RULES = {"M5": "5min", "M15": "15min", "H1": "1h", "H4": "4h"}

st.set_page_config(
    page_title="GOLD AI X10",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ----------------------------- UI -----------------------------
st.markdown(
    """
<style>
:root{
  --bg:#070a10;--panel:#0e1622;--line:#26364f;--gold:#d4af37;
  --text:#f5f7fb;--muted:#8fa2ba;--green:#24c97d;--red:#f05d68;
  --amber:#f2b84b;
}
html,body,[class*="css"]{
  font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif
}
.stApp{background:var(--bg);color:var(--text)}
.block-container{max-width:1450px;padding:1rem 1rem 4rem}
.hero{
  border:1px solid #5f4d18;border-radius:22px;padding:22px;
  background:radial-gradient(circle at 90% 10%,rgba(212,175,55,.15),transparent 30%),var(--panel)
}
.card,.mini{
  border:1px solid var(--line);border-radius:16px;background:var(--panel)
}
.card{padding:16px;margin-bottom:12px}
.mini{padding:14px;min-width:0}
.kicker,.mini .l{font-size:.76rem;color:var(--muted)}
.kicker{letter-spacing:.13em}
.big{font-size:clamp(2rem,6vw,4.3rem);font-weight:900;line-height:1.05}
.mini .v{
  font-size:clamp(1.05rem,2vw,1.55rem);font-weight:800;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis
}
.gold{color:var(--gold)}.buy,.state-ok{color:var(--green)}
.sell,.state-bad{color:var(--red)}.muted{color:var(--muted)}
.state-wait{color:var(--amber)}
.badge{
  display:inline-block;padding:4px 9px;border:1px solid var(--line);
  border-radius:999px;font-size:.78rem;color:var(--muted)
}
.status-grid,.tf-grid,.plan-grid,.paper-grid,.bt-grid{
  display:grid;gap:12px;margin:14px 0 18px
}
.status-grid{grid-template-columns:repeat(6,minmax(0,1fr))}
.tf-grid{grid-template-columns:repeat(4,minmax(0,1fr))}
.plan-grid{grid-template-columns:repeat(8,minmax(0,1fr))}
.paper-grid{grid-template-columns:repeat(5,minmax(0,1fr))}
.bt-grid{grid-template-columns:repeat(6,minmax(0,1fr))}
.chart-wrap{
  border:1px solid var(--line);border-radius:16px;padding:8px;
  background:var(--panel);margin:10px 0 18px
}
.gate-row{
  display:grid;grid-template-columns:1.4fr .9fr;gap:8px;
  padding:9px 0;border-bottom:1px solid rgba(143,162,186,.14)
}
.gate-row:last-child{border-bottom:none}
div[data-testid="stMetric"]{
  background:var(--panel);border:1px solid var(--line);
  border-radius:14px;padding:8px
}
@media (max-width:900px){
  .block-container{padding:.65rem .65rem 5rem}
  .hero{padding:16px;border-radius:18px}
  .status-grid,.paper-grid,.bt-grid,.tf-grid,.plan-grid{
    grid-template-columns:repeat(2,minmax(0,1fr));gap:8px
  }
  .mini{padding:11px;border-radius:13px}
  .mini .l{font-size:.70rem}.mini .v{font-size:1.08rem}
  h1{font-size:1.75rem!important}h2{font-size:1.35rem!important}
  h3{font-size:1.15rem!important}
}
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

# -------------------------- utilities -------------------------
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


def floor_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    precision = max(0, int(round(-math.log10(step))) + 2) if step < 1 else 6
    return round(math.floor((value + 1e-12) / step) * step, precision)


def mini_grid(items: list[tuple[str, str, str]], css_class: str = "status-grid") -> None:
    cards = []
    for label, value, state in items:
        state_class = f" state-{state}" if state in {"ok", "bad", "wait"} else ""
        cards.append(
            f"<div class='mini'><div class='l'>{label}</div>"
            f"<div class='v{state_class}'>{value}</div></div>"
        )
    st.markdown(f"<div class='{css_class}'>" + "".join(cards) + "</div>", unsafe_allow_html=True)


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
        "last_alert_candle": {},
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
    return (
        df.drop_duplicates("datetime")
        .sort_values("datetime")
        .reset_index(drop=True)
    )


def closed_m5(frame: pd.DataFrame, as_of: pd.Timestamp | None = None) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    cutoff = now_utc() if as_of is None else pd.Timestamp(as_of)
    cutoff = cutoff.tz_localize("UTC") if cutoff.tzinfo is None else cutoff.tz_convert("UTC")
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
    cutoff = cutoff.tz_localize("UTC") if cutoff.tzinfo is None else cutoff.tz_convert("UTC")
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
    """Analysis/Paper quote. Live execution uses the broker quote, not this quote."""
    key = secret("TWELVE_DATA_API_KEY")
    if not key:
        return {"connected": False, "error": "TWELVE_DATA_API_KEY missing"}
    try:
        response = requests.get(
            QUOTE_URL,
            params={
                "symbol": symbol,
                "interval": "1min",
                "timezone": "UTC",
                "apikey": key,
            },
            timeout=10,
        )
        payload = response.json()
    except Exception as exc:
        return {"connected": False, "error": str(exc)}

    bid = payload.get("bid")
    ask = payload.get("ask")
    last = payload.get("close", payload.get("price", payload.get("last")))
    market_open_raw = payload.get("is_market_open")
    market_open = market_open_raw if isinstance(market_open_raw, bool) else None
    base = {"market_open": market_open, "raw": payload}

    if finite(bid) and finite(ask) and float(ask) >= float(bid):
        return {
            **base,
            "connected": True,
            "bid": float(bid),
            "ask": float(ask),
            "last": float(last) if finite(last) else (float(bid) + float(ask)) / 2,
            "spread": float(ask) - float(bid),
        }
    if finite(last):
        return {
            **base,
            "connected": True,
            "bid": None,
            "ask": None,
            "last": float(last),
            "spread": None,
        }
    return {**base, "connected": False, "error": str(payload.get("message") or "No quote")}


def data_quality(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"ok": False, "label": "لا توجد بيانات", "age_min": math.inf}
    duplicate_count = int(frame["datetime"].duplicated().sum())
    monotonic = bool(frame["datetime"].is_monotonic_increasing)
    age_min = max(0.0, (now_utc() - frame["datetime"].iloc[-1]).total_seconds() / 60)
    return {
        "ok": monotonic and duplicate_count == 0,
        "age_min": age_min,
        "duplicates": duplicate_count,
        "label": f"آخر شمعة منذ {age_min:.0f} دقيقة • تكرار {duplicate_count}",
    }


def parse_timestamp(value: Any) -> pd.Timestamp | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
            n = float(value)
            unit = "ms" if n > 10_000_000_000 else "s"
            ts = pd.to_datetime(n, unit=unit, utc=True, errors="coerce")
        else:
            ts = pd.to_datetime(value, utc=True, errors="coerce")
        if pd.notna(ts):
            return pd.Timestamp(ts)
    except Exception:
        pass
    return None


def quote_timestamp(quote: dict[str, Any]) -> tuple[pd.Timestamp | None, str | None]:
    raw = quote.get("raw") if isinstance(quote, dict) else None
    if not isinstance(raw, dict):
        return None, None

    # /quote timestamp is the opening time of the chosen interval, not necessarily
    # the exact tick time. Prefer the explicit update fields.
    candidates = (
        ("last_update_at", raw.get("last_update_at")),
        ("last_quote_at", raw.get("last_quote_at")),
        ("last_update", raw.get("last_update")),
        ("extended_timestamp", raw.get("extended_timestamp")),
        ("timestamp", raw.get("timestamp")),
        ("datetime", raw.get("datetime")),
    )
    for name, value in candidates:
        ts = parse_timestamp(value)
        if ts is not None:
            return ts, name
    return None, None


def feed_integrity(frame: pd.DataFrame, quote: dict[str, Any]) -> dict[str, Any]:
    """Separate historical structure quality from execution-price readiness."""
    base = data_quality(frame)
    structural_reasons: list[str] = []
    execution_reasons: list[str] = []

    if not base.get("ok"):
        structural_reasons.append("ترتيب/تكرار الشموع غير سليم")

    sample = frame.tail(min(120, len(frame))).copy()
    if len(sample) < 30:
        structural_reasons.append("عدد الشموع الحديثة غير كافٍ")
        return {
            **base,
            "trusted": False,
            "execution_ok": False,
            "structural_reasons": structural_reasons,
            "execution_reasons": structural_reasons,
            "reasons": structural_reasons,
            "unique_ratio": 0.0,
            "alternation_ratio": 0.0,
            "zero_range_ratio": 0.0,
            "volatility_ratio": None,
            "recent_range_pct": None,
            "quote_age_min": None,
            "quote_ts_source": None,
            "market_open": quote.get("market_open"),
        }

    close = pd.to_numeric(sample["close"], errors="coerce").dropna()
    high = pd.to_numeric(sample["high"], errors="coerce")
    low = pd.to_numeric(sample["low"], errors="coerce")
    unique_ratio = float(close.nunique() / max(len(close), 1))

    ref = max(abs(float(close.iloc[-1])) if len(close) else 1.0, 1.0)
    tick_eps = max(ref * 1e-8, 1e-8)
    zero_range_ratio = float(((high - low).abs() <= tick_eps).mean())

    diffs = close.diff().dropna()
    nonzero = diffs[diffs.abs() > tick_eps]
    alternation_ratio = 0.0
    if len(nonzero) >= 20:
        signs = np.sign(nonzero.to_numpy())
        alternation_ratio = float(np.mean(signs[1:] != signs[:-1]))

    if len(close) >= 60 and unique_ratio < 0.08:
        structural_reasons.append("السعر متكرر بدرجة غير طبيعية")
    if zero_range_ratio > 0.80:
        structural_reasons.append("نسبة كبيرة من الشموع بلا نطاق سعري")
    if alternation_ratio > 0.93 and unique_ratio < 0.35:
        structural_reasons.append("نمط صعود/هبوط متناوب متكرر بشكل غير طبيعي")

    # Compare recent true range with the instrument's own older regime.
    hist = frame.tail(min(1200, len(frame))).copy()
    h_close = pd.to_numeric(hist["close"], errors="coerce")
    h_high = pd.to_numeric(hist["high"], errors="coerce")
    h_low = pd.to_numeric(hist["low"], errors="coerce")
    prev_close = h_close.shift(1)
    hist_tr = pd.concat(
        [h_high - h_low, (h_high - prev_close).abs(), (h_low - prev_close).abs()],
        axis=1,
    ).max(axis=1).dropna()

    recent_tr = hist_tr.tail(min(120, len(hist_tr)))
    older_tr = hist_tr.iloc[:-120] if len(hist_tr) > 240 else hist_tr
    recent_med = float(recent_tr.median()) if len(recent_tr) else 0.0
    baseline_med = float(older_tr.median()) if len(older_tr) else 0.0
    volatility_ratio = recent_med / baseline_med if baseline_med > 0 else None
    recent_range_pct = float((high.max() - low.min()) / ref * 100.0)

    if (
        volatility_ratio is not None
        and len(hist_tr) >= 300
        and volatility_ratio < 0.05
        and recent_range_pct < 0.03
    ):
        structural_reasons.append("التذبذب الحديث منخفض بشكل شاذ مقارنة بتاريخ الأصل")

    q_ts, q_source = quote_timestamp(quote)
    q_age = None
    if q_ts is not None:
        q_age = max(0.0, (now_utc() - q_ts).total_seconds() / 60.0)

    if quote.get("connected") and finite(quote.get("last")) and len(close):
        q = float(quote["last"])
        c = float(close.iloc[-1])
        mismatch_pct = abs(q - c) / max(abs(c), 1e-9) * 100.0
        if mismatch_pct > 3.0:
            execution_reasons.append(f"فرق Quote عن آخر شمعة كبير ({mismatch_pct:.2f}%)")

    structural_ok = bool(base.get("ok", False) and not structural_reasons)

    if not quote.get("connected"):
        execution_reasons.append("Quote المباشر غير متصل")
    if base.get("age_min", math.inf) > 15:
        execution_reasons.append("آخر شمعة M5 أقدم من 15 دقيقة")
    if q_ts is None:
        execution_reasons.append("لا يوجد توقيت موثوق لآخر Quote")
    elif q_age is not None and q_age > 5:
        execution_reasons.append(f"Quote قديم ({q_age:.0f} دقيقة)")
    if quote.get("market_open") is False:
        execution_reasons.append("السوق مغلق حسب مزود البيانات")
    if not structural_ok:
        execution_reasons.extend(structural_reasons)

    execution_reasons = list(dict.fromkeys(execution_reasons))
    return {
        **base,
        "trusted": structural_ok,
        "execution_ok": bool(structural_ok and not execution_reasons),
        "structural_reasons": list(dict.fromkeys(structural_reasons)),
        "execution_reasons": execution_reasons,
        "reasons": list(dict.fromkeys(structural_reasons + execution_reasons)),
        "unique_ratio": unique_ratio,
        "alternation_ratio": alternation_ratio,
        "zero_range_ratio": zero_range_ratio,
        "volatility_ratio": volatility_ratio,
        "recent_range_pct": recent_range_pct,
        "quote_age_min": q_age,
        "quote_ts_source": q_source,
        "market_open": quote.get("market_open"),
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


def row_trend(row: pd.Series) -> str:
    ema100_up = (not finite(row.get("ema100"))) or row["close"] > row["ema100"]
    ema100_down = (not finite(row.get("ema100"))) or row["close"] < row["ema100"]
    if row["close"] > row["ema20"] > row["ema50"] and ema100_up:
        return "UP"
    if row["close"] < row["ema20"] < row["ema50"] and ema100_down:
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
        "trend": row_trend(row),
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
    bullish_retest = (
        bullish_break
        and float(current["low"]) <= resistance + current_atr * retest_atr
        and float(current["close"]) > resistance
    )
    bearish_retest = (
        bearish_break
        and float(current["high"]) >= support - current_atr * retest_atr
        and float(current["close"]) < support
    )
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


def score_signal(snaps: dict[str, dict[str, Any]], b2: dict[str, Any]) -> tuple[str, int, int, str]:
    m5 = snaps["M5"]
    trends = [snaps[x]["trend"] for x in TIMEFRAMES]
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
        return "BUY", buy_score, sell_score, "اتجاه متعدد الأطر + زخم صاعد متوافق"
    if sell_score >= 75 and sell_score >= buy_score + 20:
        return "SELL", buy_score, sell_score, "اتجاه متعدد الأطر + زخم هابط متوافق"
    return "WAIT", buy_score, sell_score, "شروط الدخول غير مكتملة"


def analyze_mtf(raw: pd.DataFrame) -> dict[str, Any]:
    frames = {
        "M5": raw,
        "M15": resample_closed(raw, "15min"),
        "H1": resample_closed(raw, "1h"),
        "H4": resample_closed(raw, "4h"),
    }
    snaps = {name: snapshot(frame) for name, frame in frames.items()}
    if any(v is None for v in snaps.values()):
        return {
            "signal": "WAIT",
            "strength": 0,
            "reason": "بيانات غير كافية لكل الأطر",
            "snapshots": snaps,
            "b2": {},
            "buy_score": 0,
            "sell_score": 0,
        }

    b2 = b2_signal(snaps["M5"]["frame"])
    signal, buy_score, sell_score, reason = score_signal(snaps, b2)
    return {
        "signal": signal,
        "strength": min(100, max(buy_score, sell_score)),
        "reason": reason,
        "snapshots": snaps,
        "b2": b2,
        "buy_score": buy_score,
        "sell_score": sell_score,
    }

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
        raise ValueError("مدخلات المخاطرة غير صالحة")

    stop_distance = max(atr_value * stop_atr, 1e-9)
    risk_budget = equity * risk_pct / 100.0
    raw_qty = risk_budget / (stop_distance * spec.point_value)
    qty = floor_step(raw_qty, spec.qty_step)

    # Critical safety rule: never force the minimum quantity upward if it would
    # violate the requested risk budget.
    if qty < spec.min_qty:
        raise ValueError("الحد الأدنى للكمية يرفع المخاطرة فوق ميزانية الصفقة")
    qty = min(qty, spec.max_qty)

    if signal == "BUY":
        stop = entry - stop_distance
        tp1 = entry + stop_distance * tp1_r
        tp2 = entry + stop_distance * tp2_r
    else:
        stop = entry + stop_distance
        tp1 = entry - stop_distance * tp1_r
        tp2 = entry - stop_distance * tp2_r

    estimated_risk = abs(entry - stop) * spec.point_value * qty
    actual_risk_pct = estimated_risk / equity * 100.0

    return {
        "side": signal,
        "symbol": spec.symbol,
        "qty": float(qty),
        "raw_qty": float(raw_qty),
        "entry_reference": float(entry),
        "stop_loss": float(stop),
        "take_profit_1": float(tp1),
        "take_profit_2": float(tp2),
        "risk_budget": float(risk_budget),
        "estimated_risk": float(estimated_risk),
        "risk_pct": float(risk_pct),
        "actual_risk_pct": float(actual_risk_pct),
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
    day_start_equity: float | None = None,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if equity <= 0:
        reasons.append("Equity غير صالح")
    if not trading_enabled:
        reasons.append("الوسيط عطّل التداول")
    if open_positions >= max_open_positions:
        reasons.append("وصلت الحد الأقصى للمراكز المفتوحة")

    loss_base = day_start_equity if day_start_equity and day_start_equity > 0 else equity
    if loss_base > 0 and day_pnl <= -(loss_base * max_daily_loss_pct / 100.0):
        reasons.append("وصلت حد الخسارة اليومية")

    requested = float(plan.get("risk_pct", 999))
    actual = float(plan.get("actual_risk_pct", requested))
    if requested > max_order_risk_pct + 1e-9:
        reasons.append("المخاطرة المطلوبة أعلى من الحد الصلب")
    if actual > max_order_risk_pct + 1e-9:
        reasons.append("المخاطرة الفعلية بعد تقريب الكمية أعلى من الحد الصلب")
    if actual > requested + 1e-6:
        reasons.append("المخاطرة الفعلية أعلى من النسبة المطلوبة")
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
    st.session_state.paper_history.insert(
        0,
        {
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
        },
    )
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

# ------------------------- live bridge ------------------------
class BrokerBridgeError(RuntimeError):
    pass


@dataclass
class BrokerBridge:
    base_url: str
    token: str
    account_path: str = "/account"
    quote_path: str = "/quote"
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

    def _json(self, response: requests.Response, context: str) -> dict[str, Any]:
        if response.status_code >= 400:
            raise BrokerBridgeError(f"{context} HTTP {response.status_code}: {response.text[:400]}")
        try:
            payload = response.json()
        except Exception as exc:
            raise BrokerBridgeError(f"{context}: الرد ليس JSON") from exc
        if not isinstance(payload, dict):
            raise BrokerBridgeError(f"{context}: الرد غير صالح")
        return payload

    def get_account(self) -> dict[str, Any]:
        try:
            response = requests.get(self._url(self.account_path), headers=self._headers(), timeout=self.timeout)
        except Exception as exc:
            raise BrokerBridgeError(f"تعذر الاتصال بحالة الحساب: {exc}") from exc
        return self._json(response, "/account")

    def get_quote(self, symbol: str) -> dict[str, Any]:
        try:
            response = requests.get(
                self._url(self.quote_path),
                params={"symbol": symbol},
                headers=self._headers(),
                timeout=self.timeout,
            )
        except Exception as exc:
            raise BrokerBridgeError(f"تعذر جلب Broker Quote: {exc}") from exc
        return self._json(response, "/quote")

    def submit_order(self, order: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(order, separators=(",", ":"), sort_keys=True).encode()
        try:
            response = requests.post(
                self._url(self.order_path),
                data=body,
                headers=self._headers(body),
                timeout=self.timeout,
            )
        except Exception as exc:
            raise BrokerBridgeError(f"فشل إرسال الأمر: {exc}") from exc

        payload = self._json(response, "/orders")
        status = str(payload.get("status", payload.get("state", ""))).strip().lower()
        if status in {"rejected", "failed", "error", "cancelled", "canceled"}:
            raise BrokerBridgeError(f"الوسيط لم يقبل الأمر: {status}")
        if not payload.get("order_id") and not payload.get("id"):
            raise BrokerBridgeError("رد الوسيط لا يحتوي order_id/id موثق")
        return payload

    def cancel_all(self) -> dict[str, Any]:
        body = b"{}"
        try:
            response = requests.post(
                self._url(self.cancel_all_path),
                data=body,
                headers=self._headers(body),
                timeout=self.timeout,
            )
        except Exception as exc:
            raise BrokerBridgeError(f"فشل إلغاء الأوامر: {exc}") from exc
        return self._json(response, "/orders/cancel-all")


def make_bridge() -> BrokerBridge | None:
    base = secret("BROKER_BRIDGE_URL")
    token = secret("BROKER_BRIDGE_TOKEN")
    if not base or not token:
        return None
    return BrokerBridge(
        base_url=str(base),
        token=str(token),
        account_path=str(secret("BROKER_ACCOUNT_PATH", "/account")),
        quote_path=str(secret("BROKER_QUOTE_PATH", "/quote")),
        order_path=str(secret("BROKER_ORDER_PATH", "/orders")),
        cancel_all_path=str(secret("BROKER_CANCEL_ALL_PATH", "/orders/cancel-all")),
        hmac_secret=secret("BROKER_HMAC_SECRET"),
    )


def broker_quote_state(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "Broker Quote غير متوفر"}

    bid = payload.get("bid")
    ask = payload.get("ask")
    last = payload.get("last", payload.get("price"))
    if finite(bid) and finite(ask) and float(ask) >= float(bid):
        execution_price = (float(bid) + float(ask)) / 2.0
    elif finite(last):
        execution_price = float(last)
    else:
        return {"ok": False, "reason": "Broker Quote لا يحتوي سعر صالح"}

    ts = None
    for key in ("timestamp", "last_update_at", "datetime"):
        ts = parse_timestamp(payload.get(key))
        if ts is not None:
            break

    if ts is None:
        return {"ok": False, "reason": "Broker Quote بلا timestamp موثوق", "price": execution_price}

    age_min = max(0.0, (now_utc() - ts).total_seconds() / 60.0)
    if age_min > 2:
        return {
            "ok": False,
            "reason": f"Broker Quote قديم ({age_min:.1f} دقيقة)",
            "price": execution_price,
            "age_min": age_min,
        }
    if payload.get("market_open") is False:
        return {
            "ok": False,
            "reason": "الوسيط يذكر أن السوق مغلق",
            "price": execution_price,
            "age_min": age_min,
        }
    return {"ok": True, "price": execution_price, "age_min": age_min, "raw": payload}


def live_payload(
    plan: dict[str, Any],
    analysis: dict[str, Any],
    instrument: InstrumentSpec,
    candle_id: str,
    broker_quote: dict[str, Any],
) -> dict[str, Any]:
    # Deterministic idempotency key: repeated taps on the same signal candle
    # produce the same client_order_id.
    idem_src = f"{VERSION}|{instrument.symbol}|{plan['side']}|{candle_id}"
    idem = hashlib.sha256(idem_src.encode()).hexdigest()[:20]
    return {
        "client_order_id": f"goldai-{idem}",
        "strategy": "GOLD_AI_V34_X10_MTF",
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
        "risk_pct_requested": plan["risk_pct"],
        "risk_pct_actual": round(plan["actual_risk_pct"], 6),
        "estimated_risk": round(plan["estimated_risk"], 2),
        "signal_strength": int(analysis["strength"]),
        "signal_candle": candle_id,
        "broker_quote_timestamp": broker_quote.get("timestamp")
        or broker_quote.get("last_update_at")
        or broker_quote.get("datetime"),
        "requested_at": now_riyadh().isoformat(),
    }

# ---------------------- matched MTF backtest ------------------
def feature_frame(frame: pd.DataFrame, rule: str) -> pd.DataFrame:
    calc = add_indicators(frame).copy()
    calc["trend"] = calc.apply(
        lambda r: row_trend(r)
        if all(finite(r.get(k)) for k in ("ema20", "ema50", "close"))
        else "MIXED",
        axis=1,
    )
    calc["effective_time"] = calc["datetime"] + pd.Timedelta(rule)
    return calc


def precompute_b2(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["b2_valid"] = False
    out["b2_side"] = None
    out["b2_breakout"] = False
    lookback = 20
    for i in range(lookback + 2, len(out)):
        window = out.iloc[: i + 1]
        sig = b2_signal(window)
        out.at[i, "b2_valid"] = bool(sig.get("valid"))
        out.at[i, "b2_side"] = sig.get("side")
        out.at[i, "b2_breakout"] = bool(sig.get("breakout"))
    return out


def backtest_mtf(
    raw: pd.DataFrame,
    spec: InstrumentSpec,
    risk_pct: float = 0.5,
    cost_bps_roundtrip: float = 2.0,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if len(raw) < 3500:
        return pd.DataFrame(), {
            "trades": 0,
            "warning": "الـBacktest متعدد الأطر يحتاج تقريبًا 3500 شمعة M5 على الأقل",
        }

    base = raw.copy().reset_index(drop=True)
    tf_frames = {
        "M5": base,
        "M15": (
            base.set_index("datetime")[["open", "high", "low", "close"]]
            .resample("15min", label="left", closed="left")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna()
            .reset_index()
        ),
        "H1": (
            base.set_index("datetime")[["open", "high", "low", "close"]]
            .resample("1h", label="left", closed="left")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna()
            .reset_index()
        ),
        "H4": (
            base.set_index("datetime")[["open", "high", "low", "close"]]
            .resample("4h", label="left", closed="left")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna()
            .reset_index()
        ),
    }

    features: dict[str, pd.DataFrame] = {}
    for tf, frame in tf_frames.items():
        f = feature_frame(frame, TF_RULES[tf])
        keep = [
            "effective_time", "datetime", "open", "high", "low", "close",
            "rsi", "adx", "atr", "momentum", "macd_hist", "trend",
        ]
        f = f[keep].dropna(subset=["rsi", "adx", "atr", "momentum", "macd_hist"])
        suffix = tf.lower()
        f = f.rename(
            columns={
                c: f"{c}_{suffix}"
                for c in f.columns
                if c not in {"effective_time"}
            }
        )
        features[tf] = f.sort_values("effective_time").reset_index(drop=True)

    m5_full = add_indicators(base).dropna(
        subset=["ema20", "ema50", "rsi", "atr", "macd_hist", "momentum", "adx"]
    ).reset_index(drop=True)
    m5_full["trend"] = m5_full.apply(row_trend, axis=1)
    m5_full = precompute_b2(m5_full)
    m5_full["effective_time"] = m5_full["datetime"] + pd.Timedelta("5min")

    bt = m5_full[
        [
            "effective_time", "datetime", "open", "high", "low", "close",
            "rsi", "adx", "atr", "momentum", "macd_hist", "trend",
            "b2_valid", "b2_side",
        ]
    ].copy()

    bt = bt.rename(
        columns={
            "datetime": "datetime_m5",
            "open": "open_m5",
            "high": "high_m5",
            "low": "low_m5",
            "close": "close_m5",
            "rsi": "rsi_m5",
            "adx": "adx_m5",
            "atr": "atr_m5",
            "momentum": "momentum_m5",
            "macd_hist": "macd_hist_m5",
            "trend": "trend_m5",
        }
    ).sort_values("effective_time")

    for tf in ("M15", "H1", "H4"):
        bt = pd.merge_asof(
            bt.sort_values("effective_time"),
            features[tf].sort_values("effective_time"),
            on="effective_time",
            direction="backward",
        )

    bt = bt.dropna(
        subset=[
            "trend_m5", "trend_m15", "trend_h1", "trend_h4",
            "rsi_m5", "adx_m5", "atr_m5", "momentum_m5", "macd_hist_m5",
        ]
    ).reset_index(drop=True)

    equity = 100_000.0
    initial_equity = equity
    trades: list[dict[str, Any]] = []
    position: dict[str, Any] | None = None

    # map next M5 bar by open time
    raw_by_time = base.set_index("datetime")

    for _, row in bt.iterrows():
        eval_time = pd.Timestamp(row["effective_time"])
        if eval_time not in raw_by_time.index:
            continue
        nxt = raw_by_time.loc[eval_time]
        if isinstance(nxt, pd.DataFrame):
            nxt = nxt.iloc[0]

        snaps = {
            "M5": {
                "trend": row["trend_m5"], "rsi": float(row["rsi_m5"]),
                "adx": float(row["adx_m5"]), "atr": float(row["atr_m5"]),
                "momentum": float(row["momentum_m5"]),
                "macd_hist": float(row["macd_hist_m5"]),
            },
            "M15": {"trend": row["trend_m15"]},
            "H1": {"trend": row["trend_h1"]},
            "H4": {"trend": row["trend_h4"]},
        }
        b2 = {"valid": bool(row["b2_valid"]), "side": row["b2_side"]}
        signal, buy_score, sell_score, _ = score_signal(snaps, b2)

        if position is None and signal in {"BUY", "SELL"}:
            try:
                plan = build_trade_plan(
                    signal,
                    float(nxt["open"]),
                    float(row["atr_m5"]),
                    equity,
                    risk_pct,
                    spec,
                )
            except ValueError:
                continue
            position = {
                "side": signal,
                "entry": plan["entry_reference"],
                "stop": plan["stop_loss"],
                "stop_initial": plan["stop_loss"],
                "tp1": plan["take_profit_1"],
                "tp2": plan["take_profit_2"],
                "risk": plan["estimated_risk"],
                "qty": plan["qty"],
                "point_value": spec.point_value,
                "remaining": 1.0,
                "realized_r": 0.0,
                "tp1_hit": False,
                "opened": eval_time,
                "buy_score": buy_score,
                "sell_score": sell_score,
            }

        if position is None:
            continue

        hi = float(nxt["high"])
        lo = float(nxt["low"])
        side = position["side"]
        d = abs(position["entry"] - position["stop_initial"])
        if d <= 0:
            position = None
            continue

        closed = False
        exit_price = None
        exit_reason = None
        total_r = None

        if side == "BUY":
            # Conservative intrabar ordering: stop is checked before profit targets.
            if lo <= position["stop"]:
                total_r = position["realized_r"] + position["remaining"] * (
                    (position["stop"] - position["entry"]) / d
                )
                exit_price, exit_reason, closed = position["stop"], "STOP", True
            else:
                if not position["tp1_hit"] and hi >= position["tp1"]:
                    position["tp1_hit"] = True
                    position["remaining"] = 0.5
                    position["realized_r"] = 0.5
                    position["stop"] = position["entry"]
                if position["tp1_hit"] and hi >= position["tp2"]:
                    total_r = position["realized_r"] + 0.5 * (
                        (position["tp2"] - position["entry"]) / d
                    )
                    exit_price, exit_reason, closed = position["tp2"], "TP2", True
        else:
            if hi >= position["stop"]:
                total_r = position["realized_r"] + position["remaining"] * (
                    (position["entry"] - position["stop"]) / d
                )
                exit_price, exit_reason, closed = position["stop"], "STOP", True
            else:
                if not position["tp1_hit"] and lo <= position["tp1"]:
                    position["tp1_hit"] = True
                    position["remaining"] = 0.5
                    position["realized_r"] = 0.5
                    position["stop"] = position["entry"]
                if position["tp1_hit"] and lo <= position["tp2"]:
                    total_r = position["realized_r"] + 0.5 * (
                        (position["entry"] - position["tp2"]) / d
                    )
                    exit_price, exit_reason, closed = position["tp2"], "TP2", True

        if closed and total_r is not None:
            gross = total_r * position["risk"]
            notional = abs(position["entry"] * position["qty"] * position["point_value"])
            costs = notional * (cost_bps_roundtrip / 10_000.0)
            pnl = gross - costs
            equity += pnl
            trades.append(
                {
                    "opened": position["opened"],
                    "closed": eval_time,
                    "side": side,
                    "entry": position["entry"],
                    "exit": exit_price,
                    "R_gross": round(total_r, 4),
                    "costs": round(costs, 2),
                    "PnL": round(pnl, 2),
                    "equity": round(equity, 2),
                    "reason": exit_reason,
                }
            )
            position = None

    if not trades:
        return pd.DataFrame(), {"trades": 0, "warning": "لم ينتج الاختبار صفقات"}

    df = pd.DataFrame(trades)
    gross_win = df.loc[df["PnL"] > 0, "PnL"].sum()
    gross_loss = abs(df.loc[df["PnL"] < 0, "PnL"].sum())
    pf = gross_win / gross_loss if gross_loss else math.inf

    eq_curve = pd.Series([initial_equity] + df["equity"].astype(float).tolist())
    drawdown = eq_curve.cummax() - eq_curve
    drawdown_pct = drawdown / eq_curve.cummax().replace(0, np.nan) * 100

    stats = {
        "trades": len(df),
        "win_rate": float((df["PnL"] > 0).mean() * 100),
        "profit_factor": float(pf),
        "net_pnl": float(df["PnL"].sum()),
        "max_dd": float(drawdown.max()),
        "max_dd_pct": float(drawdown_pct.max()),
        "ending_equity": float(df["equity"].iloc[-1]),
    }
    return df, stats

# --------------------------- self test ------------------------
def self_test() -> tuple[bool, str]:
    try:
        idx = pd.date_range(
            end=now_utc().floor("5min") - pd.Timedelta(minutes=5),
            periods=420,
            freq="5min",
            tz="UTC",
        )
        close = np.linspace(100, 130, len(idx))
        df = pd.DataFrame(
            {
                "datetime": idx,
                "open": close - 0.1,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
            }
        )

        calc = add_indicators(df)
        assert len(calc) == len(df)

        spec = PRESETS["Gold — XAU/USD"]
        plan = build_trade_plan("BUY", 2000.0, 10.0, 100000.0, 0.5, spec)
        assert plan["qty"] > 0
        assert plan["actual_risk_pct"] <= plan["risk_pct"] + 1e-6
        ok, reasons = risk_gate(100000.0, 0.0, 0, plan, 2.0, 3, 1.0)
        assert ok and not reasons

        healthy = feed_integrity(
            df,
            {
                "connected": True,
                "last": float(df["close"].iloc[-1]),
                "market_open": True,
                "raw": {"last_update_at": int(now_utc().timestamp())},
            },
        )
        assert healthy["trusted"] and healthy["execution_ok"]

        bad = df.tail(120).copy().reset_index(drop=True)
        bad["close"] = np.where(np.arange(len(bad)) % 2 == 0, 100.0, 100.1)
        bad["open"] = bad["close"]
        bad["high"] = bad["close"] + 0.01
        bad["low"] = bad["close"] - 0.01
        suspicious = feed_integrity(
            bad,
            {
                "connected": True,
                "last": float(bad["close"].iloc[-1]),
                "market_open": True,
                "raw": {"last_update_at": int(now_utc().timestamp())},
            },
        )
        assert not suspicious["trusted"]

        too_large_min = InstrumentSpec("TEST", "TEST", "TEST", 1000.0, 1.0, 1.0, 10.0)
        try:
            build_trade_plan("BUY", 100.0, 10.0, 1000.0, 0.05, too_large_min)
            raise AssertionError("min-qty guard failed")
        except ValueError:
            pass

        return True, "OK"
    except Exception as exc:
        return False, str(exc)

# ----------------------------- app ----------------------------
engine_ok, engine_error = self_test()
if not engine_ok:
    st.error(f"فشل اختبار المحرك الداخلي: {engine_error}")
    st.stop()

st.sidebar.markdown("## ⚡ GOLD AI X10")
st.sidebar.caption(f"v{VERSION} • Gold • Stocks • Futures/Contracts")

preset_name = st.sidebar.selectbox("السوق", list(PRESETS.keys()), index=0)
base_spec = PRESETS[preset_name]

with st.sidebar.expander("إعدادات الأصل", expanded=False):
    custom_symbol = st.text_input("رمز البيانات/الوسيط", value=base_spec.symbol)
    point_value = st.number_input(
        "قيمة حركة سعر 1 لكل وحدة",
        min_value=0.000001,
        value=float(base_spec.point_value),
        format="%.6f",
    )
    qty_step = st.number_input(
        "خطوة الكمية",
        min_value=0.000001,
        value=float(base_spec.qty_step),
        format="%.6f",
    )
    min_qty = st.number_input(
        "أقل كمية",
        min_value=0.0,
        value=float(base_spec.min_qty),
        format="%.6f",
    )
    max_qty = st.number_input(
        "أعلى كمية",
        min_value=min_qty,
        value=float(base_spec.max_qty),
        format="%.6f",
    )

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
risk_pct = st.sidebar.number_input(
    "مخاطرة الصفقة %",
    min_value=0.05,
    max_value=1.0,
    value=0.50,
    step=0.05,
)
st.session_state.kill_switch = st.sidebar.toggle(
    "KILL SWITCH",
    value=st.session_state.kill_switch,
    help="ON يمنع أي أمر Live جديد",
)

with st.sidebar.expander("إعدادات المخاطر والتحديث", expanded=False):
    max_daily_loss_pct = st.number_input(
        "حد الخسارة اليومية %",
        min_value=0.5,
        max_value=5.0,
        value=2.0,
        step=0.5,
    )
    max_open_positions = st.number_input(
        "أقصى مراكز مفتوحة",
        min_value=1,
        max_value=10,
        value=3,
        step=1,
    )
    max_order_risk_pct = st.number_input(
        "الحد الصلب لمخاطرة الأمر %",
        min_value=0.05,
        max_value=1.0,
        value=1.0,
        step=0.05,
    )
    st.session_state.auto_refresh = st.toggle(
        "تحديث تلقائي",
        value=st.session_state.auto_refresh,
    )
    refresh_seconds = st.slider("ثواني التحديث", 10, 120, 30)

if not st.session_state.auto_refresh:
    refresh_seconds = 30

live_unlocked = truthy(secret("LIVE_TRADING_ENABLED", "false"))
auto_live_unlocked = truthy(secret("AUTO_EXECUTION_ALLOWED", "false"))
contract_metadata_verified = truthy(secret("BROKER_CONTRACT_METADATA_VERIFIED", "false"))
bridge = make_bridge()

account: dict[str, Any] | None = None
broker_quote_payload: dict[str, Any] | None = None
broker_error: str | None = None

if bridge:
    try:
        account = bridge.get_account()
        broker_quote_payload = bridge.get_quote(instrument.symbol)
    except Exception as exc:
        broker_error = str(exc)

broker_quote = broker_quote_state(broker_quote_payload)

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
feed = feed_integrity(raw, quote)
reference_price = (
    float(quote["last"])
    if quote.get("connected") and finite(quote.get("last"))
    else float(raw["close"].iloc[-1])
)

analysis = analyze_mtf(raw)
if not feed.get("trusted", False) and analysis.get("signal") in {"BUY", "SELL"}:
    analysis = {
        **analysis,
        "signal": "WAIT",
        "reason": "تم حجب الإشارة بسبب فشل فحص بنية بيانات السوق",
    }

candle_id = str(raw["datetime"].iloc[-1])

# Never manage a Paper position from a stale/unverifiable execution price.
if st.session_state.paper_position and feed.get("execution_ok", False):
    mark = paper_mark_price(st.session_state.paper_position, quote, reference_price)
    manage_paper(mark)

if st.session_state.last_signal_candle.get(instrument.symbol) != candle_id:
    st.session_state.last_signal_candle[instrument.symbol] = candle_id
    st.session_state.decisions.insert(
        0,
        {
            "time": now_riyadh().isoformat(),
            "symbol": instrument.symbol,
            "candle": candle_id,
            "signal": analysis["signal"],
            "strength": analysis["strength"],
            "buy_score": analysis.get("buy_score", 0),
            "sell_score": analysis.get("sell_score", 0),
            "feed_execution_ready": feed.get("execution_ok", False),
            "reason": analysis["reason"],
        },
    )
    st.session_state.decisions = st.session_state.decisions[:500]

    if analysis["signal"] in {"BUY", "SELL"}:
        alert_key = f"{instrument.symbol}:{candle_id}:{analysis['signal']}"
        if st.session_state.last_alert_candle.get(instrument.symbol) != alert_key:
            st.session_state.last_alert_candle[instrument.symbol] = alert_key
            st.toast(
                f"{instrument.symbol} • {analysis['signal']} • قوة {analysis['strength']}%",
                icon="⚡",
            )

mini_grid(
    [
        ("السعر", fmt(reference_price, 4), ""),
        ("القرار", analysis["signal"], "ok" if analysis["signal"] in {"BUY", "SELL"} else "wait"),
        ("القوة", f"{analysis['strength']}%", ""),
        ("Execution Feed", "READY" if feed.get("execution_ok") else "BLOCKED", "ok" if feed.get("execution_ok") else "bad"),
        ("Live", "UNLOCKED" if live_unlocked else "LOCKED", "ok" if live_unlocked else "wait"),
        ("Kill Switch", "ON" if st.session_state.kill_switch else "OFF", "wait" if st.session_state.kill_switch else "ok"),
    ],
    "status-grid",
)

if not feed.get("trusted", False):
    st.error("فحص بنية بيانات السوق لم ينجح. تم حجب أي إشارة تنفيذية.")
    for reason in feed.get("structural_reasons", []):
        st.caption("• " + reason)
elif not feed.get("execution_ok", False):
    st.warning("البيانات تصل للتحليل، لكن التنفيذ محجوب حتى يصبح سعر التنفيذ حديثًا وقابلًا للتحقق.")
    for reason in feed.get("execution_reasons", []):
        st.caption("• " + reason)

# ------------------------- command center ---------------------
st.subheader("Multi-Timeframe Command Center")
tf_cards = []
for tf in TIMEFRAMES:
    snap = analysis.get("snapshots", {}).get(tf)
    if snap:
        cls = "buy" if snap["trend"] == "UP" else "sell" if snap["trend"] == "DOWN" else ""
        tf_cards.append(
            f"<div class='card'><div class='kicker'>{tf}</div>"
            f"<h2 class='{cls}'>{snap['trend']}</h2>"
            f"<div class='muted'>RSI {snap['rsi']:.1f} • ADX {snap['adx']:.1f}</div></div>"
        )
    else:
        tf_cards.append(
            f"<div class='card'><div class='kicker'>{tf}</div>"
            f"<h2>WAIT</h2><div class='muted'>Insufficient data</div></div>"
        )
st.markdown("<div class='tf-grid'>" + "".join(tf_cards) + "</div>", unsafe_allow_html=True)

signal_class = "buy" if analysis["signal"] == "BUY" else "sell" if analysis["signal"] == "SELL" else ""
st.markdown(
    f"<div class='card'><div class='kicker'>FINAL DECISION</div>"
    f"<div class='big {signal_class}'>{analysis['signal']}</div>"
    f"<p>{analysis['reason']}</p>"
    f"<div class='muted'>BUY score {analysis.get('buy_score',0)} • "
    f"SELL score {analysis.get('sell_score',0)}</div></div>",
    unsafe_allow_html=True,
)

# ---------------------------- chart ---------------------------
chart_df = raw.tail(288)[["datetime", "close"]].copy()
chart_min = float(chart_df["close"].min())
chart_max = float(chart_df["close"].max())
chart_span = max(chart_max - chart_min, max(abs(reference_price) * 0.00005, 1e-6))
chart_pad = chart_span * 0.14

price_chart = (
    alt.Chart(chart_df)
    .mark_line(strokeWidth=2)
    .encode(
        x=alt.X("datetime:T", title=None, axis=alt.Axis(labelOverlap=True, grid=False)),
        y=alt.Y(
            "close:Q",
            title=None,
            scale=alt.Scale(zero=False, domain=[chart_min - chart_pad, chart_max + chart_pad]),
            axis=alt.Axis(format=",.2f"),
        ),
        tooltip=[
            alt.Tooltip("datetime:T", title="Time"),
            alt.Tooltip("close:Q", title="Close", format=",.4f"),
        ],
    )
    .properties(height=285)
)
st.markdown("<div class='chart-wrap'>", unsafe_allow_html=True)
st.altair_chart(price_chart, use_container_width=True)
st.markdown("</div>", unsafe_allow_html=True)

# ---------------------- decision diagnostics -----------------
m5_diag = analysis.get("snapshots", {}).get("M5") or {}
trends_diag = [
    (analysis.get("snapshots", {}).get(tf) or {}).get("trend")
    for tf in TIMEFRAMES
]
up_count = sum(t == "UP" for t in trends_diag)
down_count = sum(t == "DOWN" for t in trends_diag)
b2_diag = analysis.get("b2") or {}

diag_rows = [
    ("اتجاه الأطر", f"UP {up_count}/4 • DOWN {down_count}/4", up_count >= 3 or down_count >= 3),
    ("RSI M5", f"{m5_diag.get('rsi', 0):.1f}" if m5_diag else "—", bool(m5_diag) and (m5_diag.get("rsi", 50) >= 52 or m5_diag.get("rsi", 50) <= 48)),
    ("زخم MACD", "متوافق" if m5_diag and ((m5_diag.get("momentum",0)>0 and m5_diag.get("macd_hist",0)>0) or (m5_diag.get("momentum",0)<0 and m5_diag.get("macd_hist",0)<0)) else "غير مكتمل", bool(m5_diag) and ((m5_diag.get("momentum",0)>0 and m5_diag.get("macd_hist",0)>0) or (m5_diag.get("momentum",0)<0 and m5_diag.get("macd_hist",0)<0))),
    ("ADX M5", f"{m5_diag.get('adx', 0):.1f}" if m5_diag else "—", bool(m5_diag) and m5_diag.get("adx",0) >= 20),
    ("B2 Break/Retest", b2_diag.get("side") or ("Breakout فقط" if b2_diag.get("breakout") else "بانتظار التأكيد"), bool(b2_diag.get("valid"))),
    ("بنية البيانات", "سليمة" if feed.get("trusted") else "تحقق مطلوب", bool(feed.get("trusted"))),
    ("جاهزية Paper", "جاهز" if feed.get("execution_ok") else "محجوب", bool(feed.get("execution_ok"))),
]

with st.expander("لماذا هذا القرار؟", expanded=False):
    for label, value, passed in diag_rows:
        state = "state-ok" if passed else "state-wait"
        icon = "✓" if passed else "•"
        st.markdown(
            f"<div class='gate-row'><div>{label}</div>"
            f"<div class='{state}'>{icon} {value}</div></div>",
            unsafe_allow_html=True,
        )

# ----------------------- analysis trade plan ------------------
paper_plan: dict[str, Any] | None = None
m5_snap = analysis.get("snapshots", {}).get("M5")
if analysis["signal"] in {"BUY", "SELL"} and m5_snap:
    try:
        paper_plan = build_trade_plan(
            analysis["signal"],
            reference_price,
            float(m5_snap["atr"]),
            float(st.session_state.paper_balance),
            float(risk_pct),
            instrument,
        )
    except ValueError as exc:
        st.warning(f"لا يمكن بناء خطة بالحجم الحالي: {exc}")

if paper_plan:
    st.subheader("Trade Plan — Analysis/Paper")
    mini_grid(
        [
            ("Side", paper_plan["side"], "ok"),
            ("Qty", fmt(paper_plan["qty"], 6), ""),
            ("Entry", fmt(paper_plan["entry_reference"], 4), ""),
            ("SL", fmt(paper_plan["stop_loss"], 4), "bad"),
            ("TP1", fmt(paper_plan["take_profit_1"], 4), "ok"),
            ("TP2", fmt(paper_plan["take_profit_2"], 4), "ok"),
            ("Est. Risk", "$" + fmt(paper_plan["estimated_risk"], 2), "wait"),
            ("Actual Risk %", fmt(paper_plan["actual_risk_pct"], 3) + "%", "ok"),
        ],
        "plan-grid",
    )

# --------------------------- Paper ----------------------------
if mode == "Paper":
    st.subheader("Paper Trading")
    day_pnl = float(st.session_state.paper_balance) - float(st.session_state.paper_day_start_balance)
    p_open = 1 if st.session_state.paper_position else 0
    p_gate_ok = False
    p_gate_reasons: list[str] = []

    if paper_plan:
        p_gate_ok, p_gate_reasons = risk_gate(
            float(st.session_state.paper_balance),
            day_pnl,
            p_open,
            paper_plan,
            float(max_daily_loss_pct),
            int(max_open_positions),
            float(max_order_risk_pct),
            True,
            float(st.session_state.paper_day_start_balance),
        )
        if not feed.get("execution_ok", False):
            p_gate_ok = False
            p_gate_reasons.append(
                "Execution Feed غير جاهز؛ لا يتم فتح Paper على سعر قديم/غير قابل للتحقق"
            )

    gate_label = "WAIT SIGNAL" if not paper_plan else ("PASS" if p_gate_ok else "BLOCK")
    gate_state = "wait" if not paper_plan else ("ok" if p_gate_ok else "bad")
    mini_grid(
        [
            ("Paper Balance", f"${st.session_state.paper_balance:,.2f}", ""),
            ("Day P&L", f"${day_pnl:,.2f}", "ok" if day_pnl >= 0 else "bad"),
            ("Trades Today", str(st.session_state.paper_trades_today), ""),
            ("Open Position", "YES" if st.session_state.paper_position else "NO", "wait" if st.session_state.paper_position else ""),
            ("Risk Gate", gate_label, gate_state),
        ],
        "paper-grid",
    )

    st.caption("حالة Paper محفوظة داخل جلسة Streamlit الحالية وليست قاعدة بيانات دائمة.")

    st.session_state.auto_paper = st.toggle(
        "Auto Paper",
        value=st.session_state.auto_paper,
        help="يفتح Paper فقط عند وجود إشارة، خطة، وسعر تنفيذ حديث",
    )

    if paper_plan and not st.session_state.paper_position:
        if st.button(
            "فتح صفقة Paper الآن",
            type="primary",
            use_container_width=True,
            disabled=not p_gate_ok,
        ):
            open_paper(paper_plan, instrument)
            st.rerun()

    if (
        st.session_state.auto_paper
        and paper_plan
        and p_gate_ok
        and not st.session_state.paper_position
    ):
        if st.session_state.last_auto_paper_candle.get(instrument.symbol) != candle_id:
            st.session_state.last_auto_paper_candle[instrument.symbol] = candle_id
            open_paper(paper_plan, instrument)
            st.rerun()

    for reason in dict.fromkeys(p_gate_reasons):
        st.warning(reason)

    p = st.session_state.paper_position
    if p:
        if feed.get("execution_ok", False):
            mark = paper_mark_price(p, quote, reference_price)
            ur = paper_unrealized_r(p, mark)
            upnl = ur * p["risk_money"]
            st.info(
                f"{p['side']} {p['symbol']} • Entry {fmt(p['entry'],4)} • "
                f"Mark {fmt(mark,4)} • Unrealized {ur:.2f}R / ${upnl:,.2f}"
            )
            if st.button("إغلاق Paper يدوي", use_container_width=True):
                close_paper(p, mark, "MANUAL", ur)
                st.rerun()
        else:
            st.warning("المركز Paper مفتوح لكن تحديثه موقوف لأن سعر التنفيذ غير جاهز.")

    if st.session_state.paper_history:
        paper_df = pd.DataFrame(st.session_state.paper_history)
        st.dataframe(paper_df, hide_index=True, use_container_width=True)
        st.download_button(
            "تنزيل سجل Paper CSV",
            data=paper_df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"gold_ai_paper_{now_riyadh().date().isoformat()}.csv",
            mime="text/csv",
            use_container_width=True,
        )

# ---------------------------- Live ----------------------------
if mode == "Live":
    st.subheader("Live Execution — Broker Authoritative")
    st.caption(
        "في Live: Twelve Data للتحليل فقط. سعر الدخول وحالة الحساب والمراكز يجب أن تأتي من Broker Bridge."
    )

    if not live_unlocked:
        st.warning("LIVE_TRADING_ENABLED=false — التنفيذ الحقيقي مقفول.")
    if not bridge:
        st.warning("Broker Bridge غير مضبوط.")
    if broker_error:
        st.error(broker_error)
    if not contract_metadata_verified:
        st.warning(
            "BROKER_CONTRACT_METADATA_VERIFIED=false — يجب توثيق point value / qty step / min qty مع الوسيط قبل Live."
        )

    pin_secret = secret("LIVE_UI_PIN")
    entered_pin = st.text_input("Live UI PIN", value="", type="password")
    pin_ok = bool(
        pin_secret
        and entered_pin
        and hmac.compare_digest(str(entered_pin), str(pin_secret))
    )
    if not pin_secret:
        st.warning("LIVE_UI_PIN غير مضبوط في Secrets؛ Live سيبقى محجوبًا.")

    if account:
        equity = float(account.get("equity") or 0)
        day_pnl = float(account.get("day_pnl") or 0)
        day_start_equity = float(account.get("day_start_equity") or equity or 0)
        positions = account.get("positions")
        positions_verified = isinstance(positions, list)
        open_positions = int(
            account.get("open_positions")
            or (len(positions) if positions_verified else 0)
        )
        trading_enabled = bool(account.get("trading_enabled", True))

        same_symbol_open = False
        if positions_verified:
            target = instrument.symbol.strip().upper()
            for pos in positions:
                if not isinstance(pos, dict):
                    continue
                status = str(pos.get("status", "OPEN")).strip().upper()
                pos_symbol = str(pos.get("symbol", "")).strip().upper()
                if pos_symbol == target and status not in {
                    "CLOSED", "CANCELLED", "CANCELED", "FLAT"
                }:
                    same_symbol_open = True
                    break

        ac = st.columns(4)
        ac[0].metric("Equity", f"${equity:,.2f}")
        ac[1].metric("Day P&L", f"${day_pnl:,.2f}")
        ac[2].metric("Open Positions", open_positions)
        ac[3].metric("Broker Quote", "READY" if broker_quote.get("ok") else "BLOCKED")

        live_plan = None
        if (
            analysis["signal"] in {"BUY", "SELL"}
            and m5_snap
            and broker_quote.get("ok")
            and equity > 0
        ):
            try:
                live_plan = build_trade_plan(
                    analysis["signal"],
                    float(broker_quote["price"]),
                    float(m5_snap["atr"]),
                    equity,
                    float(risk_pct),
                    instrument,
                )
            except ValueError as exc:
                st.warning(f"Live sizing blocked: {exc}")

        if live_plan:
            st.markdown("### Broker-priced Live Plan")
            mini_grid(
                [
                    ("Side", live_plan["side"], "ok"),
                    ("Qty", fmt(live_plan["qty"], 6), ""),
                    ("Broker Entry", fmt(live_plan["entry_reference"], 4), ""),
                    ("SL", fmt(live_plan["stop_loss"], 4), "bad"),
                    ("TP1", fmt(live_plan["take_profit_1"], 4), "ok"),
                    ("TP2", fmt(live_plan["take_profit_2"], 4), "ok"),
                    ("Est. Risk", "$" + fmt(live_plan["estimated_risk"], 2), "wait"),
                    ("Actual Risk %", fmt(live_plan["actual_risk_pct"], 3) + "%", "ok"),
                ],
                "plan-grid",
            )

        live_gate_ok = False
        live_reasons: list[str] = []

        if live_plan:
            live_gate_ok, live_reasons = risk_gate(
                equity,
                day_pnl,
                open_positions,
                live_plan,
                float(max_daily_loss_pct),
                int(max_open_positions),
                float(max_order_risk_pct),
                trading_enabled,
                day_start_equity,
            )

        if not positions_verified:
            live_gate_ok = False
            live_reasons.append("الوسيط لم يرسل positions موثقة")
        if same_symbol_open:
            live_gate_ok = False
            live_reasons.append("يوجد مركز مفتوح بالفعل على نفس الرمز")
        if not broker_quote.get("ok"):
            live_gate_ok = False
            live_reasons.append(str(broker_quote.get("reason", "Broker Quote غير جاهز")))
        if not feed.get("trusted", False) or quality.get("age_min", math.inf) > 15:
            live_gate_ok = False
            live_reasons.append("بيانات التحليل غير حديثة/غير سليمة")
        if not contract_metadata_verified:
            live_gate_ok = False
            live_reasons.append("بيانات عقد الوسيط غير موثقة")
        if st.session_state.kill_switch:
            live_gate_ok = False
            live_reasons.append("KILL SWITCH مفعّل")
        if not live_unlocked:
            live_gate_ok = False
            live_reasons.append("Live غير مفتوح من Secrets")
        if not pin_ok:
            live_gate_ok = False
            live_reasons.append("Live UI PIN غير صحيح أو غير مُدخل")

        for reason in dict.fromkeys(live_reasons):
            st.warning(reason)

        confirm_text = st.text_input(
            "للتنفيذ اليدوي اكتب LIVE",
            value="",
            type="default",
        )
        can_submit = bool(
            live_plan
            and live_gate_ok
            and confirm_text.strip().upper() == "LIVE"
        )

        if st.button(
            "إرسال أمر Live",
            type="primary",
            use_container_width=True,
            disabled=not can_submit,
        ):
            payload = live_payload(
                live_plan,
                analysis,
                instrument,
                candle_id,
                broker_quote_payload or {},
            )
            try:
                result = bridge.submit_order(payload)
                st.session_state.orders.insert(
                    0,
                    {
                        "time": now_riyadh().isoformat(),
                        "request": payload,
                        "response": result,
                    },
                )
                st.success("تم قبول الأمر من Broker Bridge")
                st.json(result)
            except Exception as exc:
                st.error(str(exc))

        st.session_state.auto_live = st.toggle(
            "Auto Live",
            value=st.session_state.auto_live,
            disabled=not auto_live_unlocked,
            help="يحتاج AUTO_EXECUTION_ALLOWED=true بالإضافة لكل بوابات الأمان",
        )

        if (
            st.session_state.auto_live
            and auto_live_unlocked
            and live_plan
            and live_gate_ok
        ):
            if st.session_state.last_auto_live_candle.get(instrument.symbol) != candle_id:
                payload = live_payload(
                    live_plan,
                    analysis,
                    instrument,
                    candle_id,
                    broker_quote_payload or {},
                )
                try:
                    result = bridge.submit_order(payload)
                    st.session_state.last_auto_live_candle[instrument.symbol] = candle_id
                    st.session_state.orders.insert(
                        0,
                        {
                            "time": now_riyadh().isoformat(),
                            "request": payload,
                            "response": result,
                        },
                    )
                    st.success("Auto Live order accepted")
                except Exception as exc:
                    st.error(f"Auto Live failed: {exc}")

        if st.button("Emergency: Cancel All Orders", use_container_width=True):
            if st.session_state.kill_switch:
                try:
                    st.json(bridge.cancel_all())
                    st.success("تم إرسال طلب إلغاء جميع الأوامر")
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
            safe_rows.append(
                {
                    "time": item.get("time"),
                    "symbol": req.get("symbol"),
                    "side": req.get("side"),
                    "qty": req.get("quantity"),
                    "client_order_id": req.get("client_order_id"),
                    "status": res.get("status", res.get("state", "accepted")),
                    "broker_order_id": res.get("order_id", res.get("id")),
                }
            )
        st.dataframe(pd.DataFrame(safe_rows), hide_index=True, use_container_width=True)

# -------------------------- backtest --------------------------
st.subheader("Backtest Lab — Matched MTF")
st.caption(
    "يستخدم نفس منطق M5/M15/H1/H4 ونفس TP1/Break-even/TP2 تقريبًا، مع تكلفة تداول تقديرية. "
    "النتائج تشخيصية وليست ضمانًا للربحية."
)

if st.button("تشغيل Backtest متعدد الأطر", use_container_width=True):
    with st.spinner("تشغيل الاختبار..."):
        bt_trades, bt_stats = backtest_mtf(
            raw,
            instrument,
            risk_pct=float(risk_pct),
            cost_bps_roundtrip=2.0,
        )
        st.session_state.backtest = {
            "trades": bt_trades,
            "stats": bt_stats,
        }

if st.session_state.backtest:
    bt = st.session_state.backtest
    stats = bt["stats"]
    if stats.get("trades", 0):
        mini_grid(
            [
                ("Trades", str(stats["trades"]), ""),
                ("Win Rate", f"{stats['win_rate']:.1f}%", ""),
                ("Profit Factor", "∞" if not math.isfinite(stats["profit_factor"]) else f"{stats['profit_factor']:.2f}", ""),
                ("Net P&L", f"${stats['net_pnl']:,.2f}", "ok" if stats["net_pnl"] >= 0 else "bad"),
                ("Max DD", f"${stats['max_dd']:,.2f} / {stats['max_dd_pct']:.2f}%", "wait"),
                ("Ending Equity", f"${stats['ending_equity']:,.2f}", ""),
            ],
            "bt-grid",
        )
        st.dataframe(bt["trades"].tail(100), hide_index=True, use_container_width=True)
    else:
        st.info(stats.get("warning", "لا توجد نتائج"))

# --------------------------- health ---------------------------
st.subheader("System Health")

quote_age_label = (
    "N/A"
    if feed.get("quote_age_min") is None
    else f"{feed['quote_age_min']:.1f}m"
)
vol_ratio_label = (
    "N/A"
    if feed.get("volatility_ratio") is None
    else f"{feed['volatility_ratio']:.2f}x"
)
market_label = (
    "UNKNOWN"
    if feed.get("market_open") is None
    else ("OPEN" if feed.get("market_open") else "CLOSED")
)

health_rows = [
    {"Component": "Engine self-test", "Status": "ONLINE"},
    {"Component": "Analysis market data", "Status": "ONLINE" if not raw.empty else "BLOCKED"},
    {"Component": "Paper quote API", "Status": "ONLINE" if quote.get("connected") else "CHECK"},
    {"Component": "Feed structure", "Status": "OK" if feed.get("trusted") else "CHECK"},
    {"Component": "Paper execution feed", "Status": "READY" if feed.get("execution_ok") else "BLOCKED"},
    {"Component": "Paper engine", "Status": "ONLINE"},
    {"Component": "Broker bridge", "Status": "ONLINE" if account else ("CHECK" if bridge else "NOT CONFIGURED")},
    {"Component": "Broker quote", "Status": "READY" if broker_quote.get("ok") else ("BLOCKED" if bridge else "NOT CONFIGURED")},
    {"Component": "Contract metadata", "Status": "VERIFIED" if contract_metadata_verified else "UNVERIFIED"},
    {"Component": "Live trading", "Status": "UNLOCKED" if live_unlocked else "LOCKED"},
    {"Component": "Auto live", "Status": "UNLOCKED" if auto_live_unlocked else "LOCKED"},
]
st.dataframe(pd.DataFrame(health_rows), hide_index=True, use_container_width=True)

st.caption(
    f"{quality['label']} • Structure {'OK' if feed.get('trusted') else 'CHECK'} • "
    f"Paper Execution {'READY' if feed.get('execution_ok') else 'BLOCKED'} • "
    f"Market {market_label} • Quote age {quote_age_label} ({feed.get('quote_ts_source') or 'N/A'}) • "
    f"unique {feed.get('unique_ratio',0)*100:.0f}% • "
    f"alternation {feed.get('alternation_ratio',0)*100:.0f}% • "
    f"vol {vol_ratio_label} • {len(raw):,} bars"
)

with st.expander("Decision Log", expanded=False):
    if st.session_state.decisions:
        decisions_df = pd.DataFrame(st.session_state.decisions)
        st.dataframe(decisions_df, hide_index=True, use_container_width=True)
        st.download_button(
            "تنزيل سجل القرارات CSV",
            data=decisions_df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"gold_ai_decisions_{now_riyadh().date().isoformat()}.csv",
            mime="text/csv",
            use_container_width=True,
        )
    else:
        st.info("لا يوجد سجل بعد")

st.info(
    "Live يبقى مقفولًا افتراضيًا. قبل التنفيذ الحقيقي يلزم Broker Bridge فعلي، "
    "Broker Quote حديث، positions موثقة، بيانات عقد موثقة، LIVE_UI_PIN، "
    "وفتح LIVE_TRADING_ENABLED."
)

if st.session_state.auto_refresh:
    time.sleep(refresh_seconds)
    st.rerun()
