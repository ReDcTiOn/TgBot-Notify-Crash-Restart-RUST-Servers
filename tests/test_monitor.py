import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import rustwatch.monitor as monitor_mod
from rustwatch.config import MonitorConfig, RconConfig, ServerConfig
from rustwatch.monitor import ServerMonitor
from rustwatch.rcon import HEARTBEAT_ID, RconWatcher
from rustwatch.state import StateStore
from rustwatch.tracker import EventKind, ServerTracker, TrackerSettings

SERVER = ServerConfig("FR #1", "127.0.0.1", 20520, 20521, 20522, "pw", 7)


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def notify(self, text, thread_id=None):
        self.sent.append((thread_id, text))


class World:
    """Управляемое «состояние сервера» и часы для проверки всей связки."""

    def __init__(self, monkeypatch, tmp_path):
        self.now = 1000.0
        self.query_up = True
        self.notifier = FakeNotifier()
        self.tracker = ServerTracker(TrackerSettings())
        self.rcon = RconWatcher(SERVER, RconConfig(), self.tracker.note_graceful, lambda: self.now)
        self.mon = ServerMonitor(
            SERVER, MonitorConfig(), self.tracker, self.rcon, self.notifier,
            StateStore(tmp_path / "state.json"), ZoneInfo("Europe/Moscow"), lambda: self.now,
        )

        async def fake_ainfo(address, timeout):
            if not self.query_up:
                raise TimeoutError
            return SimpleNamespace(player_count=12)

        monkeypatch.setattr(monitor_mod.a2s, "ainfo", fake_ainfo)

    def rcon_up(self, uptime=500):
        self.rcon.connected = True
        info = {"Uptime": uptime, "Players": 12, "Restarting": False}
        self.rcon.handle_message(json.dumps({"Identifier": HEARTBEAT_ID, "Message": json.dumps(info)}))

    def rcon_down(self):
        self.rcon.connected = False

    async def tick(self, n=1):
        kinds = []
        for _ in range(n):
            kinds += [e.kind for e in await self.mon.tick()]
            self.now += 10
        return kinds


@pytest.fixture
def world(monkeypatch, tmp_path):
    return World(monkeypatch, tmp_path)


async def test_crash_then_recovery_goes_to_the_servers_thread(world):
    world.rcon_up()
    assert await world.tick() == []

    world.query_up = False
    world.rcon_down()
    assert await world.tick(3) == [EventKind.CRASH]
    thread, text = world.notifier.sent[-1]
    assert thread == 7 and "КРАШ" in text and "12" in text

    world.query_up = True
    assert await world.tick(2) == [EventKind.RECOVERED]
    assert "СНОВА ОНЛАЙН" in world.notifier.sent[-1][1]


async def test_rcon_log_line_turns_outage_into_planned_restart(world):
    world.rcon_up()
    await world.tick()
    world.rcon.handle_message(json.dumps({"Identifier": 0, "Type": "Generic", "Message": "Server is restarting in 30 seconds"}))

    world.query_up = False
    world.rcon_down()
    assert await world.tick(3) == [EventKind.STOPPED]
    world.query_up = True
    assert await world.tick(2) == [EventKind.RESTARTED]


async def test_flaky_query_does_not_alert_while_rcon_is_alive(world):
    world.rcon_up()
    await world.tick()
    world.query_up = False
    for _ in range(10):
        world.rcon_up(uptime=500 + int(world.now - 1000))  # heartbeat приходит
        assert await world.tick() == []
    assert world.notifier.sent == []


async def test_works_without_rcon(monkeypatch, tmp_path):
    w = World(monkeypatch, tmp_path)
    w.mon.rcon = None
    await w.tick()
    w.query_up = False
    assert await w.tick(3) == [EventKind.CRASH]
