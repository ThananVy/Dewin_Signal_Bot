"""telegram_bot.py — Sends signals to Telegram. Cambodia Time (UTC+7)."""

import os
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

ICT = timezone(timedelta(hours=7))

PAIR_LABEL = {"EURUSD": "EUR/USD", "USDJPY": "USD/JPY", "GOLD": "XAU/USD"}
PAIR_DECIMALS = {"EURUSD": 5, "USDJPY": 3, "GOLD": 2}
DIR_EMOJI = {"LONG": "🟢", "SHORT": "🔴"}


def _ict_now() -> str:
    return datetime.now(ICT).strftime("%H:%M ICT")


def _fmt_price(price: float, pair: str) -> str:
    decimals = PAIR_DECIMALS.get(pair, 5)
    return f"{price:.{decimals}f}"


def _conf_bar(score: int) -> str:
    filled = round(score / 20)
    return "█" * filled + "░" * (5 - filled)


def _parse_chat_ids() -> list[str]:
    raw = os.getenv("TELEGRAM_CHAT_IDS", os.getenv("TELEGRAM_CHAT_ID", ""))
    return [c.strip() for c in raw.split(",") if c.strip()]


def _min_confidence() -> int:
    return int(os.getenv("MIN_CONFIDENCE", "80"))


class TelegramBot:
    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_ids = _parse_chat_ids()
        self._base = f"https://api.telegram.org/bot{self.token}"

    def _broadcast(self, text: str, silent: bool = False) -> bool:
        if not self.token or not self.chat_ids:
            print("  WARNING: Telegram credentials missing in .env")
            return False
        ok = False
        for cid in self.chat_ids:
            try:
                r = requests.post(f"{self._base}/sendMessage", json={
                    "chat_id": cid, "text": text, "parse_mode": "HTML",
                    "disable_web_page_preview": True, "disable_notification": silent,
                }, timeout=10)
                if r.ok:
                    ok = True
            except Exception as e:
                print(f"  Telegram error ({cid}): {e}")
        return ok

    def send_startup(self, pairs: list[str], interval: int) -> None:
        pairs_str = ", ".join(PAIR_LABEL.get(p, p) for p in pairs)
        self._broadcast(
            f"<b>Signal Bot Started</b>\n"
            f"Pairs: {pairs_str}\n"
            f"Every {interval} min  |  7AM-11PM Cambodia\n"
            f"Min confidence: {_min_confidence()}%  |  {_ict_now()}"
        )

    def send_signal(self, signal: dict, reason: str = "") -> bool:
        direction = signal.get("direction", "")
        bias = signal.get("bias", "")

        # Accept both old-style (BULLISH/BEARISH) and new-style (LONG/SHORT)
        if direction not in ("LONG", "SHORT"):
            if bias == "BULLISH":
                direction = "LONG"
            elif bias == "BEARISH":
                direction = "SHORT"
            else:
                return False

        if "error" in signal:
            return False

        conf = int(signal.get("confidence", 0))
        if conf < _min_confidence():
            print(f"    Skip: confidence {conf}% < {_min_confidence()}%")
            return False

        pair = signal.get("pair", "")
        label = PAIR_LABEL.get(pair, pair)
        emoji = DIR_EMOJI.get(direction, "")

        entry = signal.get("entry", 0)
        sl = signal.get("stop_loss", 0)
        be = signal.get("be", entry)
        tp = signal.get("tp", 0)
        sl_pips = signal.get("sl_pips", 0)
        tp_pips = signal.get("tp_pips", 0)
        tp_rr = signal.get("tp_rr", 2.0)
        rr_str = f"1:{int(tp_rr) if tp_rr == int(tp_rr) else tp_rr}"

        p = _fmt_price
        text = (
            f"{emoji} <b>{direction} — {label}</b>\n"
            f"{_conf_bar(conf)} {conf}% hit TP  |  {rr_str} R:R  |  {_ict_now()}\n"
            f"<i>{reason}</i>\n"
            "─────────────────────\n"
            f"<b>Entry</b>  {p(entry, pair)}\n"
            f"<b>SL</b>     {p(sl, pair)}  (-{sl_pips} pips)\n"
            f"<b>BE</b>     {p(be, pair)}  (move SL to entry when hit)\n"
            f"<b>TP</b>     {p(tp, pair)}  (+{tp_pips} pips)\n"
            "─────────────────────\n"
            f"<i>{signal.get('entry_condition', '')}</i>\n"
            f"<i>{signal.get('timeframe_confluence', '')}</i>"
        )
        return self._broadcast(text)

    def send_heartbeat(self, signals: dict, next_run: str) -> None:
        min_conf = _min_confidence()
        lines = [f"<b>Heartbeat</b>  {_ict_now()}\n"]
        active = [
            (p, s) for p, s in signals.items()
            if s.get("bias") in ("BULLISH", "BEARISH")
            and int(s.get("confidence", 0)) >= min_conf
            and "error" not in s
        ]
        if active:
            lines.append("<b>Active setups:</b>")
            for pair, sig in active:
                d = sig.get("direction", "LONG" if sig["bias"] == "BULLISH" else "SHORT")
                e = DIR_EMOJI.get(d, "")
                p = _fmt_price
                lines.append(
                    f"{e} {PAIR_LABEL.get(pair, pair)}  {d}  {sig.get('confidence')}%  "
                    f"Entry {p(sig.get('entry', 0), pair)}  "
                    f"TP {p(sig.get('tp', 0), pair)}"
                )
        else:
            lines.append(f"No setups >= {min_conf}% right now.")
        lines.append(f"\nNext: {next_run}")
        self._broadcast("\n".join(lines), silent=True)

    def send_error(self, message: str) -> None:
        self._broadcast(f"<b>Bot Error</b>\n<code>{message[:400]}</code>")
