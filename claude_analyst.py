"""
claude_analyst.py — Single merged Groq prompt for all 3 pairs.

Strategy: Squeeze Momentum + SuperTrend stack (Daily → 1H → 15m)
  - Model assesses bias and entry trigger validity only (no prices).
  - Python places SL at nearest structural swing (15m), TP at nearest
    opposing swing (1H), calculates real R:R, and skips if R:R < MIN_RR.
"""

import json
import os
import re

from groq import Groq
from dotenv import load_dotenv

load_dotenv()

PAIR_DISPLAY = {"EURUSD": "EURUSD", "USDJPY": "USDJPY", "GOLD": "XAUUSD (Gold)"}
PIP_MULTIPLIER = {"EURUSD": 10000, "USDJPY": 100, "GOLD": 10}


# ─── Data block ───────────────────────────────────────────────

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
            f"SwingHighs={[h['price'] for h in highs]} | "
            f"SwingLows={[l['price'] for l in lows]}"
        )
    return "\n".join(lines)


# ─── Confidence scoring (rule-based, no AI) ───────────────────

def calculate_confidence(summaries: dict, bias: str) -> int:
    """
    Objective confidence score (0-100) from measurable indicator conditions.

    Scoring:
      40 pts — all 3 TFs SuperTrend aligned (foundation)
      30 pts — 15m squeeze fired (SQZ_OFF) with momentum in trade direction
      15 pts — 15m EMA fully stacked (FULL_BULLISH / FULL_BEARISH)
      15 pts — 15m momentum strong (not weakening)

    80+ requires squeeze to be firing. Without it the max is 70 → signal is skipped.
    """
    if bias not in ("BULLISH", "BEARISH"):
        return 0

    score = 0
    sqz_15m = summaries.get("15m", {}).get("squeeze", {})
    sqz_status = sqz_15m.get("status", "")
    sqz_mom = sqz_15m.get("momentum", "")
    ema_15m = summaries.get("15m", {}).get("ema_alignment", "")

    mom_matches = (
        (bias == "BULLISH" and sqz_mom in ("BULLISH", "BULLISH_WEAKENING")) or
        (bias == "BEARISH" and sqz_mom in ("BEARISH", "BEARISH_WEAKENING"))
    )

    # 1. SuperTrend alignment across all 3 TFs (40 pts)
    aligned = sum(
        1 for tf in ["Daily", "1H", "15m"]
        if summaries.get(tf, {}).get("supertrend_direction") == bias
    )
    if aligned == 3:
        score += 40
    elif aligned == 2:
        score += 15

    # 2. 15m Squeeze momentum firing in trade direction (30 pts)
    if sqz_status == "OFF" and mom_matches:
        score += 30
    elif sqz_status == "ON" and mom_matches:
        score += 10   # building, not fired yet
    elif sqz_status == "OFF" and not mom_matches:
        score += 5    # fired but momentum against us

    # 3. 15m EMA alignment (15 pts)
    full_aligned = f"FULL_{bias}"
    if ema_15m == full_aligned:
        score += 15
    elif "MIXED" in ema_15m:
        score += 5

    # 4. 15m Momentum strength — not weakening (15 pts)
    strong_mom = bias  # "BULLISH" or "BEARISH"
    if sqz_mom == strong_mom:
        score += 15
    elif mom_matches:   # weakening but still in right direction
        score += 7

    return min(score, 100)


# ─── Structural SL / TP from swing data ───────────────────────

def _structural_sl(summaries: dict, bias: str, entry: float) -> float | None:
    """
    SL at the nearest swing level that acts as structure, plus a small ATR buffer.
    Uses 15m swings — tightest relevant structure for scalping.
    LONG → highest swing low below entry.
    SHORT → lowest swing high above entry.
    """
    ref = summaries.get("15m") or {}
    atr = float(ref.get("atr") or 0)

    if bias == "BULLISH":
        candidates = [s["price"] for s in ref.get("swing_lows", []) if s["price"] < entry]
        if not candidates:
            return None
        level = max(candidates)
        return round(level - atr * 0.3, 5)   # just below the swing low
    else:
        candidates = [s["price"] for s in ref.get("swing_highs", []) if s["price"] > entry]
        if not candidates:
            return None
        level = min(candidates)
        return round(level + atr * 0.3, 5)   # just above the swing high


def _structural_tp(summaries: dict, bias: str, entry: float) -> float | None:
    """
    TP at the nearest opposing swing level on 1H (bigger structure = cleaner targets).
    A small buffer keeps TP slightly inside the level so it actually fills.
    LONG → nearest swing high above entry (set TP slightly below it).
    SHORT → nearest swing low below entry (set TP slightly above it).
    """
    ref = summaries.get("1H") or summaries.get("15m") or {}
    atr = float(ref.get("atr") or 0)

    if bias == "BULLISH":
        candidates = [s["price"] for s in ref.get("swing_highs", []) if s["price"] > entry]
        if not candidates:
            return None
        level = min(candidates)
        return round(level - atr * 0.15, 5)  # slightly below resistance
    else:
        candidates = [s["price"] for s in ref.get("swing_lows", []) if s["price"] < entry]
        if not candidates:
            return None
        level = max(candidates)
        return round(level + atr * 0.15, 5)  # slightly above support


# ─── Signal reconstruction ────────────────────────────────────

