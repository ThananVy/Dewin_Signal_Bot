"""
run_once.py — Single-run version for GitHub Actions / cron scheduling.
Fetches data, runs analysis, sends Telegram only if signal changed, saves cache.
Run this instead of bot.py when using scheduled cloud execution.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

from colorama import Fore, init
from dotenv import load_dotenv

load_dotenv()
init(autoreset=True)

from data_fetcher import PAIRS, fetch_all_pairs
from indicators import calculate_all_indicators, extract_indicator_summary
from claude_analyst import analyze_all_pairs
from output import save_json_output
from telegram_bot import TelegramBot
from bot import load_cache, save_cache, signal_changed

ICT = timezone(timedelta(hours=7))
VALID_PAIRS = list(PAIRS.keys())


def main():
    now = datetime.now(ICT).strftime("%Y-%m-%d %H:%M ICT")
    print(f"\n{Fore.CYAN}[{now}] Starting one-shot analysis...")

    # Load previous signals for change detection
    cache = load_cache()
    last_signals: dict = cache.get("signals", {})

    # Step 1: Fetch data
    print(f"{Fore.YELLOW}Fetching market data...")
    raw_data = fetch_all_pairs(VALID_PAIRS)
    if not raw_data:
        print(f"{Fore.RED}ERROR: No data fetched.")
        sys.exit(1)

    # Step 2: Calculate indicators
    print(f"{Fore.YELLOW}Calculating indicators...")
    all_indicators: dict = {}
    for pair, tf_data in raw_data.items():
        all_indicators[pair] = {}
        for tf, df in tf_data.items():
            enriched = calculate_all_indicators(df)
            all_indicators[pair][tf] = extract_indicator_summary(enriched, tf)

    # Step 3: Analyse with AI
    print(f"{Fore.YELLOW}Sending to AI for analysis...")
    signals = analyze_all_pairs(all_indicators)

    # Step 4: Send Telegram alerts for changed signals
    bot = TelegramBot()
    sent_any = False

    for pair, new_sig in signals.items():
        old_sig = last_signals.get(pair, {})
        changed, reason = signal_changed(old_sig, new_sig)

        if "error" in new_sig:
            print(f"{Fore.RED}  {pair}: ERROR — {new_sig['error']}")
            continue

        bias = new_sig.get("bias", "NEUTRAL")
        conf = new_sig.get("confidence", 0)
        min_conf = int(os.getenv("MIN_CONFIDENCE", "60"))

        print(f"{Fore.CYAN}  {pair}: bias={bias}  conf={conf}%  changed={changed}  reason='{reason}'")

        if not changed:
            print(f"{Fore.WHITE}    → Skipping: no change from last run.")
        elif bias not in ("BULLISH", "BEARISH"):
            print(f"{Fore.YELLOW}    → Skipping: NEUTRAL setup.")
        elif int(conf) < min_conf:
            print(f"{Fore.YELLOW}    → Skipping: confidence {conf}% below threshold {min_conf}%.")
        else:
            print(f"{Fore.GREEN}    → Sending Telegram alert...")
            sent = bot.send_signal(new_sig, reason)
            if sent:
                sent_any = True
                print(f"{Fore.GREEN}    → Sent OK.")

    if not sent_any:
        print(f"{Fore.WHITE}No signals sent this run.")

    # Step 5: Save updated cache and JSON output
    save_cache(signals)
    save_json_output(signals)
    print(f"{Fore.GREEN}Done.")


if __name__ == "__main__":
    main()
