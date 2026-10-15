"""Оформление уведомлений (Telegram HTML: b, i, code, blockquote)."""
from __future__ import annotations

import re
from datetime import datetime
from html import escape
from typing import Sequence
from zoneinfo import ZoneInfo

from .config import ServerConfig
from .tracker import Event, EventKind

# kind -> (эмодзи, заголовок, уточнение, хэштег события)
STYLE: dict[EventKind, tuple[str, str, str | None, str]] = {
    EventKind.CRASH: ("💥", "КРАШ", None, "crash"),
    EventKind.STOPPED: ("⏹", "ОСТАНОВЛЕН", "штатно", "stopped"),
    EventKind.STILL_OFF: ("🔴", "ВЫКЛЮЧЕН", None, "off"),
    EventKind.UNREACHABLE: ("⚠️", "НЕДОСТУПЕН", None, "unreachable"),
    EventKind.RECOVERED: ("✅", "СНОВА ОНЛАЙН", "после краша", "online"),
    EventKind.RESTARTED: ("🔄", "РЕСТАРТ ЗАВЕРШЁН", None, "restart"),
    EventKind.STARTED: ("🟢", "СЕРВЕР ВКЛЮЧЁН", None, "started"),
    EventKind.BACK_ONLINE: ("🟢", "СНОВА ДОСТУПЕН", None, "online"),
    EventKind.RESTART_MISSED: ("🔄", "БЫЛ РЕСТАРТ", "между проверками", "restart"),
}


def fmt_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} ч {m:02d} мин"
    if m:
        return f"{m} мин {s:02d} с" if s else f"{m} мин"
    return f"{s} с"


def fmt_time(ts: float, tz: ZoneInfo) -> str:
    dt = datetime.fromtimestamp(ts, tz)
    return f"<b>{dt:%H:%M:%S}</b> {dt:%Z} · {dt:%d.%m.%Y}"


def server_tag(name: str) -> str:
    """'FR #1 [NOLIMIT | X3]' -> '#FR_1' (для поиска истории сервера в чате)."""
    base = name.split("[")[0] or name
    tag = re.sub(r"\W+", "_", base).strip("_")
    if not tag or tag.isdigit():
        tag = f"server{tag}"
    return f"#{tag}"


def _row(icon: str, label: str, value: str) -> str:
    return f"{icon} {label}: {value}"


def _quote(rows: list[str]) -> str:
    return "<blockquote>" + "\n".join(rows) + "</blockquote>"


def _timeline(ev: Event, tz: ZoneInfo, down_label: str, up_label: str, dur_label: str) -> list[str]:
    downtime = ev.downtime or 0
    return [
        _row("⬇️", down_label, fmt_time(ev.at - downtime, tz)),
        _row("⬆️", up_label, fmt_time(ev.at, tz)),
        _row("⏱", dur_label, f"<b>{fmt_duration(downtime)}</b>"),
    ]


def format_event(name: str, address: str, ev: Event, tz: ZoneInfo, restart_window: float) -> str:
    emoji, title, sub, tag = STYLE[ev.kind]
    head = f"{emoji} <b>{title}</b>" + (f" · {sub}" if sub else "")
    note: str | None = None

    if ev.kind is EventKind.CRASH:
        rows = [_row("🕐", "Пропал", fmt_time(ev.at, tz))]
        if ev.players is not None:
            rows.append(_row("👥", "Игроков было", f"<b>{ev.players}</b>"))
        rows.append(_row("❓", "Причина", "<b>без предупреждения</b>"))
        note = "Перед падением не было признаков штатной остановки."
    elif ev.kind is EventKind.STOPPED:
        rows = [_row("🕐", "Остановлен", fmt_time(ev.at, tz))]
        if ev.players is not None:
            rows.append(_row("👥", "Игроков было", f"<b>{ev.players}</b>"))
        rows.append(_row("🔎", "Признак", f"<i>{escape(ev.reason or '')}</i>"))
        note = (
            f"Вернётся в течение {fmt_duration(restart_window)}: это рестарт. "
            "Не вернётся: отмечу как выключенный."
        )
    elif ev.kind is EventKind.STILL_OFF:
        rows = [_row("⏱", "Не вернулся за", f"<b>{fmt_duration(ev.downtime or 0)}</b>")]
        note = "Это не рестарт: сервер остановлен и обратно не запущен."
    elif ev.kind is EventKind.UNREACHABLE:
        rows = [_row("🕐", "Недоступен с", fmt_time(ev.at, tz))]
        note = "Обнаружено при запуске бота, причина неизвестна."
    elif ev.kind is EventKind.RECOVERED:
        rows = _timeline(ev, tz, "Пропал", "Вернулся", "Простой")
    elif ev.kind is EventKind.RESTARTED:
        rows = _timeline(ev, tz, "Остановлен", "Вернулся", "Простой")
    elif ev.kind is EventKind.STARTED:
        rows = _timeline(ev, tz, "Выключен", "Включён", "Был выключен")
    elif ev.kind is EventKind.BACK_ONLINE:
        rows = _timeline(ev, tz, "Пропал", "Вернулся", "Простой")
        note = "Причина простоя неизвестна."
    else:  # RESTART_MISSED
        rows = [_row("🕐", "Примерно в", fmt_time(ev.at, tz))]
        note = "Определено по uptime сервера."

    parts = [head, f"<b>{escape(name)}</b>", "", _quote(rows)]
    if note:
        parts.append(f"<i>{escape(note)}</i>")
    parts += ["", f"<code>{escape(address)}</code> · {server_tag(name)} #{tag}"]
    return "\n".join(parts)


def format_bot_started(servers: Sequence[ServerConfig]) -> str:
    rows = [f"• <b>{escape(s.name)}</b>  <code>{escape(s.key)}</code>" for s in servers]
    parts = [
        "🤖 <b>RustWatch запущен</b>",
        f"Слежу за серверами: <b>{len(servers)}</b>",
        "",
        "<blockquote expandable>" + "\n".join(rows) + "</blockquote>",
    ]
    no_rcon = [s.name for s in servers if not s.rcon_password]
    if no_rcon:
        parts += [
            "",
            "⚠️ <i>Без RCON (причину падения определить не смогу):</i> " + escape(", ".join(no_rcon)),
        ]
    return "\n".join(parts)


def sample_events(now: float) -> list[Event]:
    """По одному примеру каждого сообщения (для команды preview)."""
    return [
        Event(EventKind.CRASH, now - 600, players=87),
        Event(EventKind.RECOVERED, now - 420, downtime=192),
        Event(
            EventKind.STOPPED, now - 300, players=64,
            reason="в логе сервера: «Server is restarting in 60 seconds»",
        ),
        Event(EventKind.RESTARTED, now - 120, downtime=158),
        Event(EventKind.STILL_OFF, now - 60, downtime=900),
        Event(EventKind.STARTED, now - 30, downtime=5400),
        Event(EventKind.RESTART_MISSED, now - 1260),
        Event(EventKind.UNREACHABLE, now - 90),
        Event(EventKind.BACK_ONLINE, now, downtime=240),
    ]
