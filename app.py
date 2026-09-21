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
# GOLD AI v4.0 — X10 FINAL
# Single-file multi-asset Streamlit terminal.
# Analysis • Paper • normalized/capped audit • broker-authoritative Live
# ============================================================

VERSION = "4.5.1-x10-independent-fix"
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
        "independent_validation": None,
        "research_gate": {
            "passed": False,
            "context": None,
            "rules": [],
            "reasons": ["لم يتم تشغيل Final Research Audit في هذه الجلسة"],
        },
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)

    today = now_riyadh().date().isoformat()
    if st.session_state.paper_day != today:
        st.session_state.paper_day = today
        st.session_state.paper_day_start_balance = float(st.session_state.paper_balance)
        st.session_state.paper_trades_today = 0


init_state()

# Defensive session-state normalization for upgrades/hot reloads.
if st.session_state.get("independent_validation") is not None and not isinstance(
    st.session_state.get("independent_validation"), dict
):
    st.session_state.independent_validation = None

if st.session_state.get("research_gate") is None:
    st.session_state.research_gate = {
        "passed": False,
        "context": None,
        "rules": [],
        "reasons": ["Research Gate غير مهيأ في هذه الجلسة"],
    }

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



@st.cache_data(ttl=21600, show_spinner=False)
def fetch_long_history(
    symbol: str,
    history_days: int = 180,
    chunk_days: int = 17,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Fetch long M5 history using date windows small enough to stay below
    Twelve Data's 5,000-point limit per request.

    The function is cached for 6 hours so the audit does not repeatedly spend
    API credits. It also reads api-credits-left response headers and waits for
    the next minute only when the current quota is nearly exhausted.
    """
    key = secret("TWELVE_DATA_API_KEY")
    if not key:
        raise RuntimeError("TWELVE_DATA_API_KEY missing")

    end_ts = now_utc().floor("5min")
    start_ts = end_ts - pd.Timedelta(days=int(history_days))
    cursor = start_ts

    frames: list[pd.DataFrame] = []
    request_count = 0
    waits = 0
    credit_samples: list[int] = []
    errors: list[str] = []

    while cursor < end_ts:
        chunk_end = min(cursor + pd.Timedelta(days=int(chunk_days)), end_ts)

        params = {
            "symbol": symbol,
            "interval": "5min",
            "timezone": "UTC",
            "order": "asc",
            "start_date": cursor.strftime("%Y-%m-%d %H:%M:%S"),
            "end_date": chunk_end.strftime("%Y-%m-%d %H:%M:%S"),
            "apikey": key,
        }

        def _request_once() -> tuple[pd.DataFrame, int | None]:
            nonlocal request_count
            response = requests.get(DATA_URL, params=params, timeout=30)
            request_count += 1

            left_raw = response.headers.get("api-credits-left")
            left = None
            if left_raw is not None:
                try:
                    left = int(float(left_raw))
                except Exception:
                    left = None

            try:
                payload = response.json()
            except Exception as exc:
                raise RuntimeError(
                    f"Historical API returned non-JSON HTTP {response.status_code}"
                ) from exc

            if response.status_code >= 400 or "values" not in payload:
                message = str(payload.get("message") or payload)
                raise RuntimeError(f"Historical API HTTP {response.status_code}: {message}")

            frame = normalize_ohlcv(payload["values"])
            return frame, left

        try:
            frame, credits_left = _request_once()
        except RuntimeError as exc:
            msg = str(exc)
            rate_limited = (
                "429" in msg
                or "credit" in msg.lower()
                or "limit" in msg.lower()
            )
            if rate_limited:
                wait_seconds = max(3, 62 - int(now_utc().second))
                time.sleep(wait_seconds)
                waits += 1
                frame, credits_left = _request_once()
            else:
                raise

        if credits_left is not None:
            credit_samples.append(credits_left)

        if not frame.empty:
            frames.append(frame)

        # Basic plans can be as low as 8 API credits/min. If the provider tells
        # us the remaining credits are nearly exhausted, wait for the minute reset.
        if credits_left is not None and credits_left <= 1 and chunk_end < end_ts:
            wait_seconds = max(3, 62 - int(now_utc().second))
            time.sleep(wait_seconds)
            waits += 1

        cursor = chunk_end

    if not frames:
        raise RuntimeError("لم يتم جلب أي بيانات تاريخية صالحة")

    combined = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates("datetime")
        .sort_values("datetime")
        .reset_index(drop=True)
    )
    combined = closed_m5(combined)

    meta = {
        "requested_days": int(history_days),
        "bars": int(len(combined)),
        "requests": int(request_count),
        "quota_waits": int(waits),
        "first_bar": combined["datetime"].iloc[0].isoformat() if len(combined) else None,
        "last_bar": combined["datetime"].iloc[-1].isoformat() if len(combined) else None,
        "min_credits_left_seen": min(credit_samples) if credit_samples else None,
    }
    return combined, meta



@st.cache_data(ttl=21600, show_spinner=False)
def fetch_history_window(
    symbol: str,
    start_ts: str,
    end_ts: str,
    chunk_days: int = 17,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Fetch an explicit historical M5 window.

    Used only for independent validation so the candidate strategy is tested
    on data that predates the development window. The requested start/end are
    frozen before the test begins and are not optimized after seeing results.
    """
    key = secret("TWELVE_DATA_API_KEY")
    if not key:
        raise RuntimeError("TWELVE_DATA_API_KEY missing")

    start = pd.Timestamp(start_ts)
    end = pd.Timestamp(end_ts)
    if start.tzinfo is None:
        start = start.tz_localize("UTC")
    else:
        start = start.tz_convert("UTC")
    if end.tzinfo is None:
        end = end.tz_localize("UTC")
    else:
        end = end.tz_convert("UTC")

    if start >= end:
        raise RuntimeError("Independent validation window is invalid")

    cursor = start
    frames: list[pd.DataFrame] = []
    request_count = 0
    waits = 0
    credit_samples: list[int] = []

    while cursor < end:
        chunk_end = min(cursor + pd.Timedelta(days=int(chunk_days)), end)

        params = {
            "symbol": symbol,
            "interval": "5min",
            "timezone": "UTC",
            "order": "asc",
            "start_date": cursor.strftime("%Y-%m-%d %H:%M:%S"),
            "end_date": chunk_end.strftime("%Y-%m-%d %H:%M:%S"),
            "apikey": key,
        }

        def _request_once() -> tuple[pd.DataFrame, int | None]:
            nonlocal request_count
            response = requests.get(DATA_URL, params=params, timeout=30)
            request_count += 1

            left_raw = response.headers.get("api-credits-left")
            left = None
            if left_raw is not None:
                try:
                    left = int(float(left_raw))
                except Exception:
                    left = None

            try:
                payload = response.json()
            except Exception as exc:
                raise RuntimeError(
                    f"Historical API returned non-JSON HTTP {response.status_code}"
                ) from exc

            if response.status_code >= 400 or "values" not in payload:
                message = str(payload.get("message") or payload)
                raise RuntimeError(
                    f"Historical API HTTP {response.status_code}: {message}"
                )

            return normalize_ohlcv(payload["values"]), left

        try:
            frame, credits_left = _request_once()
        except RuntimeError as exc:
            msg = str(exc).lower()
            if "429" in msg or "credit" in msg or "limit" in msg:
                wait_seconds = max(3, 62 - int(now_utc().second))
                time.sleep(wait_seconds)
                waits += 1
                frame, credits_left = _request_once()
            else:
                raise

        if credits_left is not None:
            credit_samples.append(credits_left)

        if not frame.empty:
            frames.append(frame)

        if credits_left is not None and credits_left <= 1 and chunk_end < end:
            wait_seconds = max(3, 62 - int(now_utc().second))
            time.sleep(wait_seconds)
            waits += 1

        cursor = chunk_end

    if not frames:
        raise RuntimeError("لم يتم جلب بيانات لفترة Independent Validation")

    combined = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates("datetime")
        .sort_values("datetime")
        .reset_index(drop=True)
    )
    combined = closed_m5(combined)

    requested_days = max(
        1,
        int(round((end - start).total_seconds() / 86400.0)),
    )

    meta = {
        "requested_days": requested_days,
        "bars": int(len(combined)),
        "requests": int(request_count),
        "quota_waits": int(waits),
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "first_bar": combined["datetime"].iloc[0].isoformat()
        if len(combined) else None,
        "last_bar": combined["datetime"].iloc[-1].isoformat()
        if len(combined) else None,
        "min_credits_left_seen": min(credit_samples)
        if credit_samples else None,
    }
    return combined, meta


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
        "ema20": float(row["ema20"]),
        "ema50": float(row["ema50"]),
        "ema100": float(row["ema100"]) if finite(row.get("ema100")) else None,
        "candle": row["datetime"],
        "frame": calc,
    }


def b2_signal(frame: pd.DataFrame, lookback: int = 20, retest_atr: float = 0.30) -> dict[str, Any]:
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


def score_signal(
    snaps: dict[str, dict[str, Any]],
    b2: dict[str, Any],
) -> tuple[str, int, int, str]:
    """
    X10 GOLD FINAL strict-entry model.

    BUY/SELL is emitted only when every required condition is true.
    The percentage scores are diagnostic only; they do not override a failed rule.
    """
    m5 = snaps["M5"]
    m15 = snaps["M15"]
    h1 = snaps["H1"]
    h4 = snaps["H4"]

    buy_conditions = [
        h4["trend"] == "UP",
        h1["trend"] == "UP",
        m15["trend"] == "UP",
        m5["close"] > m5["ema20"] > m5["ema50"],
        finite(h1.get("ema100")) and h1["close"] > h1["ema100"],
        52 <= m5["rsi"] <= 68,
        m5["momentum"] > 0,
        m5["macd_hist"] > 0,
        max(m15["adx"], h1["adx"]) >= 20,
        b2.get("valid") and b2.get("side") == "BUY",
    ]

    sell_conditions = [
        h4["trend"] == "DOWN",
        h1["trend"] == "DOWN",
        m15["trend"] == "DOWN",
        m5["close"] < m5["ema20"] < m5["ema50"],
        finite(h1.get("ema100")) and h1["close"] < h1["ema100"],
        32 <= m5["rsi"] <= 48,
        m5["momentum"] < 0,
        m5["macd_hist"] < 0,
        max(m15["adx"], h1["adx"]) >= 20,
        b2.get("valid") and b2.get("side") == "SELL",
    ]

    buy_score = round(sum(bool(x) for x in buy_conditions) / len(buy_conditions) * 100)
    sell_score = round(sum(bool(x) for x in sell_conditions) / len(sell_conditions) * 100)

    if all(buy_conditions):
        return (
            "BUY",
            buy_score,
            sell_score,
            "X10 FINAL: اتجاه صاعد + زخم + Breakout/Retest مؤكد",
        )

    if all(sell_conditions):
        return (
            "SELL",
            buy_score,
            sell_score,
            "X10 FINAL: اتجاه هابط + زخم + Breakout/Retest مؤكد",
        )

    return (
        "WAIT",
        buy_score,
        sell_score,
        "X10 FINAL: بانتظار اكتمال جميع شروط الدخول",
    )

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
    stop_atr: float = 1.6,
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
    stepped_qty = floor_step(raw_qty, spec.qty_step)

    # Critical safety rule: never force the minimum quantity upward if it would
    # violate the requested risk budget.
    if stepped_qty < spec.min_qty:
        raise ValueError("الحد الأدنى للكمية يرفع المخاطرة فوق ميزانية الصفقة")

    max_qty_hit = stepped_qty > spec.max_qty
    qty = min(stepped_qty, spec.max_qty)

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
        "stepped_qty": float(stepped_qty),
        "max_qty_hit": bool(max_qty_hit),
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
        "strategy": "GOLD_AI_V40_X10_FINAL",
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

    close = pd.to_numeric(calc["close"], errors="coerce")
    ema20 = pd.to_numeric(calc["ema20"], errors="coerce")
    ema50 = pd.to_numeric(calc["ema50"], errors="coerce")
    ema100 = pd.to_numeric(calc["ema100"], errors="coerce")

    valid = close.notna() & ema20.notna() & ema50.notna()
    up = valid & (close > ema20) & (ema20 > ema50) & (ema100.isna() | (close > ema100))
    down = valid & (close < ema20) & (ema20 < ema50) & (ema100.isna() | (close < ema100))

    calc["trend"] = np.select([up, down], ["UP", "DOWN"], default="MIXED")
    calc["effective_time"] = calc["datetime"] + pd.Timedelta(rule)
    return calc


