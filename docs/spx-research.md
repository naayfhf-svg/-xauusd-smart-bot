# SPX research (7.7.0)

This is a research module, not a live SPX signal engine. Select SPX in the instrument menu. Existing GLD and spot-gold paths are unchanged.

Options: explicit user-triggered GET of Alpaca SPX snapshots, feed=indicative only. Existing Alpaca credentials stay server-side; errors omit raw exceptions. Three-page cap is labelled partial. A missing entitlement returns unavailable without paid fallback. Indicative quotes are modified and trades delayed; even a recent timestamp cannot authorize entry. No order endpoints or automatic trading.

Historical analysis: optional SPX CSV, UTF-8, maximum 2 MB/5000 bars. Columns symbol,timestamp,open,high,low,close. Require 60 completed 5-minute bars with explicit timezone, valid OHLC, no duplicates or intraday gaps. Times refer to bar starts within weekday 09:30–16:00 New York. This is a regular-hours envelope, not an exchange holiday/early-close calendar; uploaded provenance is not verified. All output remains historical-only. EMA20/50, simple-average RSI14/ATR14 and prior-six-bar bounds are descriptive, not tested predictors. No index volume, VWAP or inferred option premiums.

Calculator: long Call/Put premium x 100 x whole contracts + entered entry fees. Maximum loss excludes additional exit/settlement charges. Default zero fees are not a claim of free trading.

Remaining live dependencies: authorized SPX index quote/history source and real option quotes with account entitlements. No proof of profitability, no option selection algorithm, no live trade logging yet.

Sources checked 2026-09-28:
- https://docs.alpaca.markets/us/reference/optionchain
- https://docs.alpaca.markets/us/docs/historical-option-data
- https://docs.alpaca.markets/us/docs/index-options
- https://www.cboe.com/tradable-products/sp-500/spx-options/spx-specifications

Validation: pytest -q. Network mocked in tests. Live indicative SPX availability must be checked in the deployed account; the mock tests do not prove entitlement.
