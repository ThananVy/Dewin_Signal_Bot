"""
telegram_bot.py — Sends trading signal messages to multiple Telegram chats.
Only sends BULLISH/BEARISH setups with confidence >= MIN_CONFIDENCE (default 60%).
Uses Cambodia Time (UTC+7).
"""

import os
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

ICT = timezone(timedelta(hours=7))  # Cambodia / Indochina Time (UTC+7)

BIAS_EMOJI = {"BULLISH": "📈", "BEARISH": "📉"}

PAIR_LABEL = {
    "EURUSD": "EUR/USD",
    "USDJPY": "USD/JPY",
    "GOLD": "XAU/USD (Gold)",
}


def _ict_now(fmt: str = "%Y-%m-%d %H:%M ICT") -> str:
    return datetime.now(ICT).strftime(fmt)


def _conf_bar(score: int) -> str:
    """Visual confidence bar: e.g. 75 → '███░░ 75%'"""
    filled = round(score / 20)  # 5 blocks total
    bar = "█" * filled + "░" * (5 - filled)
    return f"{bar} {score}%"


def _parse_chat_ids() -> list[str]:
    raw = os.getenv("TELEGRAM_CHAT_IDS", os.getenv("TELEGRAM_CHAT_ID", ""))
    return [cid.strip() for cid in raw.split(",") if cid.strip()]


def _min_confidence() -> int:
    return int(os.getenv("MIN_CONFIDENCE", "60"))


class TelegramBot:
    def __init__(self, token: str = None):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_ids = _parse_chat_ids()
        self._base = f"https://api.telegram.org/bot{self.token}"

    def _post_to(self, chat_id: str, text: str, silent: bool = False) -> bool:
        try:
            resp = requests.post(
                f"{self._base}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                    "disable_notification": silent,
                },
                timeout=10,
            )
            return resp.ok
        except Exception as exc:
            print(f"  Telegram error (chat {chat_id}): {exc}")
            return False

    def _broadcast(self, text: str, silent: bool = False) -> bool:
        if not self.token or not self.chat_ids:
            print("  WARNING: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_IDS not set in .env")
            return False
        results = [self._post_to(cid, text, silent) for cid in self.chat_ids]
        return any(results)

    # ── Public methods ────────────────────────────────────────

    def send_startup(self, pairs: list[str], interval_minutes: int) -> None:
        pairs_str = ", ".join(PAIR_LABEL.get(p, p) for p in pairs)
        recipients = len(self.chat_ids)
        text = (
            "🤖 <b>Trading Signal Bot Started</b>\n\n"
            f"📌 Pairs      : {pairs_str}\n"
            f"⏱ Interval   : every {interval_minutes} minutes\n"
            f"🎯 Min. Conf. : {_min_confidence()}%\n"
            f"👥 Recipients : {recipients} user(s)\n"
            f"🕐 {_ict_now()}\n\n"
            f"Signals sent only when confidence ≥ <b>{_min_confidence()}%</b> "
            f"and bias is <b>BULLISH or BEARISH</b>."
        )
        self._broadcast(text)

    def send_signal(self, signal: dict, reason: str = "New signal") -> bool:
        bias = signal.get("bias", "NEUTRAL")

        # Only send real directional setups
        if bias not in ("BULLISH", "BEARISH") or "error" in signal:
            return False

        # Confidence filter — support both int (new) and string (fallback)
        raw_conf = signal.get("confidence", 0)
        if isinstance(raw_conf, str):
            conf_score = {"HIGH": 80, "MEDIUM": 65, "LOW": 40}.get(raw_conf.upper(), 0)
        else:
            conf_score = int(raw_conf)

        min_conf = _min_confidence()
        if conf_score < min_conf:
            print(f"    Skipping — confidence {conf_score}% < {min_conf}% threshold")
            return False

        pair = signal.get("pair", "UNKNOWN")
        label = PAIR_LABEL.get(pair, pair)
        be = BIAS_EMOJI.get(bias, "")

        ez = signal.get("entry_zone", {})
        entry_from = ez.get("from", "?")
        entry_to   = ez.get("to", "?")
        sl         = signal.get("stop_loss", "?")
        sl_pips    = signal.get("sl_pips", "?")
        tp1        = signal.get("tp1", "?")
        tp2        = signal.get("tp2", "?")
        rr         = signal.get("risk_reward", "?")
        entry_cond = signal.get("entry_condition", "")
        invalidate = signal.get("invalidation", "")
        tfc        = signal.get("timeframe_confluence", "")

        text = (
            f"{be} <b>{label}  —  {bias}</b>\n"
            f"📊 Confidence : <b>{_conf_bar(conf_score)}</b>\n"
            f"🕐 {_ict_now()}  |  {reason}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>Entry  :</b>  {entry_from} — {entry_to}\n"
            f"🛑 <b>SL     :</b>  {sl}  <i>({sl_pips} pips)</i>\n"
            f"✅ <b>TP1    :</b>  {tp1}\n"
            f"🏆 <b>TP2    :</b>  {tp2}\n"
            f"⚖️ <b>R:R    :</b>  {rr}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📋 <i>{entry_cond}</i>\n"
            f"❌ <b>Cancel if:</b> {invalidate}\n"
        )
        if tfc:
            text += f"\n💬 <i>{tfc}</i>"

        return self._broadcast(text)

    def send_heartbeat(self, signals: dict, next_run: str) -> None:
        min_conf = _min_confidence()
        active = {}
        for p, s in signals.items():
            if s.get("bias") not in ("BULLISH", "BEARISH") or "error" in s:
                continue
            raw = s.get("confidence", 0)
            score = int(raw) if isinstance(raw, (int, float)) else {"HIGH": 80, "MEDIUM": 65, "LOW": 40}.get(str(raw).upper(), 0)
            if score >= min_conf:
                active[p] = {**s, "_score": score}

        lines = [f"💓 <b>Heartbeat</b>  —  {_ict_now()}\n"]

        if active:
            lines.append("🔥 <b>Active setups:</b>")
            for pair, sig in active.items():
                label = PAIR_LABEL.get(pair, pair)
                bias  = sig.get("bias", "?")
                score = sig["_score"]
                ez    = sig.get("entry_zone", {})
                be    = BIAS_EMOJI.get(bias, "")
                lines.append(
                    f"{be} <b>{label}</b>  {bias}  {score}%\n"
                    f"   Entry {ez.get('from','?')}–{ez.get('to','?')} "
                    f"| SL {sig.get('stop_loss','?')} "
                    f"| TP1 {sig.get('tp1','?')}"
                )
        else:
            lines.append(f"😴 No setups ≥ {min_conf}% confidence right now.")

        lines.append(f"\n⏭ Next check: {next_run}")
        self._broadcast("\n".join(lines), silent=True)

    def send_error(self, message: str) -> None:
        self._broadcast(f"⚠️ <b>Bot Error</b>\n<code>{message[:400]}</code>")
