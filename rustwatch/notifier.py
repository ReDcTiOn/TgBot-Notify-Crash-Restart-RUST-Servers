"""Отправка уведомлений в Telegram (Bot API, sendMessage) через очередь с ретраями."""
from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
MAX_ATTEMPTS = 8


class TelegramNotifier:
    def __init__(
        self,
        token: str,
        chat_id: int,
        default_thread_id: int | None = None,
        dry_run: bool = False,
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._default_thread = default_thread_id
        self._dry_run = dry_run
        self._queue: asyncio.Queue[tuple[str, int | None]] = asyncio.Queue()
        self._client = httpx.AsyncClient(timeout=15)
        self._worker: asyncio.Task | None = None

    def start(self) -> None:
        self._worker = asyncio.create_task(self._run())

    def notify(self, text: str, thread_id: int | None = None) -> None:
        self._queue.put_nowait((text, thread_id or self._default_thread))

    async def close(self, drain_timeout: float = 10.0) -> None:
        try:
            await asyncio.wait_for(self._queue.join(), drain_timeout)
        except asyncio.TimeoutError:
            log.warning("В очереди остались неотправленные уведомления: %d", self._queue.qsize())
        if self._worker:
            self._worker.cancel()
        await self._client.aclose()

    async def _run(self) -> None:
        while True:
            text, thread = await self._queue.get()
            try:
                await self._send(text, thread)
            except Exception:
                log.exception("Не удалось отправить уведомление, оно потеряно")
            finally:
                self._queue.task_done()

    async def _send(self, text: str, thread: int | None) -> None:
        if self._dry_run:
            log.info("[dry-run] thread=%s\n%s", thread, text)
            return
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if thread:
            payload["message_thread_id"] = thread

        delay = 1.0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = await self._client.post(f"{API}/bot{self._token}/sendMessage", json=payload)
            except httpx.HTTPError as exc:
                log.warning("Telegram недоступен (%s), попытка %d/%d", type(exc).__name__, attempt, MAX_ATTEMPTS)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
                continue

            if resp.status_code == 200:
                return
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            description = str(body.get("description", resp.text))[:200]

            if resp.status_code == 429:
                wait = int(body.get("parameters", {}).get("retry_after", delay)) + 1
                log.warning("Telegram просит подождать %d с", wait)
                await asyncio.sleep(wait)
                continue
            if resp.status_code == 400 and "thread" in description.lower() and "message_thread_id" in payload:
                log.error("Тема %s не найдена (%s), отправляю без темы", thread, description)
                payload.pop("message_thread_id")
                continue
            if resp.status_code >= 500:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            raise RuntimeError(f"Telegram вернул {resp.status_code}: {description}")
        raise RuntimeError("Исчерпаны попытки отправки в Telegram")
