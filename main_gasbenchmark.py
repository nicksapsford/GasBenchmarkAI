"""
main_oilbenchmark.py -- GasBenchmark A.I. engine (Benchmark Desk)
================================================================================
Parallel scientific baseline for GasTrader. NO Arthur (AI), NO Morgan
(confidence), NO Guinevere (news), NO phantom logging.

Decision engine, every 5m candle:

  STEP 1  All Lancelot pre-checks must pass       (identical to GasTrader)
  STEP 2  Daily + 1h + 5m SSL must ALL agree      (the direction signal)
  STEP 3  Direction switch decides execution:
            WITH    -> trade the SSL direction
            AGAINST -> trade the opposite (contrarian). Lancelot validates the
                       SIGNAL direction; only the executed direction flips.

Exits: 0.05pt trailing stop / 0.08pt target / Profit Protection Ladder (Variant 2),
all handled by Stanley's monitor_trade().

Trades NORMALLY -- NOT the original GasTrader's data-collection mode. If NatGas is
too low-volatility (~$2.83/MMBtu) to be profitable, the benchmark P&L proves it
empirically. The Lancelot oversold/overbought entry veto (check_rsi_timing) is
retained as-is.

Natural Gas, Capital.com, port 5026, £1,000 paper, £100/pt, 0.005pt spread,
session 22:00-21:00 UTC. Template: GasTrader v1.3.7. All times UTC.
"""
import logging
import signal
import sys
import time
import random
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

from data_feed_gas import GasDataFeed, GAS_EPIC, is_market_open, get_liquidity_period
from capitalcom_connector import CapitalComConnector
from notifier_gas import (
    notify_system_startup, notify_trade_opened,
    notify_trade_closed_win, notify_trade_closed_loss, notify_system_error,
)
from paper_trader_gas import PaperTraderGas
from pre_checks_gas import run_all_pre_checks, run_individual_pre_checks, check_kill_switch_reset
from strategy_gas import should_force_close, get_gbpusd_rate
import direction_switch

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
_VER = BASE_DIR / "VERSION"
VERSION = _VER.read_text().strip() if _VER.exists() else "1.0.0"

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
SHUTDOWN_FLAG = LOG_DIR / "shutdown.flag"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logging.Formatter.converter = time.gmtime
log = logging.getLogger("GasBenchmark")

PORT = 5026
DASHBOARD_URL = "http://localhost:%d/api/update" % PORT
PAPER_TRADING_MODE = True
CANDLE_SECONDS = 300
MONITOR_SECONDS = 30

_SHUTDOWN = False


def _handle_signal(sig, frame):
    global _SHUTDOWN
    _SHUTDOWN = True
    log.info("Signal %s -- shutting down", sig)


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


class AccountState:
    """Live account state passed to the Lancelot pre-checks (kill switch, losses)."""

    def __init__(self, capital: float) -> None:
        self.capital_gbp = capital
        self.daily_pnl_gbp = 0.0
        self.consecutive_losses = 0
        self.last_loss_time = None
        self.kill_switch_active = False
        self.kill_switch_tier = 0
        self.kill_switch_until = None
        self.kill_switch_reason = ""
        self.kill_history = []

    def record_trade(self, pnl_gbp: float) -> None:
        self.daily_pnl_gbp = round(self.daily_pnl_gbp + pnl_gbp, 2)
        self.capital_gbp = round(self.capital_gbp + pnl_gbp, 2)
        if pnl_gbp < 0:
            self.consecutive_losses += 1
            self.last_loss_time = datetime.now(timezone.utc)
        else:
            self.consecutive_losses = 0


# ── SSL 3-timeframe agreement (the benchmark's direction signal) ──────────────

def _ssl(bar):
    if bar is None:
        return None
    v = bar.get("ssl_bull")
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return "LONG" if bool(v) else "SHORT"


def ssl_agreement(bar_1d, bar_1h, bar_5m):
    d, h, m = _ssl(bar_1d), _ssl(bar_1h), _ssl(bar_5m)
    if d is not None and d == h == m:
        return d
    return None


def _get_price(feed):
    try:
        return float(feed.latest_bar("5m")["close"])
    except Exception:
        return None


# ── State (last-known, for the dashboard) ─────────────────────────────────────

_last = {"price": None, "signal": None, "lancelot": "awaiting first tick",
         "checks": {}, "session": "--"}


