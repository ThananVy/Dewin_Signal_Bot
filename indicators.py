"""
indicators.py — All technical indicators implemented with pure pandas / numpy.
No pandas-ta or numba dependency — works on any Python version.

Indicators:
  - EMA 20, 50, 200
  - ATR(14)  using Wilder's RMA
  - SuperTrend (ATR period=10, multiplier=3)
  - Squeeze Momentum (BB 20 / KC 20, TTM-style)
  - Volume average (20-period SMA)
  - Swing Highs / Swing Lows (last 5)
"""

import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────────────────────
# Low-level primitives
# ─────────────────────────────────────────────────────────────

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing — identical to EMA with alpha=1/period."""
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


def _true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr


def _atr_series(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    return _rma(_true_range(high, low, close), period)


def _linreg_rolling(series: pd.Series, length: int) -> pd.Series:
    """
    Rolling linear regression — returns the last fitted value at each bar.
    Used for Squeeze Momentum histogram (TTM-style).
    """
    values = series.values.astype(float)
    n = len(values)
    out = np.full(n, np.nan)

    x = np.arange(length, dtype=float)
    x_mean = x.mean()
    ss_xx = float(np.sum((x - x_mean) ** 2))

    for i in range(length - 1, n):
        y = values[i - length + 1 : i + 1]
        if np.any(np.isnan(y)):
            continue
        y_mean = float(y.mean())
        ss_xy = float(np.sum((x - x_mean) * (y - y_mean)))
        slope = ss_xy / ss_xx
        intercept = y_mean - slope * x_mean
        out[i] = slope * (length - 1) + intercept

    return pd.Series(out, index=series.index)


# ─────────────────────────────────────────────────────────────
# SuperTrend
# ─────────────────────────────────────────────────────────────

def _supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 10,
    multiplier: float = 3.0,
) -> tuple[pd.Series, pd.Series]:
    """
    Returns (supertrend_line, direction).
    direction: +1 = bullish (price above line), -1 = bearish.
    """
    atr = _atr_series(high, low, close, period).values
    hl2 = ((high + low) / 2).values
    c = close.values
    n = len(c)

    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr

    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    st = np.full(n, np.nan)
    direction = np.full(n, np.nan)

    for i in range(1, n):
        if np.isnan(atr[i]):
            continue

        # ── Final upper band ──────────────────────────────────
        prev_fu = final_upper[i - 1] if not np.isnan(final_upper[i - 1]) else basic_upper[i]
        final_upper[i] = basic_upper[i] if (basic_upper[i] < prev_fu or c[i - 1] > prev_fu) else prev_fu

        # ── Final lower band ──────────────────────────────────
        prev_fl = final_lower[i - 1] if not np.isnan(final_lower[i - 1]) else basic_lower[i]
        final_lower[i] = basic_lower[i] if (basic_lower[i] > prev_fl or c[i - 1] < prev_fl) else prev_fl

        # ── Direction ─────────────────────────────────────────
        if np.isnan(st[i - 1]):
            # Initialise
            if c[i] <= final_upper[i]:
                st[i], direction[i] = final_upper[i], -1
            else:
                st[i], direction[i] = final_lower[i], 1
        elif st[i - 1] == final_upper[i - 1]:
            if c[i] <= final_upper[i]:
                st[i], direction[i] = final_upper[i], -1
            else:
                st[i], direction[i] = final_lower[i], 1
        else:
            if c[i] >= final_lower[i]:
                st[i], direction[i] = final_lower[i], 1
            else:
                st[i], direction[i] = final_upper[i], -1

    return pd.Series(st, index=close.index), pd.Series(direction, index=close.index)


# ─────────────────────────────────────────────────────────────
# Squeeze Momentum  (TTM / LazyBear style)
# ─────────────────────────────────────────────────────────────

def _squeeze_momentum(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    bb_length: int = 20,
    kc_length: int = 20,
    bb_mult: float = 2.0,
    kc_mult: float = 1.5,
    mom_length: int = 12,
) -> dict:
    """
    Returns dict with keys:
      momentum  pd.Series  — histogram values (positive = bullish, negative = bearish)
      sqz_on    pd.Series  — bool, BB inside KC
      sqz_off   pd.Series  — bool, BB outside KC
      no_sqz    pd.Series  — bool, neither
    """
    # Bollinger Bands
    bb_basis = close.rolling(bb_length).mean()
    bb_std = close.rolling(bb_length).std(ddof=0)
    bb_upper = bb_basis + bb_mult * bb_std
    bb_lower = bb_basis - bb_mult * bb_std

    # Keltner Channels
    kc_mid = _ema(close, kc_length)
    atr_kc = _atr_series(high, low, close, kc_length)
    kc_upper = kc_mid + kc_mult * atr_kc
    kc_lower = kc_mid - kc_mult * atr_kc

    # Squeeze states
    sqz_on = (bb_lower > kc_lower) & (bb_upper < kc_upper)
    sqz_off = (bb_lower < kc_lower) & (bb_upper > kc_upper)
    no_sqz = ~sqz_on & ~sqz_off

    # Momentum histogram (delta from midpoint, then linear-reg smoothed)
    highest_high = high.rolling(kc_length).max()
    lowest_low = low.rolling(kc_length).min()
    mid = (highest_high + lowest_low) / 2
    delta = close - (mid + kc_mid) / 2
    momentum = _linreg_rolling(delta, mom_length)

    return {
        "momentum": momentum,
        "sqz_on": sqz_on,
        "sqz_off": sqz_off,
        "no_sqz": no_sqz,
    }


# ─────────────────────────────────────────────────────────────
# Swing high / low detection
# ─────────────────────────────────────────────────────────────

def _find_swings(series: pd.Series, mode: str, lookback: int = 3, top_n: int = 5) -> list[dict]:
    """
    mode='high' → swing highs, mode='low' → swing lows.
    A pivot requires `lookback` bars on each side strictly higher/lower.
    """
    values = series.values
    idx = series.index
    swings: list[dict] = []
    compare = (lambda a, b: a > b) if mode == "high" else (lambda a, b: a < b)

    for i in range(lookback, len(values) - lookback):
        segment = values[i - lookback : i + lookback + 1]
        if np.any(np.isnan(segment)):
            continue
        pivot = values[i]
        if all(compare(pivot, values[j]) for j in range(i - lookback, i + lookback + 1) if j != i):
            swings.append({"date": str(idx[i])[:10], "price": round(float(pivot), 5)})

    return swings[-top_n:]


# ─────────────────────────────────────────────────────────────
# Helpers for reading computed columns
# ─────────────────────────────────────────────────────────────

def _st_direction(df: pd.DataFrame) -> str:
    if "ST_direction" in df.columns:
        series = df["ST_direction"].dropna()
        if not series.empty:
            return "BULLISH" if float(series.iloc[-1]) > 0 else "BEARISH"
    return "UNKNOWN"


def _sqz_status(df: pd.DataFrame) -> dict:
    status = "UNKNOWN"
    momentum_dir = None

    if "SQZ_ON" in df.columns:
        recent = df[["SQZ_ON", "SQZ_OFF", "SQZ_NO"]].dropna()
        if not recent.empty:
            last = recent.iloc[-1]
            if last["SQZ_ON"]:
                status = "ON"
            elif last["SQZ_OFF"]:
                status = "OFF"
            else:
                status = "NO_SQUEEZE"

    if "SQZ_MOM" in df.columns:
        mom = df["SQZ_MOM"].dropna()
        if len(mom) >= 2:
            last_v = float(mom.iloc[-1])
            prev_v = float(mom.iloc[-2])
            if last_v > 0:
                momentum_dir = "BULLISH" if last_v >= prev_v else "BULLISH_WEAKENING"
            elif last_v < 0:
                momentum_dir = "BEARISH" if last_v <= prev_v else "BEARISH_WEAKENING"
            else:
                momentum_dir = "FLAT"

    return {"status": status, "momentum": momentum_dir}


def _ema_alignment(close: float, e20, e50, e200) -> str:
    if any(v is None or np.isnan(v) for v in [e20, e50, e200]):
        return "INSUFFICIENT_DATA"
    if close > e20 > e50 > e200:
        return "FULL_BULLISH"
    if close < e20 < e50 < e200:
        return "FULL_BEARISH"
    return "BULLISH_MIXED" if close > e200 else "BEARISH_MIXED"


def _safe(val, decimals: int = 5):
    try:
        f = float(val)
        return round(f, decimals) if not np.isnan(f) else None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────

def calculate_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Attach all indicator columns to a copy of df.
    Requires at least 50 rows.
    """
    if df is None or df.empty or len(df) < 50:
        return df

    df = df.copy()
    high, low, close = df["High"], df["Low"], df["Close"]

    # ── EMAs ─────────────────────────────────────────────────
    df["EMA_20"] = _ema(close, 20)
    df["EMA_50"] = _ema(close, 50)
    df["EMA_200"] = _ema(close, 200)

    # ── ATR(14) ───────────────────────────────────────────────
    df["ATR"] = _atr_series(high, low, close, 14)

    # ── SuperTrend(10, 3) ─────────────────────────────────────
    st_line, st_dir = _supertrend(high, low, close, period=10, multiplier=3.0)
    df["ST_line"] = st_line
    df["ST_direction"] = st_dir

    # ── Squeeze Momentum ─────────────────────────────────────
    sqz = _squeeze_momentum(high, low, close, bb_length=20, kc_length=20)
    df["SQZ_MOM"] = sqz["momentum"]
    df["SQZ_ON"] = sqz["sqz_on"]
    df["SQZ_OFF"] = sqz["sqz_off"]
    df["SQZ_NO"] = sqz["no_sqz"]

    # ── Volume average(20) ────────────────────────────────────
    df["Volume_Avg_20"] = df["Volume"].rolling(20, min_periods=1).mean()

    return df


