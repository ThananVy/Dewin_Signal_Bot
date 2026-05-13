"""telegram_bot.py — Sends signals to Telegram. Cambodia Time (UTC+7). Setup-only alerts."""

import os
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

ICT = timezone(timedelta(hours=7))
BIAS_EMOJI = {"BULLISH": "📈", "BEARISH": "📉"}
PAIR_LABEL = {"EURUSD": "EUR/USD", "USDJPY": "USD/JPY", "GOLD": "XAU/USD (Gold)"}


def _ict_now() -> str:
    return datetime.now(ICT).strftime("%Y-%m-%d %H:%M ICT")


def _conf_bar(score: int) -> str:
    filled = round(score / 20)
    return "█" * filled + "░" * (5 - filled) + f" {score}%"


def _parse_chat_ids() -> list[str]:
    raw = os.getenv("TELEGRAM_CHAT_IDS", os.getenv("TELEGRAM_CHAT_ID", ""))
    return [c.strip() for c in raw.split(",") if c.strip()]


def _min_confidence() -> int:
    return int(os.getenv("MIN_CONFIDENCE", "60"))


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
            f"🤖 <b>Signal Bot Started</b>\n"
            f"📌 Pairs: {pairs_str}\n"
            f"⏱ Every {interval} min | 7AM–11PM Cambodia\n"
            f"🎯 Min confidence: {_min_confidence()}%\n"
            f"🕐 {_ict_now()}"
        )

    def send_signal(self, signal: dict, reason: str = "") -> bool:
        bias = signal.get("bias", "NEUTRAL")
        if bias not in ("BULLISH", "BEARISH") or "error" in signal:
            return False

        raw_conf = signal.get("confidence", 0)
        score = int(raw_conf) if isinstance(raw_conf, (int, float)) else 0
        if score < _min_confidence():
            print(f"    Skip: confidence {score}% < {_min_confidence()}%")
            return False

        pair = signal.get("pair", "")
        label = PAIR_LABEL.get(pair, pair)
        ez = signal.get("entry_zone", {})

        text = (
            f"{BIAS_EMOJI.get(bias, '')} <b>{label} — {bias}</b>\n"
            f"📊 Confidence: <b>{_conf_bar(score)}</b>\n"
            f"🕐 {_ict_now()}  |  {reason}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>Entry :</b> {ez.get('from')} — {ez.get('to')}\n"
            f"🛑 <b>SL    :</b> {signal.get('stop_loss')}  ({signal.get('sl_pips')} pips)\n"
            f"✅ <b>TP1   :</b> {signal.get('tp1')}  <i>(1:1)</i>\n"
            f"🏆 <b>TP2   :</b> {signal.get('tp2')}  <i>(1:2)</i>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📋 <i>{signal.get('entry_condition', '')}</i>\n"
            f"❌ <b>Cancel if:</b> {signal.get('invalidation', '')}\n"
        )
        tfc = signal.get("timeframe_confluence", "")
        if tfc:
            text += f"\n💬 <i>{tfc}</i>"
        return self._broadcast(text)

    def send_heartbeat(self, signals: dict, next_run: str) -> None:
        min_conf = _min_confidence()
        lines = [f"💓 <b>Heartbeat</b> — {_ict_now()}\n"]
        active = [(p, s) for p, s in signals.items()
                  if s.get("bias") in ("BULLISH", "BEARISH")
                  and int(s.get("confidence", 0)) >= min_conf
                  and "error" not in s]
        if active:
            lines.append("🔥 <b>Active setups:</b>")
            for pair, sig in active:
                ez = sig.get("entry_zone", {})
                lines.append(
                    f"{BIAS_EMOJI.get(sig['bias'], '')} {PAIR_LABEL.get(pair, pair)}: "
                    f"<b>{sig['bias']}</b> {sig.get('confidence')}% | "
                    f"Entry {ez.get('from')}–{ez.get('to')} SL {sig.get('stop_loss')} TP1 {sig.get('tp1')}"
                )
        else:
            lines.append(f"😴 No setups ≥{min_conf}% right now.")
        lines.append(f"\n⏭ Next: {next_run}")
        self._broadcast("\n".join(lines), silent=True)

    def send_error(self, message: str) -> None:
        self._broadcast(f"⚠️ <b>Bot Error</b>\n<code>{message[:400]}</code>")
