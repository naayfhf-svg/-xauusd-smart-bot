# GOLD AI v3.0

Multi-asset trading terminal for gold, stocks, and futures/contracts.

## What v3.0 adds

- Multi-asset instrument presets + custom broker/data symbols.
- M5/M15/H1/H4 strategy engine using EMA, RSI, MACD, ADX, ATR, and B2 breakout/retest confirmation.
- Position sizing from real broker equity, instrument point value, quantity step, and ATR stop distance.
- Live risk gates: daily loss limit, max open positions, max risk per order, broker trading state, and kill switch.
- Authenticated broker execution bridge with Bearer token + optional HMAC signing.
- Manual live execution and a separately hard-locked automatic execution mode.
- Audit log and emergency cancel-all action.

## Broker bridge contract

The Streamlit app intentionally does **not** hard-code a broker-specific private API. The bridge is the adapter to an approved broker API (Dariya if/when API access is granted, MT5 gateway, IBKR gateway, etc.).

`GET /account` must return JSON similar to:

```json
{
  "equity": 100000,
  "day_pnl": -250,
  "open_positions": 1,
  "trading_enabled": true
}
```

`POST /orders` receives:

```json
{
  "client_order_id": "goldai-gold-...",
  "strategy": "GOLD_AI_V3_MTF_B2",
  "version": "3.0.0",
  "symbol": "XAU/USD",
  "asset_class": "GOLD",
  "side": "BUY",
  "quantity": 0.10,
  "order_type": "MARKET",
  "stop_loss": 1234.5,
  "take_profit_1": 1245.6,
  "take_profit_2": 1260.0,
  "entry_reference": 1240.0,
  "risk_pct": 0.5,
  "estimated_risk": 500,
  "signal_strength": 100,
  "signal_candle": "2026-09-20 12:00:00+00:00",
  "requested_at": "2026-09-20T15:00:00+03:00"
}
```

The bridge should translate this canonical order into the broker's native API and return a JSON response containing at least `id` or `order_id` and `status`.

`POST /orders/cancel-all` should cancel pending/open orders according to the broker adapter rules.

## Deployment

1. Install `requirements.txt`.
2. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` locally, or configure the same keys in Streamlit Cloud Secrets.
3. Add the Twelve Data API key.
4. Configure the authenticated broker bridge.
5. Test the bridge account/status endpoints before setting `LIVE_TRADING_ENABLED=true`.
6. Keep `AUTO_EXECUTION_ALLOWED=false` until manual live orders have been validated end-to-end.

The app blocks execution if broker account state cannot be verified. No broker credentials should be committed to GitHub.
