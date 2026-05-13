"""
claude_analyst.py — Builds structured prompts and calls Google Gemini API.
Uses gemini-2.0-flash (free tier: 1,500 requests/day, 1M tokens/day).

Free API key: https://aistudio.google.com  (no credit card required)
"""

import json
import os
import re

import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

PAIR_DISPLAY = {
    "EURUSD": "EURUSD",
    "USDJPY": "USDJPY",
    "GOLD": "XAUUSD (Gold)",
}

PIP_MULTIPLIER = {
    "EURUSD": 10000,
    "USDJPY": 100,
    "GOLD": 10,
}


def _fmt(val, fallback: str = "N/A") -> str:
    return str(val) if val is not None else fallback


def _squeeze_str(sq: dict) -> str:
    return f"Status={sq.get('status', 'UNKNOWN')} | Momentum={sq.get('momentum', 'N/A')}"


def _tf_block(label: str, s: dict, include_ema200: bool = True) -> str:
    lines = [
        f"### {label}",
        f"- Close: {_fmt(s.get('close'))}",
        f"- EMA 20: {_fmt(s.get('ema_20'))}  |  EMA 50: {_fmt(s.get('ema_50'))}  |  EMA 200: {_fmt(s.get('ema_200'))}",
    ]
    if include_ema200:
        above = s.get("above_ema200")
        lines.append(f"- Price vs EMA 200: {'ABOVE' if above else 'BELOW' if above is not None else 'N/A'}")
    lines += [
        f"- EMA Alignment: {_fmt(s.get('ema_alignment'))}",
        f"- SuperTrend: {_fmt(s.get('supertrend_direction'))}",
        f"- ATR(14): {_fmt(s.get('atr'))}",
        f"- Squeeze: {_squeeze_str(s.get('squeeze', {}))}",
        f"- Volume: {_fmt(s.get('volume'))}  |  Avg(20): {_fmt(s.get('volume_avg'))}  |  Ratio: {_fmt(s.get('volume_vs_avg'))}x",
        f"- Swing Highs (last 5): {json.dumps(s.get('swing_highs', []))}",
        f"- Swing Lows  (last 5): {json.dumps(s.get('swing_lows', []))}",
    ]
    return "\n".join(lines)


def build_prompt_full(pair: str, daily: dict, h4: dict, h1: dict) -> str:
    display = PAIR_DISPLAY.get(pair, pair)
    pip_mult = PIP_MULTIPLIER.get(pair, 10000)
    current_price = h1.get("close") or h4.get("close") or daily.get("close")

    return f"""You are a professional multi-timeframe technical analyst. Analyze {display} and return a precise trading signal.

## MARKET DATA — {display}
Current Price: {current_price}

---
{_tf_block("DAILY (Trend Bias)", daily, include_ema200=True)}

---
{_tf_block("4H (Trade Structure)", h4, include_ema200=True)}

---
{_tf_block("1H (Entry Timing)", h1, include_ema200=False)}

---
## ANALYSIS RULES
1. Determine bias by checking Daily → 4H → 1H alignment.
2. If all three timeframes do NOT agree on direction, set "bias": "NEUTRAL" and leave entry/SL/TP as 0.0.
3. When aligned:
   - Entry zone: use last swing levels as support/resistance.
   - Stop Loss: beyond the last significant opposing swing + 0.5× 4H ATR buffer.
   - TP1: first major swing/structure target (minimum 1:1 R:R).
   - TP2: second major target (minimum 1:2 R:R).
   - sl_pips: SL distance × {pip_mult} (pip multiplier for {display}).
4. risk_reward format: "1:X.X" based on TP1 distance / SL distance.
5. Use exact price levels from the swing data above.

## RESPONSE FORMAT
Return ONLY a valid JSON object — no markdown fences, no extra text:
{{
  "pair": "{pair}",
  "bias": "BULLISH",
  "entry_zone": {{"from": 0.0000, "to": 0.0000}},
  "entry_condition": "specific trigger required before entry",
  "stop_loss": 0.0000,
  "sl_pips": 0,
  "tp1": 0.0000,
  "tp2": 0.0000,
  "risk_reward": "1:0.0",
  "invalidation": "specific condition that cancels this setup",
  "confidence": 75,
  "timeframe_confluence": "one sentence explaining how Daily+4H+1H align or conflict"
}}
Note: confidence is an integer 0-100. Use 80-100 for strong multi-TF alignment, 60-79 for moderate, below 60 for weak/uncertain."""


