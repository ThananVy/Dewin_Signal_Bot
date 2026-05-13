"""bot.py — Local persistent bot. Run manually: python bot.py"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
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
ICT = timezone(timedelta(hours=7))
VALID_PAIRS = list(PAIRS.keys())
HEARTBEAT_EVERY = 60  # minutes


def is_trading_session() -> bool:
    now = datetime.now(ICT)
    return now.weekday() < 5 and 8 <= now.hour < 23


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
    new_bias = new.get("bias", "NEUTRAL") if new and "error" not in new else "NEUTRAL"
    if new_bias not in ("BULLISH", "BEARISH"):
        return False, ""
    if not old or "error" in old:
        return True, f"New {new_bias} setup"
    old_bias = old.get("bias", "NEUTRAL")
    if old_bias != new_bias:
        return True, (f"Flipped: {old_bias}→{new_bias}" if old_bias in ("BULLISH", "BEARISH") else f"New {new_bias} setup")

    def score(c):
        return int(c) if isinstance(c, (int, float)) else {"LOW": 30, "MEDIUM": 60, "HIGH": 80}.get(str(c).upper(), 0)

    if score(new.get("confidence", 0)) >= score(old.get("confidence", 0)) + 10:
        return True, f"Confidence up: {old.get('confidence')}→{new.get('confidence')}%"
    return False, ""


def run_analysis() -> dict | None:
    raw = fetch_all_pairs(VALID_PAIRS)
    if not raw:
        return None
    indicators = {}
    for pair, tf_data in raw.items():
        indicators[pair] = {
            tf: extract_indicator_summary(calculate_all_indicators(df), tf)
            for tf, df in tf_data.items()
        }
    return analyze_all_pairs(indicators)


def main():
    interval = int(os.getenv("INTERVAL_MINUTES", "10"))
    bot = TelegramBot()

    if not os.getenv("TELEGRAM_BOT_TOKEN") or not (os.getenv("TELEGRAM_CHAT_IDS") or os.getenv("TELEGRAM_CHAT_ID")):
        print(f"{Fore.RED}ERROR: Telegram credentials missing in .env")
        sys.exit(1)

    print(f"\n{Fore.CYAN}{'═'*55}")
    print(f"{Fore.CYAN}  TRADING SIGNAL BOT  |  Every {interval}min  |  7AM-11PM ICT")
    print(f"{Fore.CYAN}{'═'*55}\n")

    cache = load_cache()
    last_signals = cache.get("signals", {})
    last_heartbeat = datetime.now(ICT) - timedelta(minutes=HEARTBEAT_EVERY)
    run_count = 0

    bot.send_startup(VALID_PAIRS, interval)

    while True:
        now_ict = datetime.now(ICT)
        next_run = (now_ict + timedelta(minutes=interval)).strftime("%H:%M ICT")
        run_count += 1

        if not is_trading_session():
            print(f"{Fore.WHITE}[{now_ict.strftime('%H:%M ICT')}] Outside session (7AM-11PM Mon-Fri) — sleeping.")
        else:
            print(f"{Fore.YELLOW}[{now_ict.strftime('%H:%M ICT')}] Run #{run_count}...")
            try:
                signals = run_analysis()
                if signals is None:
                    print(f"{Fore.RED}  Analysis failed.")
                    bot.send_error("Data fetch failed — retrying next cycle.")
                else:
                    save_json_output(signals)
                    for pair, new_sig in signals.items():
                        old_sig = last_signals.get(pair, {})
                        changed, reason = signal_changed(old_sig, new_sig)
                        bias = new_sig.get("bias", "?") if "error" not in new_sig else "ERROR"
                        conf = new_sig.get("confidence", "-")
                        if changed:
                            print(f"{Fore.GREEN}  {pair}: {bias} {conf}% — SENDING ({reason})")
                            bot.send_signal(new_sig, reason)
                        else:
                            print(f"{Fore.WHITE}  {pair}: {bias} {conf}% — no change")
                    last_signals = signals
                    save_cache(signals)

                    mins_since_hb = (now_ict - last_heartbeat).total_seconds() / 60
                    if mins_since_hb >= HEARTBEAT_EVERY:
                        bot.send_heartbeat(signals, next_run)
                        last_heartbeat = now_ict
                        print(f"{Fore.CYAN}  Heartbeat sent.")
            except KeyboardInterrupt:
                print(f"\n{Fore.YELLOW}Stopped.")
                break
            except Exception as e:
                print(f"{Fore.RED}  Error: {e}")
                bot.send_error(str(e))

        print(f"{Fore.WHITE}  Next: {next_run}\n")
        time.sleep(interval * 60)


if __name__ == "__main__":
    main()
