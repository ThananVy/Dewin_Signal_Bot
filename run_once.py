"""run_once.py — Single execution for GitHub Actions. Skips outside 7AM-11PM Cambodia time."""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from colorama import Fore, init
from dotenv import load_dotenv

load_dotenv()
init(autoreset=True)

ICT = timezone(timedelta(hours=7))
CACHE_FILE = Path(__file__).parent / "signal_cache.json"

from data_fetcher import PAIRS, fetch_all_pairs
from indicators import calculate_all_indicators, extract_indicator_summary
from claude_analyst import analyze_all_pairs
from output import save_json_output
from telegram_bot import TelegramBot
from bot import load_cache, save_cache, signal_changed

VALID_PAIRS = list(PAIRS.keys())


def is_trading_session() -> bool:
    now = datetime.now(ICT)
    return now.weekday() < 5 and 7 <= now.hour < 23


def main():
    now = datetime.now(ICT)
    print(f"\n[{now.strftime('%Y-%m-%d %H:%M ICT')}] run_once starting...")

    if not is_trading_session():
        print(f"Outside trading session (7AM-11PM Mon-Fri Cambodia). Exiting.")
        sys.exit(0)

    cache = load_cache()
    last_signals = cache.get("signals", {})

    print("Fetching data...")
    raw = fetch_all_pairs(VALID_PAIRS)
    if not raw:
        print(f"{Fore.RED}ERROR: No data fetched.")
        sys.exit(1)

    print("Calculating indicators...")
    indicators = {}
    for pair, tf_data in raw.items():
        indicators[pair] = {
            tf: extract_indicator_summary(calculate_all_indicators(df), tf)
            for tf, df in tf_data.items()
        }

    print("Analysing with Groq (1 merged prompt)...")
    signals = analyze_all_pairs(indicators)

    bot = TelegramBot()
    min_conf = int(os.getenv("MIN_CONFIDENCE", "60"))

    for pair, new_sig in signals.items():
        old_sig = last_signals.get(pair, {})
        changed, reason = signal_changed(old_sig, new_sig)

        if "error" in new_sig:
            print(f"{Fore.RED}  {pair}: ERROR - {new_sig['error']}")
            continue

        bias = new_sig.get("bias", "NEUTRAL")
        conf = int(new_sig.get("confidence", 0))
        print(f"  {pair}: bias={bias}  conf={conf}%  changed={changed}  reason='{reason}'")

        if not changed:
            print(f"    -> No change.")
        elif bias not in ("BULLISH", "BEARISH"):
            print(f"    -> NEUTRAL, skip.")
        elif conf < min_conf:
            print(f"    -> Confidence {conf}% < {min_conf}%, skip.")
        else:
            print(f"{Fore.GREEN}    -> Sending alert...")
            bot.send_signal(new_sig, reason)

    save_cache(signals)
    save_json_output(signals)
    print("Done.")


if __name__ == "__main__":
    main()