def precompute_b2(frame: pd.DataFrame) -> pd.DataFrame:
    """Vectorized b2_signal over the full M5 frame with identical lookback semantics."""
    out = frame.copy()
    lookback = 20
    retest_atr = 0.30

    high = pd.to_numeric(out["high"], errors="coerce")
    low = pd.to_numeric(out["low"], errors="coerce")
    close = pd.to_numeric(out["close"], errors="coerce")
    atr = pd.to_numeric(out["atr"], errors="coerce")

    # At row i, b2_signal uses rows i-21..i-2 as history and row i-1 as breakout.
    resistance = high.shift(2).rolling(lookback, min_periods=lookback).max()
    support = low.shift(2).rolling(lookback, min_periods=lookback).min()
    previous_close = close.shift(1)

    eligible = pd.Series(np.arange(len(out)) >= lookback + 2, index=out.index)
    atr_ok = atr.notna() & (atr > 0)
    bullish_break = eligible & atr_ok & (previous_close > resistance)
    bearish_break = eligible & atr_ok & (previous_close < support)

    bullish_retest = bullish_break & (low <= resistance + atr * retest_atr) & (close > resistance)
    bearish_retest = bearish_break & (high >= support - atr * retest_atr) & (close < support)

    out["b2_valid"] = (bullish_retest | bearish_retest).fillna(False)
    side = pd.Series([None] * len(out), index=out.index, dtype=object)
    side.loc[bullish_retest.fillna(False)] = "BUY"
    side.loc[bearish_retest.fillna(False)] = "SELL"
    out["b2_side"] = side
    out["b2_breakout"] = (bullish_break | bearish_break).fillna(False)
    return out

def _max_losing_streak(pnl: pd.Series) -> int:
    best = 0
    current = 0
    for value in pnl.fillna(0.0):
        if float(value) <= 0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _backtest_summary(df: pd.DataFrame, initial_equity: float = 100_000.0) -> dict[str, Any]:
    if df.empty:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "net_pnl": 0.0,
            "max_dd": 0.0,
            "max_dd_pct": 0.0,
            "ending_equity": initial_equity,
            "avg_r_net": 0.0,
            "risk_weighted_expectancy_r": 0.0,
            "avg_risk_money": 0.0,
            "median_risk_money": 0.0,
            "avg_risk_utilization_pct": 0.0,
            "max_qty_hit_count": 0,
            "max_qty_hit_pct": 0.0,
            "avg_duration_min": 0.0,
            "max_losing_streak": 0,
        }

    pnl = pd.to_numeric(df["PnL"], errors="coerce").fillna(0.0)
    gross_win = float(pnl[pnl > 0].sum())
    gross_loss = float(abs(pnl[pnl < 0].sum()))
    pf = gross_win / gross_loss if gross_loss else math.inf

    eq_curve = pd.Series([initial_equity] + (initial_equity + pnl.cumsum()).tolist())
    peaks = eq_curve.cummax()
    drawdown = peaks - eq_curve
    drawdown_pct = drawdown / peaks.replace(0, np.nan) * 100.0

    risk_money = pd.to_numeric(
        df["risk_money"] if "risk_money" in df.columns else pd.Series([0.0] * len(df)),
        errors="coerce",
    ).fillna(0.0)
    risk_budget = pd.to_numeric(
        df["risk_budget"] if "risk_budget" in df.columns else risk_money,
        errors="coerce",
    ).fillna(0.0)
    total_risk = float(risk_money.sum())
    weighted_expectancy = float(pnl.sum() / total_risk) if total_risk > 0 else 0.0

    if "risk_utilization_pct" in df.columns:
        risk_utilization = pd.to_numeric(df["risk_utilization_pct"], errors="coerce").fillna(0.0)
    else:
        risk_utilization = pd.Series(
            np.where(risk_budget > 0, risk_money / risk_budget * 100.0, 0.0)
        )

    if "max_qty_hit" in df.columns:
        max_qty_hits = df["max_qty_hit"].fillna(False).astype(bool)
    else:
        max_qty_hits = pd.Series([False] * len(df))

    return {
        "trades": int(len(df)),
        "win_rate": float((pnl > 0).mean() * 100.0),
        "profit_factor": float(pf),
        "net_pnl": float(pnl.sum()),
        "max_dd": float(drawdown.max()),
        "max_dd_pct": float(drawdown_pct.max()),
        "ending_equity": float(initial_equity + pnl.sum()),
        "avg_r_net": float(pd.to_numeric(df["R_net"], errors="coerce").mean()),
        "risk_weighted_expectancy_r": weighted_expectancy,
        "avg_risk_money": float(risk_money.mean()) if len(risk_money) else 0.0,
        "median_risk_money": float(risk_money.median()) if len(risk_money) else 0.0,
        "avg_risk_utilization_pct": float(risk_utilization.mean()) if len(risk_utilization) else 0.0,
        "max_qty_hit_count": int(max_qty_hits.sum()),
        "max_qty_hit_pct": float(max_qty_hits.mean() * 100.0) if len(max_qty_hits) else 0.0,
        "avg_duration_min": float(pd.to_numeric(df["duration_min"], errors="coerce").mean()),
        "max_losing_streak": int(_max_losing_streak(pnl)),
    }



