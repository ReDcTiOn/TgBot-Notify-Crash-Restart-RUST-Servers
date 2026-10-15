"""Клиентская часть RCON против локального поддельного WebRCON-сервера."""
import asyncio
import json

from websockets.asyncio.server import serve

from rustwatch.config import RconConfig, ServerConfig
from rustwatch.rcon import HEARTBEAT_ID, RconWatcher


async def run_session(handler, password="p@ss/word"):
    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        calls = []
        srv = ServerConfig("t", "127.0.0.1", port - 2, port - 1, port, password, None)
        w = RconWatcher(srv, RconConfig(heartbeat_interval=0.05), lambda ts, why: calls.append(why))
        await asyncio.wait_for(w._session(), 5)
        return w, calls


def info_reply(uptime=321, players=9):
    return json.dumps({
        "Identifier": HEARTBEAT_ID,
        "Message": json.dumps({"Uptime": uptime, "Players": players, "Restarting": False}),
    })


async def test_clean_close_after_traffic_is_graceful_evidence():
    seen_paths = []

    async def handler(ws):
        seen_paths.append(ws.request.path)
        await ws.recv()  # serverinfo
        await ws.send(info_reply())
        await ws.close(1000)

    w, calls = await run_session(handler)
    assert w.players == 9
    assert len(calls) == 1 and "штатно" in calls[0]
    assert seen_paths == ["/p%40ss%2Fword"]  # пароль экранирован в URL
    assert not w.connected


async def test_abrupt_drop_is_not_graceful_evidence():
    async def handler(ws):
        await ws.recv()
        await ws.send(info_reply())
        ws.transport.abort()  # как при kill/краше процесса

    try:
        await run_session(handler)
    except Exception as exc:
        calls = None
        assert "ConnectionClosed" in type(exc).__name__
    else:
        raise AssertionError("ожидали ConnectionClosedError")
    assert calls is None


async def test_close_without_any_traffic_is_ignored():
    """Неверный пароль/отказ до первого сообщения не должен выглядеть как штатная остановка."""
    async def handler(ws):
        await ws.close(1000)

    w, calls = await run_session(handler)
    assert calls == []
