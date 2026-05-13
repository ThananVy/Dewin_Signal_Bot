"""
bot.py — Telegram trading signal bot.
Runs analysis on a schedule and sends Telegram alerts only when signals change.

Usage:
  python bot.py                   # uses INTERVAL_MINUTES from .env (default 5)
  python bot.py --interval 10     # override interval

Setup:
  1. Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to your .env file
  2. python bot.py
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from colorama import Fore, init
from dotenv import load_dotenv

load_dotenv()
init(autoreset=True)

from data_fetcher import PAIRS, fetch_all_pairs
from indicators import calculate_all_indicators, extract_indicator_summary
from claude_analyst import analyze_all_pairs
from output import save_json_output
from telegram_bot import TelegramBot

CACHE_FILE = Path(__file__).parent / "signal_cache.json"
VALID_PAIRS = list(PAIRS.keys())
HEARTBEAT_EVERY = 1 * 60  # minutes


# ─────────────────────────────────────────────
# Signal cache helpers
# ─────────────────────────────────────────────

def load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_cache(signals: dict) -> None:
    CACHE_FILE.write_text(
        json.dumps({"saved_at": datetime.utcnow().isoformat(), "signals": signals}, indent=2),
        encoding="utf-8",
    )


def signal_changed(old: dict, new: dict) -> tuple[bool, str]:
    """
    Returns (changed: bool, reason: str).
    Only returns changed=True for BULLISH or BEARISH setups:
      - New directional setup appeared (was NEUTRAL or missing)
      - Direction flipped (BULLISH ↔ BEARISH)
      - Confidence upgraded (LOW → MEDIUM/HIGH)
    NEUTRAL signals are always ignored.
    """
    new_bias = new.get("bias", "NEUTRAL") if new and "error" not in new else "NEUTRAL"

    # Never send NEUTRAL
    if new_bias not in ("BULLISH", "BEARISH"):
        return False, ""

    if not old or "error" in old:
        return True, f"New {new_bias} setup detected"

    old_bias = old.get("bias", "NEUTRAL")

    if old_bias != new_bias:
        if old_bias in ("BULLISH", "BEARISH"):
            return True, f"Direction flipped: {old_bias} → {new_bias}"
        return True, f"New {new_bias} setup detected"

    def _to_score(c) -> int:
        if isinstance(c, (int, float)):
            return int(c)
        return {"LOW": 30, "MEDIUM": 60, "HIGH": 80}.get(str(c).upper(), 0)

    old_score = _to_score(old.get("confidence", 0))
    new_score = _to_score(new.get("confidence", 0))
    if new_score >= old_score + 10:
        return True, f"Confidence upgraded: {old_score}% → {new_score}%"

    return False, ""


# ─────────────────────────────────────────────
# Core analysis run
# ─────────────────────────────────────────────

def run_analysis(pairs_filter: list[str] | None = None) -> dict | None:
    """Fetch data, compute indicators, call AI. Returns signals dict or None on failure."""
    targets = pairs_filter or VALID_PAIRS

    raw_data = fetch_all_pairs(targets)
    if not raw_data:
        return None

    all_indicators: dict = {}
    for pair, tf_data in raw_data.items():
        all_indicators[pair] = {}
        for tf, df in tf_data.items():
            enriched = calculate_all_indicators(df)
            all_indicators[pair][tf] = extract_indicator_summary(enriched, tf)

    return analyze_all_pairs(all_indicators)


# ─────────────────────────────────────────────
# Main bot loop
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Telegram Trading Signal Bot")
    parser.add_argument("--interval", type=int, default=None,
                        help="Override INTERVAL_MINUTES from .env")
    parser.add_argument("--pair", type=str, choices=VALID_PAIRS, default=None,
                        help="Monitor a single pair only")
    args = parser.parse_args()

    interval = args.interval or int(os.getenv("INTERVAL_MINUTES", "5"))
    pairs_filter = [args.pair] if args.pair else None
    targets = pairs_filter or VALID_PAIRS

    bot = TelegramBot()

    print(f"\n{Fore.CYAN}{'═' * 60}")
    print(f"{Fore.CYAN}  TELEGRAM TRADING SIGNAL BOT")
    print(f"{Fore.CYAN}{'═' * 60}")
    print(f"{Fore.WHITE}  Pairs    : {', '.join(targets)}")
    print(f"{Fore.WHITE}  Interval : every {interval} minutes")
    print(f"{Fore.WHITE}  Started  : {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{Fore.CYAN}{'═' * 60}\n")

    # Validate Telegram credentials before starting
    if not os.getenv("TELEGRAM_BOT_TOKEN") or not (os.getenv("TELEGRAM_CHAT_IDS") or os.getenv("TELEGRAM_CHAT_ID")):
        print(f"{Fore.RED}  ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_IDS must be set in .env")
        sys.exit(1)

    cache = load_cache()
    last_signals: dict = cache.get("signals", {})
    last_heartbeat: datetime = datetime.utcnow() - timedelta(minutes=HEARTBEAT_EVERY)
    run_count = 0

    bot.send_startup(targets, interval)
    print(f"{Fore.GREEN}  Startup message sent to Telegram.\n")

    while True:
        run_count += 1
        now = datetime.utcnow()
        next_run = (now + timedelta(minutes=interval)).strftime("%H:%M UTC")

        print(f"{Fore.YELLOW}[{now.strftime('%H:%M:%S')}] Run #{run_count} — fetching & analysing...")

        try:
            signals = run_analysis(pairs_filter)

            if signals is None:
                print(f"{Fore.RED}  Analysis failed — skipping this cycle.")
                bot.send_error("Data fetch failed — will retry next cycle.")
            else:
                # Save JSON output
                save_json_output(signals)

                # Check each pair for changes and send Telegram alerts
                changed_any = False
                for pair, new_sig in signals.items():
                    old_sig = last_signals.get(pair, {})
                    changed, reason = signal_changed(old_sig, new_sig)

                    if changed:
                        changed_any = True
                        pair_label = new_sig.get("pair", pair)
                        bias = new_sig.get("bias", "?")
                        print(f"{Fore.GREEN}  {pair_label}: CHANGED ({reason}) — sending Telegram alert...")
                        bot.send_signal(new_sig, reason=reason)
                    else:
                        bias = new_sig.get("bias", "?")
                        print(f"{Fore.WHITE}  {pair}: No change ({bias}) — skipping.")

                if not changed_any:
                    print(f"{Fore.WHITE}  No changes detected across all pairs.")

                # Update cache
                last_signals = signals
                save_cache(signals)

                # Heartbeat every 4 hours
                minutes_since_heartbeat = (now - last_heartbeat).total_seconds() / 60
                if minutes_since_heartbeat >= HEARTBEAT_EVERY:
                    bot.send_heartbeat(signals, next_run)
                    last_heartbeat = now
                    print(f"{Fore.CYAN}  Heartbeat sent.")

        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}  Bot stopped by user.")
            bot.send_error("Bot was manually stopped.")
            break
        except Exception as exc:
            print(f"{Fore.RED}  Unexpected error: {exc}")
            bot.send_error(f"Unexpected error: {exc}")

        print(f"{Fore.WHITE}  Sleeping {interval} min — next run at {next_run}\n")
        time.sleep(interval * 60)


if __name__ == "__main__":
    main()
