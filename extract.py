"""
extract.py — Fetches market data, calculates indicators, prints a clean summary.
No AI API needed. Copy the output and paste it to Claude for analysis.

Usage:
  python extract.py
  python extract.py --pair GOLD
"""

import argparse
import json
import warnings
warnings.filterwarnings("ignore")

from data_fetcher import PAIRS, fetch_all_pairs
from indicators import calculate_all_indicators, extract_indicator_summary

PAIR_DISPLAY = {"EURUSD": "EURUSD", "USDJPY": "USDJPY", "GOLD": "XAUUSD (Gold)"}


def print_summary(pair: str, all_summaries: dict):
    display = PAIR_DISPLAY.get(pair, pair)
    print(f"\n{'='*60}")
    print(f"  {display}")
    print(f"{'='*60}")

    for tf in ["Daily", "4H", "1H"]:
        s = all_summaries.get(tf, {})
        if not s:
            continue

        sqz = s.get("squeeze", {})
        print(f"\n── {tf} ──")
        print(f"  Close      : {s.get('close')}")
        print(f"  EMA 20/50/200: {s.get('ema_20')} / {s.get('ema_50')} / {s.get('ema_200')}")
        print(f"  EMA Align  : {s.get('ema_alignment')}")
        print(f"  SuperTrend : {s.get('supertrend_direction')}")
        print(f"  ATR(14)    : {s.get('atr')}")
        print(f"  Squeeze    : {sqz.get('status')} | Momentum: {sqz.get('momentum')}")
        print(f"  Volume     : {s.get('volume')} (avg {s.get('volume_avg')}, ratio {s.get('volume_vs_avg')}x)")
        print(f"  Swing Highs: {json.dumps(s.get('swing_highs', []))}")
        print(f"  Swing Lows : {json.dumps(s.get('swing_lows', []))}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pair", choices=list(PAIRS.keys()), default=None)
    args = parser.parse_args()

    targets = [args.pair] if args.pair else list(PAIRS.keys())

    print("Fetching data and calculating indicators...")
    raw_data = fetch_all_pairs(targets)

    for pair, tf_data in raw_data.items():
        summaries = {}
        for tf, df in tf_data.items():
            enriched = calculate_all_indicators(df)
            summaries[tf] = extract_indicator_summary(enriched, tf)
        print_summary(pair, summaries)

    print(f"\n{'='*60}")
    print("Copy everything above and paste it to Claude for analysis.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
