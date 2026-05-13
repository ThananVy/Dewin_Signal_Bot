"""
claude_analyst.py — Single merged prompt for all 3 pairs via Groq.
Model returns: bias, entry, stop_loss, tp_rr, confidence, entry_condition, timeframe_confluence.
All derived values (sl_pips, tp, tp_pips, be, direction) are calculated in Python.
"""

import json
import os
import re

from groq import Groq
from dotenv import load_dotenv

load_dotenv()

PAIR_DISPLAY = {"EURUSD": "EURUSD", "USDJPY": "USDJPY", "GOLD": "XAUUSD (Gold)"}
PIP_MULTIPLIER = {"EURUSD": 10000, "USDJPY": 100, "GOLD": 10}


def _slim_pair_block(pair: str, summaries: dict) -> str:
    display = PAIR_DISPLAY.get(pair, pair)
    pip_mult = PIP_MULTIPLIER.get(pair, 10000)
    lines = [f"\n### {pair} [{display}]  (pip_mult={pip_mult}, JSON key: \"{pair}\")"]

    for tf in ["Daily", "1H", "15m"]:
        s = summaries.get(tf, {})
        if not s:
            continue
        sqz = s.get("squeeze", {})
        highs = s.get("swing_highs", [])[-3:]
        lows = s.get("swing_lows", [])[-3:]

        lines.append(
            f"[{tf}] Close={s.get('close')} | EMA={s.get('ema_alignment')} | "
            f"ST={s.get('supertrend_direction')} | ATR={s.get('atr')} | "
            f"SQZ={sqz.get('status')}/{sqz.get('momentum')} | "
            f"SwingHighs={[h['price'] for h in highs]} | SwingLows={[l['price'] for l in lows]}"
        )
    return "\n".join(lines)


def _get_ref_summary(summaries: dict) -> dict:
    """15m is the entry timeframe for scalping."""
    return summaries.get("15m") or summaries.get("1H") or summaries.get("Daily") or {}


def _recalculate_signal(raw: dict, pair: str, summaries: dict) -> dict:
    """
    Build the full signal dict from the model's simplified output.
    tp, be, sl_pips, tp_pips, direction all computed here — never trusted from the model.
    """
    bias = raw.get("bias", "NEUTRAL")

    if bias not in ("BULLISH", "BEARISH"):
        return {
            "pair": pair,
            "bias": "NEUTRAL",
            "direction": "NEUTRAL",
            "entry": 0,
            "stop_loss": 0,
            "sl_pips": 0,
            "be": 0,
            "tp": 0,
            "tp_pips": 0,
            "tp_rr": 0,
            "entry_condition": raw.get("entry_condition", "Timeframes not aligned"),
            "confidence": int(raw.get("confidence", 0) or 0),
            "timeframe_confluence": raw.get("timeframe_confluence", ""),
        }

    ref = _get_ref_summary(summaries)
    ref_close = float(ref.get("close") or 0)
    pip_mult = PIP_MULTIPLIER.get(pair, 10000)

    entry = float(raw.get("entry", 0) or 0)
    stop_loss = float(raw.get("stop_loss", 0) or 0)

    # Clamp tp_rr to allowed values: 1.5, 2.0, 3.0
    raw_rr = float(raw.get("tp_rr", 2.0) or 2.0)
    tp_rr = min([1.5, 2.0, 3.0], key=lambda x: abs(x - raw_rr))

    # Reject prices that are clearly wrong
    if ref_close > 0:
        if not (ref_close * 0.5 < entry < ref_close * 2.0) or \
           not (ref_close * 0.5 < stop_loss < ref_close * 2.0):
            return {
                "pair": pair,
                "bias": bias,
                "error": f"Invalid prices (entry={entry}, sl={stop_loss}, expected ~{ref_close})",
                "confidence": int(raw.get("confidence", 0) or 0),
            }
    elif entry <= 0 or stop_loss <= 0:
        return {
            "pair": pair,
            "bias": bias,
            "error": f"Missing prices (entry={entry}, sl={stop_loss})",
        }

    if bias == "BULLISH" and stop_loss >= entry:
        return {"pair": pair, "bias": bias, "error": f"BULLISH but SL {stop_loss} >= entry {entry}"}
    if bias == "BEARISH" and stop_loss <= entry:
        return {"pair": pair, "bias": bias, "error": f"BEARISH but SL {stop_loss} <= entry {entry}"}

    sl_dist = abs(entry - stop_loss)
    sl_pips = round(sl_dist * pip_mult)
    tp_pips = round(sl_dist * tp_rr * pip_mult)

    if bias == "BULLISH":
        tp = round(entry + sl_dist * tp_rr, 5)
    else:
        tp = round(entry - sl_dist * tp_rr, 5)

    return {
        "pair": pair,
        "bias": bias,
        "direction": "LONG" if bias == "BULLISH" else "SHORT",
        "entry": round(entry, 5),
        "stop_loss": round(stop_loss, 5),
        "sl_pips": sl_pips,
        "be": round(entry, 5),   # break even = move SL to entry after +1R profit
        "tp": tp,
        "tp_pips": tp_pips,
        "tp_rr": tp_rr,
        "entry_condition": raw.get("entry_condition", ""),
        "confidence": int(raw.get("confidence", 0) or 0),
        "timeframe_confluence": raw.get("timeframe_confluence", ""),
    }