def extract_indicator_summary(df: pd.DataFrame, timeframe: str = "") -> dict:
    """Build a flat summary dict from the most recent bar."""
    if df is None or df.empty:
        return {}

    last = df.iloc[-1]
    close = float(last["Close"])

    e20 = _safe(last.get("EMA_20"))
    e50 = _safe(last.get("EMA_50"))
    e200 = _safe(last.get("EMA_200"))
    atr = _safe(last.get("ATR"))
    vol = _safe(last.get("Volume", 0.0), 2)
    vol_avg = _safe(last.get("Volume_Avg_20", 0.0), 2)
    above_ema200 = (close > e200) if e200 is not None else None

    return {
        "timeframe": timeframe,
        "close": round(close, 5),
        "ema_20": e20,
        "ema_50": e50,
        "ema_200": e200,
        "ema_alignment": _ema_alignment(close, e20, e50, e200),
        "above_ema200": above_ema200,
        "atr": atr,
        "supertrend_direction": _st_direction(df),
        "squeeze": _sqz_status(df),
        "volume": vol,
        "volume_avg": vol_avg,
        "volume_vs_avg": round(vol / vol_avg, 2) if vol_avg and vol_avg > 0 else None,
        "swing_highs": _find_swings(df["High"], mode="high"),
        "swing_lows": _find_swings(df["Low"], mode="low"),
    }