def _recalculate_signal(raw: dict, pair: str, summaries: dict) -> dict:
    """
    Build the full signal from the model's bias assessment.
    All prices computed from market structure — model touches no numbers.
    """
    bias = raw.get("bias", "NEUTRAL")

    # Confidence is always computed from indicators — never trusted from the model
    conf = calculate_confidence(summaries, bias)

    _neutral = lambda reason="": {
        "pair": pair, "bias": "NEUTRAL", "direction": "NEUTRAL",
        "entry": 0, "stop_loss": 0, "sl_pips": 0,
        "be": 0, "tp": 0, "tp_pips": 0, "tp_rr": 0,
        "entry_condition": raw.get("entry_condition", reason or "No valid setup"),
        "confidence": conf,
        "timeframe_confluence": raw.get("timeframe_confluence", ""),
    }

    if bias not in ("BULLISH", "BEARISH"):
        return _neutral()

    ref_15m = summaries.get("15m") or {}
    atr_15m = float(ref_15m.get("atr") or 0)
    ref_close = float(ref_15m.get("close") or 0)
    pip_mult = PIP_MULTIPLIER.get(pair, 10000)

    if ref_close <= 0:
        return _neutral("No 15m price data")

    entry = ref_close  # scalping: enter at current market price

    # ── Structural SL ──────────────────────────────────────────
    stop_loss = _structural_sl(summaries, bias, entry)
    if stop_loss is None or (bias == "BULLISH" and stop_loss >= entry) or \
                            (bias == "BEARISH" and stop_loss <= entry):
        # Fallback: 1.5×ATR if no valid structural level found
        stop_loss = round(
            entry - atr_15m * 1.5 if bias == "BULLISH" else entry + atr_15m * 1.5, 5
        )

    sl_dist = abs(entry - stop_loss)
    if sl_dist == 0:
        return _neutral("Zero SL distance")
    sl_pips = round(sl_dist * pip_mult)

    # ── Structural TP ──────────────────────────────────────────
    tp = _structural_tp(summaries, bias, entry)
    if tp is None or (bias == "BULLISH" and tp <= entry) or \
                     (bias == "BEARISH" and tp >= entry):
        # Fallback: 2×SL distance
        tp = round(
            entry + sl_dist * 2 if bias == "BULLISH" else entry - sl_dist * 2, 5
        )

    tp_dist = abs(tp - entry)
    tp_pips = round(tp_dist * pip_mult)
    tp_rr = round(tp_dist / sl_dist, 2)

    # ── R:R gate — skip if not worth the trade ─────────────────
    min_rr = float(os.getenv("MIN_RR", "1.5"))
    if tp_rr < min_rr:
        return _neutral(
            f"R:R {tp_rr} below minimum {min_rr} "
            f"(SL {sl_pips} pips, TP {tp_pips} pips — TP too close to structure)"
        )

    # ── BE trigger price (1×SL distance from entry) ────────────
    be = round(entry + sl_dist if bias == "BULLISH" else entry - sl_dist, 5)

    return {
        "pair": pair,
        "bias": bias,
        "direction": "LONG" if bias == "BULLISH" else "SHORT",
        "entry": round(entry, 5),
        "stop_loss": round(stop_loss, 5),
        "sl_pips": sl_pips,
        "be": be,
        "tp": tp,
        "tp_pips": tp_pips,
        "tp_rr": tp_rr,
        "entry_condition": raw.get("entry_condition", ""),
        "confidence": conf,
        "timeframe_confluence": raw.get("timeframe_confluence", ""),
    }


# ─── Prompt ───────────────────────────────────────────────────

def build_merged_prompt(all_indicators: dict) -> str:
    pair_blocks = "\n".join(
        _slim_pair_block(pair, summaries)
        for pair, summaries in all_indicators.items()
    )
    pairs_list = list(all_indicators.keys())
    pairs_str = ", ".join(pairs_list)

    return f"""You are a professional forex scalping analyst. Assess the market for each pair.

STRATEGY: Squeeze Momentum breakout confirmed by SuperTrend stack (Daily → 1H → 15m).

BIAS RULES — both conditions must be met for BULLISH or BEARISH:
  1. All three timeframes (Daily, 1H, 15m) SuperTrend point in the SAME direction.
  2. At least one entry trigger is present:
     a) 15m Squeeze just fired: SQZ status is OFF and momentum matches bias.
     b) 15m SuperTrend flipped while 1H + Daily already agree.
  If either condition is missing → NEUTRAL.

MARKET DATA:
{pair_blocks}

Return ONLY valid JSON — no markdown, no prices. Fields: pair, bias, entry_condition, timeframe_confluence. Example:
{{"EURUSD":{{"pair":"EURUSD","bias":"BEARISH","entry_condition":"15m squeeze fired bearish, all STs aligned","timeframe_confluence":"Daily+1H+15m ST all bearish, EMA stacked"}}}}

Return JSON for all {len(pairs_list)} pairs ({pairs_str}):"""


# ─── Groq call ────────────────────────────────────────────────

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
            max_tokens=600,
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


# ─── Public API ───────────────────────────────────────────────

def analyze_all_pairs(all_indicators: dict, focus_timeframe: str | None = None) -> dict:
    """
    One Groq call for all pairs.
    Model assesses bias only. Python handles all price placement and R:R validation.
    """
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
            # Log skips so you can see why a pair was passed over
            if sig.get("bias") == "NEUTRAL" and raw.get("bias") in ("BULLISH", "BEARISH"):
                print(f"    SKIP {pair}: {sig.get('entry_condition', '')}")
            elif "error" in sig:
                print(f"    WARN {pair}: {sig['error']}")
            signals[pair] = sig

    return signals
