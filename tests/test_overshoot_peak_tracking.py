"""Tests for time-window-based overshoot peak tracking."""

from datetime import datetime, timedelta

import pytest

from custom_components.adaptive_thermostat.adaptive.cycle_analysis import (
    PhaseAwareOvershootTracker,
    calculate_overshoot,
)
from custom_components.adaptive_thermostat.const import OVERSHOOT_PEAK_WINDOW_MINUTES


class TestPhaseAwareOvershootTrackerPeakWindow:
    """Test time-window-based peak tracking in PhaseAwareOvershootTracker."""

    def test_peak_within_window_counted(self):
        """Test that peak within tracking window is counted as overshoot."""
        setpoint = 21.0
        tracker = PhaseAwareOvershootTracker(
            setpoint=setpoint, peak_tracking_window_minutes=45
        )

        # Rise phase - temperature rises to setpoint
        base_time = datetime(2024, 1, 1, 10, 0)
        tracker.update(base_time, 19.0)
        tracker.update(base_time + timedelta(minutes=10), 20.0)
        tracker.update(base_time + timedelta(minutes=20), 21.0)  # Crosses setpoint

        # Heater stops
        heater_stop_time = base_time + timedelta(minutes=25)
        tracker.on_heater_stopped(heater_stop_time)

        # Peak occurs 15 minutes after heater stops (within 45-minute window)
        tracker.update(heater_stop_time + timedelta(minutes=5), 21.3)
        tracker.update(heater_stop_time + timedelta(minutes=10), 21.5)
        tracker.update(heater_stop_time + timedelta(minutes=15), 21.7)  # Peak
        tracker.update(heater_stop_time + timedelta(minutes=20), 21.4)
        tracker.update(heater_stop_time + timedelta(minutes=25), 21.2)

        # Peak within window should be counted
        overshoot = tracker.get_overshoot()
        assert overshoot is not None
        assert overshoot == pytest.approx(0.7, abs=0.01)  # 21.7 - 21.0

    def test_peak_outside_window_ignored(self):
        """Test that peak outside tracking window is ignored."""
        setpoint = 21.0
        tracker = PhaseAwareOvershootTracker(
            setpoint=setpoint, peak_tracking_window_minutes=45
        )

        # Rise phase - temperature rises to setpoint
        base_time = datetime(2024, 1, 1, 10, 0)
        tracker.update(base_time, 19.0)
        tracker.update(base_time + timedelta(minutes=10), 20.0)
        tracker.update(base_time + timedelta(minutes=20), 21.0)  # Crosses setpoint

        # Heater stops
        heater_stop_time = base_time + timedelta(minutes=25)
        tracker.on_heater_stopped(heater_stop_time)

        # Small peak within window
        tracker.update(heater_stop_time + timedelta(minutes=5), 21.2)
        tracker.update(heater_stop_time + timedelta(minutes=10), 21.3)  # Early peak
        tracker.update(heater_stop_time + timedelta(minutes=15), 21.2)

        # Temperature drops, then rises again much later (outside window)
        tracker.update(heater_stop_time + timedelta(minutes=30), 21.1)
        tracker.update(heater_stop_time + timedelta(minutes=50), 21.0)  # 50 min > 45 min window
        tracker.update(heater_stop_time + timedelta(minutes=60), 21.8)  # Late peak - ignored

        # Only early peak within window should be counted
        overshoot = tracker.get_overshoot()
        assert overshoot is not None
        assert overshoot == pytest.approx(0.3, abs=0.01)  # 21.3 - 21.0, not 21.8

    def test_window_closure_logged_once(self):
        """Test that window closure is logged only once."""
        setpoint = 21.0
        tracker = PhaseAwareOvershootTracker(
            setpoint=setpoint, peak_tracking_window_minutes=30
        )

        base_time = datetime(2024, 1, 1, 10, 0)
        tracker.update(base_time, 19.0)
        tracker.update(base_time + timedelta(minutes=10), 21.0)

        heater_stop_time = base_time + timedelta(minutes=15)
        tracker.on_heater_stopped(heater_stop_time)

        # Peak within window
        tracker.update(heater_stop_time + timedelta(minutes=10), 21.5)

        # First update outside window - window closes
        tracker.update(heater_stop_time + timedelta(minutes=35), 21.3)

        # Multiple updates after window closed - should not re-log
        tracker.update(heater_stop_time + timedelta(minutes=40), 21.2)
        tracker.update(heater_stop_time + timedelta(minutes=50), 21.9)  # Late peak

        # Verify window closed flag is set
        assert tracker._peak_window_closed is True

        # Verify overshoot is from early peak only
        overshoot = tracker.get_overshoot()
        assert overshoot == pytest.approx(0.5, abs=0.01)

    def test_no_heater_stop_tracks_all_peaks(self):
        """Test that without heater stop signal, all peaks are tracked."""
        setpoint = 21.0
        tracker = PhaseAwareOvershootTracker(
            setpoint=setpoint, peak_tracking_window_minutes=45
        )

        base_time = datetime(2024, 1, 1, 10, 0)
        tracker.update(base_time, 19.0)
        tracker.update(base_time + timedelta(minutes=10), 21.0)  # Cross setpoint

        # No on_heater_stopped() call
        # All peaks should be tracked regardless of time
        tracker.update(base_time + timedelta(minutes=20), 21.5)
        tracker.update(base_time + timedelta(minutes=60), 21.3)
        tracker.update(base_time + timedelta(minutes=90), 21.8)  # Late peak
        tracker.update(base_time + timedelta(minutes=120), 21.6)

        # Maximum peak should be tracked even though it's very late
        overshoot = tracker.get_overshoot()
        assert overshoot is not None
        assert overshoot == pytest.approx(0.8, abs=0.01)  # 21.8 - 21.0

    def test_reset_clears_heater_stop_time(self):
        """Test that reset clears heater stop time and window state."""
        setpoint = 21.0
        tracker = PhaseAwareOvershootTracker(
            setpoint=setpoint, peak_tracking_window_minutes=45
        )

        base_time = datetime(2024, 1, 1, 10, 0)
        tracker.update(base_time, 19.0)
        tracker.update(base_time + timedelta(minutes=10), 21.0)

        heater_stop_time = base_time + timedelta(minutes=15)
        tracker.on_heater_stopped(heater_stop_time)
        tracker.update(heater_stop_time + timedelta(minutes=10), 21.5)

        # Reset tracker
        tracker.reset()

        # Verify internal state is cleared
        assert tracker._heater_stop_time is None
        assert tracker._peak_window_closed is False
        assert tracker._max_settling_temp is None

        # After reset, should start fresh
        new_base_time = datetime(2024, 1, 1, 12, 0)
        tracker.update(new_base_time, 19.0)
        tracker.update(new_base_time + timedelta(minutes=10), 21.0)
        tracker.on_heater_stopped(new_base_time + timedelta(minutes=15))
        tracker.update(new_base_time + timedelta(minutes=20), 21.3)

        overshoot = tracker.get_overshoot()
        assert overshoot == pytest.approx(0.3, abs=0.01)

    def test_custom_window_duration(self):
        """Test using custom peak tracking window duration."""
        setpoint = 21.0
        # Use shorter 20-minute window
        tracker = PhaseAwareOvershootTracker(
            setpoint=setpoint, peak_tracking_window_minutes=20
        )

        base_time = datetime(2024, 1, 1, 10, 0)
        tracker.update(base_time, 19.0)
        tracker.update(base_time + timedelta(minutes=10), 21.0)

        heater_stop_time = base_time + timedelta(minutes=15)
        tracker.on_heater_stopped(heater_stop_time)

        # Peak at 15 minutes - within 20-minute window
        tracker.update(heater_stop_time + timedelta(minutes=5), 21.3)
        tracker.update(heater_stop_time + timedelta(minutes=10), 21.5)
        tracker.update(heater_stop_time + timedelta(minutes=15), 21.6)

        # Peak at 25 minutes - outside 20-minute window
        tracker.update(heater_stop_time + timedelta(minutes=25), 21.9)

        # Only peaks within 20 minutes should count
        overshoot = tracker.get_overshoot()
        assert overshoot is not None
        assert overshoot == pytest.approx(0.6, abs=0.01)  # 21.6 - 21.0

    def test_default_window_matches_constant(self):
        """Test that default window matches OVERSHOOT_PEAK_WINDOW_MINUTES constant."""
        setpoint = 21.0
        tracker = PhaseAwareOvershootTracker(setpoint=setpoint)

        # Verify default is set correctly
        assert tracker._peak_tracking_window_minutes == OVERSHOOT_PEAK_WINDOW_MINUTES
        assert tracker._peak_tracking_window_minutes == 45

    def test_setpoint_change_invalidates_window(self):
        """Test that setpoint change resets window state."""
        setpoint = 21.0
        tracker = PhaseAwareOvershootTracker(
            setpoint=setpoint, peak_tracking_window_minutes=45
        )

        base_time = datetime(2024, 1, 1, 10, 0)
        tracker.update(base_time, 19.0)
        tracker.update(base_time + timedelta(minutes=10), 21.0)

        heater_stop_time = base_time + timedelta(minutes=15)
        tracker.on_heater_stopped(heater_stop_time)
        tracker.update(heater_stop_time + timedelta(minutes=10), 21.5)

        # Setpoint changes - resets tracker
        tracker.reset(new_setpoint=22.0)

        # Verify window state is cleared
        assert tracker._heater_stop_time is None
        assert tracker._peak_window_closed is False

        # New cycle with new setpoint
        new_base_time = datetime(2024, 1, 1, 11, 0)
        tracker.update(new_base_time, 20.0)
        tracker.update(new_base_time + timedelta(minutes=10), 22.0)
        tracker.on_heater_stopped(new_base_time + timedelta(minutes=15))
        tracker.update(new_base_time + timedelta(minutes=20), 22.4)

        overshoot = tracker.get_overshoot()
        assert overshoot == pytest.approx(0.4, abs=0.01)  # 22.4 - 22.0


