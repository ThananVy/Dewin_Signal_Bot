"""
claude_analyst.py — Single merged prompt for all 3 pairs via Groq.
Model returns only: bias, entry, stop_loss, confidence + text fields.
All derived values (entry_zone, sl_pips, tp1, tp2, r:r) are calculated in Python.
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
    """Compact data block for one pair across 3 timeframes."""
    display = PAIR_DISPLAY.get(pair, pair)
    pip_mult = PIP_MULTIPLIER.get(pair, 10000)
    # Use the JSON key name as the header so the model knows which key to use in its response
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
    """15m is the entry timeframe for scalping — use its ATR/close for SL zone calculation."""
    return summaries.get("15m") or summaries.get("1H") or summaries.get("Daily") or {}


def _recalculate_signal(raw: dict, pair: str, summaries: dict) -> dict:
    """
    Reconstruct the full signal dict from the model's simplified output.
    entry_zone, sl_pips, tp1, tp2, risk_reward are all computed here in Python.
    """
    bias = raw.get("bias", "NEUTRAL")

    # NEUTRAL — zero everything out
    if bias not in ("BULLISH", "BEARISH"):
        return {
            "pair": pair,
            "bias": "NEUTRAL",
            "entry_zone": {"from": 0, "to": 0},
            "entry_condition": raw.get("entry_condition", "Timeframes not aligned"),
            "stop_loss": 0,
            "sl_pips": 0,
            "tp1": 0,
            "tp2": 0,
            "risk_reward": "1:0",
            "invalidation": raw.get("invalidation", ""),
            "confidence": int(raw.get("confidence", 0) or 0),
            "timeframe_confluence": raw.get("timeframe_confluence", ""),
        }

    ref = _get_ref_summary(summaries)
    atr = float(ref.get("atr") or 0)
    ref_close = float(ref.get("close") or 0)
    pip_mult = PIP_MULTIPLIER.get(pair, 10000)

    entry = float(raw.get("entry", 0) or 0)
    stop_loss = float(raw.get("stop_loss", 0) or 0)

    # Reject if prices are clearly wrong (0, negative, or far from actual close)
    if ref_close > 0:
        min_price = ref_close * 0.5
        max_price = ref_close * 2.0
        if not (min_price < entry < max_price) or not (min_price < stop_loss < max_price):
            return {
                "pair": pair,
                "bias": bias,
                "error": (
                    f"Invalid prices from model (entry={entry}, sl={stop_loss}). "
                    f"Expected near {ref_close}."
                ),
                "confidence": int(raw.get("confidence", 0) or 0),
            }
    elif entry <= 0 or stop_loss <= 0:
        return {
            "pair": pair,
            "bias": bias,
            "error": f"Model returned zero/missing prices (entry={entry}, sl={stop_loss})",
        }

    # Validate directional logic
    if bias == "BULLISH" and stop_loss >= entry:
        return {"pair": pair, "bias": bias, "error": f"BULLISH but SL {stop_loss} >= entry {entry}"}
    if bias == "BEARISH" and stop_loss <= entry:
        return {"pair": pair, "bias": bias, "error": f"BEARISH but SL {stop_loss} <= entry {entry}"}

    sl_dist = abs(entry - stop_loss)

    # Entry zone: ±(0.2 × ATR) around entry — fallback to 0.1% of price if ATR missing
    zone_half = (atr * 0.2) if atr > 0 else (entry * 0.001)
    entry_from = round(entry - zone_half, 5)
    entry_to = round(entry + zone_half, 5)

    # TP1 = 1:1 R:R, TP2 = 1:2 R:R from entry
    if bias == "BULLISH":
        tp1 = round(entry + sl_dist, 5)
        tp2 = round(entry + 2 * sl_dist, 5)
    else:
        tp1 = round(entry - sl_dist, 5)
        tp2 = round(entry - 2 * sl_dist, 5)

    return {
        "pair": pair,
        "bias": bias,
        "entry_zone": {"from": entry_from, "to": entry_to},
        "entry_condition": raw.get("entry_condition", ""),
        "stop_loss": round(stop_loss, 5),
        "sl_pips": round(sl_dist * pip_mult),
        "tp1": tp1,
        "tp2": tp2,
        "risk_reward": "1:2.0",
        "invalidation": raw.get("invalidation", ""),
        "confidence": int(raw.get("confidence", 0) or 0),
        "timeframe_confluence": raw.get("timeframe_confluence", ""),
    }


def build_merged_prompt(all_indicators: dict) -> str:
    """
    Scalping prompt — entry at 15m current price, tight SL from 15m ATR.
    Model returns only: bias, entry, stop_loss, confidence + text fields.
    All derived values calculated in Python.
    """
    pair_blocks = "\n".join(
        _slim_pair_block(pair, summaries)
        for pair, summaries in all_indicators.items()
    )

    pairs_list = list(all_indicators.keys())

    # Build realistic example from first pair's 15m close (scalping entry = current price)
    ex_pair = pairs_list[0]
    ex_ref = all_indicators[ex_pair].get("15m") or _get_ref_summary(all_indicators[ex_pair])
    ex_close = float(ex_ref.get("close") or 1.0)
    ex_atr = float(ex_ref.get("atr") or ex_close * 0.001)
    ex_entry = round(ex_close, 5)
    ex_sl = round(ex_close - ex_atr * 1.2, 5)

    pairs_str = ", ".join(pairs_list)

    return f"""You are a professional forex scalping analyst. Generate SCALPING signals — enter near current price with tight stop loss.

RULES:
1. Daily SuperTrend = overall bias. 1H SuperTrend = trend filter. 15m SuperTrend = entry signal.
2. All three agree → BULLISH or BEARISH. Any conflict → NEUTRAL.
3. entry = 15m Close value (current market price — do NOT use distant swing levels).
4. stop_loss: BULLISH → entry minus 1.2×ATR(15m). BEARISH → entry plus 1.2×ATR(15m).
5. For NEUTRAL: entry=0, stop_loss=0.
6. Target SL size: EURUSD 5-15 pips, USDJPY 8-18 pips, GOLD 15-40 pips.
7. confidence: 85+ all TFs aligned with squeeze firing, 65-84 aligned no squeeze, below 65 skip.
8. All prices must match the 15m Close scale (e.g. {ex_close}).

MARKET DATA:
{pair_blocks}

Return ONLY valid JSON — no markdown. Example if {ex_pair} is BULLISH at 15m close {ex_close} (ATR {ex_atr:.5f}):
{{"{ex_pair}":{{"pair":"{ex_pair}","bias":"BULLISH","entry":{ex_entry},"stop_loss":{ex_sl},"confidence":82,"entry_condition":"15m ST bullish, squeeze firing — enter at market {ex_entry}","invalidation":"15m close below {ex_sl}","timeframe_confluence":"Daily+1H+15m ST all bullish"}}}}

Now return JSON for all {len(pairs_list)} pairs ({pairs_str}):"""


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
    """One Groq call for all pairs. Prices are validated and derived fields computed in Python."""
    print(f"  Sending 1 merged prompt for {len(all_indicators)} pairs to Groq...")

    prompt = build_merged_prompt(all_indicators)
    result, error = _call_groq(prompt)

    if error:
        print(f"    ERROR: {error}")
        return {pair: {"pair": pair, "error": error} for pair in all_indicators}

    # Normalize keys — the model may use "XAUUSD", "XAUUSD (Gold)", etc. instead of "GOLD"
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
