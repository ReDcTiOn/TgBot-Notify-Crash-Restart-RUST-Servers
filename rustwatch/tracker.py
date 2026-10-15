"""Конечный автомат одного сервера: без I/O, только логика классификации событий."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Status(str, Enum):
    UNKNOWN = "unknown"
    UP = "up"
    DOWN = "down"


class DownKind(str, Enum):
    CRASH = "crash"        # пропал без предупреждения
    PLANNED = "planned"    # были признаки штатной остановки/рестарта
    UNKNOWN = "unknown"    # недоступен уже при запуске бота


class EventKind(str, Enum):
    CRASH = "crash"
    STOPPED = "stopped"
    STILL_OFF = "still_off"
    UNREACHABLE = "unreachable"
    RECOVERED = "recovered"          # поднялся после краша
    RESTARTED = "restarted"          # вернулся после штатной остановки в пределах окна рестарта
    STARTED = "started"              # вернулся после штатной остановки, которая затянулась
    BACK_ONLINE = "back_online"      # вернулся, причина простоя неизвестна
    RESTART_MISSED = "restart_missed"  # рестарт между проверками, найден по uptime


@dataclass(frozen=True)
class Event:
    kind: EventKind
    at: float
    downtime: float | None = None
    reason: str | None = None
    players: int | None = None


@dataclass(frozen=True)
class TrackerSettings:
    fail_threshold: int = 3
    recover_threshold: int = 2
    graceful_window: float = 900.0
    restart_max_downtime: float = 900.0
    uptime_slack: float = 90.0


@dataclass(frozen=True)
class Observation:
    now: float
    alive: bool
    uptime: float | None = None
    players: int | None = None


class ServerTracker:
    def __init__(self, settings: TrackerSettings | None = None) -> None:
        self.settings = settings or TrackerSettings()
        self.status = Status.UNKNOWN
        self.down_since: float | None = None
        self.down_kind: DownKind | None = None
        self.last_players: int | None = None
        self._fails = 0
        self._oks = 0
        self._first_fail: float | None = None
        self._still_off_sent = False
        self._last_uptime: float | None = None
        self._last_seen: float | None = None
        self._graceful: tuple[float, str] | None = None

    def note_graceful(self, ts: float, reason: str) -> None:
        """Признак штатной остановки (строка лога, Restarting=true, чистое закрытие RCON)."""
        if self.status is Status.DOWN:
            return
        self._graceful = (ts, reason)

    def update(self, obs: Observation) -> list[Event]:
        return self._on_alive(obs) if obs.alive else self._on_dead(obs)

    def _on_alive(self, obs: Observation) -> list[Event]:
        events: list[Event] = []
        self._fails = 0
        self._first_fail = None

        if self.status is Status.DOWN:
            self._oks += 1
            if self._oks < self.settings.recover_threshold:
                return events
            events.append(self._recovery_event(obs))
            self.status = Status.UP
            self.down_since = None
            self.down_kind = None
            self._still_off_sent = False
            self._graceful = None
            self._oks = 0
        elif self.status is Status.UNKNOWN:
            self.status = Status.UP
        else:
            missed = self._restart_missed(obs)
            if missed:
                events.append(missed)
                self._graceful = None

        if obs.uptime is not None:
            self._last_uptime = obs.uptime
            self._last_seen = obs.now
        if obs.players is not None:
            self.last_players = obs.players
        return events

    def _on_dead(self, obs: Observation) -> list[Event]:
        self._oks = 0
        s = self.settings

        if self.status is Status.DOWN:
            if (
                self.down_kind is DownKind.PLANNED
                and not self._still_off_sent
                and obs.now - self.down_since >= s.restart_max_downtime
            ):
                self._still_off_sent = True
                return [Event(EventKind.STILL_OFF, obs.now, downtime=obs.now - self.down_since)]
            return []

        self._fails += 1
        if self._first_fail is None:
            self._first_fail = obs.now
        if self._fails < s.fail_threshold:
            return []

        was_unknown = self.status is Status.UNKNOWN
        self.status = Status.DOWN
        self.down_since = self._first_fail
        self._still_off_sent = False

        if was_unknown:
            self.down_kind = DownKind.UNKNOWN
            return [Event(EventKind.UNREACHABLE, self.down_since)]

        graceful = self._graceful
        if graceful and obs.now - graceful[0] <= s.graceful_window:
            self.down_kind = DownKind.PLANNED
            return [Event(EventKind.STOPPED, self.down_since, reason=graceful[1], players=self.last_players)]
        self.down_kind = DownKind.CRASH
        return [Event(EventKind.CRASH, self.down_since, players=self.last_players)]

    def _recovery_event(self, obs: Observation) -> Event:
        downtime = obs.now - (self.down_since if self.down_since is not None else obs.now)
        if self.down_kind is DownKind.CRASH:
            kind = EventKind.RECOVERED
        elif self.down_kind is DownKind.PLANNED:
            kind = (
                EventKind.RESTARTED
                if downtime <= self.settings.restart_max_downtime
                else EventKind.STARTED
            )
        else:
            kind = EventKind.BACK_ONLINE
        return Event(kind, obs.now, downtime=downtime)

    def _restart_missed(self, obs: Observation) -> Event | None:
        if obs.uptime is None or self._last_uptime is None or self._last_seen is None:
            return None
        expected = self._last_uptime + (obs.now - self._last_seen)
        if obs.uptime < expected - self.settings.uptime_slack:
            return Event(EventKind.RESTART_MISSED, obs.now - obs.uptime)
        return None

    def snapshot(self) -> dict:
        return {
            "status": self.status.value,
            "down_since": self.down_since,
            "down_kind": self.down_kind.value if self.down_kind else None,
            "still_off_sent": self._still_off_sent,
            "last_uptime": self._last_uptime,
            "last_seen": self._last_seen,
            "last_players": self.last_players,
            "graceful": list(self._graceful) if self._graceful else None,
        }

    def restore(self, data: dict) -> None:
        self.status = Status(data.get("status", "unknown"))
        self.down_since = data.get("down_since")
        kind = data.get("down_kind")
        self.down_kind = DownKind(kind) if kind else None
        self._still_off_sent = bool(data.get("still_off_sent"))
        self._last_uptime = data.get("last_uptime")
        self._last_seen = data.get("last_seen")
        self.last_players = data.get("last_players")
        g = data.get("graceful")
        self._graceful = (float(g[0]), str(g[1])) if g else None