def _open(stanley, ig, direction, price, period, gbpusd):
    trade = stanley.open_trade(direction, price, gbpusd, period)
    try:
        notify_trade_opened(direction=direction, entry_price=price, stop_loss=trade.stop_loss,
                            take_profit=trade.take_profit, stake=trade.stake,
                            liquidity_period=period, size_oz=trade.size_oz)
    except Exception as exc:
        log.warning("Percival open notify failed: %s", exc)
    log.info("OPEN %s @ $%.2f | stop=$%.2f target=$%.2f stake=£%.2f/pt",
             direction, price, trade.stop_loss, trade.take_profit, trade.stake)


def _on_close(stanley, account, price, reason):
    trade = stanley.trade_history[-1] if stanley.trade_history else None
    if trade is None:
        return
    account.record_trade(trade.pnl_gbp)
    try:
        if trade.pnl_gbp >= 0:
            notify_trade_closed_win(trade.direction, price, trade.points_gained,
                                    trade.pnl_gbp, account.capital_gbp, reason)
        else:
            notify_trade_closed_loss(trade.direction, price, trade.points_gained,
                                     trade.pnl_gbp, account.capital_gbp, reason)
    except Exception as exc:
        log.warning("Percival close notify failed: %s", exc)
    log.info("CLOSED %s @ $%.2f | %s | pts=%+.2f | P&L=£%+.2f | capital=£%.2f",
             trade.direction, price, reason, trade.points_gained, trade.pnl_gbp, account.capital_gbp)


def monitor(feed, stanley, account, ig, now_utc):
    if not stanley.in_trade:
        return
    price = _get_price(feed)
    if price is None:
        return
    gbpusd = get_gbpusd_rate(ig)
    if should_force_close(now_utc):
        stanley.close_trade(price, "FORCE_CLOSE_2045", gbpusd)
        _on_close(stanley, account, price, "FORCE_CLOSE_2045")
        return
    reason = stanley.monitor_trade(price, gbpusd)   # trailing + ladder + stop/target
    if reason:
        _on_close(stanley, account, price, reason)


def run_candle_tick(feed, stanley, account, ig):
    now_utc = datetime.now(timezone.utc)
    period = get_liquidity_period(now_utc)
    _last["session"] = period
    try:
        feed.refresh()
    except Exception as exc:
        log.error("Data refresh failed: %s -- skipping tick", exc)
        return
    try:
        bar_1d = feed.latest_bar("1d")
    except Exception:
        bar_1d = None
    try:
        bar_1h = feed.latest_bar("1h")
        bar_5m = feed.latest_bar("5m")
    except Exception:
        log.warning("Insufficient indicator data -- skipping tick")
        return

    price = float(bar_5m["close"])
    gbpusd = get_gbpusd_rate(ig)
    _last["price"] = price

    if stanley.in_trade:
        return   # entries only when flat; exits handled by the monitor loop

    if not is_market_open(now_utc):
        _last["lancelot"] = "market closed (%s)" % period
        return

    signal_dir = ssl_agreement(bar_1d, bar_1h, bar_5m)
    _last["signal"] = signal_dir
    checks = run_all_pre_checks(bar_1h, bar_5m, account, None, bar_1d,
                                proposed_direction=(signal_dir or "BOTH"), now_utc=now_utc)
    _last["checks"] = run_individual_pre_checks(bar_1h, bar_5m, account, None, bar_1d,
                                                proposed_direction=(signal_dir or "BOTH"), now_utc=now_utc)
    _last["lancelot"] = "CLEAR" if checks.get("passed") else ("BLOCKED: " + str(checks.get("reason") or "--"))

    if not checks.get("passed"):
        log.info("Lancelot BLOCK: %s", checks.get("reason"))
        return
    if signal_dir is None:
        log.info("No 3-TF SSL agreement -- no trade")
        return

    mode = direction_switch.get_mode()
    exec_dir = signal_dir if mode == "WITH" else direction_switch.flip(signal_dir)
    log.info("SIGNAL %s | switch %s -> execute %s", signal_dir, mode, exec_dir)
    _open(stanley, ig, exec_dir, price, period, gbpusd)


# ── Dashboard push ────────────────────────────────────────────────────────────

