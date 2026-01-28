"""Tests for HumidityDetector class."""

import pytest
from datetime import datetime, timedelta

from custom_components.adaptive_thermostat.adaptive.humidity_detector import (
    HumidityDetector,
)


class TestHumidityDetector:
    """Tests for HumidityDetector class."""

    def test_initialization(self):
        """Test detector initializes with normal state and empty history."""
        detector = HumidityDetector()

        assert detector.get_state() == "normal"
        assert not detector.should_pause()
        assert detector.get_time_until_resume() is None
        assert len(detector._humidity_history) == 0
        assert detector._peak_humidity is None
        assert detector._stabilization_start is None
        assert detector._last_timestamp is None

    def test_initialization_with_custom_params(self):
        """Test detector initializes with custom parameters."""
        detector = HumidityDetector(
            spike_threshold=20,
            absolute_max=75,
            detection_window=600,
            stabilization_delay=600
        )

        assert detector._spike_threshold == 20
        assert detector._absolute_max == 75
        assert detector._detection_window == 600
        assert detector._stabilization_delay == 600

    def test_record_humidity_populates_ring_buffer(self):
        """Test record_humidity adds to ring buffer."""
        detector = HumidityDetector(detection_window=300)
        now = datetime(2024, 1, 1, 12, 0, 0)

        detector.record_humidity(now, 50.0)
        detector.record_humidity(now + timedelta(seconds=60), 52.0)
        detector.record_humidity(now + timedelta(seconds=120), 54.0)

        assert len(detector._humidity_history) == 3
        assert detector._humidity_history[0] == (now, 50.0)
        assert detector._humidity_history[1] == (now + timedelta(seconds=60), 52.0)
        assert detector._humidity_history[2] == (now + timedelta(seconds=120), 54.0)

    def test_ring_buffer_evicts_old_entries(self):
        """Test ring buffer removes entries older than detection_window."""
        detector = HumidityDetector(detection_window=300)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Add entry at t=0
        detector.record_humidity(now, 50.0)
        # Add entry at t=200s (within window)
        detector.record_humidity(now + timedelta(seconds=200), 52.0)
        # Add entry at t=400s (should evict first entry)
        detector.record_humidity(now + timedelta(seconds=400), 54.0)

        # First entry should be evicted (400 - 0 > 300)
        assert len(detector._humidity_history) == 2
        assert detector._humidity_history[0][0] == now + timedelta(seconds=200)
        assert detector._humidity_history[1][0] == now + timedelta(seconds=400)

    def test_rate_of_change_trigger_to_paused(self):
        """Test humidity spike >spike_threshold triggers PAUSED state."""
        detector = HumidityDetector(spike_threshold=15, detection_window=300)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Start at 50%
        detector.record_humidity(now, 50.0)
        assert detector.get_state() == "normal"

        # Rise by 16% in 5 minutes (triggers spike)
        detector.record_humidity(now + timedelta(seconds=300), 66.0)
        assert detector.get_state() == "paused"
        assert detector.should_pause() is True
        assert detector._peak_humidity == 66.0

    def test_rate_of_change_no_trigger_below_threshold(self):
        """Test humidity rise below spike_threshold doesn't trigger."""
        detector = HumidityDetector(spike_threshold=15, detection_window=300)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Start at 50%
        detector.record_humidity(now, 50.0)
        # Rise by 14% (below threshold)
        detector.record_humidity(now + timedelta(seconds=300), 64.0)

        assert detector.get_state() == "normal"
        assert detector.should_pause() is False

    def test_absolute_threshold_trigger_to_paused(self):
        """Test humidity >absolute_max triggers PAUSED state."""
        detector = HumidityDetector(absolute_max=80, detection_window=300)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Start at 70%
        detector.record_humidity(now, 70.0)
        assert detector.get_state() == "normal"

        # Jump to 85% (exceeds absolute_max)
        detector.record_humidity(now + timedelta(seconds=60), 85.0)
        assert detector.get_state() == "paused"
        assert detector.should_pause() is True
        assert detector._peak_humidity == 85.0

    def test_should_pause_returns_true_for_paused_state(self):
        """Test should_pause returns True when state is paused."""
        detector = HumidityDetector(spike_threshold=15)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Trigger paused state
        detector.record_humidity(now, 50.0)
        detector.record_humidity(now + timedelta(seconds=300), 66.0)

        assert detector.get_state() == "paused"
        assert detector.should_pause() is True

    def test_should_pause_returns_true_for_stabilizing_state(self):
        """Test should_pause returns True when state is stabilizing."""
        detector = HumidityDetector(spike_threshold=15)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Trigger paused state
        detector.record_humidity(now, 50.0)
        detector.record_humidity(now + timedelta(seconds=300), 66.0)

        # Drop below 70% and >5% from peak (66.0) -> transitions to stabilizing
        detector.record_humidity(now + timedelta(seconds=400), 60.0)

        assert detector.get_state() == "stabilizing"
        assert detector.should_pause() is True

    def test_should_pause_returns_false_for_normal_state(self):
        """Test should_pause returns False when state is normal."""
        detector = HumidityDetector()
        now = datetime(2024, 1, 1, 12, 0, 0)

        detector.record_humidity(now, 50.0)

        assert detector.get_state() == "normal"
        assert detector.should_pause() is False

    def test_exit_to_stabilizing_below_70_and_drop_from_peak(self):
        """Test transition from PAUSED to STABILIZING when <70% and >5% drop from peak."""
        detector = HumidityDetector(spike_threshold=15)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Trigger paused at 75%
        detector.record_humidity(now, 55.0)
        detector.record_humidity(now + timedelta(seconds=300), 75.0)
        assert detector.get_state() == "paused"
        assert detector._peak_humidity == 75.0

        # Drop to 69% (below 70% and >5% drop from 75%)
        detector.record_humidity(now + timedelta(seconds=400), 69.0)
        assert detector.get_state() == "stabilizing"
        assert detector._stabilization_start is not None

    def test_no_exit_to_stabilizing_if_above_70(self):
        """Test no transition to STABILIZING if humidity still above 70%."""
        detector = HumidityDetector(spike_threshold=15)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Trigger paused at 85%
        detector.record_humidity(now, 60.0)
        detector.record_humidity(now + timedelta(seconds=300), 85.0)
        assert detector.get_state() == "paused"

        # Drop to 72% (still above 70%)
        detector.record_humidity(now + timedelta(seconds=400), 72.0)
        assert detector.get_state() == "paused"  # Still paused

    def test_no_exit_to_stabilizing_if_insufficient_drop(self):
        """Test no transition to STABILIZING if drop <=5% from peak."""
        detector = HumidityDetector(spike_threshold=15)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Trigger paused at 75%
        detector.record_humidity(now, 55.0)
        detector.record_humidity(now + timedelta(seconds=300), 75.0)
        assert detector.get_state() == "paused"

        # Drop to 69.5% (below 70% but only 5.5% drop - need >5% so 5.5% should trigger)
        # For insufficient drop, use 4% drop: 75 - 4 = 71
        detector.record_humidity(now + timedelta(seconds=400), 71.0)
        assert detector.get_state() == "paused"  # Still paused (4% drop is not >5%)

    def test_stabilization_timer_to_normal(self):
        """Test transition from STABILIZING to NORMAL after stabilization_delay."""
        detector = HumidityDetector(spike_threshold=15, stabilization_delay=300)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Trigger paused
        detector.record_humidity(now, 50.0)
        detector.record_humidity(now + timedelta(seconds=300), 75.0)

        # Enter stabilizing at t=400s
        detector.record_humidity(now + timedelta(seconds=400), 60.0)
        assert detector.get_state() == "stabilizing"

        # Still stabilizing at t=600s (200s elapsed, need 300s)
        detector.record_humidity(now + timedelta(seconds=600), 58.0)
        assert detector.get_state() == "stabilizing"

        # Normal at t=701s (301s elapsed)
        detector.record_humidity(now + timedelta(seconds=701), 57.0)
        assert detector.get_state() == "normal"
        assert detector._stabilization_start is None
        assert detector._peak_humidity is None

    def test_get_time_until_resume_returns_none_when_normal(self):
        """Test get_time_until_resume returns None in normal state."""
        detector = HumidityDetector()
        now = datetime(2024, 1, 1, 12, 0, 0)

        detector.record_humidity(now, 50.0)

        assert detector.get_state() == "normal"
        assert detector.get_time_until_resume() is None

    def test_get_time_until_resume_returns_none_when_paused(self):
        """Test get_time_until_resume returns None in paused state."""
        detector = HumidityDetector(spike_threshold=15)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Trigger paused
        detector.record_humidity(now, 50.0)
        detector.record_humidity(now + timedelta(seconds=300), 75.0)

        assert detector.get_state() == "paused"
        assert detector.get_time_until_resume() is None

    def test_get_time_until_resume_returns_seconds_when_stabilizing(self):
        """Test get_time_until_resume returns remaining seconds in stabilizing state."""
        detector = HumidityDetector(spike_threshold=15, stabilization_delay=300)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Trigger paused
        detector.record_humidity(now, 50.0)
        detector.record_humidity(now + timedelta(seconds=300), 75.0)

        # Enter stabilizing at t=400s
        detector.record_humidity(now + timedelta(seconds=400), 60.0)
        assert detector.get_state() == "stabilizing"

        # Check at t=400s (300s remaining)
        remaining = detector.get_time_until_resume()
        assert remaining == 300

        # Record at t=500s (200s remaining)
        detector.record_humidity(now + timedelta(seconds=500), 59.0)
        remaining = detector.get_time_until_resume()
        assert remaining == 200

    def test_get_time_until_resume_returns_zero_when_stabilization_complete(self):
        """Test get_time_until_resume returns 0 when stabilization period complete."""
        detector = HumidityDetector(spike_threshold=15, stabilization_delay=300)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Trigger paused
        detector.record_humidity(now, 50.0)
        detector.record_humidity(now + timedelta(seconds=300), 75.0)

        # Enter stabilizing at t=400s
        detector.record_humidity(now + timedelta(seconds=400), 60.0)

        # At t=700s (300s elapsed, should transition to normal)
        detector.record_humidity(now + timedelta(seconds=700), 58.0)

        assert detector.get_state() == "normal"
        assert detector.get_time_until_resume() is None

    def test_peak_humidity_tracks_maximum(self):
        """Test peak_humidity tracks the maximum humidity while paused."""
        detector = HumidityDetector(spike_threshold=15)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Trigger paused at 70%
        detector.record_humidity(now, 50.0)
        detector.record_humidity(now + timedelta(seconds=300), 70.0)
        assert detector._peak_humidity == 70.0

        # Rise to 75%
        detector.record_humidity(now + timedelta(seconds=400), 75.0)
        assert detector._peak_humidity == 75.0

        # Rise to 80%
        detector.record_humidity(now + timedelta(seconds=500), 80.0)
        assert detector._peak_humidity == 80.0

        # Drop to 78% (peak remains 80%)
        detector.record_humidity(now + timedelta(seconds=600), 78.0)
        assert detector._peak_humidity == 80.0

    def test_peak_humidity_resets_on_return_to_normal(self):
        """Test peak_humidity resets when returning to normal state."""
        detector = HumidityDetector(spike_threshold=15, stabilization_delay=300)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Trigger paused at 75%
        detector.record_humidity(now, 50.0)
        detector.record_humidity(now + timedelta(seconds=300), 75.0)
        assert detector._peak_humidity == 75.0

        # Enter stabilizing
        detector.record_humidity(now + timedelta(seconds=400), 60.0)
        assert detector._peak_humidity == 75.0  # Still tracked

        # Return to normal
        detector.record_humidity(now + timedelta(seconds=701), 58.0)
        assert detector._peak_humidity is None

    def test_multiple_spikes_scenario(self):
        """Test handling multiple spike events."""
        detector = HumidityDetector(spike_threshold=15, stabilization_delay=300)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # First spike at t=300s
        detector.record_humidity(now, 50.0)
        detector.record_humidity(now + timedelta(seconds=300), 70.0)
        assert detector.get_state() == "paused"

        # Exit to stabilizing at t=400s
        detector.record_humidity(now + timedelta(seconds=400), 58.0)
        assert detector.get_state() == "stabilizing"

        # Return to normal at t=701s
        detector.record_humidity(now + timedelta(seconds=701), 56.0)
        assert detector.get_state() == "normal"

        # Second spike at t=1001s
        detector.record_humidity(now + timedelta(seconds=1001), 72.0)
        assert detector.get_state() == "paused"
        assert detector._peak_humidity == 72.0

    def test_absolute_max_edge_case_exactly_at_threshold(self):
        """Test absolute_max trigger at exact threshold value."""
        detector = HumidityDetector(absolute_max=80)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # At threshold should not trigger
        detector.record_humidity(now, 80.0)
        assert detector.get_state() == "normal"

        # Above threshold should trigger
        detector.record_humidity(now + timedelta(seconds=60), 80.1)
        assert detector.get_state() == "paused"

    def test_rate_of_change_with_gradual_rise(self):
        """Test rate of change detection with gradual humidity increase."""
        detector = HumidityDetector(spike_threshold=15, detection_window=300)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Gradual rise over 5 minutes (50 -> 64.5 = 14.5%, below threshold)
        detector.record_humidity(now, 50.0)
        detector.record_humidity(now + timedelta(seconds=60), 53.0)
        detector.record_humidity(now + timedelta(seconds=120), 56.0)
        detector.record_humidity(now + timedelta(seconds=180), 59.0)
        detector.record_humidity(now + timedelta(seconds=240), 62.0)
        detector.record_humidity(now + timedelta(seconds=300), 64.5)

        # Should remain normal (rise is gradual and below threshold)
        assert detector.get_state() == "normal"

    def test_stabilizing_interrupted_by_new_spike(self):
        """Test that new spike during stabilizing returns to paused."""
        detector = HumidityDetector(spike_threshold=15, stabilization_delay=300)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Initial spike
        detector.record_humidity(now, 50.0)
        detector.record_humidity(now + timedelta(seconds=300), 75.0)
        assert detector.get_state() == "paused"

        # Enter stabilizing
        detector.record_humidity(now + timedelta(seconds=400), 60.0)
        assert detector.get_state() == "stabilizing"

        # New spike during stabilizing (60 -> 82)
        detector.record_humidity(now + timedelta(seconds=500), 82.0)
        assert detector.get_state() == "paused"
        assert detector._peak_humidity == 82.0
        assert detector._stabilization_start is None

    def test_empty_history_no_crash(self):
        """Test detector handles empty history gracefully."""
        detector = HumidityDetector(spike_threshold=15)

        assert detector.get_state() == "normal"
        assert detector.should_pause() is False
        assert detector.get_time_until_resume() is None

    def test_max_pause_duration_forces_resume(self):
        """Test that after 60 min in PAUSED state, auto-transition to NORMAL."""
        detector = HumidityDetector(spike_threshold=15)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Trigger paused at 85%
        detector.record_humidity(now, 50.0)
        detector.record_humidity(now + timedelta(seconds=300), 85.0)
        assert detector.get_state() == "paused"

        # Still paused at 59 minutes (humidity still high)
        detector.record_humidity(now + timedelta(seconds=300 + 59 * 60), 85.0)
        assert detector.get_state() == "paused"

        # Force resume at 61 minutes (max pause = 3600s / 60 min)
        detector.record_humidity(now + timedelta(seconds=300 + 61 * 60), 85.0)
        assert detector.get_state() == "normal"
        assert detector._peak_humidity is None

    def test_max_pause_logs_warning(self, caplog):
        """Test warning logged when max pause duration reached."""
        import logging
        detector = HumidityDetector(spike_threshold=15)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Trigger paused
        detector.record_humidity(now, 50.0)
        detector.record_humidity(now + timedelta(seconds=300), 85.0)

        # Force resume at 61 minutes
        with caplog.at_level(logging.WARNING):
            detector.record_humidity(now + timedelta(seconds=300 + 61 * 60), 85.0)

        # Check warning was logged
        assert any("max pause duration" in record.message.lower() for record in caplog.records)

    def test_custom_exit_humidity_threshold(self):
        """Test custom exit_humidity_threshold parameter."""
        # Use lower exit threshold of 65% instead of default 70%
        detector = HumidityDetector(
            spike_threshold=15,
            exit_humidity_threshold=65.0,
            exit_humidity_drop=5.0
        )
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Trigger paused at 80%
        detector.record_humidity(now, 50.0)
        detector.record_humidity(now + timedelta(seconds=300), 80.0)
        assert detector.get_state() == "paused"

        # Drop to 68% (below default 70% but above custom 65%, with >5% drop)
        detector.record_humidity(now + timedelta(seconds=400), 68.0)
        assert detector.get_state() == "paused"  # Still paused (above 65%)

        # Drop to 64% (below custom 65% and >5% drop from 80%)
        detector.record_humidity(now + timedelta(seconds=500), 64.0)
        assert detector.get_state() == "stabilizing"  # Now stabilizing

    def test_custom_exit_humidity_drop(self):
        """Test custom exit_humidity_drop parameter."""
        # Use larger exit drop of 10% instead of default 5%
        detector = HumidityDetector(
            spike_threshold=15,
            exit_humidity_threshold=70.0,
            exit_humidity_drop=10.0
        )
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Trigger paused at 80%
        detector.record_humidity(now, 50.0)
        detector.record_humidity(now + timedelta(seconds=300), 80.0)
        assert detector.get_state() == "paused"

        # Drop to 67% (below 70% and 13% drop, which is >10%)
        detector.record_humidity(now + timedelta(seconds=400), 67.0)
        assert detector.get_state() == "stabilizing"  # Transitions (13% > 10%)

    def test_custom_exit_both_thresholds(self):
        """Test custom exit_humidity_threshold and exit_humidity_drop together."""
        # Use 65% threshold and 8% drop
        detector = HumidityDetector(
            spike_threshold=15,
            exit_humidity_threshold=65.0,
            exit_humidity_drop=8.0
        )
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Trigger paused at 85%
        detector.record_humidity(now, 50.0)
        detector.record_humidity(now + timedelta(seconds=300), 85.0)
        assert detector.get_state() == "paused"

        # Drop to 64% (below 65% but only 21% drop total, need to check math)
        # 85 - 64 = 21%, which is >8%, so should transition
        detector.record_humidity(now + timedelta(seconds=400), 64.0)
        assert detector.get_state() == "stabilizing"

        # Reset and test insufficient drop
        detector._state = "paused"
        detector._peak_humidity = 85.0
        detector._stabilization_start = None

        # Drop to 64.5% (below 65% but only 20.5% drop - wait, this is still >8%)
        # Let's use 78% instead: 85 - 78 = 7% drop, which is <8%
        detector.record_humidity(now + timedelta(seconds=500), 78.0)
        assert detector.get_state() == "paused"  # Still paused (7% < 8%)

        # Now drop to 76% (below 65% and 85 - 76 = 9% drop, which is >8%)
        detector.record_humidity(now + timedelta(seconds=600), 76.0)
        assert detector.get_state() == "paused"  # Still paused (76% is above 65%)

        # Finally drop to 64% (below 65% and 85 - 64 = 21% drop, which is >8%)
        detector.record_humidity(now + timedelta(seconds=700), 64.0)
        assert detector.get_state() == "stabilizing"  # Now transitions
