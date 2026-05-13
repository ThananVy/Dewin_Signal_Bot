"""
output.py — Colored terminal display and JSON file export of trading signals.
"""

import json
from datetime import datetime
from pathlib import Path

from colorama import Fore, Style, init

init(autoreset=True)

_LINE = "─" * 70
_DLINE = "═" * 70


def _bias_color(bias: str) -> str:
    return {
        "BULLISH": Fore.GREEN,
        "BEARISH": Fore.RED,
        "NEUTRAL": Fore.YELLOW,
    }.get(bias, Fore.WHITE)


def _conf_color(conf: str) -> str:
    return {
        "HIGH": Fore.GREEN,
        "MEDIUM": Fore.YELLOW,
        "LOW": Fore.RED,
    }.get(conf, Fore.WHITE)


def print_header() -> None:
    now = datetime.utcnow().strftime("%Y-%m-%d  %H:%M UTC")
    print(f"\n{Fore.CYAN}{_DLINE}")
    print(f"{Fore.CYAN}{'FOREX & GOLD TRADING SIGNALS':^70}")
    print(f"{Fore.CYAN}{'Multi-Timeframe Analysis  ·  Powered by Claude AI':^70}")
    print(f"{Fore.CYAN}{_DLINE}")
    print(f"{Fore.WHITE}  Generated : {now}")
    print()


def print_signal(signal: dict) -> None:
    pair = signal.get("pair", "UNKNOWN")
    bias = signal.get("bias", "UNKNOWN")
    confidence = signal.get("confidence", "UNKNOWN")
    bc = _bias_color(bias)
    cc = _conf_color(confidence)

    print(f"{Fore.WHITE}{_LINE}")

    # ── Header row ──────────────────────────────────────────
    print(
        f"  {Fore.WHITE}PAIR  : {Fore.CYAN}{pair:12}"
        f"{Fore.WHITE}BIAS   : {bc}{bias:10}"
        f"{Fore.WHITE}CONFIDENCE : {cc}{confidence}"
    )
    print(f"{Fore.WHITE}{_LINE}")

    if "error" in signal:
        print(f"  {Fore.RED}Error  : {signal['error']}")
        return

    if bias == "NEUTRAL":
        print(f"  {Fore.YELLOW}No clear setup — timeframes not sufficiently aligned.")
        conf_text = signal.get("timeframe_confluence", "")
        if conf_text:
            print(f"\n  {Fore.WHITE}Analysis : {Fore.LIGHTWHITE_EX}{conf_text}")
        print()
        return

    # ── Entry ────────────────────────────────────────────────
    ez = signal.get("entry_zone", {})
    entry_from = ez.get("from", "?")
    entry_to = ez.get("to", "?")
    print(f"  {Fore.WHITE}Entry Zone : {Fore.GREEN}{entry_from}  ─  {entry_to}")
    print(f"  {Fore.WHITE}Entry Cond : {Fore.LIGHTWHITE_EX}{signal.get('entry_condition', 'N/A')}")

    # ── Risk ─────────────────────────────────────────────────
    sl = signal.get("stop_loss", "?")
    sl_pips = signal.get("sl_pips", "?")
    tp1 = signal.get("tp1", "?")
    tp2 = signal.get("tp2", "?")
    rr = signal.get("risk_reward", "N/A")

    print(f"  {Fore.WHITE}Stop Loss  : {Fore.RED}{sl}  ({sl_pips} pips)")
    print(f"  {Fore.WHITE}TP 1       : {Fore.GREEN}{tp1}")
    print(f"  {Fore.WHITE}TP 2       : {Fore.GREEN}{tp2}")
    print(f"  {Fore.WHITE}R : R      : {Fore.CYAN}{rr}")

    # ── Invalidation ─────────────────────────────────────────
    inv = signal.get("invalidation", "N/A")
    print(f"  {Fore.WHITE}Invalidate : {Fore.YELLOW}{inv}")

    # ── Timeframe confluence ──────────────────────────────────
    tfc = signal.get("timeframe_confluence", "")
    if tfc:
        print(f"\n  {Fore.WHITE}MTF View   : {Fore.LIGHTWHITE_EX}{tfc}")

    print()


def print_all_signals(signals: dict) -> None:
    print_header()
    for signal in signals.values():
        print_signal(signal)
    print(f"{Fore.CYAN}{_DLINE}\n")


def save_json_output(signals: dict, output_dir: str = ".") -> str:
    """Save signals to trading_signals_YYYY-MM-DD.json in output_dir."""
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    filename = Path(output_dir) / f"trading_signals_{date_str}.json"

    payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "signal_count": len(signals),
        "signals": signals,
    }

    with open(filename, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)

    print(f"{Fore.GREEN}  Saved : {filename}")
    return str(filename)