def push_dashboard(stanley, account, mode):
    trade = stanley.current_trade
    price = _last.get("price")
    pos, floating, locked = None, 0.0, None
    if trade is not None and stanley.in_trade:
        try:
            pts = (price - trade.entry_price) if trade.direction == "LONG" else (trade.entry_price - price)
            floating = round(pts * trade.stake, 2)
        except Exception:
            floating = 0.0
        lf = getattr(trade, "ladder_floor_gbp", 0.0) or 0.0
        locked = round(lf, 2) if lf > 0 else None
        pos = {"direction": trade.direction, "entry": round(trade.entry_price, 3),
               "stop": round(trade.stop_loss, 3), "target": round(trade.take_profit, 3),
               "stake": round(trade.stake, 2), "floating_gbp": floating,
               "ladder_step": getattr(trade, "ladder_step", 0), "locked_gbp": locked}
    lanc = "IN TRADE" if stanley.in_trade else _last.get("lancelot", "--")
    payload = {
        "system": "GasBenchmark", "version": VERSION, "port": PORT,
        "mode": mode, "session": _last.get("session", "--"),
        "updated_utc": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        "price": round(price, 3) if price is not None else None,
        "in_trade": stanley.in_trade, "position": pos,
        "floating_gbp": floating, "locked_gbp": locked,
        "signal": _last.get("signal") or "--",
        "lancelot": lanc,
        "checks": _last.get("checks", {}),
        "portfolio": {"balance": round(stanley.capital_gbp, 2),
                      "today_pnl": round(account.daily_pnl_gbp, 2),
                      "floating_gbp": floating},
    }
    try:
        requests.post(DASHBOARD_URL, json=payload, timeout=3)
    except Exception:
        pass


# ── Main loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=" * 70)
    log.info("  GasBenchmark A.I. v%s  (Benchmark Desk, port %d)", VERSION, PORT)
    log.info("  Natural Gas | Capital.com | Pure Lancelot + 3-TF SSL + WITH/AGAINST")
    log.info("  Mode: %s | PAPER TRADING", direction_switch.get_mode())
    log.info("=" * 70)

    ig = CapitalComConnector()
    ig_connected = False
    try:
        ig.connect()
        ig_connected = True
        log.info("Capital.com connected")
    except Exception as exc:
        log.error("Capital.com connect failed: %s -- yfinance fallback", exc)

    feed = GasDataFeed(ig_connector=ig if ig_connected else None)
    try:
        feed.initialise()
    except Exception as exc:
        log.warning("Initial data load partial: %s", exc)

    stanley = PaperTraderGas()
    account = AccountState(capital=stanley.capital_gbp)
    try:
        notify_system_startup(capital=stanley.capital_gbp, mode="PAPER (Benchmark)")
    except Exception:
        pass
    SHUTDOWN_FLAG.unlink(missing_ok=True)

    # Stagger Capital.com requests (shared demo account with the original desk).
    delay = 30 + random.uniform(0, 10)
    log.info("Staggering %.0fs before main loop (shared Capital.com demo)", delay)
    time.sleep(delay)

    log.info("Running. Dashboard: http://localhost:%d", PORT)
    last_candle = 0.0
    last_monitor = 0.0
    last_push = 0.0

    while not _SHUTDOWN:
        try:
            if SHUTDOWN_FLAG.exists():
                log.info("Shutdown flag seen -- stopping (left for watchdog).")
                break
            now = time.monotonic()
            now_utc = datetime.now(timezone.utc)

            if check_kill_switch_reset(account):
                account.kill_switch_tier = 0

            if (now - last_monitor) >= MONITOR_SECONDS:
                try:
                    monitor(feed, stanley, account, ig, now_utc)
                except Exception as exc:
                    log.warning("monitor error: %s", exc)
                last_monitor = now

            if (now - last_candle) >= CANDLE_SECONDS:
                try:
                    run_candle_tick(feed, stanley, account, ig)
                except Exception as exc:
                    log.warning("tick error: %s", exc)
                last_candle = now

            if (now - last_push) >= 15:
                push_dashboard(stanley, account, direction_switch.get_mode())
                last_push = now

            time.sleep(2)
        except Exception as exc:
            log.error("Main loop error: %s", exc)
            time.sleep(30)

    log.info("GasBenchmark stopped. Final capital: £%.2f", stanley.capital_gbp)


if __name__ == "__main__":
    main()
