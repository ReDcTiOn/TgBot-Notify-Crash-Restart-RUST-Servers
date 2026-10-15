import json
import textwrap
from zoneinfo import ZoneInfo

import pytest

from rustwatch.config import ConfigError, RconConfig, ServerConfig, load_config
from rustwatch.messages import fmt_duration, format_event
from rustwatch.rcon import HEARTBEAT_ID, RconWatcher
from rustwatch.tracker import Event, EventKind

SERVER = ServerConfig("FR #1", "127.0.0.1", 20520, 20521, 20522, "pw", None)


def watcher(clock=lambda: 100.0):
    calls = []
    w = RconWatcher(SERVER, RconConfig(), lambda ts, why: calls.append((ts, why)), clock)
    return w, calls


def msg(text, ident=0, typ="Generic"):
    return json.dumps({"Identifier": ident, "Message": text, "Type": typ})


def test_shutdown_log_line_is_graceful_evidence():
    w, calls = watcher()
    w.handle_message(msg("Server is restarting in 60 seconds"))
    assert len(calls) == 1 and "restarting" in calls[0][1]


def test_chat_lines_never_count():
    w, calls = watcher()
    w.handle_message(msg('{"Message":"restart the server pls"}', typ="Chat"))
    w.handle_message(msg("[CHAT] Vasya : server shutting down?"))
    w.handle_message(msg("[TEAM CHAT] Vasya : restarting in 5"))
    assert calls == []


def test_command_responses_are_not_log_lines():
    w, calls = watcher()
    w.handle_message(msg("shutting down", ident=42))
    assert calls == []


def test_serverinfo_updates_uptime_players_and_restarting():
    now = [100.0]
    w, calls = watcher(lambda: now[0])
    w.connected = True
    info = {"Uptime": 600, "Players": 77, "Restarting": False}
    w.handle_message(msg(json.dumps(info), ident=HEARTBEAT_ID))
    assert w.players == 77
    assert w.uptime(100.0) == 600
    assert w.uptime(110.0) == 610  # экстраполяция до момента проверки
    assert w.alive(105.0)
    assert not w.alive(100.0 + 3 * 15 + 1)
    assert calls == []

    info["Restarting"] = True
    w.handle_message(msg(json.dumps(info), ident=HEARTBEAT_ID))
    assert len(calls) == 1 and "Restarting" in calls[0][1]


def test_garbage_is_ignored():
    w, calls = watcher()
    w.handle_message("not json")
    w.handle_message("[1, 2]")
    assert calls == []


def write_config(tmp_path, body):
    p = tmp_path / "config.toml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


BASE = """
    [telegram]
    chat_id = -100123
    [[servers]]
    name = "FR #1"
    host = "1.2.3.4"
    game_port = 20520
    rcon_password_env = "PW1"
"""


def test_config_derives_ports_and_reads_secrets(tmp_path):
    cfg = load_config(write_config(tmp_path, BASE), {"TELEGRAM_BOT_TOKEN": "t", "PW1": "secret"})
    s = cfg.servers[0]
    assert (s.query_port, s.rcon_port, s.rcon_password) == (20521, 20522, "secret")
    assert not cfg.warnings


def test_config_warns_without_rcon_password(tmp_path):
    cfg = load_config(write_config(tmp_path, BASE), {"TELEGRAM_BOT_TOKEN": "t"})
    assert cfg.servers[0].rcon_password is None
    assert cfg.warnings


def test_config_requires_token(tmp_path):
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, BASE), {})


def test_example_config_is_valid():
    env = {"TELEGRAM_BOT_TOKEN": "t", **{f"RCON_PASSWORD_{i}": "x" for i in range(1, 6)}}
    cfg = load_config("config.example.toml", env)
    assert len(cfg.servers) == 5
    assert [s.query_port for s in cfg.servers] == [20521, 20511, 20531, 20541, 20551]
    assert [s.rcon_port for s in cfg.servers] == [20522, 20512, 20532, 20542, 20552]


def test_messages_escape_html_and_format_duration():
    tz = ZoneInfo("Europe/Moscow")
    text = format_event("FR <#1> & co", Event(EventKind.CRASH, 0.0, players=3), tz, 900)
    assert "&lt;#1&gt; &amp; co" in text and "КРАШ" in text and "3" in text
    assert fmt_duration(45) == "45 с"
    assert fmt_duration(192) == "3 мин 12 с"
    assert fmt_duration(3900) == "1 ч 05 мин"


def test_every_event_kind_has_a_message():
    tz = ZoneInfo("Europe/Moscow")
    for kind in EventKind:
        assert format_event("S", Event(kind, 0.0, downtime=60, reason="why"), tz, 900)
