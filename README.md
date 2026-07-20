# GasBenchmark A.I.

Part of the **Albion Benchmark Desk** — a parallel scientific baseline for the
original Albion Trading Desk. GasBenchmark trades Natural Gas on **pure Lancelot
signals with no AI overlay**, so its P&L can be compared like-for-like against the
original GasTrader.

- **Port:** 5026 · **Instrument:** Natural Gas (NATURALGAS, Capital.com) · **Balance:** £1,000
- **Template:** GasTrader v1.3.7 · **Paper trading only** · **Session:** 22:00–21:00 UTC

> **Trades NORMALLY — not data-collection mode.** The original GasTrader is in
> data-collection mode because NatGas (~$2.83/MMBtu) is low-volatility. GasBenchmark
> trades normally with no Arthur to second-guess the signals. **If NatGas is too
> low-vol to be profitable, the benchmark P&L proves it empirically** — that is
> valuable evidence in itself.

## Decision engine (the whole thing)

Every 5-minute candle:

1. **Lancelot pre-checks** must all pass — identical to GasTrader (`pre_checks_gas`, copied verbatim, confirmed pure). This includes the **oversold/overbought entry veto** (`check_rsi_timing`: blocks SHORTs into deep-oversold RSI, LONGs into overbought), retained as-is.
2. **3-timeframe SSL agreement** — Daily + 1h + 5m SSL must all point the same way. That is the direction signal.
3. **Direction switch** decides execution:
   - `WITH` — trade the SSL direction.
   - `AGAINST` — trade the opposite (contrarian). Lancelot always validates the *signal* direction; only the executed direction flips.

Exits are pure risk management: **0.05pt trailing stop / 0.08pt take profit / Profit Protection Ladder (Variant 2)** — via Stanley's `monitor_trade()`.

**Stripped vs GasTrader:** no Arthur (AI), no Morgan (confidence), no Guinevere (news), no phantom logging, no confidence thresholds, **no data-collection mode**.

Parameters: £100/pt stake, 0.005pt spread, bidirectional, switch default **WITH**.

## The direction switch (WITH / AGAINST)
One switch, **live reload** — re-read from `logs/direction_switch.json` every tick; flip from the dashboard or BenchmarkRoundTable with **no restart**. Default WITH; persists; atomic write.

## Running
```
python dashboard_gasbenchmark.py     # port 5026 (switch + status)
python watchdog_gasbenchmark.py      # supervises main_gasbenchmark.py
```

Appears automatically on **BenchmarkRoundTable** (port 5030) once running.

All times UTC.
