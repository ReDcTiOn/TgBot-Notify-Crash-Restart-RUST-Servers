"""Постоянное WebRCON-соединение: heartbeat (serverinfo), uptime и признаки штатной остановки."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable
from urllib.parse import quote

from websockets.asyncio.client import connect

from .config import RconConfig, ServerConfig

log = logging.getLogger(__name__)

HEARTBEAT_ID = 7001
_CHAT_PREFIXES = ("[chat]", "[team chat]")

GracefulCallback = Callable[[float, str], None]


class RconWatcher:
    def __init__(
        self,
        server: ServerConfig,
        cfg: RconConfig,
        on_graceful: GracefulCallback,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.server = server
        self.cfg = cfg
        self._on_graceful = on_graceful
        self._clock = clock
        self.connected = False
        self.players: int | None = None
        self._last_response = 0.0
        self._uptime: float | None = None
        self._info_at = 0.0
        self._session_msgs = 0

    @property
    def _stale_after(self) -> float:
        return self.cfg.heartbeat_interval * 3

    def alive(self, now: float) -> bool:
        return self.connected and (now - self._last_response) <= self._stale_after

    def uptime(self, now: float) -> float | None:
        """Uptime сервера на момент `now`, если serverinfo свежий."""
        if self._uptime is None or not self.connected or now - self._info_at > self._stale_after:
            return None
        return self._uptime + (now - self._info_at)

    async def run(self) -> None:
        backoff = 2.0
        while True:
            started = self._clock()
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.info("[%s] RCON: %s: %s", self.server.name, type(exc).__name__, exc)
            self.connected = False
            if self._clock() - started > 60:
                backoff = 2.0
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _session(self) -> None:
        s = self.server
        url = f"ws://{s.host}:{s.rcon_port}/{quote(s.rcon_password or '', safe='')}"
        self._session_msgs = 0
        async with connect(url, open_timeout=5, ping_interval=None, max_size=2**22) as ws:
            self.connected = True
            self._last_response = self._clock()
            log.info("[%s] RCON подключён", s.name)
            beat = asyncio.create_task(self._heartbeat(ws))
            try:
                async for raw in ws:
                    self.handle_message(raw)
            finally:
                beat.cancel()
                self.connected = False
            # Цикл завершился без исключения: сервер закрыл соединение штатно.
            if self._session_msgs and self.cfg.clean_close_is_graceful:
                self._on_graceful(
                    self._clock(), f"RCON-соединение закрыто сервером штатно (код {ws.close_code})"
                )
            log.info("[%s] RCON закрыт (код %s)", s.name, ws.close_code)

    async def _heartbeat(self, ws) -> None:
        payload = json.dumps({"Identifier": HEARTBEAT_ID, "Message": "serverinfo", "Name": "RustWatch"})
        while True:
            await ws.send(payload)
            await asyncio.sleep(self.cfg.heartbeat_interval)

    def handle_message(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            return
        if not isinstance(msg, dict):
            return
        now = self._clock()
        self._last_response = now
        self._session_msgs += 1

        ident = msg.get("Identifier")
        text = msg.get("Message") or ""
        if ident == HEARTBEAT_ID:
            self._handle_info(text, now)
            return
        if msg.get("Type") == "Chat" or (isinstance(ident, int) and ident > 0):
            return
        for line in str(text).splitlines():
            if line.strip().lower().startswith(_CHAT_PREFIXES):
                continue
            if any(p.search(line) for p in self.cfg.shutdown_patterns):
                self._on_graceful(now, f"в логе сервера: «{line.strip()[:120]}»")
                return

    def _handle_info(self, text: str, now: float) -> None:
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            return
        if not isinstance(data, dict):
            return
        uptime = data.get("Uptime")
        if isinstance(uptime, (int, float)):
            self._uptime = float(uptime)
            self._info_at = now
        players = data.get("Players")
        if isinstance(players, int):
            self.players = players
        if data.get("Restarting"):
            self._on_graceful(now, "serverinfo: Restarting=true (идёт отсчёт до рестарта)")
