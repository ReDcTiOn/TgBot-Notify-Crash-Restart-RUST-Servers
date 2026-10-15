from rustwatch.tracker import (
    EventKind,
    Observation,
    ServerTracker,
    Status,
    TrackerSettings,
)

SETTINGS = TrackerSettings(fail_threshold=3, recover_threshold=2, graceful_window=900, restart_max_downtime=900)


class Clock:
    """Прогоняет тики по 10 секунд и собирает события."""

    def __init__(self, tracker: ServerTracker, start: float = 1000.0) -> None:
        self.t = tracker
        self.now = start

    def tick(self, alive: bool, uptime=None, players=None, n: int = 1):
        events = []
        for _ in range(n):
            events += self.t.update(Observation(self.now, alive, uptime, players))
            self.now += 10
        return events


def started() -> tuple[ServerTracker, Clock]:
    tr = ServerTracker(SETTINGS)
    clk = Clock(tr)
    assert clk.tick(True, uptime=500, players=42) == []
    assert tr.status is Status.UP
    return tr, clk


def test_single_missed_query_is_not_an_outage():
    tr, clk = started()
    assert clk.tick(False, n=2) == []
    assert clk.tick(True) == []
    assert clk.tick(False, n=2) == []  # счётчик сбросился удачной проверкой
    assert tr.status is Status.UP


def test_crash_without_warning():
    tr, clk = started()
    t_first_fail = clk.now
    events = clk.tick(False, n=3)
    assert [e.kind for e in events] == [EventKind.CRASH]
    assert events[0].at == t_first_fail
    assert events[0].players == 42
    assert tr.status is Status.DOWN


def test_crash_recovery_reports_downtime():
    tr, clk = started()
    clk.tick(False, n=3)
    assert clk.tick(False, n=10) == []  # повторных уведомлений о краше нет
    events = clk.tick(True, n=2)
    assert [e.kind for e in events] == [EventKind.RECOVERED]
    assert events[0].downtime > 100
    assert tr.status is Status.UP


def test_recovery_needs_consecutive_successes():
    tr, clk = started()
    clk.tick(False, n=3)
    assert clk.tick(True) == []
    assert clk.tick(False) == []
    assert clk.tick(True) == []
    assert tr.status is Status.DOWN
    assert [e.kind for e in clk.tick(True)] == [EventKind.RECOVERED]
    assert tr.status is Status.UP


def test_planned_restart():
    tr, clk = started()
    tr.note_graceful(clk.now - 5, "в логе сервера: «Server restarting»")
    events = clk.tick(False, n=3)
    assert [e.kind for e in events] == [EventKind.STOPPED]
    assert "restarting" in events[0].reason
    clk.tick(False, n=12)  # ~2 минуты простоя
    events = clk.tick(True, n=2)
    assert [e.kind for e in events] == [EventKind.RESTARTED]


def test_planned_stop_that_never_returns_then_started():
    tr, clk = started()
    tr.note_graceful(clk.now, "RCON закрыт штатно")
    clk.tick(False, n=3)
    events = clk.tick(False, n=95)  # > 900 секунд
    assert [e.kind for e in events] == [EventKind.STILL_OFF]
    assert clk.tick(False, n=20) == []  # о «выключен» сообщаем один раз
    events = clk.tick(True, n=2)
    assert [e.kind for e in events] == [EventKind.STARTED]


def test_stale_graceful_evidence_is_ignored():
    tr, clk = started()
    tr.note_graceful(clk.now - 3600, "старый признак")
    assert [e.kind for e in clk.tick(False, n=3)] == [EventKind.CRASH]


def test_graceful_evidence_cleared_after_recovery():
    tr, clk = started()
    tr.note_graceful(clk.now, "рестарт")
    clk.tick(False, n=3)
    clk.tick(True, n=2)
    assert [e.kind for e in clk.tick(False, n=3)] == [EventKind.CRASH]


def test_graceful_evidence_ignored_while_down():
    tr, clk = started()
    clk.tick(False, n=3)
    tr.note_graceful(clk.now, "запоздалый признак")
    assert [e.kind for e in clk.tick(True, n=2)] == [EventKind.RECOVERED]


def test_unreachable_on_first_observation():
    tr = ServerTracker(SETTINGS)
    clk = Clock(tr)
    assert [e.kind for e in clk.tick(False, n=3)] == [EventKind.UNREACHABLE]
    assert [e.kind for e in clk.tick(True, n=2)] == [EventKind.BACK_ONLINE]


def test_missed_restart_detected_by_uptime():
    tr, clk = started()  # uptime 500 в момент 1000
    events = clk.tick(True, uptime=40)  # через 10 с uptime должен быть ~510
    assert [e.kind for e in events] == [EventKind.RESTART_MISSED]
    assert events[0].at == clk.now - 10 - 40


def test_normal_uptime_growth_is_not_a_restart():
    tr, clk = started()
    for i in range(1, 20):
        assert clk.tick(True, uptime=500 + 10 * i) == []


def test_uptime_gap_without_rcon_does_not_false_positive():
    tr, clk = started()
    clk.tick(True, uptime=None, n=30)  # RCON недоступен, Query живой
    assert clk.tick(True, uptime=500 + 10 * 31) == []


def test_snapshot_roundtrip_keeps_outage_across_bot_restart():
    tr, clk = started()
    clk.tick(False, n=3)
    snap = tr.snapshot()

    tr2 = ServerTracker(SETTINGS)
    tr2.restore(snap)
    clk2 = Clock(tr2, start=clk.now + 600)
    assert clk2.tick(False, n=5) == []  # падение не объявляется повторно
    events = clk2.tick(True, n=2)
    assert [e.kind for e in events] == [EventKind.RECOVERED]
    assert events[0].downtime > 600


def test_restart_while_bot_was_offline_is_found_by_uptime():
    tr, clk = started()
    snap = tr.snapshot()

    tr2 = ServerTracker(SETTINGS)
    tr2.restore(snap)
    clk2 = Clock(tr2, start=clk.now + 3600)
    events = clk2.tick(True, uptime=120)
    assert [e.kind for e in events] == [EventKind.RESTART_MISSED]
