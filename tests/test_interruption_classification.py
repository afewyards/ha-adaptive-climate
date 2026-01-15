"""Tests for cycle interruption classification."""

from datetime import datetime
import pytest

from custom_components.adaptive_thermostat.adaptive.cycle_analysis import (
    InterruptionType,
    InterruptionClassifier,
)


class TestInterruptionClassification:
    """Test interruption classification logic."""

    def test_setpoint_major_device_inactive(self):
        """Test major setpoint change with device inactive."""
        interruption_type = InterruptionClassifier.classify_setpoint_change(
            old_temp=20.0,
            new_temp=21.0,  # 1.0°C change > 0.5°C threshold
            is_device_active=False
        )
        assert interruption_type == InterruptionType.SETPOINT_MAJOR

    def test_setpoint_minor_small_change(self):
        """Test minor setpoint change (≤0.5°C)."""
        interruption_type = InterruptionClassifier.classify_setpoint_change(
            old_temp=20.0,
            new_temp=20.3,  # 0.3°C change ≤ 0.5°C threshold
            is_device_active=False
        )
        assert interruption_type == InterruptionType.SETPOINT_MINOR

    def test_setpoint_minor_device_active(self):
        """Test setpoint change with device active (always minor)."""
        interruption_type = InterruptionClassifier.classify_setpoint_change(
            old_temp=20.0,
            new_temp=22.0,  # 2.0°C change but device active
            is_device_active=True
        )
        assert interruption_type == InterruptionType.SETPOINT_MINOR

    def test_mode_change_heating_to_off(self):
        """Test mode change from heat to off during heating."""
        interruption_type = InterruptionClassifier.classify_mode_change(
            old_mode="heat",
            new_mode="off",
            current_cycle_state="heating"
        )
        assert interruption_type == InterruptionType.MODE_CHANGE

    def test_mode_change_heating_to_cool(self):
        """Test mode change from heat to cool during heating."""
        interruption_type = InterruptionClassifier.classify_mode_change(
            old_mode="heat",
            new_mode="cool",
            current_cycle_state="heating"
        )
        assert interruption_type == InterruptionType.MODE_CHANGE

    def test_mode_change_compatible(self):
        """Test compatible mode change (no interruption)."""
        interruption_type = InterruptionClassifier.classify_mode_change(
            old_mode="heat",
            new_mode="auto",
            current_cycle_state="heating"
        )
        assert interruption_type is None

    def test_mode_change_settling_to_off(self):
        """Test mode change from settling to off."""
        interruption_type = InterruptionClassifier.classify_mode_change(
            old_mode="heat",
            new_mode="off",
            current_cycle_state="settling"
        )
        assert interruption_type == InterruptionType.MODE_CHANGE

    def test_contact_sensor_exceeds_grace_period(self):
        """Test contact sensor interruption beyond grace period."""
        interruption_type = InterruptionClassifier.classify_contact_sensor(
            contact_open_duration=400  # 400s > 300s grace period
        )
        assert interruption_type == InterruptionType.CONTACT_SENSOR

    def test_contact_sensor_within_grace_period(self):
        """Test contact sensor within grace period (no interruption)."""
        interruption_type = InterruptionClassifier.classify_contact_sensor(
            contact_open_duration=200  # 200s < 300s grace period
        )
        assert interruption_type is None

    def test_interruption_classifier_module_exists(self):
        """Test that InterruptionClassifier module can be imported."""
        assert InterruptionClassifier is not None
        assert InterruptionType is not None
        assert hasattr(InterruptionClassifier, 'classify_setpoint_change')
        assert hasattr(InterruptionClassifier, 'classify_mode_change')
        assert hasattr(InterruptionClassifier, 'classify_contact_sensor')
