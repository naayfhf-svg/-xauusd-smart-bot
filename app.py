from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import os
import time
import uuid
import threading
import sqlite3
from collections import deque
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

VERSION = "6.3.1-arabic-alerts-ui"
TZ = ZoneInfo("Asia/Riyadh")
DATA_URL = "https://api.twelvedata.com/time_series"
QUOTE_URL = "https://api.twelvedata.com/quote"
TRADIER_DEFAULT_BASE_URL = "https://api.tradier.com/v1"
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


def signal_ar(value: Any) -> str:
    """Arabic display label while keeping internal strategy constants unchanged."""
    mapping = {
        "BUY": "شراء",
        "SELL": "بيع",
        "WAIT": "انتظار",
        "Call": "كول",
        "Put": "بوت",
        "OPEN": "مفتوح",
        "CLOSED": "مغلق",
        "READY": "جاهز",
        "BLOCKED": "محجوب",
        "UNLOCKED": "مفتوح",
        "LOCKED": "مقفل",
    }
    return mapping.get(str(value), str(value))


def smart_alert_channels() -> dict[str, bool]:
    """Notification channels. In-app is always available while the session is open."""
    return {
        "in_app": True,
        "telegram": bool(secret("TELEGRAM_BOT_TOKEN") and secret("TELEGRAM_CHAT_ID")),
        "webhook": bool(secret("SMART_ALERT_WEBHOOK_URL")),
    }


def emit_smart_alert(
    event_key: str,
    title: str,
    body: str,
    icon: str = "⏰",
    payload: dict[str, Any] | None = None,
) -> dict[str, bool]:
    """Deduplicated alert for entry, exit, target, stop and reversal events."""
    keys = list(st.session_state.get("smart_alert_keys", []))
    if event_key in keys:
        return {"in_app": False, "telegram": False, "webhook": False}

    st.session_state.smart_alert_keys = (keys + [event_key])[-1000:]
    stamp = now_riyadh().strftime("%Y-%m-%d %H:%M:%S")
    message = f"{title}\n{body}\n⏱ {stamp} الرياض"
    result = {"in_app": True, "telegram": False, "webhook": False}

    try:
        st.toast(f"{title} — {body}", icon=icon)
    except Exception:
        result["in_app"] = False

    log = list(st.session_state.get("smart_alert_log", []))
    log.insert(0, {
        "time": now_riyadh().isoformat(),
        "key": event_key,
        "title": title,
        "body": body,
        **(payload or {}),
    })
    st.session_state.smart_alert_log = log[:200]

    token = str(secret("TELEGRAM_BOT_TOKEN", "") or "").strip()
    chat_id = str(secret("TELEGRAM_CHAT_ID", "") or "").strip()
    if token and chat_id:
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={
                    "chat_id": chat_id,
                    "text": message,
                    "disable_web_page_preview": "true",
                },
                timeout=8,
            )
            result["telegram"] = bool(response.ok)
        except Exception:
            result["telegram"] = False

    webhook_url = str(secret("SMART_ALERT_WEBHOOK_URL", "") or "").strip()
    if webhook_url:
        try:
            response = requests.post(
                webhook_url,
                json={
                    "event_key": event_key,
                    "title": title,
                    "body": body,
                    "timestamp": now_riyadh().isoformat(),
                    "payload": payload or {},
                },
                timeout=8,
            )
            result["webhook"] = bool(response.ok)
        except Exception:
            result["webhook"] = False

    return result


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

    # U.S. stock watchlist. These are trading symbols only; the app does NOT
    # claim current Sharia compliance without a separate up-to-date screener.
    "Apple — AAPL": InstrumentSpec("Apple — AAPL", "STOCK", "AAPL", 1.0, 1.0, 1.0, 100000.0),
    "Microsoft — MSFT": InstrumentSpec("Microsoft — MSFT", "STOCK", "MSFT", 1.0, 1.0, 1.0, 100000.0),
    "NVIDIA — NVDA": InstrumentSpec("NVIDIA — NVDA", "STOCK", "NVDA", 1.0, 1.0, 1.0, 100000.0),
    "AMD — AMD": InstrumentSpec("AMD — AMD", "STOCK", "AMD", 1.0, 1.0, 1.0, 100000.0),
    "Meta — META": InstrumentSpec("Meta — META", "STOCK", "META", 1.0, 1.0, 1.0, 100000.0),
    "Alphabet — GOOGL": InstrumentSpec("Alphabet — GOOGL", "STOCK", "GOOGL", 1.0, 1.0, 1.0, 100000.0),
    "Amazon — AMZN": InstrumentSpec("Amazon — AMZN", "STOCK", "AMZN", 1.0, 1.0, 1.0, 100000.0),
    "SPY ETF": InstrumentSpec("SPY ETF", "STOCK", "SPY", 1.0, 1.0, 1.0, 100000.0),

    "Nasdaq Futures — NQ": InstrumentSpec("Nasdaq Futures — NQ", "FUTURES", "NQ", 20.0, 1.0, 1.0, 1000.0),
    "S&P Futures — ES": InstrumentSpec("S&P Futures — ES", "FUTURES", "ES", 50.0, 1.0, 1.0, 1000.0),
}

# Curated liquid U.S. watchlist. Symbols here are market-data symbols only.
# Sharia status is NOT inferred from the company name; if the user supplies a
# current verified allow-list in Secrets, the UI can filter against it.
STOCK_UNIVERSE: dict[str, dict[str, str]] = {
    "AAPL": {"name": "Apple", "group": "Technology"},
    "MSFT": {"name": "Microsoft", "group": "Technology"},
    "NVDA": {"name": "NVIDIA", "group": "Semiconductors"},
    "AMD": {"name": "AMD", "group": "Semiconductors"},
    "META": {"name": "Meta", "group": "Communication"},
    "GOOGL": {"name": "Alphabet", "group": "Communication"},
    "AMZN": {"name": "Amazon", "group": "Consumer"},
}
STOCK_SCAN_BATCH_SIZE = 3
STOCK_SCAN_INTERVAL_SECONDS = 60
STOCK_MAX_TRADES_PER_DAY = 6
STOCK_MAX_TRADES_PER_SYMBOL_DAY = 3
STOCK_LOSS_COOLDOWN_MINUTES = 30

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


def render_readiness_panel() -> None:
    """Show a plain-language setup checklist before market analysis starts."""
    market_ready = bool(secret("TWELVE_DATA_API_KEY"))
    sharia_ready = bool(
        secret("SHARIA_APPROVED_SYMBOLS")
        and secret("SHARIA_SCREEN_SOURCE")
        and secret("SHARIA_SCREEN_DATE")
    )
    bridge_ready = bool(bridge) if "bridge" in globals() else False
    live_ready = bool(
        live_unlocked
        and automation_backend_ready
        and auto_live_unlocked
        and contract_metadata_verified
        and bridge_ready
    )
    with st.expander("مركز الجاهزية", expanded=not market_ready):
        mini_grid(
            [
                ("مفتاح البيانات", "موجود — يلزم فحص الاتصال" if market_ready else "تحتاج مفتاح", "wait"),
                ("Paper", "تجريبي — يخضع لفحص البيانات", "wait"),
                ("الفحص الشرعي", "مربوط" if sharia_ready else "اختياري", "ok" if sharia_ready else "wait"),
                ("Live", "إعدادات موجودة — يلزم فحص التنفيذ" if live_ready else "مقفول للحماية", "wait"),
            ]
        )
        if not market_ready:
            st.info("لإظهار أسعار الذهب والأسهم أضف TWELVE_DATA_API_KEY في Streamlit Secrets ثم أعد تشغيل التطبيق.")
        elif not sharia_ready and instrument.asset_class == "STOCK":
            st.caption("الفحص الشرعي غير مربوط بمصدر محدث؛ التطبيق لا يفترض الحكم من اسم الشركة.")
        if not live_ready:
            st.caption("التداول الحقيقي يبقى مقفولًا حتى يكتمل Broker Bridge وتوثيق بيانات العقد.")



# ---------------------- persistent Paper state ----------------------
# This SQLite layer survives browser refreshes / normal Streamlit reruns.
# On hosts with ephemeral disks (including some Streamlit deployments),
# a full container rebuild can still reset the local DB. If a durable
# external volume is available, set GOLD_AI_STATE_DB_PATH to that path.

PERSIST_KEYS = (
    "paper_balance",
    "paper_position",
    "paper_history",
    "paper_day",
    "paper_day_start_balance",
    "paper_trades_today",
    "auto_paper",
    "forward_candidate",
    "adaptive_profile_choice",
    "stock_favorites",
    "stock_scan_cache",
    "stock_scan_cursor",
    "stock_auto_scan",
    "stock_sharia_only",
    "last_auto_paper_candle",
    "paper_order_keys",
)


def _state_db_path() -> str:
    configured = str(secret("GOLD_AI_STATE_DB_PATH", "") or "").strip()
    return configured or "/tmp/gold_ai_x10_state.sqlite3"


@st.cache_resource
def state_db() -> dict[str, Any]:
    path = _state_db_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS kv_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return {
        "conn": conn,
        "lock": threading.RLock(),
        "path": path,
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def state_write(key: str, value: Any) -> None:
    db = state_db()
    payload = json.dumps(value, ensure_ascii=False, default=_jsonable)
    with db["lock"]:
        db["conn"].execute(
            """
            INSERT INTO kv_state(key, value, updated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at=excluded.updated_at
            """,
            (key, payload, now_riyadh().isoformat()),
        )
        db["conn"].commit()


def state_read(key: str, default: Any = None) -> Any:
    db = state_db()
    with db["lock"]:
        row = db["conn"].execute(
            "SELECT value FROM kv_state WHERE key = ?",
            (key,),
        ).fetchone()
    if not row:
        return default
    try:
        return json.loads(row[0])
    except Exception:
        return default


def restore_persistent_state() -> None:
    db = state_db()
    with db["lock"]:
        rows = dict(db["conn"].execute("SELECT key, value FROM kv_state").fetchall())
    for key in PERSIST_KEYS:
        if key in rows:
            st.session_state[key] = json.loads(rows[key])
    st.session_state._state_revision = int(json.loads(rows.get("_revision", "0")))
    st.session_state._last_good_state = copy.deepcopy({k: st.session_state[k] for k in PERSIST_KEYS if k in st.session_state})


def persist_paper_state() -> bool:
    """Atomic snapshot; reject stale browser sessions instead of losing trades."""
    try:
        db = state_db()
        with db["lock"]:
            conn = db["conn"]
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT value FROM kv_state WHERE key='_revision'").fetchone()
                revision = int(json.loads(row[0])) if row else 0
                if revision != st.session_state.get("_state_revision", 0):
                    raise RuntimeError("تغيرت المحفظة في جلسة أخرى؛ تم تحميل أحدث حالة")
                stamp = now_riyadh().isoformat()
                values = [(key, json.dumps(st.session_state[key], ensure_ascii=False,
                           default=_jsonable, allow_nan=False), stamp)
                          for key in PERSIST_KEYS if key in st.session_state]
                values.append(("_revision", json.dumps(revision + 1), stamp))
                conn.executemany("INSERT INTO kv_state(key,value,updated_at) VALUES(?,?,?) "
                                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                                 "updated_at=excluded.updated_at", values)
                conn.commit()
                st.session_state._state_revision = revision + 1
                st.session_state._persistence_error = None
                st.session_state._last_good_state = copy.deepcopy({k: st.session_state[k] for k in PERSIST_KEYS if k in st.session_state})
                return True
            except Exception:
                conn.rollback()
                raise
    except Exception as exc:
        st.session_state._persistence_error = str(exc)
        st.session_state.auto_paper = False
        try:
            restore_persistent_state()
        except Exception:
            for key, value in st.session_state.get("_last_good_state", {}).items():
                st.session_state[key] = copy.deepcopy(value)
        st.session_state.auto_paper = False
        return False


def roll_paper_day() -> None:
    today = now_riyadh().date().isoformat()
    if st.session_state.paper_day != today:
        st.session_state.paper_day = today
        st.session_state.paper_day_start_balance = float(st.session_state.paper_balance)
        st.session_state.paper_trades_today = 0
        persist_paper_state()


def adaptive_entry_brake(profile: str, asset_class: str) -> bool:
    day_pnl = float(st.session_state.paper_balance) - float(st.session_state.paper_day_start_balance)
    stock = asset_class == "STOCK"
    limit = {"صارم": 0.35 if stock else 0.40,
             "متوازن": 0.30 if stock else 0.35,
             "مرن": 0.20 if stock else 0.25}.get(profile, 0.30 if stock else 0.35)
    streak = 0
    for item in list(st.session_state.get("paper_history", []))[:5]:
        if float(item.get("PnL", 0)) >= 0:
            break
        streak += 1
    return (day_pnl > -float(st.session_state.paper_day_start_balance) * limit / 100
            and streak < (2 if profile == "مرن" else 3))


def remember_paper_order(order_key: str) -> bool:
    """
    Idempotency guard.
    Returns True only the first time an auto-paper order key is seen.
    """
    keys = list(st.session_state.get("paper_order_keys", []))
    if order_key in keys:
        return False
    keys.append(order_key)
    st.session_state.paper_order_keys = keys[-500:]
    return persist_paper_state()


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
        "last_near_entry_alert": None,
        "smart_alert_keys": [],
        "smart_alert_log": [],
        "last_reversal_alert": {},
        "paper_day": now_riyadh().date().isoformat(),
        "paper_day_start_balance": 100_000.0,
        "paper_trades_today": 0,
        "paper_order_keys": [],
        "backtest": None,
        "independent_validation": None,
        "walkforward_lab": None,
        "fresh_holdout": None,
        "forward_candidate": "V47_B2_VOL",
        "adaptive_profile_choice": "ذكي تلقائي",
        "stock_favorites": [],
        "stock_scan_cache": {},
        "stock_scan_cursor": 0,
        "stock_auto_scan": True,
        "stock_sharia_only": False,
        "stock_scan_alerts": {},
        "market_preset": "Apple — AAPL",
        "market_section": "الأسهم",
        "pending_market_preset": None,
        "research_gate": {
            "passed": False,
            "context": None,
            "rules": [],
            "reasons": ["لم يتم تشغيل Final Research Audit في هذه الجلسة"],
        },
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)

    # Restore persisted state once per browser session. Restoring on every
    # Streamlit rerun can overwrite a widget value the user just changed.
    if not st.session_state.get("_persistent_state_loaded", False):
        try:
            restore_persistent_state()
        except Exception as exc:
            st.error(f"تعذر قراءة المحفظة المحفوظة: {exc}")
            st.stop()
        st.session_state._persistent_state_loaded = True

    roll_paper_day()


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

# -------------------- Twelve Data API budget -------------------
@st.cache_resource
def td_runtime() -> dict[str, Any]:
    return {
        "lock": threading.Lock(),
        "calls": deque(),
        "market_backup": {},
        "quote_backup": {},
    }


def td_request_budget(limit: int = 7, window_seconds: int = 60) -> tuple[bool, int]:
    """
    Keep one credit in reserve on plans that allow 8 credits/min.
    Shared across Streamlit reruns through cache_resource.
    """
    runtime = td_runtime()
    now = time.monotonic()

    with runtime["lock"]:
        calls = runtime["calls"]
        while calls and now - calls[0] >= window_seconds:
            calls.popleft()

        if len(calls) >= limit:
            wait = max(1, int(window_seconds - (now - calls[0]) + 1))
            return False, wait

        calls.append(now)
        return True, 0


def td_rate_limit_text(wait_seconds: int | None = None) -> str:
    if wait_seconds:
        return (
            f"تم إيقاف طلبات Twelve Data مؤقتًا لحماية رصيد API. "
            f"سيُتاح طلب جديد تقريبًا خلال {wait_seconds} ثانية."
        )
    return (
        "وصلنا إلى حد Twelve Data للدقيقة الحالية. "
        "سيحاول النظام تلقائيًا عند تجدد الرصيد."
    )


def td_is_rate_limit_message(value: Any) -> bool:
    text = str(value).lower()
    return (
        "api credits" in text
        or "credit" in text
        or "rate limit" in text
        or "too many requests" in text
        or "current limit" in text
        or "429" in text
    )


# -------------------------- data feed -------------------------
def normalize_ohlcv(values: Any) -> pd.DataFrame:
    if not isinstance(values, list) or not values:
        return pd.DataFrame()
    df = pd.DataFrame(values)
    if not {"datetime", "open", "high", "low", "close"}.issubset(df.columns):
        return pd.DataFrame()
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    required = ["datetime", "open", "high", "low", "close"]
    df = df.dropna(subset=required)
    prices = df[["open", "high", "low", "close"]]
    valid = (np.isfinite(prices).all(axis=1) & (prices > 0).all(axis=1)
             & (df["high"] >= prices.max(axis=1)) & (df["low"] <= prices.min(axis=1)))
    df = df.loc[valid]
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


@st.cache_data(ttl=55, show_spinner=False)
def fetch_market(symbol: str, outputsize: int = 5000) -> tuple[pd.DataFrame, str]:
    key = secret("TWELVE_DATA_API_KEY")
    if not key:
        return pd.DataFrame(), "أضف TWELVE_DATA_API_KEY في Secrets"

    allowed, wait_seconds = td_request_budget()
    runtime = td_runtime()
    if not allowed:
        backup = runtime["market_backup"].get(symbol)
        if isinstance(backup, pd.DataFrame) and not backup.empty:
            return backup.copy(), td_rate_limit_text(wait_seconds)
        return pd.DataFrame(), td_rate_limit_text(wait_seconds)

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
    if not isinstance(payload, dict):
        return pd.DataFrame(), "رد مصدر البيانات غير صالح"
    if "values" not in payload:
        message = str(payload.get("message") or payload)
        if td_is_rate_limit_message(message):
            backup = runtime["market_backup"].get(symbol)
            if isinstance(backup, pd.DataFrame) and not backup.empty:
                return backup.copy(), td_rate_limit_text()
            return pd.DataFrame(), td_rate_limit_text()
        return pd.DataFrame(), message

    frame = closed_m5(normalize_ohlcv(payload["values"]))
    if frame.empty:
        return pd.DataFrame(), "تم الاتصال بالمصدر لكن لم تصل شموع صالحة"

    runtime["market_backup"][symbol] = frame.copy()
    return frame, "OK"


@st.cache_data(ttl=20, show_spinner=False)
def fetch_quote(symbol: str) -> dict[str, Any]:
    """Analysis/Paper quote. Live execution uses the broker quote, not this quote."""
    key = secret("TWELVE_DATA_API_KEY")
    if not key:
        return {"connected": False, "error": "TWELVE_DATA_API_KEY missing"}

    allowed, wait_seconds = td_request_budget()
    runtime = td_runtime()
    if not allowed:
        return {
            "connected": False,
            "error": td_rate_limit_text(wait_seconds),
            "rate_guard": True,
            "retry_after_seconds": wait_seconds,
        }

    request_started = time.perf_counter()
    received_at_utc = None
    request_latency_ms = None
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
        received_at_utc = now_utc()
        request_latency_ms = (time.perf_counter() - request_started) * 1000.0
        payload = response.json()
    except Exception as exc:
        request_latency_ms = (time.perf_counter() - request_started) * 1000.0
        return {
            "connected": False,
            "error": str(exc),
            "request_latency_ms": request_latency_ms,
        }

    if not isinstance(payload, dict):
        return {
            "connected": False,
            "error": "رد Quote غير صالح",
            "request_latency_ms": request_latency_ms,
        }
    bid = payload.get("bid")
    ask = payload.get("ask")
    last = payload.get("close", payload.get("price", payload.get("last")))
    market_open_raw = payload.get("is_market_open")
    market_open = market_open_raw if isinstance(market_open_raw, bool) else None
    base = {
        "market_open": market_open,
        "raw": payload,
        "received_at_utc": received_at_utc,
        "request_latency_ms": request_latency_ms,
    }

    if finite(bid) and finite(ask) and float(ask) >= float(bid):
        result = {
            **base,
            "connected": True,
            "bid": float(bid),
            "ask": float(ask),
            "last": float(last) if finite(last) else (float(bid) + float(ask)) / 2,
            "spread": float(ask) - float(bid),
        }
        runtime["quote_backup"][symbol] = dict(result)
        return result

    if finite(last):
        result = {
            **base,
            "connected": True,
            "bid": None,
            "ask": None,
            "last": float(last),
            "spread": None,
        }
        runtime["quote_backup"][symbol] = dict(result)
        return result

    message = str(payload.get("message") or "No quote")
    if td_is_rate_limit_message(message):
        return {
            **base,
            "connected": False,
            "error": td_rate_limit_text(),
            "rate_guard": True,
        }
    return {**base, "connected": False, "error": message}



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
            "quote_age_sec": None,
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
    q_age_sec = None
    q_age = None
    if q_ts is not None:
        q_age_sec = max(0.0, (now_utc() - q_ts).total_seconds())
        q_age = q_age_sec / 60.0

    if quote.get("connected") and finite(quote.get("last")) and len(close):
        q = float(quote["last"])
        c = float(close.iloc[-1])
        mismatch_pct = abs(q - c) / max(abs(c), 1e-9) * 100.0
        if mismatch_pct > 3.0:
            execution_reasons.append(f"فرق Quote عن آخر شمعة كبير ({mismatch_pct:.2f}%)")

    structural_ok = bool(base.get("ok", False) and not structural_reasons)

    if not finite(quote.get("last")) or float(quote.get("last", 0)) <= 0:
        execution_reasons.append("سعر Quote غير صالح")
    if q_ts is not None and q_ts > now_utc() + pd.Timedelta(minutes=1):
        execution_reasons.append("توقيت Quote في المستقبل")
    if not quote.get("connected"):
        execution_reasons.append("Quote المباشر غير متصل")
    if base.get("age_min", math.inf) > 15:
        execution_reasons.append("آخر شمعة M5 أقدم من 15 دقيقة")
    if q_ts is None:
        execution_reasons.append("لا يوجد توقيت موثوق لآخر Quote")
    elif q_age is not None and q_age > 5:
        execution_reasons.append(f"Quote قديم ({q_age_sec:.3f} ثانية)")
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
        "quote_age_sec": q_age_sec,
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


# ----------------------- stock desk helpers -------------------
def stock_session_state(as_of: pd.Timestamp | None = None) -> dict[str, Any]:
    """U.S. regular session context. Quote market_open remains authoritative."""
    ts = now_utc() if as_of is None else pd.Timestamp(as_of)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    ny = ts.tz_convert("America/New_York")
    minute = ny.hour * 60 + ny.minute
    weekday = ny.weekday() < 5
    regular = bool(weekday and (9 * 60 + 30) <= minute < 16 * 60)
    # Avoid the first 15 minutes and final 10 minutes for new entries.
    preferred = bool(weekday and (9 * 60 + 45) <= minute < (15 * 60 + 50))
    return {
        "ny_time": ny,
        "regular": regular,
        "preferred": preferred,
        "label": "جلسة مناسبة" if preferred else ("جلسة مفتوحة" if regular else "خارج الجلسة"),
    }


def sharia_config() -> dict[str, Any]:
    raw = str(secret("SHARIA_APPROVED_SYMBOLS", "") or "").strip()
    approved = {
        x.strip().upper()
        for x in raw.replace(";", ",").split(",")
        if x.strip()
    }
    return {
        "approved": approved,
        "source": str(secret("SHARIA_SCREEN_SOURCE", "") or "").strip(),
        "date": str(secret("SHARIA_SCREEN_DATE", "") or "").strip(),
        "configured": bool(approved),
    }


def stock_sharia_status(symbol: str) -> str:
    cfg = sharia_config()
    if not cfg["configured"]:
        return "غير متحقق"
    return "مُدرج بالقائمة" if symbol.upper() in cfg["approved"] else "غير مدرج"


def stock_quote_guard(quote: dict[str, Any], reference_price: float) -> tuple[bool, list[str], float | None]:
    reasons: list[str] = []
    spread_pct: float | None = None
    if quote.get("connected") and finite(quote.get("bid")) and finite(quote.get("ask")):
        bid = float(quote["bid"])
        ask = float(quote["ask"])
        mid = (bid + ask) / 2.0
        if bid <= 0 or ask <= 0 or ask < bid:
            reasons.append("أسعار العرض والطلب غير صالحة")
        if mid > 0:
            spread_pct = (ask - bid) / mid * 100.0
            if spread_pct > 0.20:
                reasons.append(f"سبريد السهم مرتفع ({spread_pct:.3f}%)")
    if not finite(reference_price) or reference_price <= 0:
        reasons.append("سعر السهم غير صالح")
    return not reasons, reasons, spread_pct


def quote_execution_ready(quote: dict[str, Any], max_age_minutes: float = 5.0) -> bool:
    if not quote.get("connected") or not finite(quote.get("last")):
        return False
    q_ts, _ = quote_timestamp(quote)
    if q_ts is None:
        return False
    age = (now_utc() - q_ts).total_seconds() / 60.0
    if float(quote["last"]) <= 0 or age < -1 or age > max_age_minutes:
        return False
    if quote.get("market_open") is False:
        return False
    return True


def stock_symbol_to_preset(symbol: str) -> str | None:
    symbol = symbol.upper()
    for label, spec in PRESETS.items():
        if spec.asset_class == "STOCK" and spec.symbol.upper() == symbol:
            return label
    return None


def stock_recent_loss(symbol: str, cooldown_minutes: int = STOCK_LOSS_COOLDOWN_MINUTES) -> bool:
    cutoff = now_utc() - pd.Timedelta(minutes=int(cooldown_minutes))
    for item in list(st.session_state.get("paper_history", [])):
        if str(item.get("symbol", "")).upper() != symbol.upper():
            continue
        if float(item.get("PnL", 0.0)) >= 0:
            continue
        ts = parse_timestamp(item.get("closed_at"))
        if ts is not None and ts >= cutoff:
            return True
    return False


