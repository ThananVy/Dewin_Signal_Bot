"""
main.py — Entry point for the Forex & Gold Trading Signal Generator.

Usage:
  python main.py                          # analyse all pairs, all timeframes
  python main.py --pair GOLD              # analyse GOLD only
  python main.py --pair EURUSD            # analyse EURUSD only
  python main.py --timeframe 4H           # focus all pairs on 4H only
  python main.py --pair USDJPY --timeframe Daily
"""

import argparse
import sys

from colorama import Fore, init

init(autoreset=True)

from data_fetcher import PAIRS, fetch_all_pairs
from indicators import calculate_all_indicators, extract_indicator_summary
from claude_analyst import analyze_all_pairs
from output import print_all_signals, save_json_output

VALID_PAIRS = list(PAIRS.keys())
VALID_TIMEFRAMES = ["Daily", "4H", "1H"]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="trading-tool",
        description="Multi-timeframe Forex & Gold signal generator using Claude AI",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--pair",
        type=str,
        choices=VALID_PAIRS,
        metavar="PAIR",
        help=f"Analyse one pair only.\nChoices: {', '.join(VALID_PAIRS)}",
    )
    parser.add_argument(
        "--timeframe",
        type=str,
        choices=VALID_TIMEFRAMES,
        metavar="TF",
        help=(
            "Focus Claude analysis on a single timeframe.\n"
            f"Choices: {', '.join(VALID_TIMEFRAMES)}\n"
            "Data for all timeframes is still fetched and computed;\n"
            "only the prompt sent to Claude is restricted to this TF."
        ),
    )
    return parser.parse_args()


def _banner(pairs_target: list[str], focus_tf: str | None) -> None:
    print(f"\n{Fore.CYAN}{'═' * 70}")
    print(f"{Fore.CYAN}{'  FOREX & GOLD SIGNAL GENERATOR':}")
    print(f"{Fore.CYAN}{'═' * 70}")
    print(f"{Fore.WHITE}  Pairs     : {', '.join(pairs_target)}")
    print(f"{Fore.WHITE}  Timeframes: {focus_tf if focus_tf else 'Daily + 4H + 1H (multi-TF)'}")
    print()

def main() -> dict:
    args = _parse_args()

    pairs_target = [args.pair] if args.pair else VALID_PAIRS
    focus_tf: str | None = args.timeframe

    _banner(pairs_target, focus_tf)

    # ── Step 1: Fetch raw OHLCV data ────────────────────────
    print(f"{Fore.YELLOW}[1/3]  Fetching market data from Yahoo Finance...")
    raw_data = fetch_all_pairs(pairs_target)

    if not raw_data:
        print(f"\n{Fore.RED}  ERROR: No data fetched — check internet connection or pair availability.")
        sys.exit(1)

    fetched = list(raw_data.keys())
    skipped = [p for p in pairs_target if p not in fetched]
    if skipped:
        print(f"{Fore.YELLOW}  Skipped (no data): {', '.join(skipped)}")
    print(f"{Fore.GREEN}  Done — {len(fetched)} pair(s) loaded: {', '.join(fetched)}\n")

    # ── Step 2: Calculate technical indicators ───────────────
    print(f"{Fore.YELLOW}[2/3]  Calculating technical indicators...")
    all_indicators: dict = {}

    for pair, tf_data in raw_data.items():
        all_indicators[pair] = {}
        for tf, df in tf_data.items():
            print(f"  {pair} {tf}  ({len(df)} bars)...")
            enriched = calculate_all_indicators(df)
            all_indicators[pair][tf] = extract_indicator_summary(enriched, tf)

    print(f"{Fore.GREEN}  Done — SuperTrend, Squeeze, EMA 20/50/200, ATR, Swings computed.\n")

    # ── Step 3: Claude API analysis ──────────────────────────
    print(f"{Fore.YELLOW}[3/3]  Sending data to Claude for analysis...")
    if focus_tf:
        print(f"{Fore.WHITE}  Mode: single-timeframe focus ({focus_tf})")

    signals = analyze_all_pairs(all_indicators, focus_timeframe=focus_tf)
    print()

    # ── Output ───────────────────────────────────────────────
    print_all_signals(signals)
    save_json_output(signals)

    return signals


if __name__ == "__main__":
    main()