class TestCalculateOvershootWithPeakWindow:
    """Test calculate_overshoot function with phase-aware tracking (includes peak window)."""

    def test_calculate_overshoot_phase_aware(self):
        """Test calculate_overshoot with phase_aware=True (default)."""
        target_temp = 21.0
        base_time = datetime(2024, 1, 1, 10, 0)

        temperature_history = [
            (base_time, 19.0),
            (base_time + timedelta(minutes=10), 20.0),
            (base_time + timedelta(minutes=20), 21.0),  # Crosses setpoint
            (base_time + timedelta(minutes=25), 21.3),  # Settling phase
            (base_time + timedelta(minutes=30), 21.5),
            (base_time + timedelta(minutes=35), 21.4),
        ]

        overshoot = calculate_overshoot(temperature_history, target_temp, phase_aware=True)
        assert overshoot is not None
        assert overshoot == pytest.approx(0.5, abs=0.01)  # 21.5 - 21.0

    def test_calculate_overshoot_legacy_mode(self):
        """Test calculate_overshoot with phase_aware=False (legacy behavior)."""
        target_temp = 21.0
        base_time = datetime(2024, 1, 1, 10, 0)

        temperature_history = [
            (base_time, 19.0),
            (base_time + timedelta(minutes=10), 20.5),
            (base_time + timedelta(minutes=20), 21.8),  # Max temp in rise phase
            (base_time + timedelta(minutes=25), 21.5),
            (base_time + timedelta(minutes=30), 21.2),
        ]

        # Legacy mode counts max temp from entire cycle
        overshoot = calculate_overshoot(temperature_history, target_temp, phase_aware=False)
        assert overshoot is not None
        assert overshoot == pytest.approx(0.8, abs=0.01)  # 21.8 - 21.0

        # Phase-aware mode ignores rise phase
        overshoot_pa = calculate_overshoot(temperature_history, target_temp, phase_aware=True)
        # Setpoint crossed at 21.8, max in settling is 21.8
        assert overshoot_pa == pytest.approx(0.8, abs=0.01)