def stock_trades_today(symbol: str | None = None) -> int:
    today = now_riyadh().date()
    count = 0
    for item in list(st.session_state.get("paper_history", [])):
        if item.get("asset_class") != "STOCK":
            continue
        if symbol and str(item.get("symbol", "")).upper() != symbol.upper():
            continue
        try:
            ts = pd.Timestamp(item.get("opened_at"))
            if ts.tzinfo is None:
                ts = ts.tz_localize(TZ)
            else:
                ts = ts.tz_convert(TZ)
            if ts.date() == today:
                count += 1
        except Exception:
            continue
    p = st.session_state.get("paper_position")
    if p and p.get("asset_class") == "STOCK":
        if not symbol or str(p.get("symbol", "")).upper() == symbol.upper():
            try:
                ts = pd.Timestamp(p.get("opened_at"))
                if ts.tzinfo is None:
                    ts = ts.tz_localize(TZ)
                else:
                    ts = ts.tz_convert(TZ)
                if ts.date() == today:
                    count += 1
            except Exception:
                pass
    return count


def stock_performance_summary() -> dict[str, Any]:
    rows = [x for x in st.session_state.get("paper_history", []) if x.get("asset_class") == "STOCK"]
    if not rows:
        return {"trades": 0, "wins": 0, "win_rate": 0.0, "net": 0.0, "avg_r": 0.0, "loss_streak": 0}
    pnl = [float(x.get("PnL", 0.0)) for x in rows]
    rvals = [float(x.get("R", 0.0)) for x in rows]
    wins = sum(v > 0 for v in pnl)
    streak = 0
    for v in pnl:  # history is newest first
        if v < 0:
            streak += 1
        else:
            break
    return {
        "trades": len(rows),
        "wins": wins,
        "win_rate": wins / len(rows) * 100.0,
        "net": sum(pnl),
        "avg_r": sum(rvals) / len(rvals),
        "loss_streak": streak,
    }


# ----------------------- smart stock engine -------------------
STOCK_PROFILE_CANDIDATES = {
    "صارم": "V500_STOCK_STRICT",
    "متوازن": "V500_STOCK_BALANCED",
    "مرن": "V500_STOCK_FLEX",
}
STOCK_CANDIDATES = set(STOCK_PROFILE_CANDIDATES.values())


def analyze_stock_candidate(raw: pd.DataFrame, profile: str) -> dict[str, Any]:
    """
    Long-only U.S. stock Paper engine, separate from the gold logic.

    Inputs are closed bars only. The engine adapts trend/volatility thresholds
    from prior data, avoids extended entries, uses relative volume when present,
    and only emits BUY/WAIT. No shorting, averaging down, or martingale.
    """
    frames = {
        "M5": raw,
        "M15": resample_closed(raw, "15min"),
        "H1": resample_closed(raw, "1h"),
        "H4": resample_closed(raw, "4h"),
    }
    snaps = {name: snapshot(frame) for name, frame in frames.items()}
    if any(v is None for v in snaps.values()):
        return {
            "signal": "WAIT", "strength": 0, "reason": "بيانات الأسهم غير كافية لكل الأطر",
            "snapshots": snaps, "b2": {}, "buy_score": 0, "sell_score": 0,
            "trend25": False, "regime_ok": False, "session_ok": False,
            "event_ok": False, "event": "NONE", "readiness_pct": 0,
            "near_entry": False, "stock_profile": profile, "volume_ratio": None,
            "volume_ok": False, "not_extended": False,
        }

    m5, m15, h1, h4 = snaps["M5"], snaps["M15"], snaps["H1"], snaps["H4"]
    m5f, h1f, h4f = m5["frame"].copy(), h1["frame"].copy(), h4["frame"].copy()

    def prior_quantile(series: pd.Series, q: float, lookback: int, fallback: float) -> float:
        values = pd.to_numeric(series, errors="coerce").dropna()
        if len(values) < 20:
            return float(fallback)
        prior = values.iloc[:-1].tail(int(lookback))
        if len(prior) < 10:
            return float(fallback)
        value = float(prior.quantile(q))
        return value if finite(value) else float(fallback)

    cfgs = {
        "صارم": {"h1_q": .60, "h4_q": .55, "h1_clip": (20.,30.), "h4_clip": (17.,26.),
                  "vol_low": .20, "vol_high": .90, "rsi": (52.,68.), "volume_min": 1.00, "max_ext": .85},
        "متوازن": {"h1_q": .45, "h4_q": .40, "h1_clip": (18.,28.), "h4_clip": (15.,24.),
                    "vol_low": .12, "vol_high": .93, "rsi": (50.,72.), "volume_min": .75, "max_ext": 1.20},
        "مرن": {"h1_q": .35, "h4_q": .30, "h1_clip": (16.,25.), "h4_clip": (14.,22.),
                 "vol_low": .08, "vol_high": .97, "rsi": (48.,75.), "volume_min": .50, "max_ext": 1.50},
    }
    cfg = cfgs.get(profile, cfgs["متوازن"])

    h1_adx = float(np.clip(prior_quantile(h1f["adx"], cfg["h1_q"], 240, 20.), *cfg["h1_clip"]))
    h4_adx = float(np.clip(prior_quantile(h4f["adx"], cfg["h4_q"], 180, 18.), *cfg["h4_clip"]))

    atr_pct = pd.to_numeric(m5f["atr"], errors="coerce") / pd.to_numeric(m5f["close"], errors="coerce").abs().replace(0, np.nan)
    atr_now = float(atr_pct.iloc[-1]) if len(atr_pct) and finite(atr_pct.iloc[-1]) else math.nan
    vol_low = prior_quantile(atr_pct, cfg["vol_low"], 2880, atr_now if finite(atr_now) else 0.)
    vol_high = prior_quantile(atr_pct, cfg["vol_high"], 2880, atr_now if finite(atr_now) else 0.)
    regime_ok = bool(finite(atr_now) and finite(vol_low) and finite(vol_high) and vol_low <= atr_now <= vol_high)

    if profile == "صارم":
        trend_ok = bool(h4["trend"] == "UP" and h1["trend"] == "UP" and m15["trend"] == "UP"
                        and float(h1["adx"]) >= h1_adx and float(h4["adx"]) >= h4_adx)
    elif profile == "مرن":
        trend_ok = bool(h1["trend"] == "UP" and m15["trend"] != "DOWN" and h4["trend"] != "DOWN"
                        and float(h1["adx"]) >= h1_adx)
    else:
        trend_ok = bool(h1["trend"] == "UP" and m15["trend"] == "UP" and h4["trend"] != "DOWN"
                        and float(h1["adx"]) >= h1_adx and float(h4["adx"]) >= h4_adx)

    current = m5f.iloc[-1]
    atr_abs = float(current["atr"]) if finite(current.get("atr")) else 0.0
    rsi_ok = cfg["rsi"][0] <= float(m5["rsi"]) <= cfg["rsi"][1]
    momentum_ok = bool(float(m5["momentum"]) > 0 and float(m5["macd_hist"]) > 0)
    price_structure_ok = bool(float(m5["close"]) > float(m5["ema20"]) >= float(m5["ema50"]))

    extension_atr = ((float(m5["close"]) - float(m5["ema20"])) / atr_abs) if atr_abs > 0 else math.inf
    not_extended = bool(finite(extension_atr) and extension_atr <= float(cfg["max_ext"]))

    # Relative volume is only evaluated while the regular U.S. session is active.
    # Outside the session it is neutral, so stale after-hours volume cannot create
    # misleading 5x/7x readings.
    session = stock_session_state()
    volume_ratio = None
    volume_ok = True
    volume_context = "خارج الجلسة"

    if session["regular"] and "volume" in m5f.columns:
        vols = pd.to_numeric(m5f["volume"], errors="coerce")
        dts = pd.to_datetime(m5f["datetime"], utc=True, errors="coerce")
        if len(vols) >= 50 and finite(vols.iloc[-1]) and pd.notna(dts.iloc[-1]):
            current_ny = dts.iloc[-1].tz_convert("America/New_York")
            current_minute = current_ny.hour * 60 + current_ny.minute

            hist = pd.DataFrame({"dt": dts.iloc[:-1], "volume": vols.iloc[:-1]}).dropna()
            if not hist.empty:
                hist["ny"] = hist["dt"].dt.tz_convert("America/New_York")
                hist["minute"] = hist["ny"].dt.hour * 60 + hist["ny"].dt.minute
                hist["weekday"] = hist["ny"].dt.weekday
                hist = hist[
                    (hist["weekday"] < 5)
                    & (hist["minute"] >= 9 * 60 + 30)
                    & (hist["minute"] < 16 * 60)
                    & ((hist["minute"] - current_minute).abs() <= 10)
                ].tail(60)

                if len(hist) >= 5:
                    baseline = float(pd.to_numeric(hist["volume"], errors="coerce").median())
                    if baseline > 0:
                        volume_ratio = float(vols.iloc[-1]) / baseline
                        volume_ok = bool(volume_ratio >= float(cfg["volume_min"]))
                        volume_context = "جلسة حية"
                    else:
                        volume_context = "مرجع حجم غير كافٍ"
                else:
                    volume_context = "مرجع حجم غير كافٍ"

    prior20 = m5f.iloc[-21:-1] if len(m5f) >= 22 else pd.DataFrame()
    prior_high = float(prior20["high"].max()) if not prior20.empty else math.nan
    breakout20 = bool(finite(prior_high) and float(current["close"]) > prior_high and momentum_ok)
    pullback = bool(atr_abs > 0 and float(current["low"]) <= float(m5["ema20"]) + .20 * atr_abs
                    and float(current["close"]) > float(m5["ema20"]) and momentum_ok)
    b2 = b2_signal(m5f)
    b2_buy = bool(b2.get("valid") and b2.get("side") == "BUY")

    if profile == "صارم":
        event_ok = bool((b2_buy or breakout20) and rsi_ok and momentum_ok and price_structure_ok and not_extended)
        event = "B2/Break20"
    else:
        event_ok = bool((b2_buy or breakout20 or pullback) and rsi_ok and momentum_ok and not_extended
                        and (price_structure_ok or profile == "مرن"))
        event = "B2/Break20/Pullback"

    session_ok = bool(session["preferred"])

    gates = [trend_ok, regime_ok, session_ok, event_ok, volume_ok]
    readiness_pct = int(round(sum(bool(x) for x in gates) / len(gates) * 100))
    near_entry = bool(trend_ok and regime_ok and session_ok and volume_ok and not event_ok)
    signal = "BUY" if all(gates) else "WAIT"

    if signal == "BUY":
        reason = f"أسهم {profile}: اتجاه + تذبذب + حجم + حدث دخول مكتملة"
    elif near_entry:
        reason = f"أسهم {profile}: السوق والاتجاه جاهزان، ننتظر حدث الدخول"
    else:
        missing = []
        if not trend_ok: missing.append("الاتجاه")
        if not regime_ok: missing.append("التذبذب")
        if not session_ok: missing.append("وقت التداول")
        if not event_ok: missing.append("حدث الدخول")
        if not volume_ok: missing.append("الحجم")
        reason = "أسهم " + profile + ": ننتظر " + " + ".join(missing or ["التأكيد"])

    return {
        "signal": signal, "strength": readiness_pct, "reason": reason,
        "snapshots": snaps, "b2": b2, "buy_score": readiness_pct, "sell_score": 0,
        "trend25": trend_ok, "regime_ok": regime_ok, "session_ok": session_ok,
        "event_ok": event_ok, "event": event, "readiness_pct": readiness_pct,
        "near_entry": near_entry, "stock_profile": profile,
        "adaptive_h1_adx": h1_adx, "adaptive_h4_adx": h4_adx,
        "adaptive_vol_low": vol_low, "adaptive_vol_high": vol_high,
        "volume_ratio": volume_ratio, "volume_ok": volume_ok,
        "volume_context": volume_context,
        "extension_atr": extension_atr, "not_extended": not_extended,
        "stock_session_label": session["label"],
    }

def choose_stock_autopilot(raw_df: pd.DataFrame) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    """Compare the three stock profiles on identical closed bars."""
    order = ["صارم", "متوازن", "مرن"]
    evaluations = {p: analyze_stock_candidate(raw_df, p) for p in order}
    loss_streak = recent_paper_loss_streak("STOCK")
    allowed = ["صارم"] if loss_streak >= 1 else order

    for p in allowed:  # prefer the strongest valid profile
        result = evaluations[p]
        if result.get("signal") == "BUY":
            return p, STOCK_PROFILE_CANDIDATES[p], result, {
                "mode": "STOCK_AUTO_SIGNAL", "loss_streak": loss_streak,
                "reason": f"اختير {p} لأنه أقوى نمط أسهم لديه دخول مكتمل",
                "evaluations": {k: {"signal": evaluations[k].get("signal"),
                                     "readiness": int(evaluations[k].get("readiness_pct", 0)),
                                     "event": evaluations[k].get("event")} for k in order},
            }

    priority = {"صارم": 3, "متوازن": 2, "مرن": 1}
    best = max(allowed, key=lambda p: (int(evaluations[p].get("readiness_pct", 0)), priority[p]))
    return best, STOCK_PROFILE_CANDIDATES[best], evaluations[best], {
        "mode": "STOCK_AUTO_WAIT", "loss_streak": loss_streak,
        "reason": ("بعد خسارة حديثة: الأسهم مقفلة على صارم" if loss_streak >= 1
                   else f"لا يوجد دخول مكتمل؛ الأقرب الآن {best}"),
        "evaluations": {k: {"signal": evaluations[k].get("signal"),
                             "readiness": int(evaluations[k].get("readiness_pct", 0)),
                             "event": evaluations[k].get("event")} for k in order},
    }


def scan_stock_batch(profile_choice: str = "ذكي تلقائي", batch_size: int = STOCK_SCAN_BATCH_SIZE) -> list[dict[str, Any]]:
    """Rotate through the watchlist without breaking the Twelve Data credit budget."""
    session = stock_session_state()
    if not session["regular"]:
        st.session_state.stock_scan_paused_reason = "السوق الأمريكي مغلق — الماسح متوقف تلقائيًا"
        return list((st.session_state.get("stock_scan_cache", {}) or {}).values())

    st.session_state.stock_scan_paused_reason = ""
    symbols = list(STOCK_UNIVERSE.keys())
    if not symbols:
        return []
    cache = dict(st.session_state.get("stock_scan_cache", {}) or {})
    cursor = int(st.session_state.get("stock_scan_cursor", 0)) % len(symbols)
    chosen = [symbols[(cursor + i) % len(symbols)] for i in range(min(int(batch_size), len(symbols)))]

    for symbol in chosen:
        raw_i, status_i = fetch_market(symbol)
        if raw_i.empty:
            old = dict(cache.get(symbol, {}) or {})
            old.update({"symbol": symbol, "status": status_i, "stale": True})
            cache[symbol] = old
            continue
        if profile_choice == "ذكي تلقائي":
            profile_i, candidate_i, result_i, _ = choose_stock_autopilot(raw_i)
        else:
            profile_i = profile_choice if profile_choice in STOCK_PROFILE_CANDIDATES else "متوازن"
            candidate_i = STOCK_PROFILE_CANDIDATES[profile_i]
            result_i = analyze_stock_candidate(raw_i, profile_i)
        m5 = result_i.get("snapshots", {}).get("M5") or {}
        cache[symbol] = {
            "symbol": symbol,
            "name": STOCK_UNIVERSE[symbol]["name"],
            "group": STOCK_UNIVERSE[symbol]["group"],
            "signal": result_i.get("signal", "WAIT"),
            "readiness": int(result_i.get("readiness_pct", 0)),
            "profile": profile_i,
            "candidate": candidate_i,
            "event": result_i.get("event", "NONE"),
            "trend": m5.get("trend", "—"),
            "rsi": round(float(m5.get("rsi", 0.0)), 1) if m5 else None,
            "volume_ratio": (round(float(result_i["volume_ratio"]), 2)
                             if finite(result_i.get("volume_ratio")) else None),
            "price": round(float(raw_i["close"].iloc[-1]), 4),
            "sharia": stock_sharia_status(symbol),
            "updated_at": now_riyadh().isoformat(),
            "status": status_i,
            "stale": False,
        }
        if result_i.get("signal") == "BUY":
            latest_candle = str(raw_i["datetime"].iloc[-1])
            alert_key = f"{symbol}:{latest_candle}:{profile_i}:BUY"
            alerts = dict(st.session_state.get("stock_scan_alerts", {}) or {})
            if alerts.get(symbol) != alert_key:
                alerts[symbol] = alert_key
                st.session_state.stock_scan_alerts = alerts
                st.toast(f"📈 Stock Scanner: {symbol} لديه BUY مكتملة — {profile_i}")

    st.session_state.stock_scan_cache = cache
    st.session_state.stock_scan_cursor = (cursor + len(chosen)) % len(symbols)
    persist_paper_state()
    return list(cache.values())


def stock_scanner_rows(sharia_only: bool = False) -> list[dict[str, Any]]:
    rows = [dict(r) for r in (st.session_state.get("stock_scan_cache", {}) or {}).values()]
    favorites = set(st.session_state.get("stock_favorites", []))
    for row in rows:
        ts = parse_timestamp(row.get("updated_at"))
        age_min = ((now_utc() - ts).total_seconds() / 60.0) if ts is not None else math.inf
        market_regular = stock_session_state()["regular"]
        row["stale"] = bool(row.get("stale", False) or age_min > 5.0)
        row["freshness"] = (
            ("حديث" if not row["stale"] else "قديم")
            if market_regular
            else "آخر جلسة"
        )
        row["favorite"] = "★" if row.get("symbol") in favorites else ""
    if sharia_only:
        rows = [r for r in rows if r.get("sharia") == "مُدرج بالقائمة"]
    rows.sort(
        key=lambda r: (
            not bool(r.get("stale")),
            r.get("signal") == "BUY",
            int(r.get("readiness", 0)),
            r.get("favorite") == "★",
        ),
        reverse=True,
    )
    return rows


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
    if spec.asset_class == "STOCK" and signal == "SELL":
        raise ValueError("محرك الأسهم Cash/Long-only ولا يسمح بصفقات Short")
    numeric = (equity, risk_pct, atr_value, entry, spec.point_value, spec.qty_step,
               spec.min_qty, spec.max_qty, stop_atr, tp1_r, tp2_r)
    if not all(finite(v) and v > 0 for v in numeric) or spec.min_qty > spec.max_qty or tp2_r <= tp1_r:
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
    cash_cap_hit = False
    if spec.asset_class == "STOCK" and signal == "BUY":
        cash_qty = floor_step(equity / entry, spec.qty_step)
        if cash_qty < spec.min_qty:
            raise ValueError("الرصيد النقدي لا يكفي للحد الأدنى من كمية السهم")
        if qty > cash_qty:
            qty = cash_qty
            cash_cap_hit = True

    if signal == "BUY":
        stop = entry - stop_distance
        tp1 = entry + stop_distance * tp1_r
        tp2 = entry + stop_distance * tp2_r
    else:
        stop = entry + stop_distance
        tp1 = entry - stop_distance * tp1_r
        tp2 = entry - stop_distance * tp2_r

    if min(stop, tp1, tp2) <= 0:
        raise ValueError("الوقف أو الهدف خارج نطاق الأسعار الصالحة")
    estimated_risk = abs(entry - stop) * spec.point_value * qty
    actual_risk_pct = estimated_risk / equity * 100.0

    return {
        "side": signal,
        "symbol": spec.symbol,
        "qty": float(qty),
        "raw_qty": float(raw_qty),
        "stepped_qty": float(stepped_qty),
        "max_qty_hit": bool(max_qty_hit),
        "cash_cap_hit": bool(cash_cap_hit),
        "notional": float(entry * qty),
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
    values = (equity, day_pnl, max_daily_loss_pct, max_order_risk_pct,
              plan.get("qty"), plan.get("risk_pct"), plan.get("actual_risk_pct"))
    if not all(finite(v) for v in values):
        return False, ["مدخلات المخاطر تحتوي أرقامًا غير صالحة"]
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
            last = float(quote["last"])
            if position.get("asset_class") == "STOCK":
                # Conservative fallback when the data plan omits bid/ask.
                return last * (1.0 - 0.0002) if position["side"] == "BUY" else last * (1.0 + 0.0002)
            return last
    return fallback


def paper_unrealized_r(position: dict[str, Any], price: float) -> float:
    distance = abs(position["entry"] - position["stop_initial"])
    if distance <= 0:
        return 0.0
    direction = 1 if position["side"] == "BUY" else -1
    return (float(position.get("realized_r", 0.0))
            + float(position.get("remaining", 1.0)) * direction * (price - position["entry"]) / distance)


def open_paper(
    plan: dict[str, Any],
    instrument: InstrumentSpec,
    order_key: str | None = None,
    candidate: str | None = None,
    profile: str | None = None,
) -> bool:
    if st.session_state.paper_position:
        return False

    if st.session_state.get("_persistence_error"):
        return False
    keys = list(st.session_state.get("paper_order_keys", []))
    if order_key and order_key in keys:
        return False
    if order_key:
        st.session_state.paper_order_keys = (keys + [order_key])[-500:]

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
        "candidate": candidate or st.session_state.get("forward_candidate", "UNKNOWN"),
        "profile": profile,
        "order_key": order_key,
    }
    st.session_state.paper_trades_today += 1
    saved = persist_paper_state()
    if saved:
        side_ar = "شراء" if plan["side"] == "BUY" else "بيع"
        emit_smart_alert(
            f"paper-open:{st.session_state.paper_position['id']}",
            f"✅ Paper — دخول {side_ar}",
            (
                f"{instrument.symbol} عند {fmt(plan['entry_reference'], 4)} • "
                f"وقف {fmt(plan['stop_loss'], 4)} • "
                f"هدف1 {fmt(plan['take_profit_1'], 4)} • "
                f"هدف2 {fmt(plan['take_profit_2'], 4)}"
            ),
            icon="⚡",
            payload={"symbol": instrument.symbol, "side": plan["side"], "type": "PAPER_ENTRY"},
        )
    return saved


def close_paper(position: dict[str, Any], exit_price: float, reason: str, r_value: float) -> None:
    current = st.session_state.get("paper_position")
    if not current or current.get("id") != position.get("id"):
        return
    if not finite(exit_price) or exit_price <= 0 or not finite(r_value):
        raise ValueError("سعر الإغلاق غير صالح")
    pnl = r_value * position["risk_money"]
    st.session_state.paper_balance += pnl
    st.session_state.paper_history.insert(
        0,
        {
            "id": position["id"],
            "symbol": position["symbol"],
            "asset_class": position.get("asset_class"),
            "side": position["side"],
            "opened_at": position["opened_at"],
            "closed_at": now_riyadh().isoformat(),
            "entry": position["entry"],
            "exit": exit_price,
            "R": round(r_value, 4),
            "PnL": round(pnl, 2),
            "reason": reason,
            "candidate": position.get("candidate"),
            "profile": position.get("profile"),
            "order_key": position.get("order_key"),
        },
    )
    event_id = str(position.get("id"))
    symbol = str(position.get("symbol"))
    side_ar = "شراء" if position.get("side") == "BUY" else "بيع"
    close_title = "🛑 خروج — وقف" if reason == "STOP" else "🎯 خروج — الهدف الثاني"
    emit_smart_alert(
        f"paper-close:{event_id}:{reason}",
        close_title,
        (
            f"{symbol} • صفقة {side_ar} • خروج {fmt(exit_price, 4)} • "
            f"{r_value:+.2f}R • P/L ${pnl:+,.2f}"
        ),
        icon="⏰",
        payload={"symbol": symbol, "side": position.get("side"), "type": "PAPER_EXIT", "reason": reason},
    )
    st.session_state.paper_position = None
    persist_paper_state()


