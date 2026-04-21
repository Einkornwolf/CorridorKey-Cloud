"""Tests for NodeSchedule timezone handling (CRKY-198).

Schedule HH:MM windows are compared against the configured
CK_SCHEDULE_TIMEZONE, not the host's local time, so behavior is
deterministic regardless of where the server runs.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from web.api.nodes import NodeSchedule, get_schedule_timezone_name


class TestScheduleTimezone:
    def test_default_tz_is_utc(self):
        # Baseline: without the env var set, schedules are UTC
        assert get_schedule_timezone_name() == "UTC"

    def test_disabled_schedule_always_active(self):
        s = NodeSchedule(enabled=False, start="09:00", end="17:00")
        assert s.is_active_now is True

    def test_within_same_day_window(self):
        s = NodeSchedule(enabled=True, start="09:00", end="17:00")
        # Freeze "now" to 12:00 UTC
        fake_now = datetime(2026, 4, 21, 12, 0, tzinfo=ZoneInfo("UTC"))
        with patch("web.api.nodes.datetime") as m:
            m.now.return_value = fake_now
            assert s.is_active_now is True

    def test_outside_same_day_window(self):
        s = NodeSchedule(enabled=True, start="09:00", end="17:00")
        fake_now = datetime(2026, 4, 21, 22, 0, tzinfo=ZoneInfo("UTC"))
        with patch("web.api.nodes.datetime") as m:
            m.now.return_value = fake_now
            assert s.is_active_now is False

    def test_overnight_window_evening(self):
        # 20:00-08:00 wraps midnight
        s = NodeSchedule(enabled=True, start="20:00", end="08:00")
        fake_now = datetime(2026, 4, 21, 23, 0, tzinfo=ZoneInfo("UTC"))
        with patch("web.api.nodes.datetime") as m:
            m.now.return_value = fake_now
            assert s.is_active_now is True

    def test_overnight_window_early_morning(self):
        s = NodeSchedule(enabled=True, start="20:00", end="08:00")
        fake_now = datetime(2026, 4, 21, 6, 0, tzinfo=ZoneInfo("UTC"))
        with patch("web.api.nodes.datetime") as m:
            m.now.return_value = fake_now
            assert s.is_active_now is True

    def test_overnight_window_midday_inactive(self):
        s = NodeSchedule(enabled=True, start="20:00", end="08:00")
        fake_now = datetime(2026, 4, 21, 14, 0, tzinfo=ZoneInfo("UTC"))
        with patch("web.api.nodes.datetime") as m:
            m.now.return_value = fake_now
            assert s.is_active_now is False

    def test_now_called_with_explicit_tz(self):
        """is_active_now must pass SCHEDULE_TIMEZONE to datetime.now, not
        rely on host local time. Regression guard for CRKY-198."""
        import web.api.nodes as nodes_mod

        s = NodeSchedule(enabled=True, start="09:00", end="17:00")
        with patch("web.api.nodes.datetime") as m:
            m.now.return_value = datetime(2026, 4, 21, 10, 0, tzinfo=ZoneInfo("UTC"))
            _ = s.is_active_now
            # datetime.now() should have been invoked with the schedule TZ
            m.now.assert_called_once_with(nodes_mod.SCHEDULE_TIMEZONE)
