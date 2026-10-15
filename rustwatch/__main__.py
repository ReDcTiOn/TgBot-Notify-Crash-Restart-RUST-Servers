from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
import time
from urllib.parse import quote

import a2s
from dotenv import load_dotenv
from websockets.asyncio.client import connect

from .config import Config, ConfigError, load_config
from .monitor import ServerMonitor
from .notifier import TelegramNotifier
from .rcon import HEARTBEAT_ID, RconWatcher
from .state import StateStore
from .tracker import ServerTracker, TrackerSettings

log = logging.getLogger("rustwatch")

FLUSH_INTERVAL = 30


async def run(cfg: Config, dry_run: bool) -> None:
    notifier = TelegramNotifier(
        cfg.telegram.token, cfg.telegram.chat_id, cfg.telegram.default_thread_id, dry_run
    )
    store = StateStore(cfg.monitor.state_file)
    settings = TrackerSettings(
        fail_threshold=cfg.monitor.fail_threshold,
        recover_threshold=cfg.monitor.recover_threshold,
        graceful_window=cfg.monitor.graceful_window,
        restart_max_downtime=cfg.monitor.restart_max_downtime,
        uptime_slack=cfg.monitor.uptime_slack,
    )

    tasks: list[asyncio.Task] = []
    for server in cfg.servers:
        tracker = ServerTracker(settings)
        if snap := store.get(server.key):
            tracker.restore(snap)
        rcon = None
        if server.rcon_password:
            rcon = RconWatcher(server, cfg.rcon, tracker.note_graceful)
            tasks.append(asyncio.create_task(rcon.run(), name=f"rcon:{server.name}"))
        monitor = ServerMonitor(server, cfg.monitor, tracker, rcon, notifier, store, cfg.tz)
        tasks.append(asyncio.create_task(monitor.run(), name=f"monitor:{server.name}"))

    async def flusher() -> None:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL)
            store.flush()

    tasks.append(asyncio.create_task(flusher(), name="flusher"))
    notifier.start()
    if cfg.telegram.notify_on_start:
        notifier.notify(f"🤖 <b>RustWatch запущен</b>\nСлежу за серверами: {len(cfg.servers)}")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass
    log.info("Мониторинг запущен: %d серверов", len(cfg.servers))
    try:
        await stop.wait()
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        store.flush()
        await notifier.close()


async def probe(cfg: Config) -> None:
    """Разовая проверка: Query + RCON каждого сервера, сырые ответы на экран."""
    for s in cfg.servers:
        print(f"\n=== {s.name} ({s.host}, game {s.game_port}, query {s.query_port}, rcon {s.rcon_port})")
        try:
            info = await a2s.ainfo((s.host, s.query_port), timeout=4)
            print(f"[query] OK: {info.server_name!r}, игроков {info.player_count}/{info.max_players}, карта {info.map_name!r}")
            print(f"[query] keywords: {info.keywords}")
        except Exception as exc:
            print(f"[query] ОШИБКА: {type(exc).__name__}: {exc}")

        if not s.rcon_password:
            print("[rcon] пароль не задан, пропускаю")
            continue
        url = f"ws://{s.host}:{s.rcon_port}/{quote(s.rcon_password, safe='')}"
        try:
            async with connect(url, open_timeout=5, ping_interval=None) as ws:
                await ws.send(json.dumps({"Identifier": HEARTBEAT_ID, "Message": "serverinfo", "Name": "RustWatch"}))
                deadline = time.monotonic() + 6
                while (left := deadline - time.monotonic()) > 0:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), left)
                    except asyncio.TimeoutError:
                        break
                    msg = json.loads(raw)
                    tag = "serverinfo" if msg.get("Identifier") == HEARTBEAT_ID else f"log id={msg.get('Identifier')} type={msg.get('Type')}"
                    print(f"[rcon] {tag}: {str(msg.get('Message'))[:600]}")
        except Exception as exc:
            print(f"[rcon] ОШИБКА: {type(exc).__name__}: {exc}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rustwatch", description="Уведомления о падениях и рестартах Rust-серверов")
    parser.add_argument("command", nargs="?", choices=["run", "probe"], default="run")
    parser.add_argument("-c", "--config", default="config.toml")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--dry-run", action="store_true", help="печатать уведомления в лог вместо отправки в Telegram")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):  # кириллица в консоли Windows
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("a2s").setLevel(logging.WARNING)

    load_dotenv(args.env_file)
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2
    for w in cfg.warnings:
        log.warning(w)

    try:
        asyncio.run(probe(cfg) if args.command == "probe" else run(cfg, args.dry_run))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
