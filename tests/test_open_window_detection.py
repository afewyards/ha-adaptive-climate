"""Tests for OpenWindowDetector class."""

import pytest
from datetime import datetime, timedelta

# Note: OpenWindowDetector doesn't exist yet - these tests follow TDD approach
# based on CLAUDE.md specification
try:
    from custom_components.adaptive_thermostat.adaptive.open_window_detection import (
        OpenWindowDetector,
    )
except ImportError:
    pytest.skip("OpenWindowDetector not yet implemented", allow_module_level=True)


class TestOpenWindowDetector:
    """Tests for OpenWindowDetector class."""

    def test_initialization_with_defaults(self):
        """Test detector initializes with default config."""
        detector = OpenWindowDetector()

        assert detector.get_state() == "normal"
        assert not detector.should_pause()
        assert detector.get_time_until_resume() is None

    def test_initialization_with_custom_config(self):
        """Test detector initializes with custom parameters."""
        detector = OpenWindowDetector(
            temp_drop=3.0,
            detection_window=600,  # 10 minutes
            pause_duration=1800,   # 30 minutes
            cooldown=600,          # 10 minutes
        )

        assert detector._temp_drop == 3.0
        assert detector._detection_window == 600
        assert detector._pause_duration == 1800
        assert detector._cooldown == 600

    def test_record_temperature_populates_ring_buffer(self):
        """Test record_temperature adds to ring buffer."""
        detector = OpenWindowDetector(detection_window=300)
        now = datetime(2024, 1, 1, 12, 0, 0)

        detector.record_temperature(now, 20.0)
        detector.record_temperature(now + timedelta(seconds=60), 19.5)
        detector.record_temperature(now + timedelta(seconds=120), 19.0)

        assert len(detector._temp_history) == 3
        assert detector._temp_history[0] == (now, 20.0)
        assert detector._temp_history[1] == (now + timedelta(seconds=60), 19.5)
        assert detector._temp_history[2] == (now + timedelta(seconds=120), 19.0)

    def test_ring_buffer_evicts_old_entries(self):
        """Test ring buffer removes entries older than detection_window."""
        detector = OpenWindowDetector(detection_window=300)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Add entry at t=0
        detector.record_temperature(now, 20.0)
        # Add entry at t=200s (within window)
        detector.record_temperature(now + timedelta(seconds=200), 19.5)
        # Add entry at t=400s (should evict first entry)
        detector.record_temperature(now + timedelta(seconds=400), 19.0)

        # First entry should be evicted (400 - 0 > 300)
        assert len(detector._temp_history) == 2
        assert detector._temp_history[0][0] == now + timedelta(seconds=200)
        assert detector._temp_history[1][0] == now + timedelta(seconds=400)

    def test_detection_trigger_on_rapid_temp_drop(self):
        """Test detection triggers when oldest - current >= temp_drop."""
        detector = OpenWindowDetector(temp_drop=2.0, detection_window=300)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Start at 20°C
        detector.record_temperature(now, 20.0)
        assert detector.get_state() == "normal"

        # Drop to 17.5°C in 5 minutes (2.5°C drop triggers detection)
        detector.record_temperature(now + timedelta(seconds=300), 17.5)
        assert detector.get_state() == "paused"
        assert detector.should_pause() is True

    def test_no_detection_when_drop_below_threshold(self):
        """Test no detection when temperature drop is below threshold."""
        detector = OpenWindowDetector(temp_drop=2.0, detection_window=300)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Start at 20°C
        detector.record_temperature(now, 20.0)
        # Drop to 18.5°C (only 1.5°C drop, below 2.0°C threshold)
        detector.record_temperature(now + timedelta(seconds=300), 18.5)

        assert detector.get_state() == "normal"
        assert detector.should_pause() is False

    def test_detection_trigger_exactly_at_threshold(self):
        """Test detection triggers at exact temp_drop threshold."""
        detector = OpenWindowDetector(temp_drop=2.0, detection_window=300)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Start at 20°C
        detector.record_temperature(now, 20.0)
        # Drop exactly 2.0°C
        detector.record_temperature(now + timedelta(seconds=300), 18.0)

        # Should trigger at exact threshold (>=)
        assert detector.get_state() == "paused"
        assert detector.should_pause() is True

    def test_no_detection_with_gradual_temp_drop(self):
        """Test no detection when temperature drops gradually over time."""
        detector = OpenWindowDetector(temp_drop=2.0, detection_window=300)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Gradual drop over multiple readings within window
        detector.record_temperature(now, 20.0)
        detector.record_temperature(now + timedelta(seconds=60), 19.7)
        detector.record_temperature(now + timedelta(seconds=120), 19.4)
        detector.record_temperature(now + timedelta(seconds=180), 19.1)
        detector.record_temperature(now + timedelta(seconds=240), 18.8)
        detector.record_temperature(now + timedelta(seconds=300), 18.6)

        # Total drop is 1.4°C over 5 minutes (below threshold)
        assert detector.get_state() == "normal"

    def test_pause_duration_auto_resume(self):
        """Test automatic resume after pause_duration expires."""
        detector = OpenWindowDetector(
            temp_drop=2.0,
            detection_window=300,
            pause_duration=600,  # 10 minutes
        )
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Trigger detection at t=300s
        detector.record_temperature(now, 20.0)
        detector.record_temperature(now + timedelta(seconds=300), 17.5)
        assert detector.get_state() == "paused"

        # Still paused at t=800s (500s elapsed, need 600s)
        detector.record_temperature(now + timedelta(seconds=800), 17.5)
        assert detector.get_state() == "paused"

        # Auto-resume at t=901s (601s elapsed)
        detector.record_temperature(now + timedelta(seconds=901), 17.5)
        assert detector.get_state() == "normal"
        assert detector.should_pause() is False

    def test_get_time_until_resume_during_pause(self):
        """Test get_time_until_resume returns remaining seconds during pause."""
        detector = OpenWindowDetector(
            temp_drop=2.0,
            detection_window=300,
            pause_duration=600,
        )
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Trigger detection at t=300s
        detector.record_temperature(now, 20.0)
        detector.record_temperature(now + timedelta(seconds=300), 17.5)
        assert detector.get_state() == "paused"

        # Check at t=300s (600s remaining)
        remaining = detector.get_time_until_resume()
        assert remaining == 600

        # Record at t=500s (400s remaining)
        detector.record_temperature(now + timedelta(seconds=500), 17.5)
        remaining = detector.get_time_until_resume()
        assert remaining == 400

    def test_get_time_until_resume_returns_none_when_normal(self):
        """Test get_time_until_resume returns None in normal state."""
        detector = OpenWindowDetector()
        now = datetime(2024, 1, 1, 12, 0, 0)

        detector.record_temperature(now, 20.0)

        assert detector.get_state() == "normal"
        assert detector.get_time_until_resume() is None

    def test_cooldown_prevents_immediate_re_detection(self):
        """Test cooldown period prevents immediate re-detection after resume."""
        detector = OpenWindowDetector(
            temp_drop=2.0,
            detection_window=300,
            pause_duration=600,
            cooldown=300,
        )
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Trigger detection at t=300s
        detector.record_temperature(now, 20.0)
        detector.record_temperature(now + timedelta(seconds=300), 17.5)
        assert detector.get_state() == "paused"

        # Auto-resume at t=901s
        detector.record_temperature(now + timedelta(seconds=901), 17.5)
        assert detector.get_state() == "normal"

        # Try to trigger again at t=1000s (within cooldown)
        detector.record_temperature(now + timedelta(seconds=1000), 20.0)
        detector.record_temperature(now + timedelta(seconds=1300), 17.5)
        # Should not trigger due to cooldown
        assert detector.get_state() == "normal"

        # After cooldown expires (t=1202s), detection should work again
        detector.record_temperature(now + timedelta(seconds=1202), 20.0)
        detector.record_temperature(now + timedelta(seconds=1502), 17.5)
        assert detector.get_state() == "paused"

    def test_suppression_during_setpoint_decrease(self):
        """Test detection is suppressed during setpoint decrease."""
        detector = OpenWindowDetector(temp_drop=2.0, detection_window=300)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Record temperatures
        detector.record_temperature(now, 20.0)

        # Suppress detection due to setpoint decrease
        detector.suppress_detection("setpoint_decrease", current_time=now)

        # Temperature drops but detection suppressed
        detector.record_temperature(now + timedelta(seconds=300), 17.5)
        assert detector.get_state() == "normal"
        assert detector.should_pause() is False

    def test_suppression_during_night_setback(self):
        """Test detection is suppressed during night setback transitions."""
        detector = OpenWindowDetector(temp_drop=2.0, detection_window=300)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Record temperatures
        detector.record_temperature(now, 20.0)

        # Suppress detection due to night setback
        detector.suppress_detection("night_setback", current_time=now)

        # Temperature drops but detection suppressed
        detector.record_temperature(now + timedelta(seconds=300), 17.5)
        assert detector.get_state() == "normal"
        assert detector.should_pause() is False

    def test_suppression_clears_after_period(self):
        """Test suppression automatically clears after suppression period."""
        detector = OpenWindowDetector(
            temp_drop=2.0,
            detection_window=300,
            suppression_duration=600,  # 10 minutes
        )
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Suppress at t=0
        detector.suppress_detection("setpoint_decrease", current_time=now)

        # Try to trigger at t=300s (within suppression period)
        detector.record_temperature(now, 20.0)
        detector.record_temperature(now + timedelta(seconds=300), 17.5)
        assert detector.get_state() == "normal"

        # After suppression expires (t=601s), detection should work
        detector.record_temperature(now + timedelta(seconds=601), 20.0)
        detector.record_temperature(now + timedelta(seconds=901), 17.5)
        assert detector.get_state() == "paused"

    def test_reset_clears_all_state(self):
        """Test reset clears detection state and history."""
        detector = OpenWindowDetector(temp_drop=2.0, detection_window=300)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Trigger detection
        detector.record_temperature(now, 20.0)
        detector.record_temperature(now + timedelta(seconds=300), 17.5)
        assert detector.get_state() == "paused"

        # Reset
        detector.reset()

        # Should be back to initial state
        assert detector.get_state() == "normal"
        assert detector.should_pause() is False
        assert len(detector._temp_history) == 0
        assert detector.get_time_until_resume() is None

    def test_empty_buffer_no_crash(self):
        """Test detector handles empty buffer gracefully."""
        detector = OpenWindowDetector(temp_drop=2.0)

        assert detector.get_state() == "normal"
        assert detector.should_pause() is False
        assert detector.get_time_until_resume() is None

    def test_single_reading_no_detection(self):
        """Test single temperature reading doesn't trigger detection."""
        detector = OpenWindowDetector(temp_drop=2.0, detection_window=300)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Only one reading
        detector.record_temperature(now, 20.0)

        assert detector.get_state() == "normal"
        assert detector.should_pause() is False

    def test_temperature_rise_no_detection(self):
        """Test temperature rise doesn't trigger detection."""
        detector = OpenWindowDetector(temp_drop=2.0, detection_window=300)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Temperature rises instead of drops
        detector.record_temperature(now, 18.0)
        detector.record_temperature(now + timedelta(seconds=300), 21.0)

        assert detector.get_state() == "normal"
        assert detector.should_pause() is False

    def test_multiple_detection_cycles(self):
        """Test handling multiple detection and resume cycles."""
        detector = OpenWindowDetector(
            temp_drop=2.0,
            detection_window=300,
            pause_duration=600,
            cooldown=300,
        )
        now = datetime(2024, 1, 1, 12, 0, 0)

        # First detection at t=300s
        detector.record_temperature(now, 20.0)
        detector.record_temperature(now + timedelta(seconds=300), 17.5)
        assert detector.get_state() == "paused"

        # Auto-resume at t=901s
        detector.record_temperature(now + timedelta(seconds=901), 17.5)
        assert detector.get_state() == "normal"

        # Wait for cooldown to expire, then trigger again
        detector.record_temperature(now + timedelta(seconds=1300), 20.0)
        detector.record_temperature(now + timedelta(seconds=1600), 17.5)
        assert detector.get_state() == "paused"

        # Second auto-resume
        detector.record_temperature(now + timedelta(seconds=2201), 17.5)
        assert detector.get_state() == "normal"

    def test_state_property_matches_internal_state(self):
        """Test get_state() returns correct state string."""
        detector = OpenWindowDetector(temp_drop=2.0, detection_window=300)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Normal state
        assert detector.get_state() == "normal"

        # Paused state
        detector.record_temperature(now, 20.0)
        detector.record_temperature(now + timedelta(seconds=300), 17.5)
        assert detector.get_state() == "paused"

    def test_should_pause_returns_false_when_suppressed(self):
        """Test should_pause returns False when detection is suppressed."""
        detector = OpenWindowDetector(temp_drop=2.0, detection_window=300)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Suppress detection
        detector.suppress_detection("setpoint_decrease", current_time=now)

        # Record temperature drop
        detector.record_temperature(now, 20.0)
        detector.record_temperature(now + timedelta(seconds=300), 17.5)

        # Should not pause due to suppression
        assert detector.should_pause() is False

    def test_rapid_successive_drops(self):
        """Test handling of rapid successive temperature drops."""
        detector = OpenWindowDetector(temp_drop=2.0, detection_window=300)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # First drop
        detector.record_temperature(now, 20.0)
        detector.record_temperature(now + timedelta(seconds=60), 17.5)
        assert detector.get_state() == "paused"

        # Continue dropping while paused
        detector.record_temperature(now + timedelta(seconds=120), 16.0)
        detector.record_temperature(now + timedelta(seconds=180), 14.5)

        # Should remain paused
        assert detector.get_state() == "paused"

    def test_detection_with_fluctuating_temps(self):
        """Test detection with fluctuating temperatures around threshold."""
        detector = OpenWindowDetector(temp_drop=2.0, detection_window=300)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Fluctuating temps that don't quite reach threshold
        detector.record_temperature(now, 20.0)
        detector.record_temperature(now + timedelta(seconds=60), 19.0)
        detector.record_temperature(now + timedelta(seconds=120), 18.5)
        detector.record_temperature(now + timedelta(seconds=180), 18.8)
        detector.record_temperature(now + timedelta(seconds=240), 18.3)
        detector.record_temperature(now + timedelta(seconds=300), 18.2)

        # Max drop is 1.8°C (20.0 - 18.2), below 2.0°C threshold
        assert detector.get_state() == "normal"

    def test_buffer_cleanup_on_pause_resume(self):
        """Test temperature history is preserved through pause/resume cycle."""
        detector = OpenWindowDetector(
            temp_drop=2.0,
            detection_window=300,
            pause_duration=600,
        )
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Trigger detection
        detector.record_temperature(now, 20.0)
        detector.record_temperature(now + timedelta(seconds=300), 17.5)
        assert detector.get_state() == "paused"

        # Continue recording during pause
        detector.record_temperature(now + timedelta(seconds=400), 17.0)
        detector.record_temperature(now + timedelta(seconds=500), 16.5)

        # History should still be maintained
        assert len(detector._temp_history) > 0

        # After resume, history continues
        detector.record_temperature(now + timedelta(seconds=901), 16.0)
        assert detector.get_state() == "normal"
        assert len(detector._temp_history) > 0
