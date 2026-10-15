from __future__ import annotations

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from .tracker import Event, EventKind


def fmt_time(ts: float, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%d.%m.%Y %H:%M:%S %Z")


def fmt_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} ч {m:02d} мин"
    if m:
        return f"{m} мин {s:02d} с"
    return f"{s} с"


def format_event(name: str, ev: Event, tz: ZoneInfo, restart_window: float) -> str:
    title = escape(name)
    when = fmt_time(ev.at, tz)
    lines: list[str]

    if ev.kind is EventKind.CRASH:
        lines = [
            f"💥 <b>КРАШ</b> · {title}",
            f"🕐 Пропал: {when}",
            "❓ Без предупреждения: в логе RCON не было сообщений о выключении или рестарте",
        ]
    elif ev.kind is EventKind.STOPPED:
        lines = [
            f"⏹ <b>Остановлен (штатно)</b> · {title}",
            f"🕐 {when}",
            f"ℹ️ {escape(ev.reason or '')}",
            f"Если не вернётся за {fmt_duration(restart_window)}, сообщу, что сервер выключен.",
        ]
    elif ev.kind is EventKind.STILL_OFF:
        lines = [
            f"🔴 <b>Выключен</b> · {title}",
            f"Не вернулся за {fmt_duration(ev.downtime or 0)} после штатной остановки, это не рестарт.",
        ]
    elif ev.kind is EventKind.UNREACHABLE:
        lines = [
            f"⚠️ <b>Недоступен</b> · {title}",
            f"🕐 С {when}",
            "Обнаружено при запуске бота, причину определить нельзя.",
        ]
    elif ev.kind is EventKind.RECOVERED:
        lines = [
            f"✅ <b>Поднялся после краша</b> · {title}",
            f"🕐 {when}",
            f"⏱ Простой: {fmt_duration(ev.downtime or 0)}",
        ]
    elif ev.kind is EventKind.RESTARTED:
        lines = [
            f"🔄 <b>Рестарт завершён</b> · {title}",
            f"🕐 {when}",
            f"⏱ Простой: {fmt_duration(ev.downtime or 0)}",
        ]
    elif ev.kind is EventKind.STARTED:
        lines = [
            f"🟢 <b>Сервер включён</b> · {title}",
            f"🕐 {when}",
            f"⏱ Был выключен: {fmt_duration(ev.downtime or 0)}",
        ]
    elif ev.kind is EventKind.BACK_ONLINE:
        lines = [
            f"🟢 <b>Снова доступен</b> · {title}",
            f"🕐 {when}",
            f"⏱ Недоступен был: {fmt_duration(ev.downtime or 0)}, причина неизвестна",
        ]
    else:  # RESTART_MISSED
        lines = [
            f"🔄 <b>Был рестарт между проверками</b> · {title}",
            f"🕐 Примерно в {when} (по uptime)",
        ]

    if ev.players is not None and ev.kind in (EventKind.CRASH, EventKind.STOPPED):
        lines.append(f"👥 Игроков на момент падения: {ev.players}")
    return "\n".join(lines)