def build_prompt_single_tf(pair: str, summary: dict, timeframe: str) -> str:
    display = PAIR_DISPLAY.get(pair, pair)
    pip_mult = PIP_MULTIPLIER.get(pair, 10000)

    return f"""You are a professional technical analyst. Analyze {display} on the {timeframe} timeframe only.

## MARKET DATA — {display} ({timeframe})
{_tf_block(timeframe, summary, include_ema200=True)}

---
## ANALYSIS RULES
1. Assess bias (BULLISH/BEARISH/NEUTRAL) from this single timeframe.
2. If no clear directional setup, set "bias": "NEUTRAL" and leave entry/SL/TP as 0.0.
3. When directional:
   - Entry zone based on swing levels shown above.
   - SL beyond last opposing swing + 0.5× ATR buffer.
   - TP1 at first swing target (1:1 minimum), TP2 at second target (1:2 minimum).
   - sl_pips: SL distance × {pip_mult}.

## RESPONSE FORMAT
Return ONLY a valid JSON object:
{{
  "pair": "{pair}",
  "bias": "BULLISH",
  "entry_zone": {{"from": 0.0000, "to": 0.0000}},
  "entry_condition": "specific trigger",
  "stop_loss": 0.0000,
  "sl_pips": 0,
  "tp1": 0.0000,
  "tp2": 0.0000,
  "risk_reward": "1:0.0",
  "invalidation": "specific condition that cancels this setup",
  "confidence": 75,
  "timeframe_confluence": "{timeframe} standalone — no multi-TF confluence"
}}
Note: confidence is an integer 0-100. Use 80-100 for strong setup, 60-79 for moderate, below 60 for weak."""


def _call_gemini(prompt: str) -> tuple[dict | None, str | None]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None, "GEMINI_API_KEY not set in .env  (free key at https://aistudio.google.com)"

    model_name = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name=model_name,
            generation_config=genai.types.GenerationConfig(
                temperature=0.2,
                max_output_tokens=1024,
            ),
        )
        response = model.generate_content(prompt)
        raw = response.text.strip()
    except Exception as exc:
        return None, f"Gemini API call failed: {exc}"

    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return None, f"No JSON found in response.\n--- RAW ---\n{raw}"

    try:
        return json.loads(match.group()), None
    except json.JSONDecodeError as exc:
        return None, f"JSON parse error: {exc}\n--- RAW ---\n{raw}"


def analyze_pair(
    pair: str,
    all_summaries: dict,
    focus_timeframe: str | None = None,
) -> tuple[dict | None, str | None]:
    if focus_timeframe:
        summary = all_summaries.get(focus_timeframe)
        if not summary:
            return None, f"No data for timeframe '{focus_timeframe}'"
        prompt = build_prompt_single_tf(pair, summary, focus_timeframe)
    else:
        daily = all_summaries.get("Daily", {})
        h4 = all_summaries.get("4H", {})
        h1 = all_summaries.get("1H", {})
        if not (daily and h4 and h1):
            return None, "Missing one or more timeframes (Daily/4H/1H)"
        prompt = build_prompt_full(pair, daily, h4, h1)

    return _call_gemini(prompt)


def analyze_all_pairs(
    all_indicators: dict,
    focus_timeframe: str | None = None,
) -> dict:
    results: dict = {}

    for pair, tf_summaries in all_indicators.items():
        display = PAIR_DISPLAY.get(pair, pair)
        tf_label = f" [{focus_timeframe}]" if focus_timeframe else " [MTF]"
        print(f"  Analysing {display}{tf_label} with Gemini 2.0 Flash...")

        signal, error = analyze_pair(pair, tf_summaries, focus_timeframe)

        if error:
            print(f"    ERROR: {error}")
            results[pair] = {"pair": pair, "error": error}
        else:
            results[pair] = signal

    return results