def build_merged_prompt(all_indicators: dict) -> str:
    pair_blocks = "\n".join(
        _slim_pair_block(pair, summaries)
        for pair, summaries in all_indicators.items()
    )

    pairs_list = list(all_indicators.keys())

    ex_pair = pairs_list[0]
    ex_ref = all_indicators[ex_pair].get("15m") or _get_ref_summary(all_indicators[ex_pair])
    ex_close = float(ex_ref.get("close") or 1.0)
    ex_atr = float(ex_ref.get("atr") or ex_close * 0.001)
    ex_sl = round(ex_close - ex_atr * 1.2, 5)

    pairs_str = ", ".join(pairs_list)

    return f"""You are a professional forex scalping analyst.

RULES:
1. Daily ST = overall bias. 1H ST = trend filter. 15m ST = entry signal. All three must agree.
2. All agree → BULLISH or BEARISH. Any conflict → NEUTRAL (entry=0, stop_loss=0).
3. entry = 15m Close (current market price — never use a distant swing level).
4. stop_loss: BULLISH → entry minus 1.2×ATR(15m). BEARISH → entry plus 1.2×ATR(15m).
5. tp_rr: choose 1.5 (choppy/TP near key level), 2.0 (normal), or 3.0 (strong momentum, clear path).
6. confidence = your estimated % probability that price reaches TP before SL. Be realistic.
   85-100: all TFs aligned + squeeze firing + no key level blocking TP.
   65-84: aligned but no squeeze, or minor level near TP.
   Below 65: → use NEUTRAL instead.
7. Prices must match 15m Close scale (e.g. {ex_close}).

MARKET DATA:
{pair_blocks}

Return ONLY valid JSON. Example if {ex_pair} is BULLISH at 15m close {ex_close} (ATR {ex_atr:.5f}):
{{"{ex_pair}":{{"pair":"{ex_pair}","bias":"BULLISH","entry":{ex_close},"stop_loss":{ex_sl},"tp_rr":2.0,"confidence":82,"entry_condition":"15m ST bullish, squeeze just fired","timeframe_confluence":"Daily+1H+15m ST all bullish"}}}}

Return JSON for all {len(pairs_list)} pairs ({pairs_str}):"""


def _call_groq(prompt: str) -> tuple[dict | None, str | None]:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None, "GROQ_API_KEY not set in .env"

    model = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

    try:
        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=900,
            temperature=0.1,
        )
        raw = response.choices[0].message.content.strip()
    except Exception as exc:
        return None, f"Groq API call failed: {exc}"

    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return None, f"No JSON found.\n--- RAW ---\n{raw}"

    try:
        return json.loads(match.group()), None
    except json.JSONDecodeError as exc:
        return None, f"JSON parse error: {exc}\n--- RAW ---\n{raw}"


def analyze_all_pairs(all_indicators: dict, focus_timeframe: str | None = None) -> dict:
    """One Groq call for all pairs. Prices validated and all derived fields computed in Python."""
    print(f"  Sending 1 merged prompt for {len(all_indicators)} pairs to Groq...")

    prompt = build_merged_prompt(all_indicators)
    result, error = _call_groq(prompt)

    if error:
        print(f"    ERROR: {error}")
        return {pair: {"pair": pair, "error": error} for pair in all_indicators}

    KEY_ALIASES = {
        "XAUUSD": "GOLD", "XAUUSD (Gold)": "GOLD", "XAU/USD": "GOLD", "XAU": "GOLD",
        "EUR/USD": "EURUSD", "USD/JPY": "USDJPY",
    }
    result = {KEY_ALIASES.get(k, k): v for k, v in result.items()}

    signals = {}
    for pair in all_indicators:
        raw = result.get(pair)
        if raw is None:
            signals[pair] = {"pair": pair, "error": "Missing from Groq response"}
        else:
            sig = _recalculate_signal(raw, pair, all_indicators[pair])
            if "error" in sig:
                print(f"    WARN {pair}: {sig['error']}")
            signals[pair] = sig

    return signals
