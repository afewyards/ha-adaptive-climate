import pytest
from custom_components.adaptive_climate.managers.heat_pipeline import HeatPipeline


class TestHeatPipeline:
    """Tests for committed heat tracking."""

    def test_no_committed_heat_when_valve_closed(self):
        """No committed heat when valve never opened."""
        pipeline = HeatPipeline(transport_delay=600.0, valve_time=120.0)
        assert pipeline.committed_heat_remaining(now=1000.0) == 0.0

    def test_committed_heat_while_valve_opening(self):
        """Committed heat accumulates as valve opens."""
        pipeline = HeatPipeline(transport_delay=600.0, valve_time=120.0)
        pipeline.valve_opened(at=0.0)
        assert pipeline.committed_heat_remaining(now=60.0) == 60.0
        assert pipeline.committed_heat_remaining(now=120.0) == 120.0

    def test_committed_heat_caps_at_transport_delay(self):
        """Committed heat maxes at transport_delay."""
        pipeline = HeatPipeline(transport_delay=600.0, valve_time=120.0)
        pipeline.valve_opened(at=0.0)
        assert pipeline.committed_heat_remaining(now=1000.0) == 600.0

    def test_committed_heat_drains_after_close(self):
        """Committed heat drains after valve closes."""
        pipeline = HeatPipeline(transport_delay=600.0, valve_time=120.0)
        pipeline.valve_opened(at=0.0)
        pipeline.valve_closed(at=700.0)
        assert pipeline.committed_heat_remaining(now=700.0) == 600.0
        assert pipeline.committed_heat_remaining(now=1000.0) == 300.0
        assert pipeline.committed_heat_remaining(now=1300.0) == 0.0

    def test_committed_heat_no_negative(self):
        """Committed heat never goes negative."""
        pipeline = HeatPipeline(transport_delay=600.0, valve_time=120.0)
        pipeline.valve_opened(at=0.0)
        pipeline.valve_closed(at=100.0)
        assert pipeline.committed_heat_remaining(now=10000.0) == 0.0

    def test_valve_open_duration_calculation(self):
        """Calculate how long to keep valve open for target duty."""
        pipeline = HeatPipeline(transport_delay=600.0, valve_time=120.0)
        duration = pipeline.calculate_valve_open_duration(
            requested_duty=0.5,
            pwm_period=900.0,
            committed=0.0,
        )
        # Need 450s heat + 60s half-valve = 510s
        assert duration == 510.0

    def test_valve_open_duration_with_committed(self):
        """Committed heat reduces needed valve open time."""
        pipeline = HeatPipeline(transport_delay=600.0, valve_time=120.0)
        duration = pipeline.calculate_valve_open_duration(
            requested_duty=0.5,
            pwm_period=900.0,
            committed=200.0,
        )
        # Need (450-200)=250s heat + 60s half-valve = 310s
        assert duration == 310.0

    def test_valve_open_duration_zero_when_committed_exceeds(self):
        """No valve open needed if committed heat exceeds request."""
        pipeline = HeatPipeline(transport_delay=600.0, valve_time=120.0)
        duration = pipeline.calculate_valve_open_duration(
            requested_duty=0.3,
            pwm_period=900.0,
            committed=400.0,
        )
        assert duration == 0.0

    def test_reset_clears_state(self):
        """Reset clears valve timing state."""
        pipeline = HeatPipeline(transport_delay=600.0, valve_time=120.0)
        pipeline.valve_opened(at=0.0)
        pipeline.reset()
        assert pipeline.committed_heat_remaining(now=100.0) == 0.0