def _fixed_risk_normalized(
    df: pd.DataFrame,
    initial_equity: float = 100_000.0,
    risk_pct: float = 0.5,
    cost_bps_roundtrip: float = 2.0,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Reprice the SAME historical entries/exits with uncapped, fixed-percent risk.

    This removes max_qty/qty-step sizing distortion and answers one question:
    does the signal/exit logic itself have positive expectancy before broker caps?
    """
    if df.empty:
        empty = pd.DataFrame()
        return empty, _backtest_summary(empty, initial_equity)

    equity = float(initial_equity)
    rows: list[dict[str, Any]] = []

    for _, trade in df.sort_values("opened").iterrows():
        stop_distance = float(trade.get("stop_distance", 0.0))
        point_value = float(trade.get("point_value", 0.0))
        entry = float(trade.get("entry", 0.0))
        r_gross = float(trade.get("R_gross", 0.0))

        if stop_distance <= 0 or point_value <= 0 or equity <= 0:
            continue

        risk_budget = equity * float(risk_pct) / 100.0
        qty_uncapped = risk_budget / (stop_distance * point_value)
        notional = abs(entry * qty_uncapped * point_value)
        costs = notional * (float(cost_bps_roundtrip) / 10_000.0)
        gross_pnl = r_gross * risk_budget
        pnl = gross_pnl - costs
        r_net = pnl / risk_budget if risk_budget > 0 else 0.0
        equity += pnl

        row = dict(trade)
        row.update(
            {
                "normalized_qty": float(qty_uncapped),
                "normalized_risk_money": float(risk_budget),
                "normalized_costs": float(costs),
                "normalized_pnl": float(pnl),
                "normalized_r_net": float(r_net),
                "normalized_equity": float(equity),
                # Map to common summary field names.
                "PnL": float(pnl),
                "R_net": float(r_net),
                "risk_money": float(risk_budget),
                "risk_budget": float(risk_budget),
                "risk_utilization_pct": 100.0,
                "max_qty_hit": False,
            }
        )
        rows.append(row)

    norm = pd.DataFrame(rows)
    return norm, _backtest_summary(norm, initial_equity)


def _research_gate(
    capped_stats: dict[str, Any],
    cost_scenarios: pd.DataFrame,
    context: str,
    history_days: int,
    history_bars: int,
) -> dict[str, Any]:
    """
    Conservative research gate. Passing it is NOT a promise of profitability.
    It only prevents Live from being enabled on obviously weak/fragile evidence.
    """
    oos = capped_stats.get("normalized_out_of_sample", {})
    normalized = capped_stats.get("normalized", {})

    row10 = None
    if isinstance(cost_scenarios, pd.DataFrame) and not cost_scenarios.empty:
        matches = cost_scenarios[
            pd.to_numeric(cost_scenarios["Round-trip cost (bps)"], errors="coerce") == 10.0
        ]
        if not matches.empty:
            row10 = matches.iloc[0].to_dict()

    rules = [
        {
            "name": "التاريخ المطلوب ≥ 180 يوم",
            "pass": int(history_days) >= 180,
            "value": int(history_days),
        },
        {
            "name": "شموع M5 التاريخية ≥ 20,000",
            "pass": int(history_bars) >= 20_000,
            "value": int(history_bars),
        },
        {
            "name": "إجمالي الصفقات ≥ 40",
            "pass": int(capped_stats.get("trades", 0)) >= 40,
            "value": int(capped_stats.get("trades", 0)),
        },
        {
            "name": "OUT normalized trades ≥ 12",
            "pass": int(oos.get("trades", 0)) >= 12,
            "value": int(oos.get("trades", 0)),
        },
        {
            "name": "OUT normalized PF ≥ 1.20",
            "pass": float(oos.get("profit_factor", 0.0)) >= 1.20,
            "value": float(oos.get("profit_factor", 0.0)),
        },
        {
            "name": "OUT normalized Avg R ≥ +0.05R",
            "pass": float(oos.get("avg_r_net", 0.0)) >= 0.05,
            "value": float(oos.get("avg_r_net", 0.0)),
        },
        {
            "name": "10 bps normalized PF ≥ 1.10",
            "pass": bool(row10) and float(row10.get("Normalized PF", 0.0)) >= 1.10,
            "value": None if row10 is None else float(row10.get("Normalized PF", 0.0)),
        },
        {
            "name": "10 bps normalized P&L > 0",
            "pass": bool(row10) and float(row10.get("Normalized Net P&L", 0.0)) > 0.0,
            "value": None if row10 is None else float(row10.get("Normalized Net P&L", 0.0)),
        },
        {
            "name": "Normalized Max DD ≤ 5%",
            "pass": float(normalized.get("max_dd_pct", 999.0)) <= 5.0,
            "value": float(normalized.get("max_dd_pct", 999.0)),
        },
        {
            "name": "Max-qty hits ≤ 20%",
            "pass": float(capped_stats.get("max_qty_hit_pct", 100.0)) <= 20.0,
            "value": float(capped_stats.get("max_qty_hit_pct", 100.0)),
        },
    ]

    failed = [r["name"] for r in rules if not r["pass"]]
    return {
        "passed": not failed,
        "context": context,
        "rules": rules,
        "reasons": failed,
        "checked_at": now_riyadh().isoformat(),
    }


def _group_audit(df: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if df.empty or column not in df.columns:
        return rows
    for key, part in df.groupby(column, dropna=False):
        s = _backtest_summary(part)
        rows.append(
            {
                column: key,
                "trades": s["trades"],
                "win_rate": round(s["win_rate"], 1),
                "profit_factor": math.inf if not math.isfinite(s["profit_factor"]) else round(s["profit_factor"], 2),
                "net_pnl": round(s["net_pnl"], 2),
                "avg_r_net": round(s["avg_r_net"], 3),
                "risk_weighted_expectancy_r": round(s["risk_weighted_expectancy_r"], 3),
                "avg_risk_money": round(s["avg_risk_money"], 2),
                "max_qty_hit_pct": round(s["max_qty_hit_pct"], 1),
            }
        )
    return rows


def backtest_mtf(
    raw: pd.DataFrame,
    spec: InstrumentSpec,
    risk_pct: float = 0.5,
    cost_bps_roundtrip: float = 2.0,
    research_variant: str = "STRICT_BOTH",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if len(raw) < 3500:
        return pd.DataFrame(), {
            "trades": 0,
            "warning": "الـBacktest متعدد الأطر يحتاج تقريبًا 3500 شمعة M5 على الأقل",
        }

    base = raw.copy().reset_index(drop=True)
    split_index = max(1, min(len(base) - 1, int(len(base) * 0.70)))
    split_time = pd.Timestamp(base["datetime"].iloc[split_index])

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
            "ema20", "ema50", "ema100",
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

    # Research-only volatility regime built from PRIOR bars only.
    m5_full["atr_pct"] = (m5_full["atr"] / m5_full["close"].replace(0, np.nan)) * 100.0
    vol_window = 288 * 10
    m5_full["atr_pct_q20"] = (
        m5_full["atr_pct"].shift(1).rolling(vol_window, min_periods=288 * 3).quantile(0.20)
    )
    m5_full["atr_pct_q90"] = (
        m5_full["atr_pct"].shift(1).rolling(vol_window, min_periods=288 * 3).quantile(0.90)
    )
    m5_full["effective_time"] = m5_full["datetime"] + pd.Timedelta("5min")

    bt = m5_full[
        [
            "effective_time", "datetime", "open", "high", "low", "close",
            "ema20", "ema50",
            "rsi", "adx", "atr", "momentum", "macd_hist", "trend",
            "atr_pct", "atr_pct_q20", "atr_pct_q90",
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
            "ema20": "ema20_m5",
            "ema50": "ema50_m5",
            "atr_pct": "atr_pct_m5",
            "atr_pct_q20": "atr_pct_q20_m5",
            "atr_pct_q90": "atr_pct_q90_m5",
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
            "close_m5", "ema20_m5", "ema50_m5",
            "rsi_m5", "adx_m5", "atr_m5", "momentum_m5", "macd_hist_m5",
            "adx_m15", "adx_h1", "close_h1", "ema100_h1",
        ]
    ).reset_index(drop=True)

    equity = 100_000.0
    initial_equity = equity
    trades: list[dict[str, Any]] = []
    position: dict[str, Any] | None = None
    raw_by_time = base.set_index("datetime")

    weekday_map = {
        0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu",
        4: "Fri", 5: "Sat", 6: "Sun",
    }

    for _, row in bt.iterrows():
        eval_time = pd.Timestamp(row["effective_time"])
        if eval_time not in raw_by_time.index:
            continue
        nxt = raw_by_time.loc[eval_time]
        if isinstance(nxt, pd.DataFrame):
            nxt = nxt.iloc[0]

        snaps = {
            "M5": {
                "trend": row["trend_m5"],
                "close": float(row["close_m5"]),
                "ema20": float(row["ema20_m5"]),
                "ema50": float(row["ema50_m5"]),
                "rsi": float(row["rsi_m5"]),
                "adx": float(row["adx_m5"]),
                "atr": float(row["atr_m5"]),
                "momentum": float(row["momentum_m5"]),
                "macd_hist": float(row["macd_hist_m5"]),
            },
            "M15": {
                "trend": row["trend_m15"],
                "adx": float(row["adx_m15"]),
            },
            "H1": {
                "trend": row["trend_h1"],
                "adx": float(row["adx_h1"]),
                "close": float(row["close_h1"]),
                "ema100": float(row["ema100_h1"]),
            },
            "H4": {
                "trend": row["trend_h4"],
            },
        }
        b2 = {"valid": bool(row["b2_valid"]), "side": row["b2_side"]}
        signal, buy_score, sell_score, _ = score_signal(snaps, b2)

        # Research variants affect backtests only, never the live decision engine.
        entry_hour = int(pd.Timestamp(eval_time).hour)
        atr_pct_now = float(row.get("atr_pct_m5", np.nan))
        atr_q20 = float(row.get("atr_pct_q20_m5", np.nan))
        atr_q90 = float(row.get("atr_pct_q90_m5", np.nan))

        if research_variant == "SELL_ONLY":
            if signal != "SELL":
                signal = "WAIT"

        elif research_variant == "SELL_SESSION":
            if signal != "SELL" or not (6 <= entry_hour < 20):
                signal = "WAIT"

        elif research_variant == "SELL_SESSION_VOL":
            regime_ok = (
                finite(atr_pct_now)
                and finite(atr_q20)
                and finite(atr_q90)
                and atr_q20 <= atr_pct_now <= atr_q90
            )
            if signal != "SELL" or not (6 <= entry_hour < 20) or not regime_ok:
                signal = "WAIT"

        opened_this_bar = False
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
                "risk_budget": plan["risk_budget"],
                "actual_risk_pct": plan["actual_risk_pct"],
                "raw_qty": plan["raw_qty"],
                "stepped_qty": plan["stepped_qty"],
                "max_qty_hit": plan["max_qty_hit"],
                "qty": plan["qty"],
                "point_value": spec.point_value,
                "remaining": 1.0,
                "realized_r": 0.0,
                "tp1_hit": False,
                "opened": eval_time,
                "buy_score": buy_score,
                "sell_score": sell_score,
            }
            opened_this_bar = True

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

        # Entry occurs at the M5 open. Intrabar order is unknown, so stop is
        # checked before targets. A same-bar exit is recorded at bar end (+5m)
        # rather than showing identical open/close timestamps.
        if side == "BUY":
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
            gross = float(total_r * position["risk"])
            notional = abs(position["entry"] * position["qty"] * position["point_value"])
            costs = float(notional * (cost_bps_roundtrip / 10_000.0))
            pnl = float(gross - costs)
            equity += pnl

            close_time = eval_time + pd.Timedelta(minutes=5)
            duration_min = max(
                5.0,
                (close_time - pd.Timestamp(position["opened"])).total_seconds() / 60.0,
            )
            r_net = pnl / position["risk"] if position["risk"] > 0 else 0.0
            opened_ts = pd.Timestamp(position["opened"])

            trades.append(
                {
                    "opened": opened_ts,
                    "closed": close_time,
                    "side": side,
                    "segment": "IN" if opened_ts < split_time else "OUT",
                    "entry_hour_utc": int(opened_ts.hour),
                    "entry_weekday": weekday_map[int(opened_ts.dayofweek)],
                    "duration_min": float(duration_min),
                    "entry": float(position["entry"]),
                    "exit": float(exit_price),
                    "stop_distance": float(d),
                    "point_value": float(position["point_value"]),
                    "R_gross": float(total_r),
                    "R_net": float(r_net),
                    "risk_money": float(position["risk"]),
                    "risk_budget": float(position["risk_budget"]),
                    "risk_utilization_pct": (
                        float(position["risk"]) / float(position["risk_budget"]) * 100.0
                        if float(position["risk_budget"]) > 0 else 0.0
                    ),
                    "actual_risk_pct": float(position["actual_risk_pct"]),
                    "raw_qty": float(position["raw_qty"]),
                    "stepped_qty": float(position["stepped_qty"]),
                    "qty": float(position["qty"]),
                    "max_qty_hit": bool(position["max_qty_hit"]),
                    "gross_pnl": gross,
                    "costs": costs,
                    "PnL": pnl,
                    "equity": float(equity),
                    "reason": exit_reason,
                    "same_bar_exit": bool(opened_this_bar),
                }
            )
            position = None

    if not trades:
        return pd.DataFrame(), {"trades": 0, "warning": "لم ينتج الاختبار صفقات"}

    df = pd.DataFrame(trades).sort_values("opened").reset_index(drop=True)
    stats = _backtest_summary(df, initial_equity)
    stats["cost_bps_roundtrip"] = float(cost_bps_roundtrip)
    stats["split_time"] = split_time.isoformat()
    stats["research_variant"] = research_variant

    in_df = df[df["segment"] == "IN"].copy()
    out_df = df[df["segment"] == "OUT"].copy()
    stats["in_sample"] = _backtest_summary(in_df, initial_equity)
    stats["out_of_sample"] = _backtest_summary(out_df, initial_equity)
    stats["side_stats"] = _group_audit(df, "side")
    stats["hour_stats"] = _group_audit(df, "entry_hour_utc")
    stats["day_stats"] = _group_audit(df, "entry_weekday")

    _, normalized_stats = _fixed_risk_normalized(
        df,
        initial_equity=initial_equity,
        risk_pct=risk_pct,
        cost_bps_roundtrip=cost_bps_roundtrip,
    )
    _, normalized_in = _fixed_risk_normalized(
        in_df,
        initial_equity=initial_equity,
        risk_pct=risk_pct,
        cost_bps_roundtrip=cost_bps_roundtrip,
    )
    _, normalized_out = _fixed_risk_normalized(
        out_df,
        initial_equity=initial_equity,
        risk_pct=risk_pct,
        cost_bps_roundtrip=cost_bps_roundtrip,
    )
    stats["normalized"] = normalized_stats
    stats["normalized_in_sample"] = normalized_in
    stats["normalized_out_of_sample"] = normalized_out

    return df, stats



# ---------------- optimized research engine ----------------
def prepare_research_context(raw: pd.DataFrame) -> dict[str, Any]:
    """Prepare indicators/MTF/B2/signals once and reuse them across all research runs."""
    if len(raw) < 3500:
        return {"ok": False, "warning": "الاختبار يحتاج تقريبًا 3500 شمعة M5 على الأقل"}

    t0 = time.perf_counter()
    base = raw.copy().reset_index(drop=True)
    split_index = max(1, min(len(base) - 1, int(len(base) * 0.70)))
    split_time = pd.Timestamp(base["datetime"].iloc[split_index])

    tf_frames = {
        "M15": (
            base.set_index("datetime")[["open", "high", "low", "close"]]
            .resample("15min", label="left", closed="left")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna().reset_index()
        ),
        "H1": (
            base.set_index("datetime")[["open", "high", "low", "close"]]
            .resample("1h", label="left", closed="left")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna().reset_index()
        ),
        "H4": (
            base.set_index("datetime")[["open", "high", "low", "close"]]
            .resample("4h", label="left", closed="left")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna().reset_index()
        ),
    }

    features: dict[str, pd.DataFrame] = {}
    for tf, frame in tf_frames.items():
        f = feature_frame(frame, TF_RULES[tf])
        keep = [
            "effective_time", "datetime", "open", "high", "low", "close",
            "ema20", "ema50", "ema100", "rsi", "adx", "atr", "momentum", "macd_hist", "trend",
        ]
        f = f[keep].dropna(subset=["rsi", "adx", "atr", "momentum", "macd_hist"])
        suffix = tf.lower()
        f = f.rename(columns={c: f"{c}_{suffix}" for c in f.columns if c != "effective_time"})
        features[tf] = f.sort_values("effective_time").reset_index(drop=True)

    m5 = add_indicators(base).dropna(
        subset=["ema20", "ema50", "rsi", "atr", "macd_hist", "momentum", "adx"]
    ).reset_index(drop=True)

    close = pd.to_numeric(m5["close"], errors="coerce")
    ema20 = pd.to_numeric(m5["ema20"], errors="coerce")
    ema50 = pd.to_numeric(m5["ema50"], errors="coerce")
    ema100 = pd.to_numeric(m5["ema100"], errors="coerce")
    m5["trend"] = np.select(
        [
            (close > ema20) & (ema20 > ema50) & (ema100.isna() | (close > ema100)),
            (close < ema20) & (ema20 < ema50) & (ema100.isna() | (close < ema100)),
        ],
        ["UP", "DOWN"],
        default="MIXED",
    )
    m5 = precompute_b2(m5)

    m5["atr_pct"] = (m5["atr"] / m5["close"].replace(0, np.nan)) * 100.0
    vol_window = 288 * 10
    m5["atr_pct_q20"] = m5["atr_pct"].shift(1).rolling(vol_window, min_periods=288 * 3).quantile(0.20)
    m5["atr_pct_q90"] = m5["atr_pct"].shift(1).rolling(vol_window, min_periods=288 * 3).quantile(0.90)
    m5["effective_time"] = m5["datetime"] + pd.Timedelta("5min")

    bt = m5[[
        "effective_time", "datetime", "close", "ema20", "ema50", "rsi", "adx", "atr",
        "momentum", "macd_hist", "trend", "atr_pct", "atr_pct_q20", "atr_pct_q90",
        "b2_valid", "b2_side",
    ]].copy().rename(columns={
        "datetime": "datetime_m5", "close": "close_m5", "ema20": "ema20_m5", "ema50": "ema50_m5",
        "rsi": "rsi_m5", "adx": "adx_m5", "atr": "atr_m5", "momentum": "momentum_m5",
        "macd_hist": "macd_hist_m5", "trend": "trend_m5", "atr_pct": "atr_pct_m5",
        "atr_pct_q20": "atr_pct_q20_m5", "atr_pct_q90": "atr_pct_q90_m5",
    }).sort_values("effective_time")

    for tf in ("M15", "H1", "H4"):
        bt = pd.merge_asof(
            bt.sort_values("effective_time"), features[tf].sort_values("effective_time"),
            on="effective_time", direction="backward",
        )

    bt = bt.dropna(subset=[
        "trend_m5", "trend_m15", "trend_h1", "trend_h4", "close_m5", "ema20_m5", "ema50_m5",
        "rsi_m5", "adx_m5", "atr_m5", "momentum_m5", "macd_hist_m5", "adx_m15", "adx_h1",
        "close_h1", "ema100_h1",
    ]).reset_index(drop=True)

    adx_ok = np.maximum(bt["adx_m15"].astype(float), bt["adx_h1"].astype(float)) >= 20
    buy_conditions = [
        bt["trend_h4"].eq("UP"), bt["trend_h1"].eq("UP"), bt["trend_m15"].eq("UP"),
        (bt["close_m5"] > bt["ema20_m5"]) & (bt["ema20_m5"] > bt["ema50_m5"]),
        bt["close_h1"] > bt["ema100_h1"], bt["rsi_m5"].between(52, 68, inclusive="both"),
        bt["momentum_m5"] > 0, bt["macd_hist_m5"] > 0, adx_ok,
        bt["b2_valid"].astype(bool) & bt["b2_side"].eq("BUY"),
    ]
    sell_conditions = [
        bt["trend_h4"].eq("DOWN"), bt["trend_h1"].eq("DOWN"), bt["trend_m15"].eq("DOWN"),
        (bt["close_m5"] < bt["ema20_m5"]) & (bt["ema20_m5"] < bt["ema50_m5"]),
        bt["close_h1"] < bt["ema100_h1"], bt["rsi_m5"].between(32, 48, inclusive="both"),
        bt["momentum_m5"] < 0, bt["macd_hist_m5"] < 0, adx_ok,
        bt["b2_valid"].astype(bool) & bt["b2_side"].eq("SELL"),
    ]
    buy_count = sum(c.astype(np.int8) for c in buy_conditions)
    sell_count = sum(c.astype(np.int8) for c in sell_conditions)
    bt["buy_score"] = (buy_count * 10).astype(np.int16)
    bt["sell_score"] = (sell_count * 10).astype(np.int16)
    bt["base_signal"] = np.select(
        [buy_count.eq(10), sell_count.eq(10)], ["BUY", "SELL"], default="WAIT"
    )
    bt["entry_hour_utc"] = pd.to_datetime(bt["effective_time"], utc=True).dt.hour.astype(np.int8)
    bt["regime_ok"] = (
        bt["atr_pct_m5"].notna() & bt["atr_pct_q20_m5"].notna() & bt["atr_pct_q90_m5"].notna()
        & (bt["atr_pct_m5"] >= bt["atr_pct_q20_m5"]) & (bt["atr_pct_m5"] <= bt["atr_pct_q90_m5"])
    )

    # effective_time is the next M5 bar, which is the execution/evaluation bar in the original engine.
    execution = base[["datetime", "open", "high", "low", "close"]].rename(columns={
        "datetime": "effective_time", "open": "open_exec", "high": "high_exec",
        "low": "low_exec", "close": "close_exec",
    })
    bt = bt.merge(execution, on="effective_time", how="inner", validate="many_to_one")
    bt = bt.sort_values("effective_time").reset_index(drop=True)

    return {
        "ok": True, "bt": bt, "split_time": split_time, "bars": int(len(base)),
        "prepared_rows": int(len(bt)), "prepare_seconds": float(time.perf_counter() - t0),
    }


def _variant_signal(base_signal: str, hour: int, regime_ok: bool, variant: str) -> str:
    if variant == "SELL_ONLY":
        return "SELL" if base_signal == "SELL" else "WAIT"
    if variant == "SELL_SESSION":
        return "SELL" if base_signal == "SELL" and 6 <= hour < 20 else "WAIT"
    if variant == "SELL_SESSION_VOL":
        return "SELL" if base_signal == "SELL" and 6 <= hour < 20 and regime_ok else "WAIT"
    return base_signal


def simulate_prepared_research(
    prepared: dict[str, Any], spec: InstrumentSpec, risk_pct: float,
    cost_bps_roundtrip: float = 2.0, research_variant: str = "STRICT_BOTH",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not prepared.get("ok", False):
        return pd.DataFrame(), {"trades": 0, "warning": prepared.get("warning", "prepare failed")}

    bt = prepared["bt"]
    split_time = pd.Timestamp(prepared["split_time"])
    equity = 100_000.0
    initial_equity = equity
    trades: list[dict[str, Any]] = []
    position: dict[str, Any] | None = None
    weekday_map = {0:"Mon",1:"Tue",2:"Wed",3:"Thu",4:"Fri",5:"Sat",6:"Sun"}

    for row in bt.itertuples(index=False, name="BT"):
        eval_time = pd.Timestamp(row.effective_time)
        signal = _variant_signal(str(row.base_signal), int(row.entry_hour_utc), bool(row.regime_ok), research_variant)
        opened_this_bar = False

        if position is None and signal in {"BUY", "SELL"}:
            try:
                plan = build_trade_plan(signal, float(row.open_exec), float(row.atr_m5), equity, risk_pct, spec)
            except ValueError:
                continue
            position = {
                "side": signal, "entry": plan["entry_reference"], "stop": plan["stop_loss"],
                "stop_initial": plan["stop_loss"], "tp1": plan["take_profit_1"], "tp2": plan["take_profit_2"],
                "risk": plan["estimated_risk"], "risk_budget": plan["risk_budget"],
                "actual_risk_pct": plan["actual_risk_pct"], "raw_qty": plan["raw_qty"],
                "stepped_qty": plan["stepped_qty"], "max_qty_hit": plan["max_qty_hit"], "qty": plan["qty"],
                "point_value": spec.point_value, "remaining": 1.0, "realized_r": 0.0, "tp1_hit": False,
                "opened": eval_time, "buy_score": int(row.buy_score), "sell_score": int(row.sell_score),
            }
            opened_this_bar = True

        if position is None:
            continue

        hi, lo = float(row.high_exec), float(row.low_exec)
        side = position["side"]
        d = abs(position["entry"] - position["stop_initial"])
        if d <= 0:
            position = None
            continue

        closed=False; exit_price=None; exit_reason=None; total_r=None
        if side == "BUY":
            if lo <= position["stop"]:
                total_r = position["realized_r"] + position["remaining"] * ((position["stop"]-position["entry"])/d)
                exit_price, exit_reason, closed = position["stop"], "STOP", True
            else:
                if not position["tp1_hit"] and hi >= position["tp1"]:
                    position["tp1_hit"] = True; position["remaining"] = 0.5; position["realized_r"] = 0.5; position["stop"] = position["entry"]
                if position["tp1_hit"] and hi >= position["tp2"]:
                    total_r = position["realized_r"] + 0.5 * ((position["tp2"]-position["entry"])/d)
                    exit_price, exit_reason, closed = position["tp2"], "TP2", True
        else:
            if hi >= position["stop"]:
                total_r = position["realized_r"] + position["remaining"] * ((position["entry"]-position["stop"])/d)
                exit_price, exit_reason, closed = position["stop"], "STOP", True
            else:
                if not position["tp1_hit"] and lo <= position["tp1"]:
                    position["tp1_hit"] = True; position["remaining"] = 0.5; position["realized_r"] = 0.5; position["stop"] = position["entry"]
                if position["tp1_hit"] and lo <= position["tp2"]:
                    total_r = position["realized_r"] + 0.5 * ((position["entry"]-position["tp2"])/d)
                    exit_price, exit_reason, closed = position["tp2"], "TP2", True

        if closed and total_r is not None:
            gross = float(total_r * position["risk"])
            notional = abs(position["entry"] * position["qty"] * position["point_value"])
            costs = float(notional * (cost_bps_roundtrip / 10_000.0))
            pnl = float(gross - costs); equity += pnl
            close_time = eval_time + pd.Timedelta(minutes=5)
            duration_min = max(5.0, (close_time - pd.Timestamp(position["opened"])).total_seconds()/60.0)
            r_net = pnl / position["risk"] if position["risk"] > 0 else 0.0
            opened_ts = pd.Timestamp(position["opened"])
            trades.append({
                "opened": opened_ts, "closed": close_time, "side": side,
                "segment": "IN" if opened_ts < split_time else "OUT",
                "entry_hour_utc": int(opened_ts.hour), "entry_weekday": weekday_map[int(opened_ts.dayofweek)],
                "duration_min": float(duration_min), "entry": float(position["entry"]), "exit": float(exit_price),
                "stop_distance": float(d), "point_value": float(position["point_value"]),
                "R_gross": float(total_r), "R_net": float(r_net), "risk_money": float(position["risk"]),
                "risk_budget": float(position["risk_budget"]),
                "risk_utilization_pct": float(position["risk"])/float(position["risk_budget"])*100.0 if float(position["risk_budget"])>0 else 0.0,
                "actual_risk_pct": float(position["actual_risk_pct"]), "raw_qty": float(position["raw_qty"]),
                "stepped_qty": float(position["stepped_qty"]), "qty": float(position["qty"]),
                "max_qty_hit": bool(position["max_qty_hit"]), "gross_pnl": gross, "costs": costs, "PnL": pnl,
                "equity": float(equity), "reason": exit_reason, "same_bar_exit": bool(opened_this_bar),
            })
            position = None

    if not trades:
        return pd.DataFrame(), {"trades":0,"warning":"لم ينتج الاختبار صفقات","research_variant":research_variant}

    df = pd.DataFrame(trades).sort_values("opened").reset_index(drop=True)
    stats = _backtest_summary(df, initial_equity)
    stats["cost_bps_roundtrip"] = float(cost_bps_roundtrip)
    stats["split_time"] = split_time.isoformat()
    stats["research_variant"] = research_variant
    in_df = df[df["segment"]=="IN"].copy(); out_df = df[df["segment"]=="OUT"].copy()
    stats["in_sample"] = _backtest_summary(in_df, initial_equity)
    stats["out_of_sample"] = _backtest_summary(out_df, initial_equity)
    stats["side_stats"] = _group_audit(df, "side")
    stats["hour_stats"] = _group_audit(df, "entry_hour_utc")
    stats["day_stats"] = _group_audit(df, "entry_weekday")
    _, stats["normalized"] = _fixed_risk_normalized(df, initial_equity, risk_pct, cost_bps_roundtrip)
    _, stats["normalized_in_sample"] = _fixed_risk_normalized(in_df, initial_equity, risk_pct, cost_bps_roundtrip)
    _, stats["normalized_out_of_sample"] = _fixed_risk_normalized(out_df, initial_equity, risk_pct, cost_bps_roundtrip)
    return df, stats


def _research_robustness(trades: pd.DataFrame) -> dict[str, Any]:
    """Extra diagnostics: chronological quarters, bootstrap mean-R CI, and cost break-even."""
    if trades.empty:
        return {"quarters": [], "bootstrap_low": 0.0, "bootstrap_high": 0.0, "cost_break_even_bps": 0.0}

    ordered = trades.sort_values("opened").reset_index(drop=True)
    quarter_rows=[]
    for q, part in enumerate(np.array_split(ordered, 4), start=1):
        ss=_backtest_summary(part)
        quarter_rows.append({
            "Quarter": f"Q{q}", "Trades": ss["trades"], "PF": math.inf if not math.isfinite(ss["profit_factor"]) else round(ss["profit_factor"],2),
            "Avg R": round(ss["avg_r_net"],3), "Net P&L": round(ss["net_pnl"],2), "Max DD %": round(ss["max_dd_pct"],2),
        })

    r = pd.to_numeric(ordered["R_net"], errors="coerce").dropna().to_numpy(float)
    if len(r):
        rng=np.random.default_rng(44)
        means=rng.choice(r, size=(1000,len(r)), replace=True).mean(axis=1)
        low, high = np.quantile(means,[0.025,0.975])
    else:
        low=high=0.0

    gross = float(pd.to_numeric(ordered["gross_pnl"], errors="coerce").fillna(0).sum())
    # costs = notional * bps / 10000, so derive notional from recorded 2bps-like rows safely.
    costs = pd.to_numeric(ordered["costs"], errors="coerce").fillna(0.0)
    # Current rows may come from any cost. Infer aggregate notional from per-row entry*qty*point value.
    notional = (ordered["entry"].abs() * ordered["qty"].abs() * ordered["point_value"].abs()).sum()
    break_even = (gross / notional * 10000.0) if notional > 0 else 0.0
    return {"quarters": quarter_rows, "bootstrap_low": float(low), "bootstrap_high": float(high), "cost_break_even_bps": float(break_even)}



FROZEN_VALIDATION_VARIANT = "SELL_SESSION"
FROZEN_VALIDATION_LABEL = "SELL only + UTC 06:00–20:00"


def _independent_validation_gate(
    stats_2bps: dict[str, Any],
    cost_df: pd.DataFrame,
    robustness: dict[str, Any],
    history_meta: dict[str, Any],
) -> dict[str, Any]:
    """
    Gate for a truly older, untouched validation window.

    This gate is intentionally NOT connected to Live. Passing it means the
    frozen candidate deserves forward Paper validation; it is not a promise
    of profitability and does not unlock execution.
    """
    norm = stats_2bps.get("normalized", {})

    cost5 = {}
    cost10 = {}
    if isinstance(cost_df, pd.DataFrame) and not cost_df.empty:
        m5 = cost_df[
            pd.to_numeric(
                cost_df["Round-trip cost (bps)"],
                errors="coerce",
            ).eq(5.0)
        ]
        m10 = cost_df[
            pd.to_numeric(
                cost_df["Round-trip cost (bps)"],
                errors="coerce",
            ).eq(10.0)
        ]
        if not m5.empty:
            cost5 = m5.iloc[0].to_dict()
        if not m10.empty:
            cost10 = m10.iloc[0].to_dict()

    quarters = robustness.get("quarters", []) or []
    profitable_quarters = sum(
        1
        for q in quarters
        if float(q.get("Avg R", 0.0)) > 0.0
    )

    rules = [
        {
            "name": "نافذة مستقلة ≥ 170 يوم",
            "pass": int(history_meta.get("requested_days", 0)) >= 170,
            "value": int(history_meta.get("requested_days", 0)),
        },
        {
            "name": "شموع M5 مستقلة ≥ 20,000",
            "pass": int(history_meta.get("bars", 0)) >= 20_000,
            "value": int(history_meta.get("bars", 0)),
        },
        {
            "name": "Independent trades ≥ 30",
            "pass": int(stats_2bps.get("trades", 0)) >= 30,
            "value": int(stats_2bps.get("trades", 0)),
        },
        {
            "name": "2 bps Normalized PF ≥ 1.20",
            "pass": float(norm.get("profit_factor", 0.0)) >= 1.20,
            "value": float(norm.get("profit_factor", 0.0)),
        },
        {
            "name": "2 bps Normalized Avg R ≥ +0.05R",
            "pass": float(norm.get("avg_r_net", 0.0)) >= 0.05,
            "value": float(norm.get("avg_r_net", 0.0)),
        },
        {
            "name": "2 bps Normalized P&L > 0",
            "pass": float(norm.get("net_pnl", 0.0)) > 0.0,
            "value": float(norm.get("net_pnl", 0.0)),
        },
        {
            "name": "5 bps Normalized PF ≥ 1.10",
            "pass": bool(cost5)
            and float(cost5.get("Normalized PF", 0.0)) >= 1.10,
            "value": None
            if not cost5
            else float(cost5.get("Normalized PF", 0.0)),
        },
        {
            "name": "5 bps Normalized P&L > 0",
            "pass": bool(cost5)
            and float(cost5.get("Normalized Net P&L", 0.0)) > 0.0,
            "value": None
            if not cost5
            else float(cost5.get("Normalized Net P&L", 0.0)),
        },
        {
            "name": "Normalized Max DD ≤ 5%",
            "pass": float(norm.get("max_dd_pct", 999.0)) <= 5.0,
            "value": float(norm.get("max_dd_pct", 999.0)),
        },
        {
            "name": "Bootstrap 95% Low > 0R",
            "pass": float(
                robustness.get("bootstrap_low", -999.0)
            ) > 0.0,
            "value": float(
                robustness.get("bootstrap_low", -999.0)
            ),
        },
        {
            "name": "ربحية زمنية ≥ 3 من 4 أرباع",
            "pass": profitable_quarters >= 3,
            "value": profitable_quarters,
        },
    ]

    failed = [r["name"] for r in rules if not r["pass"]]
    return {
        "passed": not failed,
        "rules": rules,
        "reasons": failed,
        "profitable_quarters": profitable_quarters,
        "cost_10bps_pf": None
        if not cost10
        else cost10.get("Normalized PF"),
        "cost_10bps_net": None
        if not cost10
        else cost10.get("Normalized Net P&L"),
        "checked_at": now_riyadh().isoformat(),
    }


def _validation_cost_row(
    cost_bps: float,
    stats: dict[str, Any],
) -> dict[str, Any]:
    norm = stats.get("normalized", {})
    return {
        "Round-trip cost (bps)": float(cost_bps),
        "Trades": int(stats.get("trades", 0)),
        "Win Rate %": round(stats.get("win_rate", 0.0), 1),
        "PF": (
            math.inf
            if not math.isfinite(stats.get("profit_factor", 0.0))
            else round(stats.get("profit_factor", 0.0), 3)
        ),
        "Net P&L": round(stats.get("net_pnl", 0.0), 2),
        "Normalized PF": (
            math.inf
            if not math.isfinite(norm.get("profit_factor", 0.0))
            else round(norm.get("profit_factor", 0.0), 3)
        ),
        "Normalized Avg R": round(norm.get("avg_r_net", 0.0), 4),
        "Normalized Net P&L": round(norm.get("net_pnl", 0.0), 2),
        "Normalized DD %": round(norm.get("max_dd_pct", 0.0), 2),
    }


RESEARCH_VARIANTS = {
    "STRICT_BOTH": "Baseline BUY + SELL",
    "SELL_ONLY": "SELL only",
    "SELL_SESSION": "SELL only + UTC 06:00–20:00",
    "SELL_SESSION_VOL": "SELL only + UTC 06:00–20:00 + prior-only volatility filter",
}


def run_research_variants(
    raw: pd.DataFrame,
    spec: InstrumentSpec,
    risk_pct: float,
    prepared: dict[str, Any] | None = None,
    baseline_stats: dict[str, Any] | None = None,
) -> pd.DataFrame:
    if prepared is None:
        prepared = prepare_research_context(raw)
    rows: list[dict[str, Any]] = []
    for variant, description in RESEARCH_VARIANTS.items():
        if variant == "STRICT_BOTH" and baseline_stats:
            stats = baseline_stats
        else:
            _, stats = simulate_prepared_research(
                prepared, spec, risk_pct=risk_pct, cost_bps_roundtrip=2.0, research_variant=variant
            )
        norm = stats.get("normalized", {}); out = stats.get("normalized_out_of_sample", {})
        rows.append({
            "Variant": variant, "Description": description, "Trades": int(stats.get("trades",0)),
            "Norm PF": math.inf if not math.isfinite(norm.get("profit_factor",0.0)) else round(norm.get("profit_factor",0.0),3),
            "Norm Avg R": round(norm.get("avg_r_net",0.0),4), "Norm Net P&L": round(norm.get("net_pnl",0.0),2),
            "Norm DD %": round(norm.get("max_dd_pct",0.0),2), "OUT Trades": int(out.get("trades",0)),
            "OUT PF": math.inf if not math.isfinite(out.get("profit_factor",0.0)) else round(out.get("profit_factor",0.0),3),
            "OUT Avg R": round(out.get("avg_r_net",0.0),4), "OUT Net P&L": round(out.get("net_pnl",0.0),2),
        })
    return pd.DataFrame(rows)


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

        audit_df = pd.DataFrame(
            {
                "PnL": [-100.0, -50.0, 120.0, -20.0],
                "R_net": [-1.0, -0.5, 1.2, -0.2],
                "risk_money": [100.0, 100.0, 100.0, 100.0],
                "risk_budget": [100.0, 100.0, 100.0, 100.0],
                "risk_utilization_pct": [100.0, 100.0, 100.0, 100.0],
                "max_qty_hit": [False, False, True, False],
                "duration_min": [5.0, 10.0, 15.0, 5.0],
            }
        )
        audit = _backtest_summary(audit_df)
        assert audit["max_losing_streak"] == 2
        assert audit["trades"] == 4
        assert abs(audit["risk_weighted_expectancy_r"] - (-0.125)) < 1e-9
        assert audit["max_qty_hit_count"] == 1

        norm_test = audit_df.copy()
        norm_test["opened"] = pd.date_range("2026-01-01", periods=4, freq="5min", tz="UTC")
        norm_test["entry"] = [100.0, 100.0, 100.0, 100.0]
        norm_test["stop_distance"] = [1.0, 1.0, 1.0, 1.0]
        norm_test["point_value"] = [1.0, 1.0, 1.0, 1.0]
        norm_test["R_gross"] = [-1.0, -0.5, 1.2, -0.2]
        _, normalized_audit = _fixed_risk_normalized(
            norm_test,
            initial_equity=100_000.0,
            risk_pct=0.5,
            cost_bps_roundtrip=0.0,
        )
        assert normalized_audit["trades"] == 4

        # Verify vectorized B2 matches the reference function on every eligible row.
        b2_frame = calc.tail(80).copy().reset_index(drop=True)
        b2_fast = precompute_b2(b2_frame)
        for i in range(22, len(b2_frame)):
            reference = b2_signal(b2_frame.iloc[max(0, i - 22): i + 1])
            assert bool(b2_fast.loc[i, "b2_valid"]) == bool(reference.get("valid"))
            assert b2_fast.loc[i, "b2_side"] == reference.get("side")

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
    max_value=0.50,
    value=0.25,
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
        max_value=3.0,
        value=1.5,
        step=0.5,
    )
    max_open_positions = st.number_input(
        "أقصى مراكز مفتوحة",
        min_value=1,
        max_value=3,
        value=1,
        step=1,
    )
    max_order_risk_pct = st.number_input(
        "الحد الصلب لمخاطرة الأمر %",
        min_value=0.05,
        max_value=0.50,
        value=0.50,
        step=0.05,
    )
    st.session_state.auto_refresh = st.toggle(
        "Paper heartbeat تلقائي",
        value=st.session_state.auto_refresh,
        help="يحدّث محرك Paper داخل Fragment بدون إعادة تحميل الصفحة كاملة",
    )
    refresh_seconds = st.slider("ثواني heartbeat", 30, 120, 30, step=15)

if not st.session_state.auto_refresh:
    refresh_seconds = 30

live_unlocked = truthy(secret("LIVE_TRADING_ENABLED", "false"))
automation_backend_ready = truthy(secret("AUTOMATION_BACKEND_READY", "false"))
auto_live_unlocked = (
    truthy(secret("AUTO_EXECUTION_ALLOWED", "false"))
    and automation_backend_ready
)
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

st.caption(
    "X10 GOLD v4.5 INDEPENDENT • Frozen SELL_SESSION validation • "
    "Retest 0.30 ATR • SL 1.6 ATR • TP1 1R / TP2 2.2R • Default Risk 0.25%"
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
research_context = hashlib.sha256(
    (
        f"{VERSION}|{instrument.symbol}|{instrument.point_value}|"
        f"{instrument.qty_step}|{instrument.min_qty}|{instrument.max_qty}|"
        f"{float(risk_pct):.6f}"
    ).encode()
).hexdigest()[:16]

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

c_refresh1, c_refresh2 = st.columns(2)
with c_refresh1:
    if st.button("تحديث البيانات الآن", use_container_width=True):
        fetch_market.clear()
        fetch_quote.clear()
        st.rerun()
with c_refresh2:
    if st.button("مسح كاش التاريخ", use_container_width=True):
        fetch_long_history.clear()
        st.session_state.backtest = None
        st.session_state.research_gate = {
            "passed": False,
            "context": None,
            "rules": [],
            "reasons": ["تم مسح كاش التاريخ؛ أعد Final Research Audit"],
        }
        st.rerun()

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

        research_gate = st.session_state.get("research_gate", {})
        if not research_gate.get("passed", False):
            live_gate_ok = False
            live_reasons.append("Final Research Gate لم يجتز الاختبارات")
        elif research_gate.get("context") != research_context:
            live_gate_ok = False
            live_reasons.append("إعدادات الأصل/المخاطرة تغيّرت بعد الاختبار؛ أعد Final Research Audit")

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
            help="يحتاج AUTO_EXECUTION_ALLOWED=true وAUTOMATION_BACKEND_READY=true بالإضافة لكل بوابات الأمان",
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
st.subheader("Research Audit — v4.5 Independent Validation")
st.caption(
    "نفس قواعد v4.3 بدون تخفيف للاستراتيجية. المؤشرات وMTF وB2 تُحسب مرة واحدة فقط، "
    "ثم يعاد استخدام نفس السياق لكل Cost Stress وResearch Variant، مع فحص ثبات إضافي."
)

history_days = st.selectbox(
    "فترة التاريخ للاختبار",
    options=[90, 180, 365],
    index=1,
    format_func=lambda d: f"{d} يوم" if d != 365 else "365 يوم (سنة)",
)
estimated_requests = math.ceil(int(history_days) / 17)
st.caption(
    f"متوقع تقريبًا {estimated_requests} طلب تاريخي. على الخطة المجانية قد ينتظر التطبيق "
    "إعادة ضبط الرصيد للدقيقة تلقائيًا إذا لزم."
)

if st.button("تحميل التاريخ وتشغيل Final Research Audit", use_container_width=True):
    total_t0 = time.perf_counter()
    with st.status("تشغيل Research Audit المحسّن...", expanded=True) as audit_status:
        st.write("1/4 • جلب التاريخ الطويل")
        fetch_t0 = time.perf_counter()
        try:
            audit_raw, history_meta = fetch_long_history(
                instrument.symbol, history_days=int(history_days), chunk_days=17,
            )
        except Exception as exc:
            st.error(f"تعذر جلب التاريخ الطويل: {exc}")
            audit_raw = pd.DataFrame(); history_meta = {}
        fetch_seconds = time.perf_counter() - fetch_t0

        if not audit_raw.empty:
            st.write("2/4 • تجهيز المؤشرات + MTF + B2 مرة واحدة")
            prepared = prepare_research_context(audit_raw)
            if not prepared.get("ok", False):
                st.error(prepared.get("warning", "تعذر تجهيز الاختبار"))
            else:
                scenario_rows=[]; base_trades=pd.DataFrame(); base_stats={}
                st.write(f"تم تجهيز {prepared.get('prepared_rows',0):,} صف خلال {prepared.get('prepare_seconds',0):.1f} ثانية")
                audit_t0=time.perf_counter()

                st.write("3/4 • Cost Stress 2 / 5 / 10 bps")
                for cost_bps in (2.0,5.0,10.0):
                    scenario_trades, scenario_stats = simulate_prepared_research(
                        prepared, instrument, risk_pct=float(risk_pct), cost_bps_roundtrip=cost_bps,
                        research_variant="STRICT_BOTH",
                    )
                    if cost_bps == 2.0:
                        base_trades=scenario_trades; base_stats=scenario_stats
                    norm=scenario_stats.get("normalized",{})
                    if scenario_stats.get("trades",0):
                        scenario_rows.append({
                            "Round-trip cost (bps)":cost_bps, "Trades":scenario_stats["trades"],
                            "Win Rate %":round(scenario_stats["win_rate"],1),
                            "Profit Factor":math.inf if not math.isfinite(scenario_stats["profit_factor"]) else round(scenario_stats["profit_factor"],2),
                            "Net P&L":round(scenario_stats["net_pnl"],2), "Max DD %":round(scenario_stats["max_dd_pct"],2),
                            "Avg Net R":round(scenario_stats["avg_r_net"],3),
                            "Risk-Weighted Exp R":round(scenario_stats["risk_weighted_expectancy_r"],3),
                            "Avg Risk $":round(scenario_stats["avg_risk_money"],2), "Max Qty Hit %":round(scenario_stats["max_qty_hit_pct"],1),
                            "Normalized PF":math.inf if not math.isfinite(norm.get("profit_factor",0.0)) else round(norm.get("profit_factor",0.0),2),
                            "Normalized Net P&L":round(norm.get("net_pnl",0.0),2), "Normalized Avg R":round(norm.get("avg_r_net",0.0),3),
                            "Normalized Max DD %":round(norm.get("max_dd_pct",0.0),2),
                        })
                    else:
                        scenario_rows.append({"Round-trip cost (bps)":cost_bps,"Trades":0,"Win Rate %":0.0,"Profit Factor":0.0,"Net P&L":0.0,"Max DD %":0.0,"Avg Net R":0.0,"Risk-Weighted Exp R":0.0,"Avg Risk $":0.0,"Max Qty Hit %":0.0,"Normalized PF":0.0,"Normalized Net P&L":0.0,"Normalized Avg R":0.0,"Normalized Max DD %":0.0})

                cost_df=pd.DataFrame(scenario_rows)
                st.write("4/4 • Variants + robustness diagnostics")
                variant_df=run_research_variants(
                    audit_raw, instrument, risk_pct=float(risk_pct), prepared=prepared, baseline_stats=base_stats,
                )
                gate=_research_gate(
                    base_stats,cost_df,research_context,
                    history_days=int(history_meta.get("requested_days",history_days)),
                    history_bars=int(history_meta.get("bars",len(audit_raw))),
                )
                robustness=_research_robustness(base_trades)
                audit_seconds=time.perf_counter()-audit_t0; total_seconds=time.perf_counter()-total_t0
                performance_meta={
                    "fetch_seconds":float(fetch_seconds), "prepare_seconds":float(prepared.get("prepare_seconds",0.0)),
                    "audit_seconds":float(audit_seconds), "total_seconds":float(total_seconds),
                    "prepared_rows":int(prepared.get("prepared_rows",0)),
                }
                st.session_state.backtest={
                    "trades":base_trades,"stats":base_stats,"cost_scenarios":cost_df,"research_gate":gate,
                    "history_meta":history_meta,"variant_comparison":variant_df,"performance_meta":performance_meta,
                    "robustness":robustness,
                }
                st.session_state.research_gate=gate
                audit_status.update(label=f"اكتمل Research Audit خلال {total_seconds:.1f} ثانية",state="complete",expanded=False)

if st.session_state.backtest:
    bt = st.session_state.backtest
    stats = bt["stats"]

    if stats.get("trades", 0):
        hmeta = bt.get("history_meta", {})
        st.markdown("### Historical Coverage")
        mini_grid(
            [
                ("Requested", f"{hmeta.get('requested_days', 0)} days", ""),
                ("M5 Bars", f"{int(hmeta.get('bars', 0)):,}", "ok" if int(hmeta.get("bars", 0)) >= 20_000 else "wait"),
                ("API Requests", str(hmeta.get("requests", 0)), ""),
                ("Quota Waits", str(hmeta.get("quota_waits", 0)), ""),
            ],
            "tf-grid",
        )
        if hmeta.get("first_bar") and hmeta.get("last_bar"):
            st.caption(
                f"History: {hmeta['first_bar']} → {hmeta['last_bar']}"
            )

        perf = bt.get("performance_meta", {})
        if perf:
            st.markdown("### Audit Performance")
            mini_grid([
                ("Fetch", f"{perf.get('fetch_seconds',0.0):.1f}s", ""),
                ("Prepare Once", f"{perf.get('prepare_seconds',0.0):.1f}s", "ok"),
                ("All Tests", f"{perf.get('audit_seconds',0.0):.1f}s", ""),
                ("Total", f"{perf.get('total_seconds',0.0):.1f}s", "ok"),
            ], "tf-grid")

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

        mini_grid(
            [
                ("Avg Net R", f"{stats['avg_r_net']:.3f}R", "ok" if stats["avg_r_net"] > 0 else "bad"),
                (
                    "Risk-Weighted Exp",
                    f"{stats['risk_weighted_expectancy_r']:.3f}R",
                    "ok" if stats["risk_weighted_expectancy_r"] > 0 else "bad",
                ),
                ("Avg Risk $", f"${stats['avg_risk_money']:,.2f}", ""),
                ("Risk Utilization", f"{stats['avg_risk_utilization_pct']:.1f}%", ""),
                ("Max Qty Hits", f"{stats['max_qty_hit_count']} / {stats['max_qty_hit_pct']:.1f}%", "wait" if stats["max_qty_hit_count"] else "ok"),
                ("Max Losing Streak", str(stats["max_losing_streak"]), "wait"),
                ("Avg Duration", f"{stats['avg_duration_min']:.0f} min", ""),
                ("Base Cost", f"{stats['cost_bps_roundtrip']:.0f} bps RT", ""),
            ],
            "plan-grid",
        )

        normalized = stats.get("normalized", {})
        st.markdown("### Fixed-Risk Normalized — Signal Edge")
        mini_grid(
            [
                ("Normalized PF", "∞" if not math.isfinite(normalized.get("profit_factor", 0.0)) else f"{normalized.get('profit_factor',0.0):.2f}", ""),
                ("Normalized Net", f"${normalized.get('net_pnl',0.0):,.2f}", "ok" if normalized.get("net_pnl",0.0) > 0 else "bad"),
                ("Normalized Avg R", f"{normalized.get('avg_r_net',0.0):.3f}R", "ok" if normalized.get("avg_r_net",0.0) > 0 else "bad"),
                ("Normalized DD", f"{normalized.get('max_dd_pct',0.0):.2f}%", "wait"),
            ],
            "tf-grid",
        )

        if (stats["avg_r_net"] < 0 < stats["risk_weighted_expectancy_r"]) or (
            stats["avg_r_net"] > 0 > stats["risk_weighted_expectancy_r"]
        ):
            st.warning(
                "Sizing asymmetry detected: متوسط R البسيط واتجاه العائد الموزون بالمخاطرة مختلفان. "
                "راجع Max Qty Hits وRisk Utilization قبل الاعتماد على النتيجة."
            )

        robustness = bt.get("robustness", {})
        if robustness:
            st.markdown("### Robustness Diagnostics")
            low = robustness.get("bootstrap_low",0.0); high = robustness.get("bootstrap_high",0.0)
            be = robustness.get("cost_break_even_bps",0.0)
            mini_grid([
                ("Bootstrap Avg-R 95% Low", f"{low:.3f}R", "ok" if low > 0 else "bad"),
                ("Bootstrap Avg-R 95% High", f"{high:.3f}R", "ok" if high > 0 else "bad"),
                ("Cost Break-even", f"{be:.2f} bps RT", "ok" if be >= 10 else "wait"),
            ], "tf-grid")
            qdf = pd.DataFrame(robustness.get("quarters", []))
            if not qdf.empty:
                st.caption("تقسيم زمني إلى 4 أرباع متتالية لاختبار ثبات النتيجة عبر الزمن.")
                st.dataframe(qdf, hide_index=True, use_container_width=True)

        st.markdown("### Research Variant Comparison")
        st.caption(
            "لا نختار الفائز من هذه الشاشة فقط. الهدف كشف هل SELL-only أو الفلاتر تستحق اختبارًا مستقلاً."
        )
        variant_df = bt.get("variant_comparison", pd.DataFrame())
        if isinstance(variant_df, pd.DataFrame) and not variant_df.empty:
            st.dataframe(variant_df, hide_index=True, use_container_width=True)

        st.markdown("### Cost Stress Test")
        st.dataframe(
            bt["cost_scenarios"],
            hide_index=True,
            use_container_width=True,
        )

        in_stats = stats.get("in_sample", {})
        out_stats = stats.get("out_of_sample", {})
        split_time = stats.get("split_time", "—")

        st.markdown(f"### In-sample / Out-of-sample")
        st.caption(f"التقسيم الزمني 70/30 • بداية Out-of-sample: {split_time}")
        norm_in = stats.get("normalized_in_sample", {})
        norm_out = stats.get("normalized_out_of_sample", {})
        split_table = pd.DataFrame(
            [
                {
                    "Segment": "IN 70%",
                    "Trades": in_stats.get("trades", 0),
                    "Win Rate %": round(in_stats.get("win_rate", 0.0), 1),
                    "Profit Factor": (
                        math.inf
                        if not math.isfinite(in_stats.get("profit_factor", 0.0))
                        else round(in_stats.get("profit_factor", 0.0), 2)
                    ),
                    "Net P&L": round(in_stats.get("net_pnl", 0.0), 2),
                    "Avg Net R": round(in_stats.get("avg_r_net", 0.0), 3),
                    "Risk-Weighted Exp R": round(in_stats.get("risk_weighted_expectancy_r", 0.0), 3),
                    "Avg Risk $": round(in_stats.get("avg_risk_money", 0.0), 2),
                    "Max Qty Hit %": round(in_stats.get("max_qty_hit_pct", 0.0), 1),
                    "Max Losing Streak": in_stats.get("max_losing_streak", 0),
                    "Norm PF": round(norm_in.get("profit_factor", 0.0), 2) if math.isfinite(norm_in.get("profit_factor", 0.0)) else math.inf,
                    "Norm Avg R": round(norm_in.get("avg_r_net", 0.0), 3),
                    "Norm Net P&L": round(norm_in.get("net_pnl", 0.0), 2),
                },
                {
                    "Segment": "OUT 30%",
                    "Trades": out_stats.get("trades", 0),
                    "Win Rate %": round(out_stats.get("win_rate", 0.0), 1),
                    "Profit Factor": (
                        math.inf
                        if not math.isfinite(out_stats.get("profit_factor", 0.0))
                        else round(out_stats.get("profit_factor", 0.0), 2)
                    ),
                    "Net P&L": round(out_stats.get("net_pnl", 0.0), 2),
                    "Avg Net R": round(out_stats.get("avg_r_net", 0.0), 3),
                    "Risk-Weighted Exp R": round(out_stats.get("risk_weighted_expectancy_r", 0.0), 3),
                    "Avg Risk $": round(out_stats.get("avg_risk_money", 0.0), 2),
                    "Max Qty Hit %": round(out_stats.get("max_qty_hit_pct", 0.0), 1),
                    "Max Losing Streak": out_stats.get("max_losing_streak", 0),
                    "Norm PF": round(norm_out.get("profit_factor", 0.0), 2) if math.isfinite(norm_out.get("profit_factor", 0.0)) else math.inf,
                    "Norm Avg R": round(norm_out.get("avg_r_net", 0.0), 3),
                    "Norm Net P&L": round(norm_out.get("net_pnl", 0.0), 2),
                },
            ]
        )
        st.dataframe(split_table, hide_index=True, use_container_width=True)

        gate = bt.get("research_gate", st.session_state.get("research_gate", {}))
        st.markdown("### Final Research Gate")
        gate_state = "PASS" if gate.get("passed") else "BLOCK LIVE"
        gate_color = "ok" if gate.get("passed") else "bad"
        mini_grid([("Research Gate", gate_state, gate_color)], "tf-grid")
        for rule in gate.get("rules", []):
            icon = "✅" if rule.get("pass") else "❌"
            st.write(f"{icon} {rule.get('name')} — {rule.get('value')}")
        if not gate.get("passed"):
            st.error("Live سيبقى مقفولًا. لا يتم تعديل الشروط لإجبار النتيجة على PASS؛ نغيّر الاستراتيجية فقط بناءً على بيانات جديدة واختبار مستقل.")

        with st.expander("BUY / SELL Audit", expanded=True):
            side_df = pd.DataFrame(stats.get("side_stats", []))
            if not side_df.empty:
                st.dataframe(side_df, hide_index=True, use_container_width=True)
            else:
                st.info("لا توجد بيانات كافية لتقسيم BUY/SELL")

        with st.expander("النتائج حسب ساعة الدخول UTC واليوم", expanded=False):
            hour_df = pd.DataFrame(stats.get("hour_stats", []))
            day_df = pd.DataFrame(stats.get("day_stats", []))
            st.markdown("**حسب الساعة UTC**")
            st.dataframe(hour_df, hide_index=True, use_container_width=True)
            st.markdown("**حسب اليوم**")
            st.dataframe(day_df, hide_index=True, use_container_width=True)

        st.markdown("### Trade Log")
        st.dataframe(
            bt["trades"].tail(150),
            hide_index=True,
            use_container_width=True,
        )

        st.download_button(
            "تنزيل نتائج Backtest CSV",
            data=bt["trades"].to_csv(index=False).encode("utf-8-sig"),
            file_name=f"gold_ai_backtest_{now_riyadh().date().isoformat()}.csv",
            mime="text/csv",
            use_container_width=True,
        )
    else:
        st.info(stats.get("warning", "لا توجد نتائج"))


# ------------------- independent validation -------------------
st.divider()
st.subheader("Independent Validation — Frozen SELL_SESSION")
st.caption(
    "المرشح مجمّد: SELL فقط خلال UTC 06:00–20:00. "
    "لا نعدّل RSI/ADX/SL/TP/الساعات بعد رؤية هذه النتائج. "
    "الاختبار يستخدم 180 يومًا أقدم بالكامل من نافذة التطوير الحالية."
)

bt_dev = st.session_state.get("backtest")
if not bt_dev or not bt_dev.get("history_meta"):
    st.info(
        "شغّل Research Audit لمدة 180 يوم أولًا حتى نثبت بداية نافذة التطوير."
    )
else:
    dev_meta = bt_dev.get("history_meta", {})
    dev_first = dev_meta.get("first_bar")

    if not dev_first:
        st.warning("تعذر تحديد بداية نافذة التطوير.")
    else:
        validation_end = pd.Timestamp(dev_first)
        if validation_end.tzinfo is None:
            validation_end = validation_end.tz_localize("UTC")
        else:
            validation_end = validation_end.tz_convert("UTC")

        validation_start = validation_end - pd.Timedelta(days=180)

        frozen_context = hashlib.sha256(
            (
                f"{instrument.symbol}|{FROZEN_VALIDATION_VARIANT}|"
                f"{float(risk_pct):.6f}|"
                f"{validation_start.isoformat()}|"
                f"{validation_end.isoformat()}"
            ).encode()
        ).hexdigest()[:16]

        mini_grid(
            [
                ("Frozen Variant", "SELL_SESSION", "ok"),
                (
                    "Window Start",
                    validation_start.strftime("%Y-%m-%d"),
                    "",
                ),
                (
                    "Window End",
                    validation_end.strftime("%Y-%m-%d"),
                    "",
                ),
                ("Risk", f"{float(risk_pct):.2f}%", ""),
            ],
            "tf-grid",
        )

        st.caption(
            f"Independent window: {validation_start.isoformat()} → "
            f"{validation_end.isoformat()} • لا تتداخل مع Development 180d."
        )

        if st.button(
            "تشغيل Independent Validation على 180 يوم الأقدم",
            use_container_width=True,
        ):
            iv_t0 = time.perf_counter()

            with st.status(
                "تشغيل الاختبار المستقل المجمد...",
                expanded=True,
            ) as iv_status:
                st.write("1/3 • جلب الفترة الأقدم غير المستخدمة")
                try:
                    iv_raw, iv_meta = fetch_history_window(
                        instrument.symbol,
                        validation_start.isoformat(),
                        validation_end.isoformat(),
                        chunk_days=17,
                    )
                except Exception as exc:
                    st.error(f"تعذر جلب Independent Window: {exc}")
                    iv_raw = pd.DataFrame()
                    iv_meta = {}

                if not iv_raw.empty:
                    st.write("2/3 • تجهيز المؤشرات مرة واحدة")
                    iv_prepared = prepare_research_context(iv_raw)

                    if not iv_prepared.get("ok", False):
                        st.error(
                            iv_prepared.get(
                                "warning",
                                "تعذر تجهيز Independent Validation",
                            )
                        )
                    else:
                        st.write(
                            "3/3 • Frozen SELL_SESSION + "
                            "Cost Stress 2/5/10 bps"
                        )

                        iv_rows = []
                        iv_trades_2 = pd.DataFrame()
                        iv_stats_2: dict[str, Any] = {}

                        for iv_cost in (2.0, 5.0, 10.0):
                            iv_trades, iv_stats = (
                                simulate_prepared_research(
                                    iv_prepared,
                                    instrument,
                                    risk_pct=float(risk_pct),
                                    cost_bps_roundtrip=iv_cost,
                                    research_variant=FROZEN_VALIDATION_VARIANT,
                                )
                            )

                            if iv_cost == 2.0:
                                iv_trades_2 = iv_trades
                                iv_stats_2 = iv_stats

                            iv_rows.append(
                                _validation_cost_row(
                                    iv_cost,
                                    iv_stats,
                                )
                            )

                        iv_cost_df = pd.DataFrame(iv_rows)
                        iv_robustness = _research_robustness(
                            iv_trades_2
                        )
                        iv_gate = _independent_validation_gate(
                            iv_stats_2,
                            iv_cost_df,
                            iv_robustness,
                            iv_meta,
                        )

                        st.session_state.independent_validation = {
                            "context": frozen_context,
                            "variant": FROZEN_VALIDATION_VARIANT,
                            "label": FROZEN_VALIDATION_LABEL,
                            "window_start": validation_start.isoformat(),
                            "window_end": validation_end.isoformat(),
                            "history_meta": iv_meta,
                            "trades": iv_trades_2,
                            "stats": iv_stats_2,
                            "cost_scenarios": iv_cost_df,
                            "robustness": iv_robustness,
                            "gate": iv_gate,
                            "elapsed_seconds": float(
                                time.perf_counter() - iv_t0
                            ),
                        }

                        iv_status.update(
                            label=(
                                "Independent Validation اكتمل خلال "
                                f"{time.perf_counter()-iv_t0:.1f} ثانية"
                            ),
                            state="complete",
                            expanded=False,
                        )

        iv = st.session_state.get("independent_validation")
        if iv:
            if iv.get("context") != frozen_context:
                st.warning(
                    "نتيجة Independent Validation المحفوظة تخص إعدادات/فترة "
                    "مختلفة. أعد الاختبار قبل الاعتماد عليها."
                )
            else:
                iv_stats = iv.get("stats", {})
                iv_norm = iv_stats.get("normalized", {})
                iv_meta = iv.get("history_meta", {})
                iv_gate = iv.get("gate", {})
                iv_robust = iv.get("robustness", {})

                st.markdown("### Independent Result — 2 bps")
                mini_grid(
                    [
                        (
                            "Trades",
                            str(iv_stats.get("trades", 0)),
                            "",
                        ),
                        (
                            "Normalized PF",
                            (
                                "∞"
                                if not math.isfinite(
                                    iv_norm.get(
                                        "profit_factor",
                                        0.0,
                                    )
                                )
                                else f"{iv_norm.get('profit_factor',0.0):.2f}"
                            ),
                            (
                                "ok"
                                if iv_norm.get(
                                    "profit_factor",
                                    0.0,
                                ) >= 1.2
                                else "bad"
                            ),
                        ),
                        (
                            "Normalized Avg R",
                            f"{iv_norm.get('avg_r_net',0.0):.3f}R",
                            (
                                "ok"
                                if iv_norm.get(
                                    "avg_r_net",
                                    0.0,
                                ) > 0
                                else "bad"
                            ),
                        ),
                        (
                            "Normalized Net",
                            f"${iv_norm.get('net_pnl',0.0):,.2f}",
                            (
                                "ok"
                                if iv_norm.get("net_pnl", 0.0) > 0
                                else "bad"
                            ),
                        ),
                        (
                            "Normalized DD",
                            f"{iv_norm.get('max_dd_pct',0.0):.2f}%",
                            "wait",
                        ),
                        (
                            "Elapsed",
                            f"{iv.get('elapsed_seconds',0.0):.1f}s",
                            "",
                        ),
                    ],
                    "plan-grid",
                )

                st.caption(
                    f"Coverage: {iv_meta.get('first_bar')} → "
                    f"{iv_meta.get('last_bar')} • "
                    f"{int(iv_meta.get('bars',0)):,} M5 bars • "
                    f"{iv_meta.get('requests',0)} API requests"
                )

                st.markdown("### Independent Cost Stress")
                st.dataframe(
                    iv.get("cost_scenarios", pd.DataFrame()),
                    hide_index=True,
                    use_container_width=True,
                )

                st.markdown("### Independent Robustness")
                mini_grid(
                    [
                        (
                            "Bootstrap 95% Low",
                            f"{iv_robust.get('bootstrap_low',0.0):.3f}R",
                            (
                                "ok"
                                if iv_robust.get(
                                    "bootstrap_low",
                                    0.0,
                                ) > 0
                                else "bad"
                            ),
                        ),
                        (
                            "Bootstrap 95% High",
                            f"{iv_robust.get('bootstrap_high',0.0):.3f}R",
                            "",
                        ),
                        (
                            "Cost Break-even",
                            f"{iv_robust.get('cost_break_even_bps',0.0):.2f} bps RT",
                            "wait",
                        ),
                    ],
                    "tf-grid",
                )

                iv_quarters = pd.DataFrame(
                    iv_robust.get("quarters", [])
                )
                if not iv_quarters.empty:
                    st.dataframe(
                        iv_quarters,
                        hide_index=True,
                        use_container_width=True,
                    )

                st.markdown("### Independent Validation Gate")
                iv_pass = bool(iv_gate.get("passed"))
                mini_grid(
                    [
                        (
                            "Independent Gate",
                            (
                                "PASS → PAPER FORWARD"
                                if iv_pass
                                else "FAIL / MORE RESEARCH"
                            ),
                            "ok" if iv_pass else "bad",
                        )
                    ],
                    "tf-grid",
                )

                for rule in iv_gate.get("rules", []):
                    icon = "✅" if rule.get("pass") else "❌"
                    st.write(
                        f"{icon} {rule.get('name')} — "
                        f"{rule.get('value')}"
                    )

                st.caption(
                    "10 bps يبقى Stress Test إضافي: "
                    f"PF={iv_gate.get('cost_10bps_pf')} • "
                    f"Net={iv_gate.get('cost_10bps_net')}"
                )

                if iv_pass:
                    st.success(
                        "المرشح اجتاز نافذة مستقلة أقدم. "
                        "الخطوة التالية Paper Forward فقط؛ "
                        "لا يتم فتح Live من هذا الاختبار."
                    )
                else:
                    st.error(
                        "المرشح لم يثبت نفسه على نافذة مستقلة. "
                        "لا نعدّل شروطه باستخدام نفس الفترة؛ "
                        "نعود للبحث أو نغيّر منطق الاستراتيجية."
                    )


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
    {"Component": "Baseline research gate", "Status": "PASS" if (st.session_state.get("research_gate", {}).get("passed") and st.session_state.get("research_gate", {}).get("context") == research_context) else "BLOCKED"},
    {"Component": "Independent validation", "Status": "PASS" if ((st.session_state.get("independent_validation") or {}).get("gate") or {}).get("passed", False) else "NOT PASSED"},
    {"Component": "Automation backend", "Status": "READY" if automation_backend_ready else "NOT CONFIGURED"},
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
    "FINAL SAFETY: Live لا يفتح إلا بعد Broker Bridge فعلي + Broker Quote حديث + positions موثقة + "
    "بيانات عقد موثقة + LIVE_UI_PIN + Final Research Gate PASS. "
    "Auto Live يحتاج أيضًا AUTOMATION_BACKEND_READY=true لأن جلسة Streamlit ليست Worker دائمًا."
)

if st.session_state.auto_refresh:
    @st.fragment(run_every=refresh_seconds)
    def _paper_heartbeat() -> None:
        """
        Session-scoped Paper heartbeat.
        It does not full-rerun the app, so mobile stays connected more reliably.
        It is NOT a 24/7 server worker and may pause if the browser session sleeps.
        """
        hb_raw, _ = fetch_market(instrument.symbol)
        if hb_raw.empty:
            st.caption("Paper heartbeat: market data unavailable")
            return

        hb_quote = fetch_quote(instrument.symbol)
        hb_feed = feed_integrity(hb_raw, hb_quote)
        hb_price = (
            float(hb_quote["last"])
            if hb_quote.get("connected") and finite(hb_quote.get("last"))
            else float(hb_raw["close"].iloc[-1])
        )

        if st.session_state.paper_position and hb_feed.get("execution_ok", False):
            hb_mark = paper_mark_price(
                st.session_state.paper_position,
                hb_quote,
                hb_price,
            )
            manage_paper(hb_mark)

        if (
            st.session_state.auto_paper
            and not st.session_state.paper_position
            and hb_feed.get("execution_ok", False)
        ):
            hb_analysis = analyze_mtf(hb_raw)
            hb_m5 = hb_analysis.get("snapshots", {}).get("M5")
            hb_candle = str(hb_raw["datetime"].iloc[-1])

            if hb_analysis.get("signal") in {"BUY", "SELL"} and hb_m5:
                try:
                    hb_plan = build_trade_plan(
                        hb_analysis["signal"],
                        hb_price,
                        float(hb_m5["atr"]),
                        float(st.session_state.paper_balance),
                        float(risk_pct),
                        instrument,
                    )
                    hb_day_pnl = (
                        float(st.session_state.paper_balance)
                        - float(st.session_state.paper_day_start_balance)
                    )
                    hb_ok, _ = risk_gate(
                        float(st.session_state.paper_balance),
                        hb_day_pnl,
                        0,
                        hb_plan,
                        float(max_daily_loss_pct),
                        int(max_open_positions),
                        float(max_order_risk_pct),
                        True,
                        float(st.session_state.paper_day_start_balance),
                    )
                    if (
                        hb_ok
                        and st.session_state.last_auto_paper_candle.get(instrument.symbol)
                        != hb_candle
                    ):
                        st.session_state.last_auto_paper_candle[instrument.symbol] = hb_candle
                        open_paper(hb_plan, instrument)
                except ValueError:
                    pass

        st.caption(
            f"Paper heartbeat • {now_riyadh().strftime('%H:%M:%S')} • "
            f"{'READY' if hb_feed.get('execution_ok') else 'BLOCKED'}"
        )

    _paper_heartbeat()
else:
    st.caption("Paper heartbeat متوقف. استخدم «تحديث البيانات الآن» للتحديث اليدوي.")