def manage_paper(price: float) -> None:
    p = st.session_state.paper_position
    if not p or not finite(price) or price <= 0:
        return
    d = abs(p["entry"] - p["stop_initial"])
    if d <= 0:
        return

    if p["side"] == "BUY":
        if price <= p["stop"]:
            r = p["realized_r"] + p["remaining"] * ((price - p["entry"]) / d)
            close_paper(p, price, "STOP", r)
            return
        if not p["tp1_hit"] and price >= p["tp1"]:
            p["tp1_hit"] = True
            p["remaining"] = 0.5
            p["realized_r"] = 0.5 * abs(p["tp1"] - p["entry"]) / d
            p["stop"] = p["entry"]
            if not persist_paper_state():
                return
            emit_smart_alert(
                f"paper-tp1:{p['id']}",
                "🎯 الهدف الأول تحقق",
                f"{p['symbol']} • شراء • الهدف الأول {fmt(p['tp1'], 4)} • تم نقل الوقف إلى نقطة الدخول",
                icon="⏰",
                payload={"symbol": p["symbol"], "side": "BUY", "type": "TP1"},
            )
        if p["tp1_hit"] and price >= p["tp2"]:
            r = p["realized_r"] + 0.5 * ((p["tp2"] - p["entry"]) / d)
            close_paper(p, p["tp2"], "TP2", r)
    else:
        if price >= p["stop"]:
            r = p["realized_r"] + p["remaining"] * ((p["entry"] - price) / d)
            close_paper(p, price, "STOP", r)
            return
        if not p["tp1_hit"] and price <= p["tp1"]:
            p["tp1_hit"] = True
            p["remaining"] = 0.5
            p["realized_r"] = 0.5 * abs(p["tp1"] - p["entry"]) / d
            p["stop"] = p["entry"]
            if not persist_paper_state():
                return
            emit_smart_alert(
                f"paper-tp1:{p['id']}",
                "🎯 الهدف الأول تحقق",
                f"{p['symbol']} • بيع • الهدف الأول {fmt(p['tp1'], 4)} • تم نقل الوقف إلى نقطة الدخول",
                icon="⏰",
                payload={"symbol": p["symbol"], "side": "SELL", "type": "TP1"},
            )
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
    if bid is not None or ask is not None:
        if not (finite(bid) and finite(ask) and 0 < float(bid) <= float(ask)):
            return {"ok": False, "reason": "أسعار Bid/Ask للوسيط غير صالحة"}
        execution_price = (float(bid) + float(ask)) / 2.0
    elif finite(last):
        execution_price = float(last)
    else:
        return {"ok": False, "reason": "Broker Quote لا يحتوي سعر صالح"}

    if not finite(execution_price) or execution_price <= 0:
        return {"ok": False, "reason": "سعر الوسيط يجب أن يكون موجبًا ومحدودًا"}

    ts = None
    for key in ("timestamp", "last_update_at", "datetime"):
        ts = parse_timestamp(payload.get(key))
        if ts is not None:
            break

    if ts is None:
        return {"ok": False, "reason": "Broker Quote بلا timestamp موثوق", "price": execution_price}

    age_min = (now_utc() - ts).total_seconds() / 60.0
    if age_min < -1:
        return {"ok": False, "reason": "توقيت سعر الوسيط في المستقبل"}
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

    # v4.7 entry events — all computed from the current CLOSED M5 bar and
    # prior bars only. Execution still occurs on the next M5 open.
    m5["touch_ema20"] = (
        pd.to_numeric(m5["high"], errors="coerce")
        >= pd.to_numeric(m5["ema20"], errors="coerce")
    )
    m5["ema_reject_raw"] = (
        m5["touch_ema20"]
        & (pd.to_numeric(m5["close"], errors="coerce") < pd.to_numeric(m5["ema20"], errors="coerce"))
        & (pd.to_numeric(m5["close"], errors="coerce") < pd.to_numeric(m5["open"], errors="coerce"))
    )
    m5["ema_reject_event"] = (
        m5["ema_reject_raw"]
        & ~m5["ema_reject_raw"].shift(1).fillna(False)
    )

    touch_recent4 = (
        m5["touch_ema20"].astype(np.int8)
        .rolling(4, min_periods=1)
        .max()
        .astype(bool)
    )
    prev_low = pd.to_numeric(m5["low"], errors="coerce").shift(1)
    m5["pullback_break_raw"] = (
        touch_recent4
        & (pd.to_numeric(m5["close"], errors="coerce") < prev_low)
    )
    m5["pullback_break_event"] = (
        m5["pullback_break_raw"]
        & ~m5["pullback_break_raw"].shift(1).fillna(False)
    )

    prior_low20 = (
        pd.to_numeric(m5["low"], errors="coerce")
        .shift(1)
        .rolling(20, min_periods=20)
        .min()
    )
    m5["break20_raw"] = (
        pd.to_numeric(m5["close"], errors="coerce") < prior_low20
    )
    m5["break20_event"] = (
        m5["break20_raw"]
        & ~m5["break20_raw"].shift(1).fillna(False)
    )

    m5["atr_pct"] = (m5["atr"] / m5["close"].replace(0, np.nan)) * 100.0
    vol_window = 288 * 10
    m5["atr_pct_q20"] = m5["atr_pct"].shift(1).rolling(vol_window, min_periods=288 * 3).quantile(0.20)
    m5["atr_pct_q90"] = m5["atr_pct"].shift(1).rolling(vol_window, min_periods=288 * 3).quantile(0.90)
    m5["effective_time"] = m5["datetime"] + pd.Timedelta("5min")

    bt = m5[[
        "effective_time", "datetime", "open", "high", "low", "close",
        "ema20", "ema50", "rsi", "adx", "atr",
        "momentum", "macd_hist", "trend", "atr_pct", "atr_pct_q20", "atr_pct_q90",
        "b2_valid", "b2_side",
        "ema_reject_event", "pullback_break_event", "break20_event",
    ]].copy().rename(columns={
        "datetime": "datetime_m5", "open": "open_m5", "high": "high_m5", "low": "low_m5",
        "close": "close_m5", "ema20": "ema20_m5", "ema50": "ema50_m5",
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
    sell_core_count = sum(c.astype(np.int8) for c in sell_conditions[:-1])
    sell_count = sell_core_count + sell_conditions[-1].astype(np.int8)
    bt["sell_core_no_b2"] = sell_core_count.eq(9)
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


# Predeclared v4.6 research family.
# These are intentionally few and interpretable. We do NOT grid-search hundreds
# of thresholds after seeing the old holdout.
WF_CANDIDATES = {
    # Benchmark: the strongest v4.6 candidate, unchanged.
    "V47_B2_VOL": "Benchmark: B2 + trend strength + prior-only volatility",
    # New entry logic: same regime, different entry events.
    "V47_EMA_REJECT": "EMA20 bearish rejection event + trend/vol regime",
    "V47_PULLBACK_BREAK": "Recent EMA20 pullback then prior-low break + trend/vol regime",
    "V47_BREAK20": "Fresh 20-bar downside breakout + trend/vol regime",
    "V47_MULTI_EVENT": "Any v4.7 event + trend/vol regime",
}



def _research_signal_from_row(row: Any, variant: str) -> str:
    base_signal = str(row.base_signal)
    hour = int(row.entry_hour_utc)
    regime_ok = bool(row.regime_ok)

    # Preserve legacy research variants exactly.
    if variant in {"STRICT_BOTH", "SELL_ONLY", "SELL_SESSION", "SELL_SESSION_VOL"}:
        return _variant_signal(base_signal, hour, regime_ok, variant)

    if variant not in WF_CANDIDATES:
        return base_signal

    # v4.7 remains SELL-only and preserves the same session used in research.
    if not (6 <= hour < 20):
        return "WAIT"

    adx_h1 = float(getattr(row, "adx_h1", 0.0))
    adx_h4 = float(getattr(row, "adx_h4", 0.0))
    trend25 = adx_h1 >= 25.0 and adx_h4 >= 20.0

    # Strong v4.6 regime is retained; only the ENTRY EVENT changes.
    if not trend25 or not regime_ok:
        return "WAIT"

    if variant == "V47_B2_VOL":
        return "SELL" if base_signal == "SELL" else "WAIT"

    sell_core = bool(getattr(row, "sell_core_no_b2", False))
    if not sell_core:
        return "WAIT"

    ema_reject = bool(getattr(row, "ema_reject_event", False))
    pullback_break = bool(getattr(row, "pullback_break_event", False))
    break20 = bool(getattr(row, "break20_event", False))

    if variant == "V47_EMA_REJECT":
        return "SELL" if ema_reject else "WAIT"
    if variant == "V47_PULLBACK_BREAK":
        return "SELL" if pullback_break else "WAIT"
    if variant == "V47_BREAK20":
        return "SELL" if break20 else "WAIT"
    if variant == "V47_MULTI_EVENT":
        return "SELL" if (ema_reject or pullback_break or break20) else "WAIT"

    return "WAIT"


def analyze_forward_candidate(
    raw: pd.DataFrame,
    candidate: str,
) -> dict[str, Any]:
    """
    Forward-only version of the v4.7 research candidate logic.

    It uses CLOSED bars only and emits a signal for the next/current quote.
    No future bar or execution-bar information is used.
    """
    base = closed_m5(raw)
    frames = {
        "M5": base,
        "M15": resample_closed(base, "15min"),
        "H1": resample_closed(base, "1h"),
        "H4": resample_closed(base, "4h"),
    }
    snaps = {name: snapshot(frame) for name, frame in frames.items()}

    if any(v is None for v in snaps.values()):
        return {
            "signal": "WAIT",
            "strength": 0,
            "reason": "Forward candidate: بيانات غير كافية",
            "candidate": candidate,
            "snapshots": snaps,
            "b2": {},
            "buy_score": 0,
            "sell_score": 0,
            "regime_ok": False,
            "trend25": False,
            "event": "NONE",
        }

    m5 = snaps["M5"]
    m15 = snaps["M15"]
    h1 = snaps["H1"]
    h4 = snaps["H4"]

    # Same strict baseline/B2 calculation used by the research benchmark.
    b2 = b2_signal(m5["frame"])
    base_signal, buy_score, sell_score, _ = score_signal(snaps, b2)

    # Session rule is evaluated on the latest fully closed M5 candle.
    candle_ts = pd.Timestamp(m5["candle"])
    if candle_ts.tzinfo is None:
        candle_ts = candle_ts.tz_localize("UTC")
    else:
        candle_ts = candle_ts.tz_convert("UTC")
    hour = int(candle_ts.hour)

    trend25 = float(h1["adx"]) >= 25.0 and float(h4["adx"]) >= 20.0

    # Prior-only volatility regime, matching research methodology.
    calc = add_indicators(base).dropna(
        subset=["ema20", "ema50", "rsi", "atr", "momentum", "macd_hist", "adx"]
    ).reset_index(drop=True)

    if len(calc) < 288 * 3 + 25:
        return {
            "signal": "WAIT",
            "strength": 0,
            "reason": "Forward candidate: تاريخ M5 غير كافٍ لحساب volatility regime",
            "candidate": candidate,
            "snapshots": snaps,
            "b2": b2,
            "buy_score": buy_score,
            "sell_score": sell_score,
            "regime_ok": False,
            "trend25": trend25,
            "event": "NONE",
        }

    atr_pct = (
        pd.to_numeric(calc["atr"], errors="coerce")
        / pd.to_numeric(calc["close"], errors="coerce").replace(0, np.nan)
    ) * 100.0
    vol_window = 288 * 10
    q10 = atr_pct.shift(1).rolling(
        vol_window, min_periods=288 * 3
    ).quantile(0.10)
    q20 = atr_pct.shift(1).rolling(
        vol_window, min_periods=288 * 3
    ).quantile(0.20)
    q90 = atr_pct.shift(1).rolling(
        vol_window, min_periods=288 * 3
    ).quantile(0.90)
    q95 = atr_pct.shift(1).rolling(
        vol_window, min_periods=288 * 3
    ).quantile(0.95)

    atr_now = float(atr_pct.iloc[-1]) if finite(atr_pct.iloc[-1]) else math.nan
    q10_now = float(q10.iloc[-1]) if finite(q10.iloc[-1]) else math.nan
    q20_now = float(q20.iloc[-1]) if finite(q20.iloc[-1]) else math.nan
    q90_now = float(q90.iloc[-1]) if finite(q90.iloc[-1]) else math.nan
    q95_now = float(q95.iloc[-1]) if finite(q95.iloc[-1]) else math.nan
    regime_ok = bool(
        finite(atr_now)
        and finite(q20_now)
        and finite(q90_now)
        and q20_now <= atr_now <= q90_now
    )
    fast_regime_ok = bool(
        finite(atr_now)
        and finite(q10_now)
        and finite(q95_now)
        and q10_now <= atr_now <= q95_now
    )


    # Adaptive Paper thresholds from PRIOR closed bars only.
    # Nothing here is manually chosen by the user at runtime.
    h1_frame = add_indicators(frames["H1"]).dropna(subset=["adx"]).reset_index(drop=True)
    h4_frame = add_indicators(frames["H4"]).dropna(subset=["adx"]).reset_index(drop=True)

    def _prior_quantile(series: pd.Series, q: float, lookback: int, fallback: float) -> float:
        values = pd.to_numeric(series, errors="coerce").dropna()
        if len(values) < 20:
            return float(fallback)
        prior = values.iloc[:-1].tail(int(lookback))
        if len(prior) < 10:
            return float(fallback)
        v = float(prior.quantile(q))
        return v if finite(v) else float(fallback)

    adaptive_candidate_profiles = {
        "V410_ADAPTIVE_STRICT": {
            "profile": "صارم",
            "h1_q": 0.60,
            "h4_q": 0.55,
            "h1_clip": (20.0, 30.0),
            "h4_clip": (17.0, 26.0),
            "vol_low_q": 0.20,
            "vol_high_q": 0.90,
        },
        "V410_ADAPTIVE_BALANCED": {
            "profile": "متوازن",
            "h1_q": 0.45,
            "h4_q": 0.40,
            "h1_clip": (18.0, 28.0),
            "h4_clip": (15.0, 24.0),
            "vol_low_q": 0.12,
            "vol_high_q": 0.93,
        },
        "V410_ADAPTIVE_FLEX": {
            "profile": "مرن",
            "h1_q": 0.35,
            "h4_q": 0.30,
            "h1_clip": (16.0, 25.0),
            "h4_clip": (14.0, 22.0),
            "vol_low_q": 0.08,
            "vol_high_q": 0.97,
        },
    }
    adaptive_cfg = adaptive_candidate_profiles.get(
        candidate,
        adaptive_candidate_profiles["V410_ADAPTIVE_BALANCED"],
    )

    adaptive_h1_adx = float(np.clip(
        _prior_quantile(
            h1_frame["adx"],
            adaptive_cfg["h1_q"],
            240,
            22.0,
        ),
        adaptive_cfg["h1_clip"][0],
        adaptive_cfg["h1_clip"][1],
    ))
    adaptive_h4_adx = float(np.clip(
        _prior_quantile(
            h4_frame["adx"],
            adaptive_cfg["h4_q"],
            180,
            18.0,
        ),
        adaptive_cfg["h4_clip"][0],
        adaptive_cfg["h4_clip"][1],
    ))

    adaptive_vol_low = float(
        _prior_quantile(
            atr_pct,
            adaptive_cfg["vol_low_q"],
            2880,
            q10_now if finite(q10_now) else atr_now,
        )
    )
    adaptive_vol_high = float(
        _prior_quantile(
            atr_pct,
            adaptive_cfg["vol_high_q"],
            2880,
            q95_now if finite(q95_now) else atr_now,
        )
    )
    adaptive_regime_ok = bool(
        finite(atr_now)
        and finite(adaptive_vol_low)
        and finite(adaptive_vol_high)
        and adaptive_vol_low <= atr_now <= adaptive_vol_high
    )

    # Core SELL logic without B2 — exactly the 9 research conditions.
    sell_core_conditions = [
        h4["trend"] == "DOWN",
        h1["trend"] == "DOWN",
        m15["trend"] == "DOWN",
        m5["close"] < m5["ema20"] < m5["ema50"],
        finite(h1.get("ema100")) and h1["close"] < h1["ema100"],
        32 <= m5["rsi"] <= 48,
        m5["momentum"] < 0,
        m5["macd_hist"] < 0,
        max(m15["adx"], h1["adx"]) >= 20,
    ]
    sell_core = all(bool(x) for x in sell_core_conditions)

    # v4.7 event family on the latest CLOSED M5 bar only.
    close = pd.to_numeric(calc["close"], errors="coerce")
    open_ = pd.to_numeric(calc["open"], errors="coerce")
    high = pd.to_numeric(calc["high"], errors="coerce")
    low = pd.to_numeric(calc["low"], errors="coerce")
    ema20 = pd.to_numeric(calc["ema20"], errors="coerce")

    touch = high >= ema20
    ema_reject_raw = touch & (close < ema20) & (close < open_)
    ema_reject_event = bool(
        ema_reject_raw.iloc[-1]
        and not bool(ema_reject_raw.shift(1).fillna(False).iloc[-1])
    )

    touch_recent4 = (
        touch.astype(np.int8)
        .rolling(4, min_periods=1)
        .max()
        .astype(bool)
    )
    pullback_break_raw = touch_recent4 & (close < low.shift(1))
    pullback_break_event = bool(
        pullback_break_raw.iloc[-1]
        and not bool(pullback_break_raw.shift(1).fillna(False).iloc[-1])
    )

    prior_low20 = low.shift(1).rolling(20, min_periods=20).min()
    break20_raw = close < prior_low20
    break20_event = bool(
        break20_raw.iloc[-1]
        and not bool(break20_raw.shift(1).fillna(False).iloc[-1])
    )

    event = "NONE"
    event_ok = False

    if candidate in {
        "V410_ADAPTIVE_STRICT",
        "V410_ADAPTIVE_BALANCED",
        "V410_ADAPTIVE_FLEX",
    }:
        adaptive_profile_name = adaptive_cfg["profile"]

        h4_structure_ok = (
            h4["trend"] == "DOWN"
            if adaptive_profile_name == "صارم"
            else h4["trend"] != "UP"
        )
        adaptive_trend_ok = bool(
            h1["trend"] == "DOWN"
            and m15["trend"] == "DOWN"
            and h4_structure_ok
            and float(h1["adx"]) >= adaptive_h1_adx
            and float(h4["adx"]) >= adaptive_h4_adx
        )

        if adaptive_profile_name == "صارم":
            # Stronger entry confirmation; fewer trades.
            adaptive_event_ok = bool(
                base_signal == "SELL"
                or pullback_break_event
            )
            event = "ADAPTIVE_STRICT"
        elif adaptive_profile_name == "مرن":
            # More opportunities, but risk is automatically smallest.
            adaptive_event_ok = bool(
                base_signal == "SELL"
                or ema_reject_event
                or pullback_break_event
                or break20_event
            )
            event = "ADAPTIVE_FLEX"
        else:
            adaptive_event_ok = bool(
                base_signal == "SELL"
                or ema_reject_event
                or pullback_break_event
                or break20_event
            )
            event = "ADAPTIVE_BALANCED"

        event_ok = adaptive_event_ok
        trend25 = adaptive_trend_ok
        regime_ok = adaptive_regime_ok
    elif candidate == "V47_B2_VOL":
        event = "B2"
        event_ok = base_signal == "SELL"
    elif candidate == "V47_EMA_REJECT":
        event = "EMA_REJECT"
        event_ok = sell_core and ema_reject_event
    elif candidate == "V47_PULLBACK_BREAK":
        event = "PULLBACK_BREAK"
        event_ok = sell_core and pullback_break_event
    elif candidate == "V47_BREAK20":
        event = "BREAK20"
        event_ok = sell_core and break20_event
    elif candidate == "V47_MULTI_EVENT":
        event = "MULTI_EVENT"
        event_ok = sell_core and (
            ema_reject_event or pullback_break_event or break20_event
        )

    adaptive_profile_candidate = candidate in {
        "V410_ADAPTIVE_STRICT",
        "V410_ADAPTIVE_BALANCED",
        "V410_ADAPTIVE_FLEX",
    }
    session_ok = True if adaptive_profile_candidate else (6 <= hour < 20)
    final_ok = bool(session_ok and trend25 and regime_ok and event_ok)

    gate_parts = {
        ("Market-open feed" if adaptive_profile_candidate else "Session 06–20 UTC"): session_ok,
        "Trend strength": trend25,
        "Volatility regime": regime_ok,
        "Entry event": event_ok,
    }
    passed_count = sum(bool(v) for v in gate_parts.values())
    strength = int(round(passed_count / len(gate_parts) * 100))
    missing = [k for k, v in gate_parts.items() if not v]

    # "Near entry" is INFORMATIONAL ONLY.
    # It never changes the execution signal from WAIT to SELL.
    near_entry = bool(
        not final_ok
        and session_ok
        and trend25
        and regime_ok
        and not event_ok
    )

    if final_ok:
        reason = (
            f"{candidate}: SELL forward signal • {event} • "
            "trend/vol/session confirmed"
        )
        signal = "SELL"
    elif near_entry:
        reason = (
            f"{candidate}: قريب من الدخول • باقي حدث الدخول {event} فقط"
        )
        signal = "WAIT"
    else:
        reason = f"{candidate}: WAIT • missing: " + ", ".join(missing)
        signal = "WAIT"

    return {
        "signal": signal,
        "strength": strength,
        "reason": reason,
        "candidate": candidate,
        "snapshots": snaps,
        "b2": b2,
        "buy_score": buy_score,
        "sell_score": sell_score,
        "regime_ok": regime_ok,
        "trend25": trend25,
        "event": event,
        "event_ok": event_ok,
        "session_ok": session_ok,
        "near_entry": near_entry,
        "missing_conditions": missing,
        "readiness_pct": strength,
        "gate_parts": gate_parts,
        "atr_pct": atr_now,
        "atr_q20": q20_now,
        "atr_q90": q90_now,
        "adaptive_h1_adx": adaptive_h1_adx,
        "adaptive_h4_adx": adaptive_h4_adx,
        "adaptive_vol_low": adaptive_vol_low,
        "adaptive_vol_high": adaptive_vol_high,
        "adaptive_profile": adaptive_cfg.get("profile", "متوازن"),
    }


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
        signal = _research_signal_from_row(row, research_variant)
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



# --------------------- v4.6 walk-forward lab -------------------
def _prepared_slice(
    prepared: dict[str, Any],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, Any]:
    if not prepared.get("ok", False):
        return prepared

    bt = prepared["bt"]
    sub = bt[
        (bt["effective_time"] >= start)
        & (bt["effective_time"] < end)
    ].copy().reset_index(drop=True)

    if sub.empty:
        return {"ok": False, "warning": "لا توجد بيانات في نافذة Walk-Forward"}

    split_idx = max(1, min(len(sub) - 1, int(len(sub) * 0.70)))
    out = dict(prepared)
    out["bt"] = sub
    out["split_time"] = pd.Timestamp(sub["effective_time"].iloc[split_idx])
    out["prepared_rows"] = int(len(sub))
    return out


def _finite_pf(value: Any) -> float:
    try:
        v = float(value)
        return v if math.isfinite(v) else 99.0
    except Exception:
        return 0.0


def _walkforward_candidate_gate(
    aggregate_2: dict[str, Any],
    aggregate_5: dict[str, Any],
    fold_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    norm2 = aggregate_2.get("normalized", {})
    norm5 = aggregate_5.get("normalized", {})

    avg_rs = [float(r["Avg R"]) for r in fold_rows]
    fold_trades = [int(r["Trades"]) for r in fold_rows]
    positive_folds = sum(
        1
        for r in fold_rows
        if float(r["Avg R"]) > 0.0 and float(r["PF"]) > 1.0
    )

    median_avg_r = float(np.median(avg_rs)) if avg_rs else 0.0
    worst_avg_r = float(min(avg_rs)) if avg_rs else -999.0
    min_fold_trades = int(min(fold_trades)) if fold_trades else 0

    rules = [
        ("Aggregate trades ≥ 40", int(aggregate_2.get("trades", 0)) >= 40),
        ("كل Fold فيه ≥ 4 صفقات", min_fold_trades >= 4),
        ("Positive folds ≥ 4/6", positive_folds >= 4),
        ("Median fold Avg R ≥ +0.05R", median_avg_r >= 0.05),
        ("Worst fold Avg R ≥ -0.35R", worst_avg_r >= -0.35),
        ("2 bps PF ≥ 1.20", float(norm2.get("profit_factor", 0.0)) >= 1.20),
        ("2 bps Avg R ≥ +0.05R", float(norm2.get("avg_r_net", 0.0)) >= 0.05),
        ("2 bps Net P&L > 0", float(norm2.get("net_pnl", 0.0)) > 0.0),
        ("5 bps PF ≥ 1.05", float(norm5.get("profit_factor", 0.0)) >= 1.05),
        ("5 bps Net P&L > 0", float(norm5.get("net_pnl", 0.0)) > 0.0),
        ("Normalized DD ≤ 5%", float(norm2.get("max_dd_pct", 999.0)) <= 5.0),
    ]

    failed_rules = [name for name, ok in rules if not ok]
    return {
        "eligible": all(ok for _, ok in rules),
        "rules": [{"name": name, "pass": ok} for name, ok in rules],
        "failed_rules": failed_rules,
        "positive_folds": int(positive_folds),
        "median_avg_r": median_avg_r,
        "worst_avg_r": worst_avg_r,
        "min_fold_trades": min_fold_trades,
    }


def run_walkforward_lab(
    prepared: dict[str, Any],
    spec: InstrumentSpec,
    risk_pct: float,
    n_folds: int = 6,
) -> dict[str, Any]:
    if not prepared.get("ok", False):
        return {
            "ok": False,
            "warning": prepared.get("warning", "Walk-Forward prepare failed"),
        }

    bt = prepared["bt"]
    if len(bt) < 10_000:
        return {
            "ok": False,
            "warning": "Walk-Forward يحتاج تاريخ أطول",
        }

    start = pd.Timestamp(bt["effective_time"].min())
    end = pd.Timestamp(bt["effective_time"].max()) + pd.Timedelta(minutes=5)
    boundaries = pd.date_range(start=start, end=end, periods=n_folds + 1)

    fold_table: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    details: dict[str, Any] = {}

    for variant, label in WF_CANDIDATES.items():
        variant_fold_rows: list[dict[str, Any]] = []

        for i in range(n_folds):
            fold_start = pd.Timestamp(boundaries[i])
            fold_end = pd.Timestamp(boundaries[i + 1])
            fold_prepared = _prepared_slice(prepared, fold_start, fold_end)

            trades, stats = simulate_prepared_research(
                fold_prepared,
                spec,
                risk_pct=float(risk_pct),
                cost_bps_roundtrip=2.0,
                research_variant=variant,
            )
            norm = stats.get("normalized", {})

            pf = _finite_pf(norm.get("profit_factor", 0.0))
            avg_r = float(norm.get("avg_r_net", 0.0))
            row = {
                "Variant": variant,
                "Fold": f"F{i+1}",
                "Start": fold_start.date().isoformat(),
                "End": fold_end.date().isoformat(),
                "Trades": int(stats.get("trades", 0)),
                "PF": round(pf, 3),
                "Avg R": round(avg_r, 4),
                "Net P&L": round(float(norm.get("net_pnl", 0.0)), 2),
                "DD %": round(float(norm.get("max_dd_pct", 0.0)), 2),
            }
            fold_table.append(row)
            variant_fold_rows.append(row)

        trades2, stats2 = simulate_prepared_research(
            prepared,
            spec,
            risk_pct=float(risk_pct),
            cost_bps_roundtrip=2.0,
            research_variant=variant,
        )
        _, stats5 = simulate_prepared_research(
            prepared,
            spec,
            risk_pct=float(risk_pct),
            cost_bps_roundtrip=5.0,
            research_variant=variant,
        )

        norm2 = stats2.get("normalized", {})
        norm5 = stats5.get("normalized", {})
        gate = _walkforward_candidate_gate(stats2, stats5, variant_fold_rows)

        summary_rows.append(
            {
                "Variant": variant,
                "Description": label,
                "Trades": int(stats2.get("trades", 0)),
                "2bps PF": round(_finite_pf(norm2.get("profit_factor", 0.0)), 3),
                "2bps Avg R": round(float(norm2.get("avg_r_net", 0.0)), 4),
                "2bps Net": round(float(norm2.get("net_pnl", 0.0)), 2),
                "5bps PF": round(_finite_pf(norm5.get("profit_factor", 0.0)), 3),
                "5bps Net": round(float(norm5.get("net_pnl", 0.0)), 2),
                "DD %": round(float(norm2.get("max_dd_pct", 0.0)), 2),
                "Positive Folds": f"{gate['positive_folds']}/{n_folds}",
                "Median Avg R": round(gate["median_avg_r"], 4),
                "Worst Avg R": round(gate["worst_avg_r"], 4),
                "Min Fold Trades": int(gate["min_fold_trades"]),
                "Eligible": "YES" if gate["eligible"] else "NO",
                "Gate Fails": " | ".join(gate.get("failed_rules", [])) or "—",
            }
        )
        details[variant] = {
            "label": label,
            "gate": gate,
            "stats_2bps": stats2,
            "stats_5bps": stats5,
            "trades_2bps": trades2,
        }

    summary_df = pd.DataFrame(summary_rows)
    folds_df = pd.DataFrame(fold_table)

    eligible = [
        row["Variant"]
        for row in summary_rows
        if row["Eligible"] == "YES"
    ]

    return {
        "ok": True,
        "summary": summary_df,
        "folds": folds_df,
        "details": details,
        "eligible": eligible,
        "research_start": start.isoformat(),
        "research_end": end.isoformat(),
        "folds_count": int(n_folds),
    }


def _fresh_holdout_gate(
    stats2: dict[str, Any],
    stats5: dict[str, Any],
    robustness: dict[str, Any],
    history_meta: dict[str, Any],
) -> dict[str, Any]:
    norm2 = stats2.get("normalized", {})
    norm5 = stats5.get("normalized", {})
    quarters = robustness.get("quarters", []) or []
    positive_quarters = sum(
        1 for q in quarters if float(q.get("Avg R", 0.0)) > 0.0
    )

    rules = [
        ("Fresh holdout ≥ 170 days", int(history_meta.get("requested_days", 0)) >= 170),
        ("Fresh M5 bars ≥ 20,000", int(history_meta.get("bars", 0)) >= 20_000),
        ("Fresh trades ≥ 25", int(stats2.get("trades", 0)) >= 25),
        ("2 bps PF ≥ 1.20", float(norm2.get("profit_factor", 0.0)) >= 1.20),
        ("2 bps Avg R ≥ +0.05R", float(norm2.get("avg_r_net", 0.0)) >= 0.05),
        ("2 bps Net > 0", float(norm2.get("net_pnl", 0.0)) > 0.0),
        ("5 bps PF ≥ 1.05", float(norm5.get("profit_factor", 0.0)) >= 1.05),
        ("5 bps Net > 0", float(norm5.get("net_pnl", 0.0)) > 0.0),
        ("DD ≤ 5%", float(norm2.get("max_dd_pct", 999.0)) <= 5.0),
        ("Bootstrap 95% Low > 0R", float(robustness.get("bootstrap_low", -999.0)) > 0.0),
        ("Positive quarters ≥ 3/4", positive_quarters >= 3),
    ]

    return {
        "passed": all(ok for _, ok in rules),
        "rules": [{"name": n, "pass": p} for n, p in rules],
        "positive_quarters": positive_quarters,
    }



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
                "volume": np.linspace(100000, 180000, len(idx)),
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

        stock_idx = pd.date_range(
            end=now_utc().floor("5min") - pd.Timedelta(minutes=5),
            periods=3600,
            freq="5min",
            tz="UTC",
        )
        stock_close = np.linspace(100.0, 145.0, len(stock_idx)) + np.sin(np.arange(len(stock_idx)) / 13.0) * 0.12
        stock_df = pd.DataFrame(
            {
                "datetime": stock_idx,
                "open": stock_close - 0.05,
                "high": stock_close + 0.30,
                "low": stock_close - 0.28,
                "close": stock_close,
                "volume": np.linspace(100000, 180000, len(stock_idx)),
            }
        )
        stock_result = analyze_stock_candidate(stock_df, "متوازن")
        assert stock_result["signal"] in {"BUY", "WAIT"}
        assert stock_result.get("sell_score", 0) == 0
        assert stock_result.get("stock_profile") == "متوازن"
        assert "volume_ok" in stock_result and "not_extended" in stock_result
        assert "stock_session_label" in stock_result
        stock_plan = build_trade_plan("BUY", 100.0, 2.0, 100000.0, 0.075, PRESETS["Apple — AAPL"], stop_atr=1.5, tp2_r=2.0)
        assert stock_plan["side"] == "BUY" and stock_plan["actual_risk_pct"] <= 0.075 + 1e-6
        assert stock_plan["notional"] <= 100000.0 + 1e-6
        try:
            build_trade_plan("SELL", 100.0, 2.0, 100000.0, 0.075, PRESETS["Apple — AAPL"])
            raise AssertionError("stock short guard failed")
        except ValueError:
            pass
        session_probe = stock_session_state()
        assert "regular" in session_probe and "preferred" in session_probe

        return True, "OK"
    except Exception as exc:
        return False, str(exc)

def create_option_watch(symbol, kind, expiry, strike, entry_low, entry_high,
                        stop, tp1, tp2, qty, multiplier, today):
    """User-defined long-option thresholds; never infer premiums from a stock."""
    symbol = str(symbol).strip().upper()
    if not symbol or len(symbol) > 12 or not all(c.isalnum() or c == '.' for c in symbol) or not symbol.isascii():
        raise ValueError("اكتب رمز الأصل بالإنجليزية كما يظهر في سهم")
    if kind not in {"Call", "Put"}:
        raise ValueError("نوع العقد غير صالح")
    expiry = pd.Timestamp(expiry).date()
    if expiry <= pd.Timestamp(today).date():
        raise ValueError("اختر انتهاء بعد اليوم؛ متابعة دخول عقود يوم الانتهاء غير متاحة")
    values = (strike, entry_low, entry_high, stop, tp1, tp2, qty, multiplier)
    if not all(finite(v) and float(v) > 0 for v in values):
        raise ValueError("أدخل أسعارًا وكميات موجبة وصحيحة")
    if int(qty) != qty or int(multiplier) != multiplier:
        raise ValueError("الكمية ومضاعف العقد يجب أن يكونا عددين صحيحين")
    if not stop < entry_low <= entry_high < tp1 < tp2:
        raise ValueError("يلزم: الوقف < أقل دخول ≤ أعلى دخول < الهدف الأول < الهدف الثاني")
    return dict(id=uuid.uuid4().hex, symbol=symbol, kind=kind,
                expiry=expiry.isoformat(), strike=float(strike),
                entry_low=float(entry_low), entry_high=float(entry_high),
                stop=float(stop), tp1=float(tp1), tp2=float(tp2), qty=int(qty),
                multiplier=int(multiplier), entered=False, closed=False,
                fired=[], events=[], last_quote=None)


def evaluate_option_watch(watch, quote, now):
    """Evaluate an explicit premium snapshot, with identity and freshness checks."""
    result = copy.deepcopy(watch)
    current = pd.Timestamp(now)
    if current.tzinfo is None:
        raise ValueError("وقت الفحص يجب أن يتضمن المنطقة الزمنية")
    if result['closed']:
        return result, [], "المتابعة مغلقة يدويًا"
    if pd.Timestamp(result['expiry']).date() < current.tz_convert('America/New_York').date():
        return result, [], "انتهى تاريخ العقد؛ راجع حالة التسوية لدى سهم"
    if quote.get('watch_id') != result['id'] or quote.get('source') != 'manual':
        return result, [], "السعر لا يطابق العقد المحدد"
    try:
        ts = pd.Timestamp(quote['timestamp'])
        if ts.tzinfo is None or not -5 <= (current - ts).total_seconds() <= 120:
            return result, [], "السعر قديم أو توقيته غير صالح؛ حدّث سعر العقد"
        bid, ask = float(quote['bid']), float(quote['ask'])
        if not finite(bid) or not finite(ask) or not 0 <= bid <= ask or ask <= 0:
            return result, [], "أسعار Bid وAsk غير صالحة"
    except (KeyError, TypeError, ValueError):
        return result, [], "بيانات السعر غير مكتملة"
    last = result.get('last_quote')
    if last and ts <= pd.Timestamp(last['timestamp']):
        return result, [], "هذا التحديث سبق فحصه"
    result['last_quote'] = dict(timestamp=ts.isoformat(), bid=bid, ask=ask, source='manual')
    fired = set(result['fired'])
    events = []
    checks = []
    if result['entered']:
        # For both purchased Calls and Puts, exit value is the option Bid.
        if bid <= result['stop']:
            checks = [('STOP', 'وصل Bid إلى الوقف أو أقل؛ راجع الخروج في سهم')]
        elif bid >= result['tp2']:
            checks = [('TP2', 'وصل Bid إلى الهدف الثاني؛ راجع الخروج في سهم')]
        elif bid >= result['tp1']:
            checks = [('TP1', 'وصل Bid إلى الهدف الأول؛ راجع جني الربح في سهم')]
    elif pd.Timestamp(result['expiry']).date() == current.tz_convert('America/New_York').date():
        return result, [], "يوم انتهاء العقد: تنبيهات الدخول محجوبة"
    elif result['entry_low'] <= ask <= result['entry_high']:
        checks = [('ENTRY', 'وصل Ask إلى نطاق دخولك المحدد؛ ليس تأكيد تنفيذ أو توصية شراء')]
    for code, message in checks:
        if code not in fired:
            events.append(dict(code=code, message=message, time=current.isoformat(),
                               premium=bid if result['entered'] else ask, source='إدخال يدوي'))
            fired.add(code)
            if code == 'TP2':
                fired.add('TP1')
    result['fired'] = sorted(fired)
    result['events'] = (result['events'] + events)[-50:]
    return result, events, "تم فحص السعر المُدخل يدويًا"


def option_direction(snaps, event, feed_ready):
    """Symmetric, explainable underlying-price rules; not a probability model."""
    required = ('M5', 'M15', 'H1')
    if any(not snaps.get(tf) for tf in required):
        return dict(bias='WAIT', setup=False, checks=[], reason='شموع غير كافية للأطر 5 و15 و60 دقيقة')
    m5, m15, h1 = (snaps[tf] for tf in required)
    if any(not finite(s.get(k)) for s in (m5, m15, h1)
           for k in ('rsi', 'adx', 'momentum', 'macd_hist', 'close', 'ema20', 'ema50')):
        return dict(bias='WAIT', setup=False, checks=[], reason='مؤشرات غير مكتملة')
    bias = 'Call' if h1['trend'] == m15['trend'] == 'UP' else 'Put' if h1['trend'] == m15['trend'] == 'DOWN' else 'WAIT'
    bullish = bias == 'Call'
    checks = [
        ('اتفاق اتجاه الساعة و15 دقيقة', bias != 'WAIT'),
        ('اتجاه 5 دقائق متوافق', m5['trend'] == ('UP' if bullish else 'DOWN') and bias != 'WAIT'),
        ('الزخم وMACD متوافقان', (m5['momentum'] > 0 and m5['macd_hist'] > 0) if bullish else (m5['momentum'] < 0 and m5['macd_hist'] < 0)),
        ('RSI داخل نطاق المحرك', (50 <= m5['rsi'] <= 68) if bullish else (32 <= m5['rsi'] <= 50)),
        ('قوة الاتجاه ADX ≥ 20', m15['adx'] >= 20),
        ('اختراق وإعادة اختبار مكتملان', bool(event.get('valid')) and event.get('side') == ('BUY' if bullish else 'SELL')),
        ('بيانات أصل حديثة وجلسة مفتوحة', bool(feed_ready)),
    ]
    ready = all(ok for _, ok in checks)
    return dict(bias=bias, setup=ready, checks=checks,
                reason='اكتملت شروط سيناريو الأصل؛ يلزم فحص العقد' if ready else 'انتظار: ' + '، '.join(label for label, ok in checks if not ok))


def option_smart_review(watch, direction, budget, now):
    """Contract checks use only the saved option quote, never the stock price."""
    current = pd.Timestamp(now)
    dte = (pd.Timestamp(watch['expiry']).date() - current.tz_convert('America/New_York').date()).days
    result = dict(state='WAIT', reasons=[], dte=dte, spread=None, pnl=None, rr=None, cost=None, breakeven=None, max_qty=None)
    if watch.get('closed'):
        result['reasons'] = ['المتابعة مغلقة']
        return result
    if dte < 0:
        result['state'] = 'EXPIRED'
        result['reasons'] = ['انتهى العقد؛ راجع التسوية أو التنفيذ لدى سهم']
        return result
    q = watch.get('last_quote') or {}
    ts = parse_timestamp(q.get('timestamp'))
    if ts is None or not -5 <= (current - ts).total_seconds() <= 120:
        result['state'] = 'UPDATE_QUOTE'
        result['reasons'] = ['حدّث Bid وAsk للعقد من سهم؛ لا يوجد سعر عقد حديث']
        if dte <= 2:
            result['reasons'].append('العقد قريب من الانتهاء')
        return result
    bid, ask = q.get('bid'), q.get('ask')
    if not finite(bid) or not finite(ask) or not 0 <= bid <= ask or ask <= 0:
        result['reasons'] = ['سعر العقد غير صالح']
        return result
    result['spread'] = 100 * (ask - bid) / ((ask + bid) / 2)
    result['cost'] = ask * watch['qty'] * watch['multiplier']
    entry = watch.get('actual_entry', watch['entry_high'])
    result['breakeven'] = watch['strike'] + entry if watch['kind'] == 'Call' else watch['strike'] - entry
    if finite(budget) and budget > 0:
        result['max_qty'] = max(0, int(budget // (ask * watch['multiplier'])))
    if watch['entered']:
        result['pnl'] = (bid - entry) * watch['qty'] * watch['multiplier']
        if bid <= watch['stop']:
            result.update(state='REVIEW_EXIT', reasons=['Bid عند الوقف أو دونه؛ راجع الخروج في سهم'])
        elif bid >= watch['tp2']:
            result.update(state='REVIEW_EXIT', reasons=['Bid عند الهدف الثاني؛ راجع جني الربح'])
        elif bid >= watch['tp1']:
            result.update(state='REVIEW_PROFIT', reasons=['Bid عند الهدف الأول؛ راجع جني الربح وفق خطتك'])
        elif dte <= 2:
            result.update(state='REVIEW_EXPIRY', reasons=['بقي يومان أو أقل؛ راجع انتهاء العقد والتسوية'])
        elif direction.get('setup') and direction['bias'] != watch['kind']:
            result.update(state='REVIEW_REVERSAL', reasons=['ظهر سيناريو معاكس على الأصل؛ راجع العقد قبل الاستمرار'])
        else:
            result.update(state='MONITOR', reasons=['لم يصل السعر المُدخل إلى الوقف أو الأهداف'])
        if not direction.get('setup'):
            result['reasons'].append('لا يوجد سيناريو أصل مؤكد حاليًا؛ المتابعة هنا تعتمد على سعر العقد المُدخل')
        if result['spread'] > 10:
            result['reasons'].append('فرق Bid/Ask واسع؛ سعر التنفيذ الفعلي قد يختلف')
        return result
    reasons = []
    if not direction.get('setup') or direction.get('bias') != watch['kind']:
        reasons.append('سيناريو الأصل لم يكتمل لصالح نوع عقدك')
    if dte <= 2:
        reasons.append('المحرك يحجب الدخول خلال آخر يومين قبل الانتهاء')
    if result['spread'] > 10 or bid == 0:
        reasons.append('فرق الأسعار يتجاوز حد المحرك 10% أو لا يوجد Bid موجب')
    if not watch['entry_low'] <= ask <= watch['entry_high']:
        reasons.append('Ask خارج نطاق دخولك؛ لا تطارد السعر')
    if not finite(budget) or budget <= 0 or result['cost'] > budget:
        reasons.append('حدد ميزانية تتحمل خسارتها كاملة؛ تكلفة الشراء يجب ألا تتجاوزها')
    if ask > watch['stop']:
        result['rr'] = (watch['tp2'] - ask) / (ask - watch['stop'])
    if result['rr'] is None or result['rr'] < 1.5:
        reasons.append('عائد الهدف الثاني إلى مسافة الوقف أقل من حد المحرك 1.5')
    result['state'] = 'REVIEW_ENTRY' if not reasons else 'WAIT'
    result['reasons'] = reasons or ['اجتازت المدخلات قواعد المراجعة؛ تحقق من السعر والأخبار في سهم قبل أي قرار']
    return result


def render_option_intelligence(symbol, budget):
    st.subheader('مراجعة ذكية — الأصل والعقد')
    st.caption('قواعد فنية قابلة للفحص، وليست نسبة نجاح أو نموذجًا مثبت الربحية. تحليل الأصل يتحدث كل 60 ثانية أثناء فتح هذه الصفحة؛ سعر العقد يدوي.')
    if not symbol or len(symbol) > 12 or not symbol.isascii() or not all(c.isalnum() or c == '.' for c in symbol):
        st.info('أدخل رمز الأصل الصحيح لبدء التحليل')
        return
    raw, message = fetch_market(symbol)
    quote = fetch_quote(symbol)
    if raw.empty:
        st.info('تحليل الأصل غير متاح؛ تحقق من مصدر البيانات أو دعم الرمز. ' + message)
        direction = dict(bias='WAIT', setup=False, reason='لا توجد بيانات أصل', checks=[])
    else:
        feed = feed_integrity(raw, quote)
        snaps = {tf: snapshot(frame) for tf, frame in {
            'M5': closed_m5(raw), 'M15': resample_closed(raw, '15min'), 'H1': resample_closed(raw, '1h')}.items()}
        event = b2_signal(snaps['M5']['frame']) if snaps['M5'] else {}
        direction = option_direction(snaps, event, feed.get('execution_ok') and
                                     quote.get('market_open') is True and stock_session_state()['regular'])
        st.write(f"ميل الأصل {symbol}: **{signal_ar(direction['bias'])}** • " + ('سيناريو مكتمل' if direction['setup'] else 'انتظار التأكيد'))

        quote_age_sec = feed.get('quote_age_sec')
        quote_age_text = '—' if quote_age_sec is None else f"{float(quote_age_sec):,.3f} ثانية"
        quote_rtt_ms = quote.get('request_latency_ms')
        quote_rtt_text = '—' if not finite(quote_rtt_ms) else f"{float(quote_rtt_ms):,.1f} ms"
        last_bar_end = pd.Timestamp(raw.iloc[-1]['datetime']) + pd.Timedelta(minutes=5)
        last_bar_age_sec = max(0.0, (now_utc() - last_bar_end).total_seconds())
        market_text = 'مفتوح' if quote.get('market_open') is True else ('مغلق' if quote.get('market_open') is False else 'غير معروف')
        mini_grid([
            ('عمر آخر Quote', quote_age_text, 'ok' if quote_age_sec is not None and float(quote_age_sec) <= 5.0 else 'wait'),
            ('عمر آخر M5', f"{last_bar_age_sec:,.3f} ثانية", 'ok' if last_bar_age_sec <= 15.0 else 'wait'),
            ('زمن استجابة API', quote_rtt_text, ''),
            ('حالة السوق', market_text, 'ok' if quote.get('market_open') is True else 'wait'),
        ], 'tf-grid')
        st.caption(
            'عمر Quote بالثواني حتى 0.001 ثانية • '
            f"مصدر توقيت Quote: {feed.get('quote_ts_source') or 'غير متاح'} • "
            'زمن API هو زمن الطلب من التطبيق للمصدر، وليس تأخر السوق نفسه.'
        )
        st.caption('آخر شمعة مغلقة: ' + pd.Timestamp(raw.iloc[-1]['datetime']).tz_convert(TZ).strftime('%Y-%m-%d %H:%M الرياض'))
        st.dataframe([{'شرط المراجعة': label, 'الحالة': 'مكتمل' if ok else 'انتظار'} for label, ok in direction['checks']], hide_index=True)
        if not feed.get('execution_ok'):
            st.warning('تحليل تاريخي فقط: ' + '، '.join(feed.get('execution_reasons', [])))
        st.line_chart(raw.tail(100).set_index('datetime')[['close']], height=180)
        st.caption('الرسم لسعر الأصل، وليس لعلاوة الخيار')
    watch = st.session_state.get('option_watch')
    if not watch or watch.get('closed') or watch['symbol'] != symbol:
        st.info('احفظ خطة عقد لربط مراجعة الأصل بفحص أسعار العقد')
        return
    review = option_smart_review(watch, direction, budget, now_utc())
    labels = dict(WAIT='انتظار', UPDATE_QUOTE='تحديث سعر العقد مطلوب', EXPIRED='انتهى العقد',
                  REVIEW_ENTRY='مرشح للمراجعة — ليس أمر دخول', REVIEW_EXIT='راجع الخروج',
                  REVIEW_PROFIT='راجع جني الربح', REVIEW_EXPIRY='راجع قرب الانتهاء',
                  REVIEW_REVERSAL='راجع انعكاس الاتجاه', MONITOR='متابعة السعر المُدخل')
    st.warning(labels[review['state']])
    for reason in review['reasons']:
        st.write('• ' + reason)
    mini_grid([
        ('أيام حتى الانتهاء', str(review['dte']), 'wait'),
        ('فرق Bid/Ask %', fmt(review['spread'], 1), ''),
        ('ربح/خسارة تقديري قبل الرسوم $', fmt(review['pnl'], 2), ''),
        ('الهدف الثاني ÷ مسافة الوقف', fmt(review['rr'], 2), ''),
        ('تعادل الأصل عند الانتهاء قبل الرسوم $', fmt(review['breakeven'], 2), ''),
        ('أقصى عدد بحسب الميزانية قبل الرسوم', str(review['max_qty']) if review['max_qty'] is not None else '—', ''),
    ], 'plan-grid')
    st.caption('لا توجد بيانات IV أو Delta أو Theta أو سلسلة عقود أو أخبار أرباح متصلة. اجتياز القواعد لا يعني أن العقد رخيص أو مناسب للشراء. حدود 10% و1.5 ويومين قواعد لهذا المحرك وليست ضمانات.')
    # Deduplicate review alerts by watch and state. Do not imply a broker fill.
    alert_states = {'REVIEW_ENTRY', 'REVIEW_EXIT', 'REVIEW_PROFIT', 'REVIEW_EXPIRY', 'REVIEW_REVERSAL'}
    key = (watch['id'], review['state'])
    if st.session_state.get('option_review_last') != key:
        st.session_state.option_review_last = key
        if review['state'] in alert_states:
            option_titles = {
                'REVIEW_ENTRY': '🚨 عقد جاهز للمراجعة — دخول',
                'REVIEW_EXIT': '🛑 عقد — راجع الخروج',
                'REVIEW_PROFIT': '🎯 عقد — راجع جني الربح',
                'REVIEW_EXPIRY': '⏰ عقد قريب من الانتهاء',
                'REVIEW_REVERSAL': '⚠️ انعكاس اتجاه الأصل — راجع الخروج',
            }
            emit_smart_alert(
                f"option-watch:{watch['id']}:{review['state']}",
                option_titles.get(review['state'], labels[review['state']]),
                (
                    f"{watch['symbol']} {watch['kind']} • Strike {watch['strike']:g} • "
                    f"انتهاء {watch['expiry']} • " + "، ".join(review['reasons'][:3])
                ),
                icon='⏰',
                payload={
                    "symbol": watch["symbol"],
                    "contract_type": watch["kind"],
                    "strike": watch["strike"],
                    "expiry": watch["expiry"],
                    "type": review["state"],
                },
            )
    if st.session_state.get('option_review_audit_key') != key:
        st.session_state.option_review_audit_key = key
        watch['reviews'] = (watch.get('reviews', []) + [dict(
            time=now_utc().isoformat(), state=review['state'], reasons=review['reasons'],
            underlying_bias=direction.get('bias'), underlying_setup=direction.get('setup'),
            premium_source='manual', version=VERSION)])[-50:]
        st.session_state.option_watch = watch
    st.session_state.option_review = review



def _tradier_token() -> str:
    return str(secret("TRADIER_API_TOKEN", "") or "").strip()


def _tradier_base_url() -> str:
    value = str(secret("TRADIER_BASE_URL", TRADIER_DEFAULT_BASE_URL) or TRADIER_DEFAULT_BASE_URL).strip()
    return value.rstrip("/")


def _tradier_headers() -> dict[str, str]:
    token = _tradier_token()
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }


@st.cache_data(ttl=300, show_spinner=False)
def tradier_option_expirations(symbol: str) -> tuple[list[str], str]:
    """Return every listed expiration reported by Tradier for one underlying."""
    token = _tradier_token()
    if not token:
        return [], "TRADIER_API_TOKEN غير موجود"
    symbol = str(symbol).strip().upper()
    try:
        response = requests.get(
            f"{_tradier_base_url()}/markets/options/expirations",
            headers=_tradier_headers(),
            params={
                "symbol": symbol,
                "includeAllRoots": "true",
                "strikes": "false",
                "contractSize": "false",
                "expirationType": "false",
            },
            timeout=15,
        )
        if response.status_code != 200:
            return [], f"Tradier expirations HTTP {response.status_code}: {response.text[:180]}"
        payload = response.json()
    except Exception as exc:
        return [], f"تعذر جلب تواريخ الانتهاء: {exc}"

    dates = ((payload.get("expirations") or {}).get("date")
             if isinstance(payload, dict) else None)
    if dates is None:
        return [], "لم ترجع Tradier تواريخ انتهاء"
    if not isinstance(dates, list):
        dates = [dates]

    clean = []
    for item in dates:
        if isinstance(item, dict):
            item = item.get("date") or item.get("expiration")
        if item:
            try:
                clean.append(pd.Timestamp(item).date().isoformat())
            except Exception:
                continue
    return sorted(set(clean)), "OK"


@st.cache_data(ttl=20, show_spinner=False)
def tradier_option_chain(symbol: str, expiration: str) -> tuple[list[dict[str, Any]], str]:
    """Fetch the complete call/put chain for one expiration, including Greeks when provided."""
    token = _tradier_token()
    if not token:
        return [], "TRADIER_API_TOKEN غير موجود"
    symbol = str(symbol).strip().upper()
    try:
        t0 = time.perf_counter()
        response = requests.get(
            f"{_tradier_base_url()}/markets/options/chains",
            headers=_tradier_headers(),
            params={"symbol": symbol, "expiration": expiration, "greeks": "true"},
            timeout=20,
        )
        latency_ms = (time.perf_counter() - t0) * 1000.0
        if response.status_code != 200:
            return [], f"Tradier chain HTTP {response.status_code}: {response.text[:180]}"
        payload = response.json()
    except Exception as exc:
        return [], f"تعذر جلب سلسلة العقود: {exc}"

    options = ((payload.get("options") or {}).get("option")
               if isinstance(payload, dict) else None)
    if options is None:
        return [], "لم ترجع Tradier عقودًا لهذا الانتهاء"
    if not isinstance(options, list):
        options = [options]

    rows: list[dict[str, Any]] = []
    today_ny = now_utc().tz_convert("America/New_York").date()
    exp_date = pd.Timestamp(expiration).date()
    dte = (exp_date - today_ny).days
    for opt in options:
        if not isinstance(opt, dict):
            continue
        greeks = opt.get("greeks") if isinstance(opt.get("greeks"), dict) else {}

        def _num(name, default=None):
            value = opt.get(name, default)
            return float(value) if finite(value) else None

        def _gnum(*names):
            for name in names:
                value = greeks.get(name)
                if finite(value):
                    return float(value)
            return None

        bid = _num("bid")
        ask = _num("ask")
        mid = ((bid + ask) / 2.0) if bid is not None and ask is not None and ask >= bid else None
        spread_pct = (
            ((ask - bid) / mid * 100.0)
            if mid is not None and mid > 0 and bid is not None and ask is not None
            else None
        )
        option_type = str(opt.get("option_type") or opt.get("type") or "").strip().lower()
        rows.append({
            "العقد": str(opt.get("symbol") or ""),
            "النوع": "Call" if option_type.startswith("call") else ("Put" if option_type.startswith("put") else option_type),
            "الانتهاء": expiration,
            "DTE": dte,
            "Strike": _num("strike"),
            "Bid": bid,
            "Ask": ask,
            "Mid": mid,
            "Spread %": spread_pct,
            "Last": _num("last"),
            "Volume": int(float(opt.get("volume"))) if finite(opt.get("volume")) else 0,
            "OI": int(float(opt.get("open_interest"))) if finite(opt.get("open_interest")) else 0,
            "Delta": _gnum("delta"),
            "Gamma": _gnum("gamma"),
            "Theta": _gnum("theta"),
            "Vega": _gnum("vega"),
            "IV": _gnum("mid_iv", "smv_vol", "iv"),
            "آخر تحديث API ms": latency_ms,
        })
    return rows, "OK"


def option_underlying_setup(symbol: str) -> dict[str, Any]:
    """Reuse the app's existing explainable setup logic for the selected underlying."""
    raw, status = fetch_market(symbol)
    quote = fetch_quote(symbol)
    if raw.empty:
        return {
            "bias": "WAIT",
            "setup": False,
            "reason": status,
            "spot": None,
            "quote": quote,
            "feed": {},
        }
    feed = feed_integrity(raw, quote)
    frames = {
        "M5": closed_m5(raw),
        "M15": resample_closed(raw, "15min"),
        "H1": resample_closed(raw, "1h"),
    }
    snaps = {tf: snapshot(frame) for tf, frame in frames.items()}
    event = b2_signal(snaps["M5"]["frame"]) if snaps.get("M5") else {}
    direction = option_direction(
        snaps,
        event,
        bool(feed.get("execution_ok")
             and quote.get("market_open") is True
             and stock_session_state()["regular"]),
    )
    return {
        **direction,
        "spot": float(quote["last"]) if finite(quote.get("last")) else float(raw["close"].iloc[-1]),
        "quote": quote,
        "feed": feed,
    }


def _option_contract_status(row: dict[str, Any], direction: dict[str, Any]) -> tuple[str, bool]:
    """Liquidity/structure screen only; not a probability estimate."""
    option_type = row.get("النوع")
    dte = int(row.get("DTE") or 0)
    bid = row.get("Bid")
    ask = row.get("Ask")
    spread = row.get("Spread %")
    volume = int(row.get("Volume") or 0)
    oi = int(row.get("OI") or 0)
    delta = row.get("Delta")

    reasons = []
    if not direction.get("setup"):
        reasons.append("سيناريو الأصل غير مكتمل")
    if option_type != direction.get("bias"):
        reasons.append("نوع العقد عكس اتجاه السيناريو")
    if dte < 2:
        reasons.append("قريب جدًا من الانتهاء")
    if not finite(bid) or not finite(ask) or float(bid) <= 0 or float(ask) <= 0:
        reasons.append("Bid/Ask غير صالح")
    if finite(spread) and float(spread) > 20:
        reasons.append("Spread أعلى من 20%")
    if volume <= 0 and oi <= 0:
        reasons.append("لا Volume ولا OI")
    if finite(delta):
        abs_delta = abs(float(delta))
        if abs_delta < 0.20 or abs_delta > 0.85:
            reasons.append("Delta خارج نطاق المتابعة 0.20–0.85")

    ok = not reasons
    return ("✅ جاهز للمراجعة" if ok else "⏳ انتظار: " + "، ".join(reasons)), ok


def build_all_option_opportunities(symbol: str, max_dte: int = 60) -> dict[str, Any]:
    """Load every contract in every listed expiration inside the requested DTE window."""
    direction = option_underlying_setup(symbol)
    expirations, exp_status = tradier_option_expirations(symbol)
    if not expirations:
        return {
            "direction": direction,
            "all_rows": [],
            "opportunities": [],
            "status": exp_status,
            "expirations_checked": 0,
        }

    today_ny = now_utc().tz_convert("America/New_York").date()
    chosen = []
    for expiration in expirations:
        try:
            dte = (pd.Timestamp(expiration).date() - today_ny).days
        except Exception:
            continue
        if 0 <= dte <= int(max_dte):
            chosen.append(expiration)

    all_rows: list[dict[str, Any]] = []
    statuses = []
    for expiration in chosen:
        rows, status = tradier_option_chain(symbol, expiration)
        statuses.append(status)
        all_rows.extend(rows)

    opportunities = []
    for row in all_rows:
        status_text, ok = _option_contract_status(row, direction)
        row["الحالة"] = status_text
        row["سيناريو الأصل"] = direction.get("bias", "WAIT")
        if ok:
            opportunities.append(dict(row))

    # Show every qualifying contract. Sorting is for readability only and does
    # not claim a higher probability of profit.
    opportunities.sort(
        key=lambda r: (
            int(r.get("OI") or 0),
            int(r.get("Volume") or 0),
            -(float(r.get("Spread %")) if finite(r.get("Spread %")) else 999.0),
        ),
        reverse=True,
    )
    all_rows.sort(
        key=lambda r: (
            int(r.get("DTE") or 0),
            float(r.get("Strike") or 0),
            str(r.get("النوع") or ""),
        )
    )
    bad_status = next((s for s in statuses if s != "OK"), None)
    return {
        "direction": direction,
        "all_rows": all_rows,
        "opportunities": opportunities,
        "status": bad_status or "OK",
        "expirations_checked": len(chosen),
    }


def render_all_option_opportunities(symbol: str, budget: float = 0.0) -> None:
    st.subheader("كل فرص العقود")
    st.caption(
        "يعرض جميع العقود ضمن نافذة الانتهاء التي تختارها، ثم يفصل العقود المستوفية "
        "لفلاتر الاتجاه والسيولة. لا يتم إخفاء بقية العقود."
    )
    if not _tradier_token():
        st.warning(
            "لإظهار كل Call وPut تلقائيًا نحتاج TRADIER_API_TOKEN في Streamlit Secrets. "
            "Twelve Data المستخدم حاليًا ممتاز لسعر الأصل لكنه لا يزوّد هذا التطبيق بسلسلة عقود كاملة عبر REST."
        )
        return

    max_dte = st.slider(
        "أقصى مدة انتهاء للفحص (DTE)",
        min_value=7,
        max_value=365,
        value=60,
        step=7,
        key="opt_chain_max_dte",
    )

    if st.button("فحص كل الفرص الآن", type="primary", use_container_width=True, key="scan_all_options"):
        st.cache_data.clear()

    result = build_all_option_opportunities(symbol, max_dte=max_dte)
    direction = result["direction"]
    spot = direction.get("spot")
    feed = direction.get("feed") or {}
    quote_age = feed.get("quote_age_sec")

    mini_grid([
        ("الأصل", symbol, ""),
        ("سعر الأصل", fmt(spot, 2), ""),
        ("اتجاه السيناريو", signal_ar(direction.get("bias", "WAIT")), "ok" if direction.get("setup") else "wait"),
        ("اكتمال السيناريو", "مكتمل" if direction.get("setup") else "انتظار", "ok" if direction.get("setup") else "wait"),
        ("عمر Quote", "—" if quote_age is None else f"{float(quote_age):.3f}s", "ok" if quote_age is not None and float(quote_age) <= 5 else "wait"),
        ("تواريخ فُحصت", str(result.get("expirations_checked", 0)), ""),
        ("كل العقود", str(len(result["all_rows"])), ""),
        ("الفرص المستوفية", str(len(result["opportunities"])), "ok" if result["opportunities"] else "wait"),
    ], "plan-grid")

    # -------------------- simple decision panel --------------------
    st.markdown("### القرار المبسط")
    opportunities = result["opportunities"]

    if result["status"] != "OK":
        st.error(
            "🔴 البيانات غير مكتملة — لا تعتمد على هذه الشاشة للدخول الآن. "
            "سبب الاتصال: " + str(result["status"])
        )
    elif not direction.get("setup"):
        missing = [label for label, ok in direction.get("checks", []) if not ok]
        st.warning("🟡 انتظار — شروط الأصل لم تكتمل بعد")
        if missing:
            st.caption("الناقص الآن: " + " • ".join(missing[:5]))
    elif not opportunities:
        st.warning(
            "🟡 اتجاه الأصل مكتمل، لكن لا يوجد عقد اجتاز فلاتر السيولة والتنفيذ حاليًا. "
            "انتظر عقدًا ببيانات أفضل بدل اختيار عقد ضعيف."
        )
    else:
        focus = opportunities[0]
        ask = focus.get("Ask")
        bid = focus.get("Bid")
        spread = focus.get("Spread %")
        iv = focus.get("IV")
        delta = focus.get("Delta")
        st.success(
            f"🟢 شروط النظام مكتملة — يوجد {len(opportunities)} عقد/عقود جاهزة للمراجعة "
            f"باتجاه {signal_ar(direction.get('bias', '—'))}"
        )
        st.caption(
            "هذه إشارة اكتمال قواعد النظام وليست ضمان ربح أو أمر شراء. "
            "العقد الظاهر أدناه هو أول عقد بعد فرز السيولة، وليس توقعًا بأنه الأعلى ربحًا."
        )
        mini_grid([
            ("العقد", str(focus.get("العقد") or "—"), "ok"),
            ("النوع", signal_ar(focus.get("النوع") or "—"), "ok"),
            ("Strike", fmt(focus.get("Strike"), 2), ""),
            ("الانتهاء", str(focus.get("الانتهاء") or "—"), ""),
            ("DTE", str(focus.get("DTE") or "—"), ""),
            ("Bid / Ask", f"{fmt(bid, 3)} / {fmt(ask, 3)}", ""),
            ("Spread", "—" if not finite(spread) else f"{float(spread):.1f}%", "ok" if finite(spread) and float(spread) <= 10 else "wait"),
            ("Delta", fmt(delta, 3), ""),
            ("Volume / OI", f"{int(focus.get('Volume') or 0):,} / {int(focus.get('OI') or 0):,}", ""),
            ("IV", fmt(iv, 3), ""),
        ], "plan-grid")
        if finite(budget) and float(budget) > 0 and finite(ask) and float(ask) > 0:
            est_contract_cost = float(ask) * 100.0
            max_qty = int(float(budget) // est_contract_cost) if est_contract_cost > 0 else 0
            st.caption(
                f"على مضاعف قياسي 100: تكلفة عقد واحد تقريبًا ${est_contract_cost:,.2f} قبل الرسوم "
                f"• ميزانيتك الحالية تسمح حسابيًا بحد أقصى {max_qty} عقد/عقود. "
                "تحقق من مضاعف العقد الفعلي قبل التنفيذ."
            )

        compact_cols = ["العقد", "النوع", "الانتهاء", "DTE", "Strike", "Ask", "Spread %", "Volume", "OI", "Delta", "الحالة"]
        focus_contract = str(focus.get("العقد") or "")
        if focus_contract:
            emit_smart_alert(
                f"option-chain-ready:{symbol}:{focus_contract}:{direction.get('bias', 'WAIT')}",
                f"🚨 فرصة عقد مكتملة — {symbol}",
                (
                    f"{focus.get('النوع')} • Strike {fmt(focus.get('Strike'), 2)} • "
                    f"انتهاء {focus.get('الانتهاء')} • Ask {fmt(focus.get('Ask'), 3)} • "
                    f"يوجد {len(opportunities)} عقد/عقود اجتازت الفلاتر"
                ),
                icon="⏰",
                payload={
                    "symbol": symbol,
                    "contract": focus_contract,
                    "contract_type": focus.get("النوع"),
                    "type": "OPTION_CHAIN_READY",
                    "opportunity_count": len(opportunities),
                },
            )

        compact_df = pd.DataFrame(opportunities[:8])
        if "النوع" in compact_df.columns:
            compact_df["النوع"] = compact_df["النوع"].map(signal_ar)
        st.dataframe(
            compact_df[[x for x in compact_cols if x in compact_df.columns]],
            hide_index=True,
            use_container_width=True,
            height=min(350, 70 + 35 * len(compact_df)),
        )

    tab1, tab2 = st.tabs(["✅ الجاهزة للمراجعة", "كل العقود والتفاصيل"])
    with tab1:
        if result["opportunities"]:
            opp_df = pd.DataFrame(result["opportunities"])
            if "النوع" in opp_df.columns:
                opp_df["النوع"] = opp_df["النوع"].map(signal_ar)
            cols = [
                "العقد", "النوع", "الانتهاء", "DTE", "Strike", "Bid", "Ask",
                "Spread %", "Volume", "OI", "Delta", "Gamma", "Theta", "Vega", "IV",
                "الحالة",
            ]
            st.dataframe(
                opp_df[[x for x in cols if x in opp_df.columns]],
                hide_index=True,
                use_container_width=True,
                height=520,
            )
            st.caption(
                "هذه كل العقود التي اجتازت الفلاتر داخل النافذة المختارة؛ "
                "ترتيب الجدول للقراءة وليس توقعًا لاحتمال الربح."
            )
        else:
            st.info(
                "لا يوجد عقد اجتاز كل الفلاتر حاليًا. افتح «كل العقود» لرؤية جميع Call وPut "
                "والسبب الذي منع كل عقد من الظهور كفرصة مستوفية."
            )

    with tab2:
        if result["all_rows"]:
            all_df = pd.DataFrame(result["all_rows"])
            if "النوع" in all_df.columns:
                all_df["النوع"] = all_df["النوع"].map(signal_ar)
            if "سيناريو الأصل" in all_df.columns:
                all_df["سيناريو الأصل"] = all_df["سيناريو الأصل"].map(signal_ar)
            cols = [
                "العقد", "النوع", "الانتهاء", "DTE", "Strike", "Bid", "Ask",
                "Spread %", "Volume", "OI", "Delta", "Gamma", "Theta", "Vega", "IV",
                "سيناريو الأصل", "الحالة",
            ]
            st.dataframe(
                all_df[[x for x in cols if x in all_df.columns]],
                hide_index=True,
                use_container_width=True,
                height=620,
            )
        else:
            st.info("لم تصل عقود من مزود السلسلة ضمن نافذة DTE الحالية.")


def render_options_workspace():
    st.title("عقود الخيارات — سهم")
    st.caption(f"v{VERSION} • كول / بوت • متابعة شراء العقود وتنفيذ يدوي")
    st.info("هذه متابعة لحدود تختارها أنت. لا يوجد اتصال بأسعار عقود سهم أو بحسابك. "
            "يظهر التنبيه عند تحديث السعر هنا فقط، ولا تصلك إشعارات عند إغلاق التطبيق.")
    st.caption("أسعار الدخول والوقف والأهداف هي علاوة الخيار بالدولار للوحدة، وليست سعر السهم. "
               "الوقف تنبيه وليس أمرًا لدى الوسيط؛ يمكن أن تتجاوز الخسارة الوقف حتى كامل تكلفة الشراء. "
               "المتابعة محفوظة في الجلسة الحالية فقط؛ إعادة فتح الجلسة قد تفقدها.")
    watch = st.session_state.get('option_watch')
    active_watch = watch and not watch.get('closed')
    if active_watch:
        analysis_symbol = watch['symbol']
    else:
        analysis_symbol = st.text_input('رمز الأصل للمراجعة الفنية', value='AAPL', key='opt_analysis_symbol').strip().upper()
    budget = st.number_input('ميزانية شراء العقود بالدولار — مبلغ تتحمل خسارته كاملًا', min_value=0.0, value=0.0, key='opt_budget')
    st.caption('يعتمد فحص الميزانية على كامل علاوة الشراء، وليس على الوقف فقط. لا يرسل التطبيق أوامر إلى سهم.')

    render_all_option_opportunities(analysis_symbol, budget=budget)

    @st.fragment(run_every=60)
    def smart_panel():
        render_option_intelligence(analysis_symbol, budget)
    smart_panel()
    if not watch or watch.get('closed'):
        with st.form('option_create'):
            symbol = st.text_input("رمز السهم أو المؤشر", key='opt_symbol', placeholder="AAPL")
            kind = st.selectbox(
                "نوع العقد المشترى",
                ['Call', 'Put'],
                format_func=lambda x: "كول" if x == "Call" else "بوت",
                key='opt_kind',
            )
            expiry = st.date_input("تاريخ انتهاء العقد", value=now_riyadh().date() + pd.Timedelta(days=7), key='opt_expiry')
            strike = st.number_input("سعر التنفيذ Strike", min_value=0.0, value=0.0, key='opt_strike')
            low = st.number_input("أقل سعر دخول للعقد", min_value=0.0, value=0.0, format='%.3f', key='opt_low')
            high = st.number_input("أعلى سعر دخول للعقد", min_value=0.0, value=0.0, format='%.3f', key='opt_high')
            stop = st.number_input("وقف سعر العقد", min_value=0.0, value=0.0, format='%.3f', key='opt_stop')
            tp1 = st.number_input("الهدف الأول للعقد", min_value=0.0, value=0.0, format='%.3f', key='opt_tp1')
            tp2 = st.number_input("الهدف الثاني للعقد", min_value=0.0, value=0.0, format='%.3f', key='opt_tp2')
            qty = st.number_input("عدد العقود", min_value=1, value=1, step=1, key='opt_qty')
            multiplier = st.number_input("مضاعف العقد — طابقه مع مواصفاته في سهم", min_value=1, value=100, step=1, key='opt_multiplier')
            if st.form_submit_button("حفظ خطة المتابعة"):
                try:
                    st.session_state.option_watch = create_option_watch(symbol, kind, expiry, strike,
                        low, high, stop, tp1, tp2, qty, multiplier, now_riyadh().date())
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        if watch:
            st.caption("سجل المتابعة السابقة")
            st.dataframe(watch['events'], hide_index=True)
        return
    st.subheader(f"{watch['symbol']} · {watch['kind']} · {watch['strike']:g} · {watch['expiry']}")
    st.write("الحالة: " + ("تم تسجيل الدخول يدويًا" if watch['entered'] else "بانتظار تسجيل دخولك في سهم"))
    mini_grid([
        ("نطاق الدخول $", f"{watch['entry_low']:.3f} – {watch['entry_high']:.3f}", ""),
        ("الوقف $", fmt(watch['stop'], 3), "bad"),
        ("هدف 1 $", fmt(watch['tp1'], 3), "ok"),
        ("هدف 2 $", fmt(watch['tp2'], 3), "ok"),
        ("عدد العقود", str(watch['qty']), ""),
        ("تكلفة الشراء القصوى قبل الرسوم $", fmt(watch['entry_high'] * watch['qty'] * watch['multiplier'], 2), "wait"),
    ], "plan-grid")
    if not watch['entered']:
        with st.form('option_enter'):
            actual = st.number_input("سعر الدخول الفعلي للعقد بعد تنفيذه في سهم", min_value=0.0,
                                     value=0.0, format='%.3f', key='opt_actual')
            if st.form_submit_button("سجل أنني اشتريت العقد في سهم"):
                if not watch['stop'] < actual < watch['tp1']:
                    st.error("يلزم أن يكون الدخول الفعلي بين الوقف والهدف الأول؛ أعد بناء الخطة إن اختلف التنفيذ")
                elif pd.Timestamp(watch['expiry']).date() <= now_utc().tz_convert('America/New_York').date():
                    st.error("تسجيل دخول يوم الانتهاء غير متاح")
                else:
                    watch['entered'] = True
                    watch['actual_entry'] = float(actual)
                    watch['last_quote'] = None
                    st.session_state.option_watch = watch
                    st.rerun()
    else:
        st.caption(f"دخولك المسجل: ${watch['actual_entry']:.3f} • لا نتأكد من التنفيذ لدى سهم")
    with st.form('option_quote'):
        st.write("حدّث أسعار نفس العقد من سهم")
        bid = st.number_input("Bid — سعر بيع العقد", min_value=0.0, value=0.0, format='%.3f', key='opt_bid')
        ask = st.number_input("Ask — سعر شراء العقد", min_value=0.0, value=0.0, format='%.3f', key='opt_ask')
        age = st.number_input("عمر السعر بالثواني", min_value=0, value=0, step=1, key='opt_age')
        checked = st.checkbox("طابقت نوع العقد وStrike والانتهاء والسعر الحالي في سهم", key='opt_checked')
        if st.form_submit_button("تحديث السعر وفحص التنبيهات"):
            if not checked:
                st.warning("طابق بيانات العقد أولًا")
            else:
                now = now_utc()
                watch, events, message = evaluate_option_watch(watch, dict(watch_id=watch['id'],
                    bid=bid, ask=ask, source='manual', timestamp=(now-pd.Timedelta(seconds=int(age))).isoformat()), now)
                if message != 'تم فحص السعر المُدخل يدويًا':
                    watch['last_quote'] = None
                st.session_state.option_watch = watch
                st.info(message)
                for event in events:
                    st.toast(event['message'], icon='🔔')
                st.session_state.option_quote_message = message
                st.rerun()
    if watch['last_quote']:
        q = watch['last_quote']
        st.caption(f"آخر سعر يدوي: Bid {q['bid']:.3f} / Ask {q['ask']:.3f} • "
                   f"{pd.Timestamp(q['timestamp']).tz_convert(TZ).strftime('%Y-%m-%d %H:%M:%S')} الرياض — ليس بثًا مباشرًا")
    if st.session_state.get('option_quote_message'):
        st.caption(st.session_state.option_quote_message)
    st.subheader("سجل التنبيهات — لا يمثل صفقات منفذة")
    if watch['events']:
        for event in reversed(watch['events']):
            stamp = pd.Timestamp(event['time']).tz_convert(TZ).strftime('%m-%d %H:%M:%S')
            st.warning(f"{stamp} • {event['message']} • ${event['premium']:.3f} • إدخال يدوي")
    else:
        st.caption("لا توجد تنبيهات مسجلة")
    with st.expander('سجل قرارات المراجعة الذكية'):
        st.dataframe(watch.get('reviews', []), hide_index=True)
    st.download_button("تنزيل الخطة وسجل التنبيهات", json.dumps(watch, ensure_ascii=False, indent=2),
                       file_name='option-watch.json', mime='application/json')
    if st.button("إنهاء المتابعة وبدء خطة جديدة", key='opt_close'):
        watch['closed'] = True
        st.session_state.option_watch = watch
        st.rerun()


# ----------------------------- app ----------------------------
engine_ok, engine_error = self_test()
if not engine_ok:
    st.error(f"فشل اختبار المحرك الداخلي: {engine_error}")
    st.stop()

st.sidebar.markdown("## ⚡ X10 SMART TERMINAL")
st.sidebar.caption(f"v{VERSION} • Gold • Smart Stocks • Futures")
_alert_channels = smart_alert_channels()
st.sidebar.caption(
    "⏰ التنبيهات: داخل التطبيق ✅"
    + (" • Telegram ✅" if _alert_channels["telegram"] else " • Telegram غير مربوط")
    + (" • Webhook ✅" if _alert_channels["webhook"] else "")
)

# Scanner buttons set a pending preset and rerun; consume it before widgets exist.
pending_market = st.session_state.get("pending_market_preset")
if pending_market in PRESETS:
    st.session_state.market_preset = pending_market
    st.session_state.pending_market_preset = None
    pending_spec = PRESETS[pending_market]
    st.session_state.market_section = (
        "الأسهم" if pending_spec.asset_class == "STOCK"
        else "العقود" if pending_spec.asset_class == "FUTURES"
        else "الذهب"
    )

section_map = {
    "الذهب": [k for k,v in PRESETS.items() if v.asset_class == "GOLD"],
    "الأسهم": [k for k,v in PRESETS.items() if v.asset_class == "STOCK"],
    "العقود": [k for k,v in PRESETS.items() if v.asset_class == "FUTURES"],
}
section_options = ["الذهب", "الأسهم", "العقود", "خيارات سهم"]
current_section = st.session_state.get("market_section", "الذهب")
if current_section not in section_options:
    current_section = "الذهب"
market_section = st.sidebar.radio("القسم", section_options, key="market_section")
if market_section == "خيارات سهم":
    render_options_workspace()
    st.stop()
market_options = section_map[market_section]
current_preset = st.session_state.get("market_preset")
if current_preset not in market_options:
    st.session_state.market_preset = market_options[0]
preset_name = st.sidebar.selectbox("السوق", market_options, key="market_preset")
base_spec = PRESETS[preset_name]

st.sidebar.divider()
mode = st.sidebar.radio("وضع التشغيل", ["تحليل", "Paper", "Live"], index=1)

st.session_state.auto_paper = st.sidebar.toggle(
    "التداول التجريبي التلقائي",
    value=st.session_state.auto_paper,
    help="يفتح Paper فقط عند اكتمال الإشارة وكل بوابات المخاطر.",
)

adaptive_paper_mode = st.sidebar.toggle(
    "المحرك الذكي — Paper",
    value=(mode == "Paper"),
    disabled=(mode != "Paper" or base_spec.asset_class == "FUTURES"),
    help="للذهب والأسهم: يضبط الشروط والمخاطرة تلقائيًا. العقود لها تطوير منفصل لاحقًا.",
)

profile_options = ["ذكي تلقائي", "صارم", "متوازن", "مرن"]
current_profile = st.session_state.get("adaptive_profile_choice", "ذكي تلقائي")
if current_profile not in profile_options:
    current_profile = "ذكي تلقائي"
st.session_state.adaptive_profile_choice = st.sidebar.selectbox(
    "نمط الدخول", profile_options,
    index=profile_options.index(current_profile),
    disabled=(mode != "Paper" or not adaptive_paper_mode),
    help="ذكي تلقائي يقارن الأنماط بنفسه. لا تحتاج ضبط ADX أو التذبذب أو المخاطرة يدويًا.",
)

if base_spec.asset_class == "STOCK":
    st.sidebar.markdown("### 📈 الأسهم")
    st.session_state.stock_auto_scan = st.sidebar.toggle(
        "ماسح الأسهم التلقائي",
        value=bool(st.session_state.get("stock_auto_scan", True)),
        help="يفحص 3 رموز في كل دورة 60 ثانية لحماية حد API.",
    )
    sh_cfg_sidebar = sharia_config()
    st.session_state.stock_sharia_only = st.sidebar.toggle(
        "عرض القائمة الشرعية فقط",
        value=bool(st.session_state.get("stock_sharia_only", False)),
        disabled=not sh_cfg_sidebar["configured"],
        help="يتطلب SHARIA_APPROVED_SYMBOLS في Secrets. التطبيق لا يخمن الحكم الشرعي.",
    )

persist_paper_state()
if st.session_state.get("_persistence_error"):
    st.error("تعذر حفظ المحفظة؛ أوقفنا فتح صفقات جديدة. " + st.session_state._persistence_error)

advanced_settings = st.sidebar.toggle(
    "إعدادات متقدمة", value=False,
    help="يعرض إعدادات الأصل والمخاطر والبحث والتشخيص.",
)

# Safe automatic defaults. Numeric controls are intentionally hidden from the
# normal experience; Smart Paper chooses its own smaller risk.
risk_pct = 0.25
custom_symbol = base_spec.symbol
point_value = float(base_spec.point_value)
qty_step = float(base_spec.qty_step)
min_qty = float(base_spec.min_qty)
max_qty = float(base_spec.max_qty)
forward_candidate = st.session_state.get("forward_candidate", "V47_B2_VOL")
advanced_ui = False
max_daily_loss_pct = 1.5
max_open_positions = 1
max_order_risk_pct = 0.50
refresh_seconds = 60

if advanced_settings:
    st.sidebar.markdown("### الإعدادات المتقدمة")
    advanced_ui = st.sidebar.toggle("إظهار أدوات البحث والتشخيص", value=False)
    risk_pct = st.sidebar.number_input("مخاطرة Legacy %", min_value=0.05, max_value=0.50, value=0.25, step=0.05)

    with st.sidebar.expander("إعدادات الأصل", expanded=False):
        custom_symbol = st.text_input("رمز البيانات/الوسيط", value=base_spec.symbol)
        point_value = st.number_input("قيمة حركة سعر 1 لكل وحدة", min_value=0.000001, value=float(base_spec.point_value), format="%.6f")
        qty_step = st.number_input("خطوة الكمية", min_value=0.000001, value=float(base_spec.qty_step), format="%.6f")
        min_qty = st.number_input("أقل كمية", min_value=0.0, value=float(base_spec.min_qty), format="%.6f")
        max_qty = st.number_input("أعلى كمية", min_value=min_qty, value=float(base_spec.max_qty), format="%.6f")

    if base_spec.asset_class == "GOLD":
        st.session_state.forward_candidate = st.sidebar.selectbox(
            "مرشح Gold Forward", list(WF_CANDIDATES.keys()),
            index=(list(WF_CANDIDATES.keys()).index(st.session_state.forward_candidate)
                   if st.session_state.forward_candidate in WF_CANDIDATES else 0),
            format_func=lambda x: f"{x} — {WF_CANDIDATES.get(x, x)}",
        )
        forward_candidate = st.session_state.forward_candidate

    st.session_state.kill_switch = st.sidebar.toggle("KILL SWITCH", value=st.session_state.kill_switch, help="ON يمنع أي أمر Live جديد")
    with st.sidebar.expander("المخاطر والتحديث", expanded=False):
        max_daily_loss_pct = st.number_input("حد الخسارة اليومية %", .5, 3.0, 1.5, .5)
        max_open_positions = st.number_input("أقصى مراكز مفتوحة", 1, 3, 1, 1)
        max_order_risk_pct = st.number_input("الحد الصلب لمخاطرة الأمر %", .05, .50, .50, .05)
        st.session_state.auto_refresh = st.toggle("تحديث Paper تلقائي", value=st.session_state.auto_refresh)
        refresh_seconds = st.slider("ثواني التحديث", 60, 180, 60, step=15)
else:
    st.session_state.auto_refresh = bool(st.session_state.auto_paper)

if not st.session_state.auto_refresh:
    refresh_seconds = 60

instrument = InstrumentSpec(
    label=base_spec.label,
    asset_class=base_spec.asset_class,
    symbol=custom_symbol.strip(),
    point_value=float(point_value),
    qty_step=float(qty_step),
    min_qty=float(min_qty),
    max_qty=float(max_qty),
)

ADAPTIVE_PROFILE_CANDIDATES = {
    "صارم": "V410_ADAPTIVE_STRICT",
    "متوازن": "V410_ADAPTIVE_BALANCED",
    "مرن": "V410_ADAPTIVE_FLEX",
}
ADAPTIVE_CANDIDATES = set(ADAPTIVE_PROFILE_CANDIDATES.values())
SMART_PAPER_CANDIDATES = ADAPTIVE_CANDIDATES | STOCK_CANDIDATES

def recent_paper_loss_streak(asset_class: str | None = None) -> int:
    streak = 0
    rows = list(st.session_state.get("paper_history", []))
    if asset_class:
        rows = [x for x in rows if x.get("asset_class") == asset_class]
    for item in rows[:5]:
        if float(item.get("PnL", 0.0)) < 0:
            streak += 1
        else:
            break
    return streak

selected_profile = st.session_state.get("adaptive_profile_choice", "ذكي تلقائي")
adaptive_profile = (
    "متوازن" if selected_profile == "ذكي تلقائي" else selected_profile
)

if mode == "Paper" and adaptive_paper_mode:
    if instrument.asset_class == "GOLD":
        active_candidate = ADAPTIVE_PROFILE_CANDIDATES[adaptive_profile]
    elif instrument.asset_class == "STOCK":
        active_candidate = STOCK_PROFILE_CANDIDATES[adaptive_profile]
    else:
        active_candidate = forward_candidate
else:
    active_candidate = forward_candidate

def adaptive_paper_risk_pct(profile: str, asset_class: str | None = None) -> float:
    """
    Risk is automatic and moves opposite to permissiveness:
    strict <= 0.10%, balanced <= 0.075%, flexible <= 0.05%.
    Recent losses in the same asset class reduce it further. No martingale.
    """
    base = {"صارم": 0.10, "متوازن": 0.075, "مرن": 0.05}.get(profile, 0.075)
    recent = list(st.session_state.get("paper_history", []))
    if asset_class:
        recent = [x for x in recent if x.get("asset_class") == asset_class]
    recent = recent[:5]
    recent_losses = sum(1 for x in recent if float(x.get("PnL", 0.0)) < 0)
    if recent_losses >= 2:
        return min(base, 0.05)
    if recent_losses == 1:
        return min(base, 0.075)
    return base

paper_risk_pct = (
    adaptive_paper_risk_pct(adaptive_profile, instrument.asset_class)
    if active_candidate in SMART_PAPER_CANDIDATES
    else float(risk_pct)
)

def choose_smart_autopilot(raw_df: pd.DataFrame) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    """
    Evaluate all adaptive Paper profiles on the same CLOSED market data.

    Selection logic:
    1) After any recent loss, allow STRICT only until the sequence stabilizes.
    2) Otherwise, if one or more profiles have a valid SELL, choose the
       strongest profile in order: strict -> balanced -> flexible.
    3) If no profile has a valid entry, display the closest profile by
       readiness, preferring the stricter profile on ties.

    This does not predict profit and never increases risk after a loss.
    """
    profile_order = ["صارم", "متوازن", "مرن"]
    evaluations: dict[str, dict[str, Any]] = {}

    for profile in profile_order:
        candidate = ADAPTIVE_PROFILE_CANDIDATES[profile]
        evaluations[profile] = analyze_forward_candidate(raw_df, candidate)

    loss_streak = recent_paper_loss_streak("GOLD")
    allowed = ["صارم"] if loss_streak >= 1 else profile_order

    # Strongest valid signal wins.
    for profile in allowed:
        result = evaluations[profile]
        if result.get("signal") == "SELL":
            return (
                profile,
                ADAPTIVE_PROFILE_CANDIDATES[profile],
                result,
                {
                    "mode": "AUTO_SIGNAL",
                    "loss_streak": loss_streak,
                    "reason": f"اختير {profile} لأنه أقوى نمط لديه إشارة مكتملة",
                    "evaluations": {
                        p: {
                            "signal": evaluations[p].get("signal"),
                            "readiness": int(evaluations[p].get("readiness_pct", 0)),
                            "event": evaluations[p].get("event"),
                        }
                        for p in profile_order
                    },
                },
            )

    # No entry yet: show the candidate nearest to completion.
    priority = {"صارم": 3, "متوازن": 2, "مرن": 1}
    best_profile = max(
        allowed,
        key=lambda p: (
            int(evaluations[p].get("readiness_pct", 0)),
            priority[p],
        ),
    )
    return (
        best_profile,
        ADAPTIVE_PROFILE_CANDIDATES[best_profile],
        evaluations[best_profile],
        {
            "mode": "AUTO_WAIT",
            "loss_streak": loss_streak,
            "reason": (
                "بعد خسارة حديثة: النظام مقفل على صارم"
                if loss_streak >= 1
                else f"لا توجد إشارة مكتملة؛ الأقرب الآن {best_profile}"
            ),
            "evaluations": {
                p: {
                    "signal": evaluations[p].get("signal"),
                    "readiness": int(evaluations[p].get("readiness_pct", 0)),
                    "event": evaluations[p].get("event"),
                }
                for p in profile_order
            },
        },
    )

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
    f"X10 v{VERSION} • Gold + U.S. Stocks • Paper Smart AutoPilot • "
    "market-specific engines • automatic risk control"
)

render_readiness_panel()

if instrument.asset_class == "STOCK":
    st.info(
        "📈 محرك الأسهم مستقل عن الذهب: Long-only في Paper، ويقرأ الاتجاه والزخم "
        "والتذبذب تلقائيًا. رموز القائمة لا تُعتبر فحصًا شرعيًا بحد ذاتها؛ "
        "الفحص الشرعي يحتاج مصدرًا محدثًا منفصلًا."
    )

raw, data_status = fetch_market(instrument.symbol)
if raw.empty:
    st.error("مصدر البيانات غير جاهز")
    st.info(data_status)
    if not secret("TWELVE_DATA_API_KEY"):
        st.code('TWELVE_DATA_API_KEY = "ضع_المفتاح_هنا"', language="toml")
    else:
        st.caption(
            "المفتاح موجود. إذا كانت المشكلة حد API، اترك Auto Paper Forward يعمل "
            "وسيعاود المحرك الطلب تلقائيًا بعد تجدد رصيد الدقيقة."
        )
    st.stop()
elif data_status != "OK":
    st.warning(data_status)

quality = data_quality(raw)
quote = fetch_quote(instrument.symbol)
feed = feed_integrity(raw, quote)
with st.expander("فحص اتصال الأسعار والتنفيذ", expanded=not feed.get("execution_ok", False)):
    if feed.get("execution_ok", False):
        st.success("اجتازت بيانات السوق فحص الحداثة والسلامة لهذه الدورة. هذا لا يفعّل التداول الحقيقي.")
    else:
        for reason in feed.get("execution_reasons", []):
            st.warning(reason)
    quote_time, _ = quote_timestamp(quote)
    if quote_time is not None:
        st.caption(f"وقت سعر المصدر: {quote_time.tz_convert(TZ).isoformat()}")
    st.caption("فحص البيانات مستقل عن وجود المفتاح وعن شروط وسيط التنفيذ.")
reference_price = (
    float(quote["last"])
    if quote.get("connected") and finite(quote.get("last"))
    else float(raw["close"].iloc[-1])
)

analysis = analyze_mtf(raw)

smart_autopilot_meta: dict[str, Any] = {
    "mode": "MANUAL_PROFILE",
    "reason": "نمط يدوي",
    "evaluations": {},
    "loss_streak": recent_paper_loss_streak(instrument.asset_class),
}

if instrument.asset_class == "STOCK":
    # Stocks always use their own engine in Analysis/Paper; never the gold model.
    if selected_profile == "ذكي تلقائي":
        (
            adaptive_profile,
            active_candidate,
            forward_analysis,
            smart_autopilot_meta,
        ) = choose_stock_autopilot(raw)
    else:
        adaptive_profile = selected_profile if selected_profile in STOCK_PROFILE_CANDIDATES else "متوازن"
        active_candidate = STOCK_PROFILE_CANDIDATES[adaptive_profile]
        forward_analysis = analyze_stock_candidate(raw, adaptive_profile)
    if mode == "Paper":
        paper_risk_pct = adaptive_paper_risk_pct(adaptive_profile, "STOCK")
elif mode == "Paper" and adaptive_paper_mode and selected_profile == "ذكي تلقائي" and instrument.asset_class == "GOLD":
    (
        adaptive_profile,
        active_candidate,
        forward_analysis,
        smart_autopilot_meta,
    ) = choose_smart_autopilot(raw)
    paper_risk_pct = adaptive_paper_risk_pct(adaptive_profile, "GOLD")
else:
    forward_analysis = analyze_forward_candidate(raw, active_candidate)
    if active_candidate in SMART_PAPER_CANDIDATES:
        paper_risk_pct = adaptive_paper_risk_pct(adaptive_profile, instrument.asset_class)

if not feed.get("trusted", False):
    if analysis.get("signal") in {"BUY", "SELL"}:
        analysis = {
            **analysis,
            "signal": "WAIT",
            "reason": "تم حجب الإشارة بسبب فشل فحص بنية بيانات السوق",
        }
    if forward_analysis.get("signal") in {"BUY", "SELL"}:
        forward_analysis = {
            **forward_analysis,
            "signal": "WAIT",
            "reason": "Forward candidate blocked: feed structure check failed",
        }

execution_analysis = (
    forward_analysis
    if instrument.asset_class == "STOCK" or mode in {"Paper", "Live"}
    else analysis
)

candle_id = str(raw["datetime"].iloc[-1])
research_context = hashlib.sha256(
    (
        f"{VERSION}|{instrument.symbol}|{instrument.point_value}|"
        f"{instrument.qty_step}|{instrument.min_qty}|{instrument.max_qty}|"
        f"{float(risk_pct):.6f}"
    ).encode()
).hexdigest()[:16]

# Manage an open Paper position from its OWN symbol, even if the user changes
# the selected market. This prevents cross-symbol marking errors.
if st.session_state.paper_position:
    _p = st.session_state.paper_position
    if str(_p.get("symbol")) == instrument.symbol and feed.get("execution_ok", False):
        mark = paper_mark_price(_p, quote, reference_price)
        manage_paper(mark)
    elif str(_p.get("symbol")) != instrument.symbol:
        _pq = fetch_quote(str(_p.get("symbol")))
        if quote_execution_ready(_pq):
            _fallback = float(_pq.get("last")) if finite(_pq.get("last")) else float(_p.get("entry", 0.0))
            manage_paper(paper_mark_price(_p, _pq, _fallback))

if st.session_state.last_signal_candle.get(instrument.symbol) != candle_id:
    st.session_state.last_signal_candle[instrument.symbol] = candle_id
    st.session_state.decisions.insert(
        0,
        {
            "time": now_riyadh().isoformat(),
            "symbol": instrument.symbol,
            "candle": candle_id,
            "signal": execution_analysis["signal"],
            "strength": execution_analysis["strength"],
            "buy_score": execution_analysis.get("buy_score", 0),
            "sell_score": execution_analysis.get("sell_score", 0),
            "feed_execution_ready": feed.get("execution_ok", False),
            "reason": execution_analysis["reason"],
            "candidate": active_candidate if mode in {"Paper", "Live"} else "LEGACY_ANALYSIS",
        },
    )
    st.session_state.decisions = st.session_state.decisions[:500]

    if execution_analysis["signal"] in {"BUY", "SELL"}:
        alert_key = f"{instrument.symbol}:{candle_id}:{execution_analysis['signal']}"
        if st.session_state.last_alert_candle.get(instrument.symbol) != alert_key:
            st.session_state.last_alert_candle[instrument.symbol] = alert_key
            side_ar = "شراء" if execution_analysis["signal"] == "BUY" else "بيع"
            asset_label = "الذهب" if instrument.asset_class == "GOLD" else instrument.symbol
            emit_smart_alert(
                f"entry-signal:{alert_key}",
                f"🚨 إشارة دخول {side_ar} — {asset_label}",
                (
                    f"السعر {fmt(reference_price, 4)} • قوة الإشارة {execution_analysis['strength']}% • "
                    f"{execution_analysis.get('reason', '')}"
                ),
                icon="⚡",
                payload={
                    "symbol": instrument.symbol,
                    "asset_class": instrument.asset_class,
                    "side": execution_analysis["signal"],
                    "price": reference_price,
                    "strength": execution_analysis["strength"],
                    "type": "ENTRY_SIGNAL",
                },
            )


# Fresh opposite signal against an open Paper position => exit-review alert.
_current_paper = st.session_state.get("paper_position")
if (
    _current_paper
    and str(_current_paper.get("symbol")) == instrument.symbol
    and execution_analysis.get("signal") in {"BUY", "SELL"}
    and execution_analysis.get("signal") != _current_paper.get("side")
):
    _rev_key = (
        f"{instrument.symbol}:{candle_id}:{_current_paper.get('id')}:"
        f"{execution_analysis.get('signal')}"
    )
    _last_rev = dict(st.session_state.get("last_reversal_alert", {}) or {})
    if _last_rev.get(instrument.symbol) != _rev_key:
        _last_rev[instrument.symbol] = _rev_key
        st.session_state.last_reversal_alert = _last_rev
        emit_smart_alert(
            f"reversal:{_rev_key}",
            "⚠️ انعكاس — راجع الخروج",
            (
                f"{instrument.symbol} • الصفقة الحالية {signal_ar(_current_paper.get('side'))} "
                f"والإشارة الجديدة {signal_ar(execution_analysis.get('signal'))} عند {fmt(reference_price, 4)}"
            ),
            icon="⏰",
            payload={
                "symbol": instrument.symbol,
                "type": "REVERSAL_EXIT_REVIEW",
                "current_side": _current_paper.get("side"),
                "new_signal": execution_analysis.get("signal"),
            },
        )

stock_market_closed = bool(
    instrument.asset_class == "STOCK"
    and (
        quote.get("market_open") is False
        or not stock_session_state()["regular"]
    )
)
execution_label = (
    "جاهز"
    if feed.get("execution_ok")
    else ("السوق مغلق" if stock_market_closed else "تحقق مطلوب")
)
execution_state = (
    "ok"
    if feed.get("execution_ok")
    else ("wait" if stock_market_closed else "bad")
)

mini_grid(
    [
        ("السعر", fmt(reference_price, 4), ""),
        ("القرار", signal_ar(execution_analysis["signal"]), "ok" if execution_analysis["signal"] in {"BUY", "SELL"} else "wait"),
        ("القوة", f"{execution_analysis['strength']}%", ""),
        ("بيانات التنفيذ", execution_label, execution_state),
        ("التداول الحقيقي", "مفتوح" if live_unlocked else "مقفل", "ok" if live_unlocked else "wait"),
        ("قفل الأمان", "مفعل" if st.session_state.kill_switch else "غير مفعل", "wait" if st.session_state.kill_switch else "ok"),
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
elif instrument.asset_class == "STOCK" and stock_market_closed:
    st.info("السوق الأمريكي مغلق الآن. التحليل يبقى ظاهرًا، والتنفيذ والماسح الثقيل متوقفان تلقائيًا حتى الجلسة.")
    if advanced_ui:
        for reason in feed.get("execution_reasons", []):
            st.caption("• " + reason)
elif not feed.get("execution_ok", False):
    st.warning("السوق يفترض أنه مفتوح، لكن سعر التنفيذ غير جاهز أو قديم. تم حجب أي دخول حتى تتحدث البيانات.")
    for reason in feed.get("execution_reasons", []):
        st.caption("• " + reason)

# ------------------------- smart stock desk --------------------
if instrument.asset_class == "STOCK":
    stock_session = stock_session_state()
    sh_cfg = sharia_config()
    selected_sharia = stock_sharia_status(instrument.symbol)
    stock_guard_ok, stock_guard_reasons, stock_spread_pct = stock_quote_guard(quote, reference_price)

    st.subheader("📈 مركز الأسهم الذكي")
    mini_grid(
        [
            ("السهم", instrument.symbol, "ok"),
            ("جلسة نيويورك", stock_session["label"], "ok" if stock_session["preferred"] else "wait"),
            ("الاتجاه", (forward_analysis.get("snapshots", {}).get("H1") or {}).get("trend", "—"), ""),
            ("الجاهزية", f"{int(forward_analysis.get('readiness_pct',0))}%", "ok" if forward_analysis.get("signal") == "BUY" else "wait"),
            (
                "الحجم النسبي",
                (
                    f"{float(forward_analysis.get('volume_ratio')):.2f}x"
                    if finite(forward_analysis.get("volume_ratio"))
                    else ("خارج الجلسة" if not stock_session["regular"] else "غير مكتمل")
                ),
                "",
            ),
            ("الفحص الشرعي", selected_sharia, "ok" if selected_sharia == "مُدرج بالقائمة" else "wait"),
        ],
        "status-grid",
    )

    if not sh_cfg["configured"]:
        st.caption("الفحص الشرعي غير مربوط بمصدر محدث حتى الآن؛ التطبيق لا يخمن الحكم.")
        if advanced_ui:
            st.caption(
                "لربط القائمة يدويًا: SHARIA_APPROVED_SYMBOLS + "
                "SHARIA_SCREEN_SOURCE + SHARIA_SCREEN_DATE في Secrets."
            )
    else:
        st.caption(
            f"قائمة الفحص الشرعي من إعداداتك"
            + (f" • المصدر: {sh_cfg['source']}" if sh_cfg['source'] else "")
            + (f" • التاريخ: {sh_cfg['date']}" if sh_cfg['date'] else "")
        )

    if stock_spread_pct is not None:
        st.caption(f"السبريد الحالي: {stock_spread_pct:.3f}%")
    for _reason in stock_guard_reasons:
        st.warning(_reason)

    def _render_stock_scanner() -> None:
        if st.session_state.get("stock_auto_scan", True):
            scan_stock_batch(st.session_state.get("adaptive_profile_choice", "ذكي تلقائي"), STOCK_SCAN_BATCH_SIZE)
        rows = stock_scanner_rows(bool(st.session_state.get("stock_sharia_only", False)))
        st.markdown("### ماسح الفرص")
        if stock_session_state()["regular"]:
            st.caption("يتحدث تلقائيًا أثناء جلسة السوق ويجمع أفضل الفرص بالتناوب.")
        else:
            st.caption("السوق مغلق الآن؛ الماسح محتفظ بنتائج آخر جلسة ولن يستهلك طلبات إضافية.")
        if advanced_ui:
            st.caption("الوضع التقني: 3 أسهم كل 60 ثانية بالتناوب لحماية حد Twelve Data.")
        if rows:
            # Mobile-first opportunity cards. The full diagnostic table is Advanced-only.
            for row in rows[:6]:
                _sig = str(row.get("signal", "WAIT"))
                _sig_cls = "buy" if _sig == "BUY" else "state-wait"
                _fav = "★ " if row.get("favorite") == "★" else ""
                _vol = (
                    f"{float(row.get('volume_ratio')):.2f}x"
                    if finite(row.get("volume_ratio"))
                    else ("خارج الجلسة" if not stock_session_state()["regular"] else "—")
                )
                _price = fmt(row.get("price"), 2)
                _sh = str(row.get("sharia", "غير متحقق"))
                _fresh = str(row.get("freshness", "—"))
                st.markdown(
                    f"<div class='card'>"
                    f"<div class='kicker'>{_fav}{row.get('name','')}</div>"
                    f"<div style='display:flex;justify-content:space-between;align-items:end;gap:12px;'>"
                    f"<div><h2 style='margin:.2rem 0'>{row.get('symbol','')}</h2>"
                    f"<div class='muted'>السعر {_price} • الحجم {_vol}</div></div>"
                    f"<div style='text-align:left'><div class='big {_sig_cls}' style='font-size:1.7rem'>{_sig}</div>"
                    f"<div class='muted'>جاهزية {int(row.get('readiness',0))}% • {row.get('profile','—')}</div></div>"
                    f"</div>"
                    f"<div class='muted' style='margin-top:.65rem'>الفحص الشرعي: {_sh} • البيانات: {_fresh}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

            if advanced_ui:
                view = pd.DataFrame(rows)
                rename = {
                    "favorite": "★", "symbol": "الرمز", "name": "الشركة", "signal": "الإشارة",
                    "readiness": "الجاهزية %", "profile": "النمط", "price": "السعر", "rsi": "RSI",
                    "volume_ratio": "الحجم النسبي", "sharia": "الفحص الشرعي",
                    "freshness": "البيانات", "updated_at": "آخر تحديث",
                }
                cols = [c for c in ["favorite","symbol","name","signal","readiness","profile","price","rsi","volume_ratio","sharia","freshness","updated_at"] if c in view.columns]
                with st.expander("جدول الماسح المتقدم", expanded=False):
                    st.dataframe(view[cols].rename(columns=rename), hide_index=True, use_container_width=True)

            best = rows[0]
            st.success(
                f"أعلى جاهزية حالياً: {best.get('symbol')} • {best.get('readiness',0)}% • "
                f"{best.get('signal','WAIT')} • {best.get('profile','—')}"
            )
            best_label = stock_symbol_to_preset(str(best.get("symbol", "")))
            if best_label and best_label != preset_name:
                if st.button(f"فتح {best.get('symbol')} في المنصة", use_container_width=True, key="open_best_stock"):
                    st.session_state.pending_market_preset = best_label
                    st.rerun()
        else:
            st.info("الماسح يجمع أول دفعة الآن؛ إذا ظهر حد API سيحتفظ بآخر نتائج متاحة.")

        c1, c2 = st.columns(2)
        with c1:
            if st.button(
                "تحديث دفعة الماسح الآن",
                use_container_width=True,
                key="stock_scan_now",
                disabled=not stock_session_state()["regular"],
            ):
                scan_stock_batch(st.session_state.get("adaptive_profile_choice", "ذكي تلقائي"), STOCK_SCAN_BATCH_SIZE)
                st.rerun()
        with c2:
            favorites = list(st.session_state.get("stock_favorites", []))
            is_fav = instrument.symbol in favorites
            if st.button("إزالة من المفضلة" if is_fav else "إضافة للمفضلة", use_container_width=True, key="stock_favorite_toggle"):
                if is_fav:
                    favorites = [x for x in favorites if x != instrument.symbol]
                else:
                    favorites.append(instrument.symbol)
                st.session_state.stock_favorites = sorted(set(favorites))
                persist_paper_state()
                st.rerun()

    if st.session_state.get("stock_auto_scan", True):
        @st.fragment(run_every=STOCK_SCAN_INTERVAL_SECONDS)
        def _stock_scan_fragment() -> None:
            _render_stock_scanner()
        _stock_scan_fragment()
    else:
        _render_stock_scanner()

    perf = stock_performance_summary()
    st.markdown("### أداء Stock Paper")
    mini_grid(
        [
            ("صفقات مغلقة", str(perf["trades"]), ""),
            ("نسبة الربح", f"{perf['win_rate']:.1f}%", "ok" if perf["win_rate"] >= 50 and perf["trades"] else "wait"),
            ("صافي P&L", f"${perf['net']:,.2f}", "ok" if perf["net"] > 0 else "bad" if perf["net"] < 0 else "wait"),
            ("متوسط R", f"{perf['avg_r']:.3f}R", "ok" if perf["avg_r"] > 0 else "wait"),
        ],
        "status-grid",
    )
    st.caption("حالة الاستراتيجية: Paper validation. نجاح الكود لا يعني أن الاستراتيجية رابحة حتى تتكوّن عينة كافية من الصفقات.")

# ------------------------- command center ---------------------
if advanced_ui:
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

near_entry = bool(forward_analysis.get("near_entry", False))
forward_signal_class = (
    "buy" if forward_analysis.get("signal") == "BUY"
    else "sell" if forward_analysis.get("signal") == "SELL"
    else "state-wait"
)
forward_display = (
    forward_analysis.get("signal")
    if forward_analysis.get("signal") in {"BUY", "SELL"}
    else ("قريب من الدخول" if near_entry else "WAIT")
)

candidate_display = (
    active_candidate
    if advanced_ui
    else (
        f"النمط {adaptive_profile}"
        if instrument.asset_class in {"STOCK", "GOLD"} and active_candidate in SMART_PAPER_CANDIDATES
        else "Paper"
    )
)
event_display = (
    str(forward_analysis.get("event", "NONE"))
    if advanced_ui
    else ("جاهز" if forward_analysis.get("event_ok") else "بانتظار حدث الدخول")
)

st.markdown(
    f"<div class='card'><div class='kicker'>قرار Paper</div>"
    f"<div class='big {forward_signal_class}'>{forward_display}</div>"
    f"<p>{forward_analysis.get('reason','')}</p>"
    f"<div class='muted'>{candidate_display} • "
    f"الجاهزية {int(forward_analysis.get('readiness_pct',0))}% • "
    f"{event_display}</div></div>",
    unsafe_allow_html=True,
)

forward_gate_cards = [
    (
        "قوة الاتجاه",
        "مكتمل" if forward_analysis.get("trend25") else "غير مكتمل",
        "ok" if forward_analysis.get("trend25") else "wait",
    ),
    (
        "نظام التذبذب",
        "مكتمل" if forward_analysis.get("regime_ok") else "غير مكتمل",
        "ok" if forward_analysis.get("regime_ok") else "wait",
    ),
    (
        "وقت التداول",
        "مكتمل" if forward_analysis.get("session_ok") else "خارج الجلسة",
        "ok" if forward_analysis.get("session_ok") else "wait",
    ),
    (
        "حدث الدخول",
        "مكتمل" if forward_analysis.get("event_ok") else "ننتظر",
        "ok" if forward_analysis.get("event_ok") else "wait",
    ),
]
if instrument.asset_class == "STOCK":
    forward_gate_cards.append(
        (
            "حجم التداول",
            "مكتمل" if forward_analysis.get("volume_ok") else "ضعيف",
            "ok" if forward_analysis.get("volume_ok") else "wait",
        )
    )
mini_grid(forward_gate_cards, "tf-grid")

if near_entry:
    st.warning(
        "🟡 قريب من الدخول: الاتجاه + التذبذب + وقت التداول مكتملة. "
        "ننتظر حدث الدخول فقط. لن يفتح Paper قبل اكتماله."
    )
elif forward_analysis.get("signal") in {"BUY", "SELL"}:
    _side = forward_analysis.get("signal")
    st.success(
        f"🟢 اكتملت شروط {_side}. إذا التداول التجريبي التلقائي مفعّل وبوابة المخاطر تسمح، "
        "سيتم فتح صفقة Paper تلقائيًا."
    )

if mode == "Paper":
    if instrument.asset_class == "STOCK":
        st.info(
            "Stock Paper جاهز على السهم المختار بأموال افتراضية. "
            "الدخول الذكي للأسهم BUY فقط، والمخاطرة تُضبط تلقائيًا."
        )
    else:
        st.info(
            "Paper Forward جاهز للتجربة على السوق الحالي بدون أموال حقيقية. "
            "مركز واحد فقط مع إدارة مخاطر تلقائية."
        )

if near_entry:
    near_key = f"{instrument.symbol}:{candle_id}:{active_candidate}:NEAR"
    if st.session_state.get("last_near_entry_alert") != near_key:
        st.session_state.last_near_entry_alert = near_key
        st.toast("قريب من الدخول: باقي حدث الدخول فقط", icon="🟡")

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
diag_analysis = forward_analysis if instrument.asset_class == "STOCK" else analysis
m5_diag = diag_analysis.get("snapshots", {}).get("M5") or {}
trends_diag = [
    (diag_analysis.get("snapshots", {}).get(tf) or {}).get("trend")
    for tf in TIMEFRAMES
]
up_count = sum(t == "UP" for t in trends_diag)
down_count = sum(t == "DOWN" for t in trends_diag)
b2_diag = diag_analysis.get("b2") or {}

diag_rows = [
    ("اتجاه الأطر", f"UP {up_count}/4 • DOWN {down_count}/4", up_count >= 3 or down_count >= 3),
    ("RSI M5", f"{m5_diag.get('rsi', 0):.1f}" if m5_diag else "—", bool(m5_diag) and (m5_diag.get("rsi", 50) >= 52 or m5_diag.get("rsi", 50) <= 48)),
    ("زخم MACD", "متوافق" if m5_diag and ((m5_diag.get("momentum",0)>0 and m5_diag.get("macd_hist",0)>0) or (m5_diag.get("momentum",0)<0 and m5_diag.get("macd_hist",0)<0)) else "غير مكتمل", bool(m5_diag) and ((m5_diag.get("momentum",0)>0 and m5_diag.get("macd_hist",0)>0) or (m5_diag.get("momentum",0)<0 and m5_diag.get("macd_hist",0)<0))),
    ("ADX M5", f"{m5_diag.get('adx', 0):.1f}" if m5_diag else "—", bool(m5_diag) and m5_diag.get("adx",0) >= 20),
    ("B2 Break/Retest", b2_diag.get("side") or ("Breakout فقط" if b2_diag.get("breakout") else "بانتظار التأكيد"), bool(b2_diag.get("valid"))),
    ("بنية البيانات", "سليمة" if feed.get("trusted") else "تحقق مطلوب", bool(feed.get("trusted"))),
    ("جاهزية Paper", "جاهز" if feed.get("execution_ok") else "محجوب", bool(feed.get("execution_ok"))),
]
if instrument.asset_class == "STOCK":
    diag_rows.extend(
        [
            (
                "حجم التداول",
                f"{float(forward_analysis.get('volume_ratio')):.2f}x" if finite(forward_analysis.get("volume_ratio")) else "غير متاح",
                bool(forward_analysis.get("volume_ok", True)),
            ),
            (
                "عدم مطاردة السعر",
                f"{float(forward_analysis.get('extension_atr',0.0)):.2f} ATR" if finite(forward_analysis.get("extension_atr")) else "—",
                bool(forward_analysis.get("not_extended", False)),
            ),
        ]
    )

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
m5_snap = forward_analysis.get("snapshots", {}).get("M5")
if forward_analysis["signal"] in {"BUY", "SELL"} and m5_snap:
    try:
        paper_entry_reference = reference_price
        if instrument.asset_class == "STOCK" and forward_analysis["signal"] == "BUY":
            paper_entry_reference = (
                float(quote["ask"])
                if finite(quote.get("ask"))
                else float(reference_price) * (1.0 + 0.0002)
            )
        paper_plan = build_trade_plan(
            forward_analysis["signal"],
            paper_entry_reference,
            float(m5_snap["atr"]),
            float(st.session_state.paper_balance),
            float(paper_risk_pct),
            instrument,
            stop_atr=1.5 if instrument.asset_class == "STOCK" else 1.6,
            tp1_r=1.0,
            tp2_r=2.0 if instrument.asset_class == "STOCK" else 2.2,
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

# ------------------------- compact status ----------------------
mini_grid(
    [
        (
            "السوق",
            "مفتوح" if feed.get("market_open") else "مغلق" if feed.get("market_open") is not None else "غير معروف",
            "ok" if feed.get("market_open") else "wait",
        ),
        (
            "بيانات التنفيذ",
            "جاهزة" if feed.get("execution_ok") else "محجوبة",
            "ok" if feed.get("execution_ok") else "bad",
        ),
        ("الحفظ", "مفعّل", "ok"),
        (
            "التلقائي",
            "مفعّل" if st.session_state.auto_paper else "متوقف",
            "ok" if st.session_state.auto_paper else "wait",
        ),
        (
            "نمط الدخول",
            (
                adaptive_profile
                if active_candidate in SMART_PAPER_CANDIDATES
                else "محافظ"
            ),
            "wait" if active_candidate in SMART_PAPER_CANDIDATES else "ok",
        ),
    ],
    "status-grid",
)

# --------------------------- Paper ----------------------------
if mode == "Paper":
    st.subheader("التداول التجريبي — السوق الحي")
    if active_candidate in SMART_PAPER_CANDIDATES:
        if selected_profile == "ذكي تلقائي":
            engine_name = "الأسهم" if instrument.asset_class == "STOCK" else "الذهب"
            st.info(
                f"🧠 طيار {engine_name} الذكي اختار الآن: {adaptive_profile} • "
                f"المخاطرة {float(paper_risk_pct):.3f}% • "
                f"{smart_autopilot_meta.get('reason','')}. "
                "يفحص صارم/متوازن/مرن في كل دورة ولا يضاعف المخاطرة بعد الخسارة."
            )
        else:
            st.info(
                f"🤖 النمط التكيفي: {adaptive_profile}. "
                f"النظام يحدد ADX والتذبذب تلقائيًا، والمخاطرة الحالية "
                f"{float(paper_risk_pct):.3f}% بدون مضاعفات أو تعزيز بعد الخسارة."
            )
    day_pnl = float(st.session_state.paper_balance) - float(st.session_state.paper_day_start_balance)
    p_open = 1 if st.session_state.paper_position else 0
    p_gate_ok = False
    p_gate_reasons: list[str] = []

    recent_closed = list(st.session_state.get("paper_history", []))[:5]
    recent_loss_count = sum(1 for x in recent_closed if float(x.get("PnL", 0.0)) < 0)
    recent_loss_streak = 0
    for x in recent_closed:
        if float(x.get("PnL", 0.0)) < 0:
            recent_loss_streak += 1
        else:
            break

    profile_loss_limit = 2 if adaptive_profile == "مرن" else 3
    adaptive_circuit_block = bool(
        active_candidate in SMART_PAPER_CANDIDATES
        and recent_loss_streak >= profile_loss_limit
    )

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
        if st.session_state.get("_persistence_error"):
            p_gate_ok = False
            p_gate_reasons.append("حفظ المحفظة غير متاح")
        if not feed.get("execution_ok", False):
            p_gate_ok = False
            p_gate_reasons.append(
                "Execution Feed غير جاهز؛ لا يتم فتح Paper على سعر قديم/غير قابل للتحقق"
            )

        # Stock execution-quality brakes before the generic adaptive brakes.
        if instrument.asset_class == "STOCK":
            _sq_ok, _sq_reasons, _ = stock_quote_guard(quote, reference_price)
            if not _sq_ok:
                p_gate_ok = False
                p_gate_reasons.extend(_sq_reasons)
            if not stock_session_state()["preferred"]:
                p_gate_ok = False
                p_gate_reasons.append("الأسهم: فتح مراكز جديدة خارج نافذة 09:45–15:50 نيويورك محجوب")
            if stock_recent_loss(instrument.symbol):
                p_gate_ok = False
                p_gate_reasons.append(f"الأسهم: تبريد {STOCK_LOSS_COOLDOWN_MINUTES} دقيقة بعد خسارة حديثة على نفس السهم")
            if stock_trades_today() >= STOCK_MAX_TRADES_PER_DAY:
                p_gate_ok = False
                p_gate_reasons.append(f"الأسهم: تم بلوغ حد {STOCK_MAX_TRADES_PER_DAY} صفقات يومية")
            if stock_trades_today(instrument.symbol) >= STOCK_MAX_TRADES_PER_SYMBOL_DAY:
                p_gate_ok = False
                p_gate_reasons.append(f"الأسهم: تم بلوغ حد {STOCK_MAX_TRADES_PER_SYMBOL_DAY} صفقات يومية لهذا السهم")
            if st.session_state.get("stock_sharia_only", False) and stock_sharia_status(instrument.symbol) != "مُدرج بالقائمة":
                p_gate_ok = False
                p_gate_reasons.append("فلتر القائمة الشرعية مفعّل وهذا الرمز غير موجود في القائمة الموثقة بإعداداتك")

        # Adaptive safety brakes.
        if active_candidate in SMART_PAPER_CANDIDATES:
            daily_stop_pct = {
                "صارم": 0.35 if instrument.asset_class == "STOCK" else 0.40,
                "متوازن": 0.30 if instrument.asset_class == "STOCK" else 0.35,
                "مرن": 0.20 if instrument.asset_class == "STOCK" else 0.25,
            }.get(adaptive_profile, 0.30 if instrument.asset_class == "STOCK" else 0.35)
            if day_pnl <= -(
                float(st.session_state.paper_day_start_balance)
                * daily_stop_pct
                / 100.0
            ):
                p_gate_ok = False
                p_gate_reasons.append(
                    f"توقف تكيفي: وصلت خسارة اليوم إلى {daily_stop_pct:.2f}%"
                )
            if adaptive_circuit_block:
                p_gate_ok = False
                p_gate_reasons.append(
                    f"توقف تكيفي: {profile_loss_limit} خسائر متتالية — يحتاج مراجعة"
                )

    gate_label = "انتظار إشارة" if not paper_plan else ("جاهز" if p_gate_ok else "محجوب")
    gate_state = "wait" if not paper_plan else ("ok" if p_gate_ok else "bad")
    mini_grid(
        [
            ("الرصيد التجريبي", f"${st.session_state.paper_balance:,.2f}", ""),
            ("ربح/خسارة اليوم", f"${day_pnl:,.2f}", "ok" if day_pnl >= 0 else "bad"),
            ("صفقات اليوم", str(st.session_state.paper_trades_today), ""),
            ("صفقة مفتوحة", "نعم" if st.session_state.paper_position else "لا", "wait" if st.session_state.paper_position else ""),
            ("بوابة المخاطر", gate_label, gate_state),
        ],
        "paper-grid",
    )

    if advanced_ui:
        st.caption(
            f"Candidate: {active_candidate} • Risk {float(paper_risk_pct):.3f}% • "
            "Paper فقط؛ لا يتم إرسال أي أمر حقيقي. "
            "الرصيد، المركز المفتوح، السجل ومفاتيح منع التكرار تُحفظ تلقائيًا."
        )
    else:
        st.caption(
            f"المخاطرة الحالية {float(paper_risk_pct):.3f}% • Paper فقط • "
            "الحفظ ومنع تكرار الأوامر مفعّلان تلقائيًا."
        )


    if active_candidate in SMART_PAPER_CANDIDATES and advanced_ui:
        mini_grid(
            [
                ("النمط", adaptive_profile, ""),
                ("H1 ADX تلقائي", f"{float(forward_analysis.get('adaptive_h1_adx',0.0)):.1f}", ""),
                ("H4 ADX تلقائي", f"{float(forward_analysis.get('adaptive_h4_adx',0.0)):.1f}", ""),
                ("مخاطرة تلقائية", f"{float(paper_risk_pct):.3f}%", "ok"),
                ("خسائر آخر 5", str(recent_loss_count), "wait" if recent_loss_count else "ok"),
            ],
            "tf-grid",
        )

        if selected_profile == "ذكي تلقائي":
            auto_eval = smart_autopilot_meta.get("evaluations", {})
            mini_grid(
                [
                    (
                        "صارم",
                        f"{int((auto_eval.get('صارم') or {}).get('readiness',0))}% • {(auto_eval.get('صارم') or {}).get('signal','WAIT')}",
                        "ok" if (auto_eval.get("صارم") or {}).get("signal") in {"BUY", "SELL"} else "wait",
                    ),
                    (
                        "متوازن",
                        f"{int((auto_eval.get('متوازن') or {}).get('readiness',0))}% • {(auto_eval.get('متوازن') or {}).get('signal','WAIT')}",
                        "ok" if (auto_eval.get("متوازن") or {}).get("signal") in {"BUY", "SELL"} else "wait",
                    ),
                    (
                        "مرن",
                        f"{int((auto_eval.get('مرن') or {}).get('readiness',0))}% • {(auto_eval.get('مرن') or {}).get('signal','WAIT')}",
                        "ok" if (auto_eval.get("مرن") or {}).get("signal") in {"BUY", "SELL"} else "wait",
                    ),
                ],
                "tf-grid",
            )

    if advanced_ui:
        mini_grid(
            [
                ("حفظ Paper", "متوقف" if st.session_state.get("_persistence_error") else "مفعّل", "bad" if st.session_state.get("_persistence_error") else "ok"),
                ("منع تكرار الأوامر", "مفعّل", "ok"),
                ("DB", "SQLite", ""),
                ("سجل الصفقات", str(len(st.session_state.paper_history)), ""),
            ],
            "tf-grid",
        )

    st.caption(
        "التداول التجريبي التلقائي: "
        + ("مفعّل" if st.session_state.auto_paper else "متوقف")
        + " — التحكم من القائمة الجانبية."
    )

    if paper_plan and not st.session_state.paper_position:
        if st.button(
            "فتح صفقة Paper الآن",
            type="primary",
            use_container_width=True,
            disabled=not p_gate_ok,
        ):
            manual_key = (
                f"MANUAL:{instrument.symbol}:{active_candidate}:"
                f"{now_riyadh().isoformat()}:{uuid.uuid4().hex[:8]}"
            )
            open_paper(paper_plan, instrument, order_key=manual_key, candidate=active_candidate, profile=adaptive_profile)
            st.rerun()

    if (
        st.session_state.auto_paper
        and paper_plan
        and p_gate_ok
        and not st.session_state.paper_position
    ):
        if st.session_state.last_auto_paper_candle.get(instrument.symbol) != candle_id:
            auto_key = (
                f"AUTO:{instrument.symbol}:{active_candidate}:{candle_id}"
            )
            if open_paper(paper_plan, instrument, order_key=auto_key, candidate=active_candidate, profile=adaptive_profile):
                st.session_state.last_auto_paper_candle[instrument.symbol] = candle_id
                persist_paper_state()
                st.rerun()

    for reason in dict.fromkeys(p_gate_reasons):
        st.warning(reason)

    p = st.session_state.paper_position
    if p:
        _display_quote = quote if str(p.get("symbol")) == instrument.symbol else fetch_quote(str(p.get("symbol")))
        _display_ready = feed.get("execution_ok", False) if str(p.get("symbol")) == instrument.symbol else quote_execution_ready(_display_quote)
        if _display_ready:
            _fallback = reference_price if str(p.get("symbol")) == instrument.symbol else float(_display_quote.get("last", p.get("entry", 0.0)))
            mark = paper_mark_price(p, _display_quote, _fallback)
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
            st.warning(f"المركز Paper على {p.get('symbol')} مفتوح لكن Quote صالح للتنفيذ غير متاح الآن.")

    if advanced_ui:
        with st.expander("حالة الحفظ", expanded=False):
            st.write(f"مسار قاعدة البيانات: `{state_db()['path']}`")
            st.write(
                "الحفظ يستمر مع Refresh وRerun. إذا أعاد مزود الاستضافة بناء الحاوية بالكامل، "
                "فقد تحتاج لاحقًا قاعدة بيانات خارجية دائمة."
            )

    if st.session_state.paper_history:
        paper_df = pd.DataFrame(st.session_state.paper_history)
        if instrument.asset_class == "STOCK" and "asset_class" in paper_df.columns:
            stock_hist = paper_df[paper_df["asset_class"] == "STOCK"].copy()
            st.markdown("### سجل Stock Paper")
            st.dataframe(stock_hist, hide_index=True, use_container_width=True)
        else:
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
    if instrument.asset_class == "STOCK":
        st.error("Live للأسهم مقفول حاليًا: محرك الأسهم في مرحلة Paper validation ولم يتم ربط تنفيذ وسيط موثق له بعد.")
        st.stop()
    if active_candidate in SMART_PAPER_CANDIDATES:
        st.error("أنماط Smart Paper للذهب والأسهم مخصصة للتجربة ولا يمكن استخدامها في Live.")
        st.stop()
    st.caption(
        "في Live: Twelve Data للتحليل فقط. سعر الدخول وحالة الحساب والمراكز يجب أن تأتي من Broker Bridge."
    )


    fresh = st.session_state.get("fresh_holdout") or {}
    fresh_gate_preview = fresh.get("gate") or {}
    paper_closed_preview = list(st.session_state.get("paper_history", []))
    paper_net_preview = sum(float(x.get("PnL", 0.0)) for x in paper_closed_preview)
    mini_grid(
        [
            ("Candidate", active_candidate, ""),
            ("Fresh Holdout", "PASS" if fresh_gate_preview.get("passed") else "NOT PASSED", "ok" if fresh_gate_preview.get("passed") else "bad"),
            ("Paper Trades", str(len(paper_closed_preview)), "ok" if len(paper_closed_preview) >= 20 else "wait"),
            ("Paper Net", f"${paper_net_preview:,.2f}", "ok" if paper_net_preview > 0 else "wait"),
        ],
        "tf-grid",
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
            forward_analysis["signal"] in {"BUY", "SELL"}
            and m5_snap
            and broker_quote.get("ok")
            and equity > 0
        ):
            try:
                live_plan = build_trade_plan(
                    forward_analysis["signal"],
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

        # v4.8 Live qualification chain:
        # 1) the exact selected candidate must pass a fresh untouched holdout;
        # 2) forward paper sample must be large enough and positive;
        # 3) all broker/risk controls below must also pass.
        fresh = st.session_state.get("fresh_holdout") or {}
        fresh_gate = fresh.get("gate") or {}
        fresh_candidate = fresh.get("candidate")

        if not fresh_gate.get("passed", False):
            live_gate_ok = False
            live_reasons.append("Fresh Holdout للمرشح لم يجتز بعد")
        elif fresh_candidate != forward_candidate:
            live_gate_ok = False
            live_reasons.append("مرشح Live الحالي لا يطابق المرشح الذي اجتاز Fresh Holdout")

        closed_forward = list(st.session_state.get("paper_history", []))
        forward_trade_count = len(closed_forward)
        forward_net = sum(float(x.get("PnL", 0.0)) for x in closed_forward)

        if forward_trade_count < 20:
            live_gate_ok = False
            live_reasons.append(
                f"Paper Forward يحتاج 20 صفقة مغلقة على الأقل — الحالي {forward_trade_count}"
            )
        if forward_trade_count >= 20 and forward_net <= 0:
            live_gate_ok = False
            live_reasons.append(
                f"Paper Forward Net يجب أن يكون موجبًا — الحالي ${forward_net:,.2f}"
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
                forward_analysis,
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
                    forward_analysis,
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

# -------------------------- advanced research ------------------
if advanced_ui and instrument.asset_class == "GOLD":
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



    # ------------------- v4.6 walk-forward research lab -------------------
    st.divider()
    st.subheader("Walk-Forward Research Lab — v4.8")
    st.caption(
        "v4.7 يثبت Regime القوي من v4.6 (trend strength + prior-only volatility) لكنه يغيّر "
        "منطق الدخول نفسه إلى أحداث EMA rejection / pullback break / fresh breakout. "
        "الهدف زيادة العينة والثبات عبر 6 نوافذ بدون تخفيف Gate أو لمس Fresh Holdout."
    )

    dev_bt = st.session_state.get("backtest")
    old_iv = st.session_state.get("independent_validation")

    # v4.6.1: Walk-Forward no longer depends on browser/session_state.
    # If the prior Research Audit / Independent Validation objects are still in
    # the current Streamlit session, reuse their exact boundaries. Otherwise,
    # reconstruct the same research design directly from time and fetch history
    # again. This makes the lab usable after refresh/new browser session.
    if dev_bt and old_iv:
        dev_meta = dev_bt.get("history_meta", {})
        old_meta = old_iv.get("history_meta", {})

        dev_first = dev_meta.get("first_bar")
        dev_last = dev_meta.get("last_bar")
        old_first = old_meta.get("first_bar")
        old_last = old_meta.get("last_bar")
    else:
        anchor_end = now_utc().floor("5min")
        dev_last = anchor_end.isoformat()
        dev_first = (anchor_end - pd.Timedelta(days=180)).isoformat()
        old_last = dev_first
        old_first = (anchor_end - pd.Timedelta(days=360)).isoformat()
        st.info(
            "Session Recovery: نتائج الجلسة السابقة غير موجودة، "
            "لكن Walk-Forward سيعيد بناء نافذة البحث 360 يوم مباشرةً بدون إعادة "
            "Research Audit وIndependent Validation يدويًا."
        )

    if not all([dev_first, dev_last, old_first, old_last]):
        st.warning("تعذر تحديد حدود نافذة Walk-Forward.")
    else:
        research_start = pd.Timestamp(old_first)
        research_end = pd.Timestamp(dev_last)
        fresh_end = research_start
        fresh_start = fresh_end - pd.Timedelta(days=180)

        mini_grid(
            [
                ("Research Start", research_start.strftime("%Y-%m-%d"), ""),
                ("Research End", research_end.strftime("%Y-%m-%d"), ""),
                ("WF Folds", "6", "ok"),
                ("Fresh Holdout", fresh_start.strftime("%Y-%m-%d"), "wait"),
            ],
            "tf-grid",
        )

        st.caption(
            "Research history = النافذة المستقلة التي فشلت + نافذة التطوير الحالية. "
            f"Fresh holdout المحجوز: {fresh_start.isoformat()} → {fresh_end.isoformat()}."
        )

        if st.button(
            "تشغيل Walk-Forward Lab على 360 يوم",
            use_container_width=True,
        ):
            wf_t0 = time.perf_counter()

            with st.status(
                "تشغيل Walk-Forward Research Lab...",
                expanded=True,
            ) as wf_status:
                st.write("1/4 • جلب نافذة البحث الأقدم (المستخدمة سابقًا)")
                try:
                    older_raw, older_meta = fetch_history_window(
                        instrument.symbol,
                        research_start.isoformat(),
                        pd.Timestamp(dev_first).isoformat(),
                        chunk_days=17,
                    )
                except Exception as exc:
                    st.error(f"تعذر جلب الجزء الأقدم: {exc}")
                    older_raw = pd.DataFrame()
                    older_meta = {}

                st.write("2/4 • جلب نافذة التطوير الحالية")
                try:
                    dev_raw, dev_hist_meta = fetch_history_window(
                        instrument.symbol,
                        pd.Timestamp(dev_first).isoformat(),
                        research_end.isoformat(),
                        chunk_days=17,
                    )
                except Exception as exc:
                    st.error(f"تعذر جلب جزء التطوير: {exc}")
                    dev_raw = pd.DataFrame()
                    dev_hist_meta = {}

                if not older_raw.empty and not dev_raw.empty:
                    research_raw = (
                        pd.concat([older_raw, dev_raw], ignore_index=True)
                        .drop_duplicates("datetime")
                        .sort_values("datetime")
                        .reset_index(drop=True)
                    )

                    st.write(
                        f"3/4 • تجهيز {len(research_raw):,} شمعة مرة واحدة"
                    )
                    wf_prepared = prepare_research_context(research_raw)

                    if not wf_prepared.get("ok", False):
                        st.error(
                            wf_prepared.get(
                                "warning",
                                "تعذر تجهيز Walk-Forward",
                            )
                        )
                    else:
                        st.write("4/4 • تشغيل 5 Entry Models × 6 نوافذ + 2/5 bps")
                        wf_result = run_walkforward_lab(
                            wf_prepared,
                            instrument,
                            risk_pct=float(risk_pct),
                            n_folds=6,
                        )

                        wf_result["elapsed_seconds"] = float(
                            time.perf_counter() - wf_t0
                        )
                        wf_result["research_bars"] = int(len(research_raw))
                        wf_result["fresh_start"] = fresh_start.isoformat()
                        wf_result["fresh_end"] = fresh_end.isoformat()

                        st.session_state.walkforward_lab = wf_result
                        st.session_state.fresh_holdout = None

                        wf_status.update(
                            label=(
                                "Walk-Forward Lab اكتمل خلال "
                                f"{time.perf_counter()-wf_t0:.1f} ثانية"
                            ),
                            state="complete",
                            expanded=False,
                        )

        wf = st.session_state.get("walkforward_lab")
        if wf and wf.get("ok"):
            st.markdown("### Walk-Forward Candidate Summary")
            summary_df = wf.get("summary", pd.DataFrame())
            st.dataframe(
                summary_df,
                hide_index=True,
                use_container_width=True,
            )

            if not summary_df.empty:
                st.markdown("### Mobile Candidate Audit")
                for _, sr in summary_df.iterrows():
                    variant_name = str(sr.get("Variant", ""))
                    d = (wf.get("details", {}) or {}).get(variant_name, {})
                    gate = d.get("gate", {})
                    mini_grid(
                        [
                            ("Candidate", variant_name, ""),
                            ("Trades", str(int(sr.get("Trades", 0))), ""),
                            ("2bps PF", f"{float(sr.get('2bps PF',0.0)):.3f}", "ok" if float(sr.get("2bps PF",0.0)) >= 1.2 else "bad"),
                            ("2bps Avg R", f"{float(sr.get('2bps Avg R',0.0)):.3f}R", "ok" if float(sr.get("2bps Avg R",0.0)) >= 0.05 else "bad"),
                            ("5bps PF", f"{float(sr.get('5bps PF',0.0)):.3f}", "ok" if float(sr.get("5bps PF",0.0)) >= 1.05 else "bad"),
                            ("Positive Folds", str(sr.get("Positive Folds","")), ""),
                            ("Min Fold Trades", str(int(sr.get("Min Fold Trades",0))), ""),
                            ("Gate", "PASS" if gate.get("eligible") else "FAIL", "ok" if gate.get("eligible") else "bad"),
                        ],
                        "plan-grid",
                    )
                    failed = gate.get("failed_rules", [])
                    if failed:
                        st.caption("Gate fails: " + " • ".join(failed))
                    else:
                        st.caption("Gate fails: none")
                    st.divider()

            st.markdown("### Fold-by-Fold Matrix")
            folds_df = wf.get("folds", pd.DataFrame())
            if not folds_df.empty:
                fold_variant = st.selectbox(
                    "اعرض Folds لمرشح واحد",
                    list(WF_CANDIDATES.keys()),
                    key="wf_fold_variant_view",
                )
                st.dataframe(
                    folds_df[folds_df["Variant"] == fold_variant],
                    hide_index=True,
                    use_container_width=True,
                )

            with st.expander("عرض كل Fold-by-Fold Matrix", expanded=False):
                st.dataframe(
                    folds_df,
                    hide_index=True,
                    use_container_width=True,
                )

            eligible = list(wf.get("eligible", []))
            mini_grid(
                [
                    ("Research Bars", f"{int(wf.get('research_bars',0)):,}", ""),
                    ("Elapsed", f"{float(wf.get('elapsed_seconds',0.0)):.1f}s", ""),
                    ("Eligible Candidates", str(len(eligible)), "ok" if eligible else "bad"),
                    ("Fresh Holdout Used?", "NO", "ok"),
                ],
                "tf-grid",
            )

            if not eligible:
                st.error(
                    "لا يوجد مرشح اجتاز Walk-Forward Gate. Fresh Holdout يبقى غير مستخدم. "
                    "لا نخفف Gate ولا نعدّل Fresh Holdout لإجبار PASS؛ نغيّر منطق الدخول فقط."
                )
            else:
                st.success(
                    "يوجد مرشح/مرشحون اجتازوا Research Gate. "
                    "يمكن الآن تجميد واحد فقط ثم فتح Fresh Holdout الأقدم لأول مرة."
                )

                chosen = st.selectbox(
                    "اختر مرشحًا مؤهلًا لتجميده قبل Fresh Holdout",
                    eligible,
                    format_func=lambda x: f"{x} — {WF_CANDIDATES.get(x, x)}",
                    key="wf_candidate_choice",
                )

                st.caption(
                    f"سيتم اختبار {chosen} على نافذة لم نستخدمها حتى الآن: "
                    f"{fresh_start.isoformat()} → {fresh_end.isoformat()}."
                )

                if st.button(
                    "تجميد المرشح وتشغيل Fresh Holdout 180 يوم",
                    use_container_width=True,
                ):
                    fh_t0 = time.perf_counter()

                    with st.status(
                        "تشغيل Fresh Holdout...",
                        expanded=True,
                    ) as fh_status:
                        try:
                            fresh_raw, fresh_meta = fetch_history_window(
                                instrument.symbol,
                                fresh_start.isoformat(),
                                fresh_end.isoformat(),
                                chunk_days=17,
                            )
                        except Exception as exc:
                            st.error(f"تعذر جلب Fresh Holdout: {exc}")
                            fresh_raw = pd.DataFrame()
                            fresh_meta = {}

                        if not fresh_raw.empty:
                            fresh_prepared = prepare_research_context(fresh_raw)

                            if not fresh_prepared.get("ok", False):
                                st.error(
                                    fresh_prepared.get(
                                        "warning",
                                        "تعذر تجهيز Fresh Holdout",
                                    )
                                )
                            else:
                                fh_trades2, fh_stats2 = simulate_prepared_research(
                                    fresh_prepared,
                                    instrument,
                                    risk_pct=float(risk_pct),
                                    cost_bps_roundtrip=2.0,
                                    research_variant=chosen,
                                )
                                _, fh_stats5 = simulate_prepared_research(
                                    fresh_prepared,
                                    instrument,
                                    risk_pct=float(risk_pct),
                                    cost_bps_roundtrip=5.0,
                                    research_variant=chosen,
                                )
                                _, fh_stats10 = simulate_prepared_research(
                                    fresh_prepared,
                                    instrument,
                                    risk_pct=float(risk_pct),
                                    cost_bps_roundtrip=10.0,
                                    research_variant=chosen,
                                )

                                fh_robust = _research_robustness(fh_trades2)
                                fh_gate = _fresh_holdout_gate(
                                    fh_stats2,
                                    fh_stats5,
                                    fh_robust,
                                    fresh_meta,
                                )

                                st.session_state.fresh_holdout = {
                                    "candidate": chosen,
                                    "history_meta": fresh_meta,
                                    "stats_2": fh_stats2,
                                    "stats_5": fh_stats5,
                                    "stats_10": fh_stats10,
                                    "trades_2": fh_trades2,
                                    "robustness": fh_robust,
                                    "gate": fh_gate,
                                    "elapsed_seconds": float(
                                        time.perf_counter() - fh_t0
                                    ),
                                }

                                fh_status.update(
                                    label=(
                                        "Fresh Holdout اكتمل خلال "
                                        f"{time.perf_counter()-fh_t0:.1f} ثانية"
                                    ),
                                    state="complete",
                                    expanded=False,
                                )

        fh = st.session_state.get("fresh_holdout")
        if fh:
            st.markdown("### Fresh Holdout Result")
            fh2 = fh.get("stats_2", {})
            fh5 = fh.get("stats_5", {})
            fh10 = fh.get("stats_10", {})
            n2 = fh2.get("normalized", {})
            n5 = fh5.get("normalized", {})
            n10 = fh10.get("normalized", {})
            fg = fh.get("gate", {})
            fr = fh.get("robustness", {})

            mini_grid(
                [
                    ("Candidate", str(fh.get("candidate")), ""),
                    ("Trades", str(fh2.get("trades", 0)), ""),
                    ("2bps PF", f"{float(n2.get('profit_factor',0.0)):.2f}", "ok" if float(n2.get("profit_factor",0.0)) >= 1.2 else "bad"),
                    ("2bps Avg R", f"{float(n2.get('avg_r_net',0.0)):.3f}R", "ok" if float(n2.get("avg_r_net",0.0)) > 0 else "bad"),
                    ("2bps Net", f"${float(n2.get('net_pnl',0.0)):,.2f}", "ok" if float(n2.get("net_pnl",0.0)) > 0 else "bad"),
                    ("5bps PF", f"{float(n5.get('profit_factor',0.0)):.2f}", "ok" if float(n5.get("profit_factor",0.0)) >= 1.05 else "bad"),
                    ("10bps PF", f"{float(n10.get('profit_factor',0.0)):.2f}", "wait"),
                    ("DD", f"{float(n2.get('max_dd_pct',0.0)):.2f}%", "wait"),
                ],
                "plan-grid",
            )

            st.markdown("### Fresh Holdout Gate")
            mini_grid(
                [
                    (
                        "Fresh Gate",
                        "PASS → PAPER FORWARD" if fg.get("passed") else "FAIL / RESEARCH AGAIN",
                        "ok" if fg.get("passed") else "bad",
                    )
                ],
                "tf-grid",
            )

            for rule in fg.get("rules", []):
                st.write(
                    f"{'✅' if rule.get('pass') else '❌'} {rule.get('name')}"
                )

            st.markdown("### Fresh Robustness")
            mini_grid(
                [
                    ("Bootstrap Low", f"{float(fr.get('bootstrap_low',0.0)):.3f}R", "ok" if float(fr.get("bootstrap_low",0.0)) > 0 else "bad"),
                    ("Bootstrap High", f"{float(fr.get('bootstrap_high',0.0)):.3f}R", ""),
                    ("Break-even", f"{float(fr.get('cost_break_even_bps',0.0)):.2f} bps RT", "wait"),
                ],
                "tf-grid",
            )

            qdf = pd.DataFrame(fr.get("quarters", []))
            if not qdf.empty:
                st.dataframe(qdf, hide_index=True, use_container_width=True)

            if fg.get("passed"):
                st.success(
                    "Fresh holdout نجح. الخطوة التالية Paper Forward فقط لمدة كافية؛ "
                    "لا يتم فتح Live تلقائيًا."
                )
            else:
                st.error(
                    "Fresh holdout فشل. لا نعدّل المرشح باستخدام هذه النافذة؛ "
                    "نرجع للبحث ونحجز نافذة أقدم جديدة لأي نسخة لاحقة."
                )



# -------------------------- advanced health --------------------
if advanced_ui:
    st.subheader("System Health")

    quote_age_label = (
        "N/A"
        if feed.get("quote_age_sec") is None
        else f"{feed['quote_age_sec']:.3f}s"
    )
    quote_latency_label = (
        "N/A"
        if not finite(quote.get("request_latency_ms"))
        else f"{float(quote['request_latency_ms']):.1f}ms"
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
        {"Component": "Paper quote API", "Status": "ONLINE" if quote.get("connected") else ("RATE GUARD" if quote.get("rate_guard") else "CHECK")},
        {"Component": "Quote API RTT", "Status": quote_latency_label},
        {"Component": "Twelve Data budget", "Status": "PROTECTED (≤7/min)"},
        {"Component": "Feed structure", "Status": "OK" if feed.get("trusted") else "CHECK"},
        {"Component": "Paper execution feed", "Status": "READY" if feed.get("execution_ok") else "BLOCKED"},
        {"Component": "Paper engine", "Status": "ONLINE"},
        {"Component": "Stock Smart Engine", "Status": "ONLINE" if instrument.asset_class == "STOCK" else "STANDBY"},
        {"Component": "Stock scanner", "Status": "AUTO" if st.session_state.get("stock_auto_scan", False) else "MANUAL"},
        {"Component": "Paper persistence", "Status": "ACTIVE"},
        {"Component": "Order idempotency", "Status": "ACTIVE"},
        {"Component": "Broker bridge", "Status": "ONLINE" if account else ("CHECK" if bridge else "NOT CONFIGURED")},
        {"Component": "Broker quote", "Status": "READY" if broker_quote.get("ok") else ("BLOCKED" if bridge else "NOT CONFIGURED")},
        {"Component": "Contract metadata", "Status": "VERIFIED" if contract_metadata_verified else "UNVERIFIED"},
        {"Component": "Baseline research gate", "Status": "PASS" if (st.session_state.get("research_gate", {}).get("passed") and st.session_state.get("research_gate", {}).get("context") == research_context) else "BLOCKED"},
        {"Component": "Independent validation", "Status": "PASS" if ((st.session_state.get("independent_validation") or {}).get("gate") or {}).get("passed", False) else "NOT PASSED"},
        {"Component": "Walk-forward lab", "Status": "CANDIDATE READY" if ((st.session_state.get("walkforward_lab") or {}).get("eligible")) else "RESEARCH"},
        {"Component": "Paper Forward", "Status": "READY" if feed.get("execution_ok") else "BLOCKED"},
        {"Component": "Fresh holdout", "Status": "PASS" if ((st.session_state.get("fresh_holdout") or {}).get("gate") or {}).get("passed", False) else "UNUSED/FAIL"},
        {"Component": "Automation backend", "Status": "READY" if automation_backend_ready else "NOT CONFIGURED"},
        {"Component": "Live trading", "Status": "UNLOCKED" if live_unlocked else "LOCKED"},
        {"Component": "Auto live", "Status": "UNLOCKED" if auto_live_unlocked else "LOCKED"},
    ]
    st.dataframe(pd.DataFrame(health_rows), hide_index=True, use_container_width=True)

    st.caption(
        f"{quality['label']} • Structure {'OK' if feed.get('trusted') else 'CHECK'} • "
        f"Paper Execution {'READY' if feed.get('execution_ok') else 'BLOCKED'} • "
        f"Market {market_label} • Quote age {quote_age_label} ({feed.get('quote_ts_source') or 'N/A'}) • "
        f"API RTT {quote_latency_label} • "
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
        "FINAL SAFETY v6.1: تقدر تبدأ Paper Forward الآن بأموال افتراضية. "
        "Live الحقيقي يبقى مقفولًا حتى يجتاز نفس المرشح Fresh Holdout + "
        "20 صفقة Paper Forward مغلقة بنتيجة كلية موجبة + Broker Bridge فعلي + "
        "Broker Quote حديث + positions موثقة + بيانات عقد موثقة + LIVE_UI_PIN + KILL SWITCH OFF. "
        "هذه البوابات لا تعني ضمان الربح؛ هي شروط تحقق وتشغيل فقط."
    )


if st.session_state.auto_refresh:
    @st.fragment(run_every=refresh_seconds)
    def _paper_heartbeat() -> None:
        """
        Session-scoped Paper heartbeat.
        It does not full-rerun the app, so mobile stays connected more reliably.
        It is NOT a 24/7 server worker and may pause if the browser session sleeps.
        """
        roll_paper_day()
        if st.session_state.get("_persistence_error"):
            st.error("التداول التلقائي متوقف بسبب تعذر حفظ المحفظة")
            return
        if (instrument.asset_class == "STOCK" and not stock_session_state()["regular"]
                and not st.session_state.paper_position):
            st.caption(
                f"Paper heartbeat • {now_riyadh().strftime('%H:%M:%S')} • "
                "السوق مغلق — متوقف تلقائيًا"
            )
            return

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

        if st.session_state.paper_position:
            _hp = st.session_state.paper_position
            if str(_hp.get("symbol")) == instrument.symbol and hb_feed.get("execution_ok", False):
                hb_mark = paper_mark_price(_hp, hb_quote, hb_price)
                manage_paper(hb_mark)
            elif str(_hp.get("symbol")) != instrument.symbol:
                _hpq = fetch_quote(str(_hp.get("symbol")))
                if quote_execution_ready(_hpq):
                    _hpfallback = float(_hpq.get("last")) if finite(_hpq.get("last")) else float(_hp.get("entry", 0.0))
                    manage_paper(paper_mark_price(_hp, _hpq, _hpfallback))

        if (
            mode == "Paper"
            and st.session_state.auto_paper
            and not st.session_state.paper_position
            and hb_feed.get("execution_ok", False)
        ):
            hb_active_candidate = active_candidate
            hb_profile = adaptive_profile
            hb_risk_pct = float(paper_risk_pct)

            if adaptive_paper_mode and selected_profile == "ذكي تلقائي":
                if instrument.asset_class == "GOLD":
                    (
                        hb_profile,
                        hb_active_candidate,
                        hb_analysis,
                        _hb_meta,
                    ) = choose_smart_autopilot(hb_raw)
                    hb_risk_pct = adaptive_paper_risk_pct(hb_profile, instrument.asset_class)
                elif instrument.asset_class == "STOCK":
                    (
                        hb_profile,
                        hb_active_candidate,
                        hb_analysis,
                        _hb_meta,
                    ) = choose_stock_autopilot(hb_raw)
                    hb_risk_pct = adaptive_paper_risk_pct(hb_profile, instrument.asset_class)
                else:
                    hb_analysis = analyze_forward_candidate(hb_raw, hb_active_candidate)
            elif adaptive_paper_mode and instrument.asset_class == "STOCK":
                hb_active_candidate = STOCK_PROFILE_CANDIDATES[hb_profile]
                hb_analysis = analyze_stock_candidate(hb_raw, hb_profile)
                hb_risk_pct = adaptive_paper_risk_pct(hb_profile, instrument.asset_class)
            else:
                hb_analysis = analyze_forward_candidate(hb_raw, hb_active_candidate)

            hb_m5 = hb_analysis.get("snapshots", {}).get("M5")
            hb_candle = str(hb_raw["datetime"].iloc[-1])

            if hb_analysis.get("signal") in {"BUY", "SELL"} and hb_m5:
                try:
                    hb_entry_price = hb_price
                    if instrument.asset_class == "STOCK" and hb_analysis["signal"] == "BUY":
                        hb_entry_price = (
                            float(hb_quote["ask"])
                            if finite(hb_quote.get("ask"))
                            else float(hb_price) * (1.0 + 0.0002)
                        )
                    hb_plan = build_trade_plan(
                        hb_analysis["signal"],
                        hb_entry_price,
                        float(hb_m5["atr"]),
                        float(st.session_state.paper_balance),
                        float(hb_risk_pct),
                        instrument,
                        stop_atr=1.5 if instrument.asset_class == "STOCK" else 1.6,
                        tp1_r=1.0,
                        tp2_r=2.0 if instrument.asset_class == "STOCK" else 2.2,
                    )
                    hb_day_pnl = (
                        float(st.session_state.paper_balance)
                        - float(st.session_state.paper_day_start_balance)
                    )
                    hb_ok, hb_reasons = risk_gate(
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
                    if hb_active_candidate in SMART_PAPER_CANDIDATES:
                        hb_ok = bool(hb_ok and adaptive_entry_brake(hb_profile, instrument.asset_class))
                    if instrument.asset_class == "STOCK":
                        _hg_ok, _, _ = stock_quote_guard(hb_quote, hb_price)
                        hb_ok = bool(hb_ok and _hg_ok and stock_session_state()["preferred"])
                        hb_ok = bool(hb_ok and not stock_recent_loss(instrument.symbol))
                        hb_ok = bool(hb_ok and stock_trades_today() < STOCK_MAX_TRADES_PER_DAY)
                        hb_ok = bool(hb_ok and stock_trades_today(instrument.symbol) < STOCK_MAX_TRADES_PER_SYMBOL_DAY)
                        if st.session_state.get("stock_sharia_only", False):
                            hb_ok = bool(hb_ok and stock_sharia_status(instrument.symbol) == "مُدرج بالقائمة")
                    if (
                        hb_ok
                        and st.session_state.last_auto_paper_candle.get(instrument.symbol)
                        != hb_candle
                    ):
                        hb_key = (
                            f"AUTO:{instrument.symbol}:{hb_active_candidate}:{hb_candle}"
                        )
                        if open_paper(
                            hb_plan,
                            instrument,
                            order_key=hb_key,
                            candidate=hb_active_candidate,
                            profile=hb_profile,
                        ):
                            st.session_state.last_auto_paper_candle[instrument.symbol] = hb_candle
                            persist_paper_state()
                except ValueError:
                    pass

        st.caption(
            f"Paper heartbeat • {now_riyadh().strftime('%H:%M:%S')} • "
            f"{'READY' if hb_feed.get('execution_ok') else 'BLOCKED'}"
        )

    _paper_heartbeat()
else:
    st.caption("Paper heartbeat متوقف. استخدم «تحديث البيانات الآن» للتحديث اليدوي.")