class TestOvershootPeakTrackingIntegration:
    """Integration tests for overshoot peak tracking in realistic scenarios."""

    def test_solar_gain_late_peak_ignored(self):
        """Test that late peak from solar gain is ignored with time window."""
        setpoint = 21.0
        tracker = PhaseAwareOvershootTracker(
            setpoint=setpoint, peak_tracking_window_minutes=45
        )

        # Morning heating cycle
        base_time = datetime(2024, 1, 1, 8, 0)
        tracker.update(base_time, 18.0)
        tracker.update(base_time + timedelta(minutes=15), 19.5)
        tracker.update(base_time + timedelta(minutes=30), 21.0)  # Reaches setpoint

        # Heater turns off
        heater_stop_time = base_time + timedelta(minutes=35)
        tracker.on_heater_stopped(heater_stop_time)

        # Normal settling with small overshoot
        tracker.update(heater_stop_time + timedelta(minutes=5), 21.2)
        tracker.update(heater_stop_time + timedelta(minutes=10), 21.3)
        tracker.update(heater_stop_time + timedelta(minutes=20), 21.2)
        tracker.update(heater_stop_time + timedelta(minutes=30), 21.1)

        # Sun comes out 60 minutes later, causes temperature spike
        tracker.update(heater_stop_time + timedelta(minutes=60), 21.0)
        tracker.update(heater_stop_time + timedelta(minutes=75), 21.5)
        tracker.update(heater_stop_time + timedelta(minutes=90), 22.0)  # Solar gain peak

        # Overshoot should only reflect early peak, not solar gain
        overshoot = tracker.get_overshoot()
        assert overshoot is not None
        assert overshoot == pytest.approx(0.3, abs=0.01)  # 21.3 - 21.0, not 22.0

    def test_occupancy_late_peak_ignored(self):
        """Test that late peak from occupancy is ignored with time window."""
        setpoint = 21.0
        tracker = PhaseAwareOvershootTracker(
            setpoint=setpoint, peak_tracking_window_minutes=45
        )

        # Heating cycle completes
        base_time = datetime(2024, 1, 1, 6, 0)
        tracker.update(base_time, 17.0)
        tracker.update(base_time + timedelta(minutes=20), 19.0)
        tracker.update(base_time + timedelta(minutes=40), 21.0)  # Reaches setpoint

        heater_stop_time = base_time + timedelta(minutes=45)
        tracker.on_heater_stopped(heater_stop_time)

        # Small overshoot during settling
        tracker.update(heater_stop_time + timedelta(minutes=10), 21.4)
        tracker.update(heater_stop_time + timedelta(minutes=20), 21.3)
        tracker.update(heater_stop_time + timedelta(minutes=40), 21.1)

        # People arrive 70 minutes later, body heat causes temperature rise
        tracker.update(heater_stop_time + timedelta(minutes=70), 21.2)
        tracker.update(heater_stop_time + timedelta(minutes=90), 21.8)  # Occupancy gain

        # Overshoot should only reflect thermal system response, not occupancy
        overshoot = tracker.get_overshoot()
        assert overshoot is not None
        assert overshoot == pytest.approx(0.4, abs=0.01)  # 21.4 - 21.0

    def test_normal_overshoot_within_window_counted(self):
        """Test that legitimate overshoot within window is properly counted."""
        setpoint = 21.0
        tracker = PhaseAwareOvershootTracker(
            setpoint=setpoint, peak_tracking_window_minutes=45
        )

        # Fast heating system with thermal lag causing overshoot
        base_time = datetime(2024, 1, 1, 10, 0)
        tracker.update(base_time, 18.0)
        tracker.update(base_time + timedelta(minutes=5), 19.5)
        tracker.update(base_time + timedelta(minutes=10), 21.0)  # Cross setpoint

        # Heater stops but system has thermal momentum
        heater_stop_time = base_time + timedelta(minutes=12)
        tracker.on_heater_stopped(heater_stop_time)

        # Temperature continues rising due to thermal lag (legitimate overshoot)
        tracker.update(heater_stop_time + timedelta(minutes=2), 21.3)
        tracker.update(heater_stop_time + timedelta(minutes=5), 21.6)
        tracker.update(heater_stop_time + timedelta(minutes=8), 21.8)  # Peak
        tracker.update(heater_stop_time + timedelta(minutes=12), 21.6)
        tracker.update(heater_stop_time + timedelta(minutes=20), 21.3)
        tracker.update(heater_stop_time + timedelta(minutes=30), 21.1)

        # This is legitimate overshoot within 45-minute window
        overshoot = tracker.get_overshoot()
        assert overshoot is not None
        assert overshoot == pytest.approx(0.8, abs=0.01)  # 21.8 - 21.0
