from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable
from zoneinfo import ZoneInfo

import a2s

from .config import MonitorConfig, ServerConfig
from .messages import format_event
from .notifier import TelegramNotifier
from .rcon import RconWatcher
from .state import StateStore
from .tracker import Event, Observation, ServerTracker

log = logging.getLogger(__name__)


class ServerMonitor:
    def __init__(
        self,
        server: ServerConfig,
        cfg: MonitorConfig,
        tracker: ServerTracker,
        rcon: RconWatcher | None,
        notifier: TelegramNotifier,
        store: StateStore,
        tz: ZoneInfo,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.server = server
        self.cfg = cfg
        self.tracker = tracker
        self.rcon = rcon
        self.notifier = notifier
        self.store = store
        self.tz = tz
        self._clock = clock

    async def run(self) -> None:
        while True:
            started = self._clock()
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("[%s] ошибка в цикле проверки", self.server.name)
            await asyncio.sleep(max(0.5, self.cfg.poll_interval - (self._clock() - started)))

    async def tick(self) -> list[Event]:
        query_ok, query_players = await self._query()
        now = self._clock()
        rcon_alive = bool(self.rcon and self.rcon.alive(now))
        if query_ok != rcon_alive and self.rcon:
            log.debug("[%s] расхождение: query=%s rcon=%s", self.server.name, query_ok, rcon_alive)

        players = self.rcon.players if rcon_alive and self.rcon.players is not None else query_players
        obs = Observation(
            now=now,
            alive=query_ok or rcon_alive,
            uptime=self.rcon.uptime(now) if rcon_alive else None,
            players=players,
        )
        events = self.tracker.update(obs)
        self.store.put(self.server.key, self.tracker.snapshot())
        for ev in events:
            log.info("[%s] событие %s", self.server.name, ev.kind.value)
            self.notifier.notify(
                format_event(self.server.name, ev, self.tz, self.cfg.restart_max_downtime),
                self.server.thread_id,
            )
        if events:
            self.store.flush()
        return events

    async def _query(self) -> tuple[bool, int | None]:
        s = self.server
        try:
            info = await asyncio.wait_for(
                a2s.ainfo((s.host, s.query_port), timeout=self.cfg.query_timeout),
                self.cfg.query_timeout + 1,
            )
            return True, info.player_count
        except Exception as exc:
            log.debug("[%s] query: %s: %s", s.name, type(exc).__name__, exc)
            return False, None
