from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo

# Шаблоны строк консоли, по которым считаем остановку штатной. Применяются
# только к не-чатовым строкам. Точный текст зависит от версии Rust и плагинов,
# поэтому список настраивается в config.toml (см. README, раздел «Проверка»).
DEFAULT_SHUTDOWN_PATTERNS = [
    r"\bshutting down\b",
    r"\bserver (is )?restarting\b",
    r"\brestarting in \d+",
    r"\brestart(ing)? (the )?server\b",
    r"\[rcon\].*\b(global\.)?(quit|restart)\b",
]


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class TelegramConfig:
    token: str
    chat_id: int
    default_thread_id: int | None = None
    notify_on_start: bool = True


@dataclass(frozen=True)
class MonitorConfig:
    poll_interval: float = 10.0
    query_timeout: float = 3.0
    fail_threshold: int = 3
    recover_threshold: int = 2
    graceful_window: float = 900.0
    restart_max_downtime: float = 900.0
    uptime_slack: float = 90.0
    timezone: str = "Europe/Moscow"
    state_file: str = "state.json"


@dataclass(frozen=True)
class RconConfig:
    heartbeat_interval: float = 15.0
    clean_close_is_graceful: bool = True
    shutdown_patterns: tuple[re.Pattern, ...] = field(
        default_factory=lambda: tuple(re.compile(p, re.I) for p in DEFAULT_SHUTDOWN_PATTERNS)
    )


@dataclass(frozen=True)
class ServerConfig:
    name: str
    host: str
    game_port: int
    query_port: int
    rcon_port: int
    rcon_password: str | None
    thread_id: int | None

    @property
    def key(self) -> str:
        return f"{self.host}:{self.game_port}"


@dataclass(frozen=True)
class Config:
    telegram: TelegramConfig
    monitor: MonitorConfig
    rcon: RconConfig
    servers: tuple[ServerConfig, ...]
    warnings: tuple[str, ...] = ()

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.monitor.timezone)


def _opt_int(value) -> int | None:
    return int(value) if value not in (None, 0, "") else None


def load_config(path: str | Path, environ: Mapping[str, str] | None = None) -> Config:
    env = os.environ if environ is None else environ
    try:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
    except FileNotFoundError:
        raise ConfigError(f"Не найден файл конфигурации: {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Ошибка синтаксиса в {path}: {exc}") from None

    warnings: list[str] = []

    tg = raw.get("telegram", {})
    token = env.get(tg.get("token_env", "TELEGRAM_BOT_TOKEN"), "").strip()
    if not token:
        raise ConfigError("Не задан токен бота (переменная TELEGRAM_BOT_TOKEN в .env)")
    if "chat_id" not in tg:
        raise ConfigError("В [telegram] не указан chat_id")
    telegram = TelegramConfig(
        token=token,
        chat_id=int(tg["chat_id"]),
        default_thread_id=_opt_int(tg.get("default_thread_id")),
        notify_on_start=bool(tg.get("notify_on_start", True)),
    )

    monitor = MonitorConfig(**raw.get("monitor", {}))
    try:
        ZoneInfo(monitor.timezone)
    except Exception:
        raise ConfigError(f"Неизвестная таймзона: {monitor.timezone}") from None

    rcon_raw = dict(raw.get("rcon", {}))
    patterns = rcon_raw.pop("shutdown_patterns", DEFAULT_SHUTDOWN_PATTERNS)
    try:
        compiled = tuple(re.compile(p, re.I) for p in patterns)
    except re.error as exc:
        raise ConfigError(f"Некорректное регулярное выражение в shutdown_patterns: {exc}") from None
    rcon = RconConfig(shutdown_patterns=compiled, **rcon_raw)

    servers: list[ServerConfig] = []
    for i, s in enumerate(raw.get("servers", []), 1):
        for req in ("name", "host", "game_port"):
            if req not in s:
                raise ConfigError(f"servers[{i}]: не указано поле {req}")
        game = int(s["game_port"])
        pw_env = s.get("rcon_password_env")
        password = env.get(pw_env, "") if pw_env else ""
        if not password:
            warnings.append(
                f"{s['name']}: пароль RCON не задан, работаю только по Query "
                "(причину падения определить не получится)"
            )
        servers.append(
            ServerConfig(
                name=s["name"],
                host=s["host"],
                game_port=game,
                query_port=int(s.get("query_port", game + 1)),
                rcon_port=int(s.get("rcon_port", game + 2)),
                rcon_password=password or None,
                thread_id=_opt_int(s.get("thread_id")),
            )
        )
    if not servers:
        raise ConfigError("В конфигурации нет ни одного [[servers]]")

    return Config(telegram, monitor, rcon, tuple(servers), tuple(warnings))
