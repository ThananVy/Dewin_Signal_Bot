"""
data_fetcher.py — Fetches OHLCV data from Yahoo Finance via yfinance.
Supports Daily (1y), 4H (resampled from 1H, 6mo), and 1H (3mo) timeframes.
"""

import time
import warnings
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

PAIRS = {
    "EURUSD": "EURUSD=X",
    "USDJPY": "JPY=X",
    "GOLD": "GC=F",
}

PAIR_DISPLAY = {
    "EURUSD": "EURUSD",
    "USDJPY": "USDJPY",
    "GOLD": "XAUUSD (Gold)",
}


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Strip timezone, drop nulls, keep OHLCV columns, remove zero closes."""
    df = df.copy()

    if df.index.tz is not None:
        df.index = df.index.tz_convert("UTC").tz_localize(None)

    df.index = pd.to_datetime(df.index)

    wanted = ["Open", "High", "Low", "Close", "Volume"]
    df = df[[c for c in wanted if c in df.columns]]
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df = df[df["Close"] > 0]
    df = df.sort_index()

    if "Volume" not in df.columns:
        df["Volume"] = 0.0

    df["Volume"] = df["Volume"].fillna(0.0)
    return df


def _resample_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Resample 1H OHLCV data to 4H bars."""
    df_4h = df_1h.resample("4h").agg(
        {
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum",
        }
    )
    df_4h = df_4h.dropna(subset=["Open", "High", "Low", "Close"])
    df_4h = df_4h[df_4h["Close"] > 0]
    return df_4h


def _fetch_once(ticker_symbol: str, period: str, interval: str) -> pd.DataFrame:
    """Single yfinance download attempt."""
    tkr = yf.Ticker(ticker_symbol)
    df = tkr.history(period=period, interval=interval, auto_adjust=True)
    return df


def fetch_pair_data(pair_name: str, retry: bool = True) -> dict | None:
    """
    Fetch Daily, 4H, and 1H data for a pair.
    Returns {timeframe: DataFrame} or None on failure.
    """
    if pair_name not in PAIRS:
        print(f"  WARNING: Unknown pair '{pair_name}'. Valid: {list(PAIRS.keys())}")
        return None

    symbol = PAIRS[pair_name]

    def attempt():
        # --- Daily (1 year) ---
        daily_raw = _fetch_once(symbol, "1y", "1d")
        if daily_raw.empty:
            raise ValueError(f"Empty daily data for {symbol}")
        daily = _clean(daily_raw)

        # --- 1H source data (6 months covers both 4H and 1H needs) ---
        h1_raw = _fetch_once(symbol, "6mo", "1h")
        if h1_raw.empty:
            raise ValueError(f"Empty 1H data for {symbol}")
        h1_6mo = _clean(h1_raw)

        # --- 4H: resample 6mo of 1H ---
        h4 = _resample_4h(h1_6mo)

        # --- 1H: last 3 months slice ---
        cutoff = datetime.utcnow() - timedelta(days=90)
        h1 = h1_6mo[h1_6mo.index >= cutoff].copy()

        if len(daily) < 50:
            raise ValueError(f"Insufficient daily rows ({len(daily)}) for {symbol}")
        if len(h4) < 30:
            raise ValueError(f"Insufficient 4H rows ({len(h4)}) for {symbol}")
        if len(h1) < 50:
            raise ValueError(f"Insufficient 1H rows ({len(h1)}) for {symbol}")

        return {"Daily": daily, "4H": h4, "1H": h1}

    try:
        return attempt()
    except Exception as exc:
        if retry:
            print(f"  Retrying {pair_name} in 3s... ({exc})")
            time.sleep(3)
            try:
                return attempt()
            except Exception as exc2:
                print(f"  WARNING: Skipping {pair_name} — {exc2}")
                return None
        print(f"  WARNING: Skipping {pair_name} — {exc}")
        return None


def fetch_all_pairs(pairs_filter: list[str] | None = None) -> dict:
    """
    Fetch data for all pairs (or a subset).
    Returns {pair_name: {timeframe: DataFrame}}.
    """
    targets = pairs_filter if pairs_filter else list(PAIRS.keys())
    all_data: dict = {}

    for pair in targets:
        print(f"  Fetching {PAIR_DISPLAY.get(pair, pair)}...")
        result = fetch_pair_data(pair)
        if result:
            all_data[pair] = result
            rows = {tf: len(df) for tf, df in result.items()}
            print(f"    OK — rows: {rows}")

    return all_data
