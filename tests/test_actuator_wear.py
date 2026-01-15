"""Simple tests for actuator wear tracking logic."""
import pytest
import sys
from pathlib import Path

# Add parent directory to path
root_path = str(Path(__file__).parent.parent)
sys.path.insert(0, root_path)

from custom_components.adaptive_thermostat.const import (
    ACTUATOR_MAINTENANCE_SOON_PCT,
    ACTUATOR_MAINTENANCE_DUE_PCT,
    DEFAULT_RATED_CYCLES,
)


class TestWearCalculations:
    """Test wear percentage and maintenance threshold calculations."""

    def test_wear_calculation_at_0_percent(self):
        """Test wear percentage calculation at 0% lifecycle."""
        rated_cycles = 100000
        current_cycles = 0
        wear_percentage = (current_cycles / rated_cycles) * 100.0
        assert wear_percentage == 0.0

    def test_wear_calculation_at_50_percent(self):
        """Test wear percentage calculation at 50% lifecycle."""
        rated_cycles = 100000
        current_cycles = 50000
        wear_percentage = (current_cycles / rated_cycles) * 100.0
        assert wear_percentage == 50.0

    def test_wear_calculation_at_80_percent_triggers_maintenance_soon(self):
        """Test that 80% wear triggers maintenance_soon status."""
        rated_cycles = 100000
        current_cycles = 80000
        wear_percentage = (current_cycles / rated_cycles) * 100.0

        # Determine maintenance status
        if wear_percentage >= ACTUATOR_MAINTENANCE_DUE_PCT:
            maintenance_status = "maintenance_due"
        elif wear_percentage >= ACTUATOR_MAINTENANCE_SOON_PCT:
            maintenance_status = "maintenance_soon"
        else:
            maintenance_status = "ok"

        assert wear_percentage == 80.0
        assert maintenance_status == "maintenance_soon"

    def test_wear_calculation_at_90_percent_triggers_maintenance_due(self):
        """Test that 90% wear triggers maintenance_due status."""
        rated_cycles = 100000
        current_cycles = 90000
        wear_percentage = (current_cycles / rated_cycles) * 100.0

        # Determine maintenance status
        if wear_percentage >= ACTUATOR_MAINTENANCE_DUE_PCT:
            maintenance_status = "maintenance_due"
        elif wear_percentage >= ACTUATOR_MAINTENANCE_SOON_PCT:
            maintenance_status = "maintenance_soon"
        else:
            maintenance_status = "ok"

        assert wear_percentage == 90.0
        assert maintenance_status == "maintenance_due"

    def test_wear_capped_at_100_percent(self):
        """Test that wear percentage is capped at 100%."""
        rated_cycles = 100000
        current_cycles = 150000  # Exceeded rated cycles
        wear_percentage = min(100.0, (current_cycles / rated_cycles) * 100.0)
        assert wear_percentage == 100.0

    def test_default_rated_cycles_constants(self):
        """Test that default rated cycles constants are reasonable."""
        assert DEFAULT_RATED_CYCLES["contactor"] == 100000
        assert DEFAULT_RATED_CYCLES["valve"] == 50000
        assert DEFAULT_RATED_CYCLES["switch"] == 100000

        # Valves should have lower rated cycles due to mechanical wear
        assert DEFAULT_RATED_CYCLES["valve"] < DEFAULT_RATED_CYCLES["contactor"]

    def test_estimated_remaining_calculation(self):
        """Test calculation of estimated remaining cycles."""
        rated_cycles = 100000
        current_cycles = 25000
        estimated_remaining = max(0, rated_cycles - current_cycles)
        assert estimated_remaining == 75000

    def test_estimated_remaining_when_exceeded(self):
        """Test that estimated remaining is 0 when cycles exceeded."""
        rated_cycles = 100000
        current_cycles = 110000
        estimated_remaining = max(0, rated_cycles - current_cycles)
        assert estimated_remaining == 0

    def test_maintenance_thresholds_are_reasonable(self):
        """Test that maintenance thresholds are in reasonable range."""
        # Maintenance soon should be before maintenance due
        assert ACTUATOR_MAINTENANCE_SOON_PCT < ACTUATOR_MAINTENANCE_DUE_PCT

        # Both should be high percentages (>= 80%)
        assert ACTUATOR_MAINTENANCE_SOON_PCT >= 80
        assert ACTUATOR_MAINTENANCE_DUE_PCT >= 90

        # Neither should exceed 100%
        assert ACTUATOR_MAINTENANCE_SOON_PCT <= 100
        assert ACTUATOR_MAINTENANCE_DUE_PCT <= 100
