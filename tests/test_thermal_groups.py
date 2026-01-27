"""Tests for thermal groups module."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock
from custom_components.adaptive_thermostat.adaptive.thermal_groups import (
    ThermalGroup,
    ThermalGroupManager,
    TransferHistory,
    validate_thermal_groups_config,
    GROUP_TYPE_OPEN_PLAN,
)


class TestThermalGroupValidation:
    """Test configuration validation for thermal groups."""

    def test_valid_open_plan_config(self):
        """Test valid open plan configuration with leader."""
        config = {
            "name": "downstairs",
            "zones": ["living_room", "kitchen", "dining"],
            "group_type": "open_plan",
            "leader": "living_room"
        }

        group = ThermalGroup(**config)

        assert group.name == "downstairs"
        assert group.zones == ["living_room", "kitchen", "dining"]
        assert group.group_type == GROUP_TYPE_OPEN_PLAN
        assert group.leader == "living_room"

    def test_valid_receives_from_config(self):
        """Test valid receives_from config with transfer_factor and delay."""
        config = {
            "name": "upstairs",
            "zones": ["bedroom1", "bedroom2"],
            "group_type": "open_plan",
            "leader": "bedroom1",
            "receives_from": "downstairs",
            "transfer_factor": 0.3,
            "delay_minutes": 15
        }

        group = ThermalGroup(**config)

        assert group.receives_from == "downstairs"
        assert group.transfer_factor == 0.3
        assert group.delay_minutes == 15

    def test_invalid_missing_leader(self):
        """Test invalid config: missing leader for open_plan type."""
        config = {
            "name": "downstairs",
            "zones": ["living_room", "kitchen"],
            "group_type": "open_plan"
            # Missing leader
        }

        with pytest.raises(ValueError, match="requires a leader zone"):
            ThermalGroup(**config)

    def test_invalid_leader_not_in_zones(self):
        """Test invalid config: leader not in zones list."""
        config = {
            "name": "downstairs",
            "zones": ["living_room", "kitchen"],
            "group_type": "open_plan",
            "leader": "dining_room"  # Not in zones list
        }

        with pytest.raises(ValueError, match="must be in zones list"):
            ThermalGroup(**config)

    def test_invalid_empty_zones(self):
        """Test invalid config: empty zones list."""
        config = {
            "name": "empty_group",
            "zones": [],
            "group_type": "open_plan",
            "leader": None
        }

        with pytest.raises(ValueError, match="must have at least one zone"):
            ThermalGroup(**config)

    def test_invalid_transfer_factor_too_high(self):
        """Test invalid config: transfer_factor > 1.0."""
        config = {
            "name": "upstairs",
            "zones": ["bedroom1"],
            "group_type": "open_plan",
            "leader": "bedroom1",
            "receives_from": "downstairs",
            "transfer_factor": 1.5  # Invalid: > 1.0
        }

        with pytest.raises(ValueError, match="transfer_factor must be between 0.0 and 1.0"):
            ThermalGroup(**config)

    def test_invalid_transfer_factor_negative(self):
        """Test invalid config: negative transfer_factor."""
        config = {
            "name": "upstairs",
            "zones": ["bedroom1"],
            "group_type": "open_plan",
            "leader": "bedroom1",
            "receives_from": "downstairs",
            "transfer_factor": -0.2  # Invalid: negative
        }

        with pytest.raises(ValueError, match="transfer_factor must be between 0.0 and 1.0"):
            ThermalGroup(**config)

    def test_invalid_negative_delay(self):
        """Test invalid config: negative delay_minutes."""
        config = {
            "name": "upstairs",
            "zones": ["bedroom1"],
            "group_type": "open_plan",
            "leader": "bedroom1",
            "receives_from": "downstairs",
            "transfer_factor": 0.3,
            "delay_minutes": -5  # Invalid: negative
        }

        with pytest.raises(ValueError, match="delay_minutes must be non-negative"):
            ThermalGroup(**config)

    def test_invalid_group_type(self):
        """Test invalid config: unknown group type."""
        config = {
            "name": "test_group",
            "zones": ["zone1"],
            "group_type": "invalid_type",  # Invalid type
            "leader": "zone1"
        }

        with pytest.raises(ValueError, match="Invalid group_type"):
            ThermalGroup(**config)


class TestThermalGroupManagerValidation:
    """Test ThermalGroupManager configuration validation."""

    def test_duplicate_zone_in_multiple_groups(self):
        """Test invalid: zone assigned to multiple groups."""
        hass = MagicMock()
        config = [
            {
                "name": "downstairs",
                "zones": ["living_room", "kitchen"],
                "leader": "living_room"
            },
            {
                "name": "upstairs",
                "zones": ["bedroom1", "kitchen"],  # kitchen duplicated
                "leader": "bedroom1"
            }
        ]

        with pytest.raises(ValueError, match="Zone 'kitchen' is already in group"):
            ThermalGroupManager(hass, config)

    def test_receives_from_non_existent_group(self):
        """Test invalid: receives_from references non-existent group."""
        hass = MagicMock()
        config = [
            {
                "name": "upstairs",
                "zones": ["bedroom1"],
                "leader": "bedroom1",
                "receives_from": "nonexistent_group",  # Does not exist
                "transfer_factor": 0.3
            }
        ]

        with pytest.raises(ValueError, match="receives_from unknown group"):
            ThermalGroupManager(hass, config)

    def test_self_reference_receives_from(self):
        """Test invalid: group cannot receive from itself."""
        hass = MagicMock()
        config = [
            {
                "name": "downstairs",
                "zones": ["living_room"],
                "leader": "living_room",
                "receives_from": "downstairs",  # Self-reference
                "transfer_factor": 0.3
            }
        ]

        with pytest.raises(ValueError, match="cannot receive from itself"):
            ThermalGroupManager(hass, config)

    def test_missing_required_name_field(self):
        """Test invalid: missing required 'name' field."""
        hass = MagicMock()
        config = [
            {
                # Missing "name"
                "zones": ["living_room"],
                "leader": "living_room"
            }
        ]

        with pytest.raises((ValueError, KeyError)):
            ThermalGroupManager(hass, config)

    def test_missing_required_zones_field(self):
        """Test invalid: missing required 'zones' field."""
        hass = MagicMock()
        config = [
            {
                "name": "downstairs",
                # Missing "zones"
                "leader": "living_room"
            }
        ]

        with pytest.raises((ValueError, KeyError)):
            ThermalGroupManager(hass, config)


class TestLeaderFollowerTracking:
    """Test leader/follower setpoint tracking."""

    def test_follower_zones_track_leader(self):
        """Test that follower zones are identified correctly."""
        hass = MagicMock()
        config = [
            {
                "name": "downstairs",
                "zones": ["living_room", "kitchen", "dining"],
                "leader": "living_room"
            }
        ]

        manager = ThermalGroupManager(hass, config)

        # Living room is the leader
        assert manager.is_leader_zone("living_room") is True
        assert manager.get_leader_zone("living_room") is None

        # Kitchen and dining are followers
        assert manager.is_leader_zone("kitchen") is False
        assert manager.get_leader_zone("kitchen") == "living_room"

        assert manager.is_leader_zone("dining") is False
        assert manager.get_leader_zone("dining") == "living_room"

    def test_get_follower_zones(self):
        """Test getting all follower zones for a leader."""
        hass = MagicMock()
        config = [
            {
                "name": "downstairs",
                "zones": ["living_room", "kitchen", "dining"],
                "leader": "living_room"
            }
        ]

        manager = ThermalGroupManager(hass, config)

        followers = manager.get_follower_zones("living_room")
        assert set(followers) == {"kitchen", "dining"}
        assert "living_room" not in followers

    def test_non_follower_zones_unaffected(self):
        """Test that zones outside the group are unaffected."""
        hass = MagicMock()
        config = [
            {
                "name": "downstairs",
                "zones": ["living_room", "kitchen"],
                "leader": "living_room"
            }
        ]

        manager = ThermalGroupManager(hass, config)

        # Zone not in any group
        assert manager.get_leader_zone("bedroom") is None
        assert manager.is_leader_zone("bedroom") is False
        assert manager.get_zone_group("bedroom") is None

    def test_should_sync_setpoint_follower(self):
        """Test should_sync_setpoint returns True for followers."""
        hass = MagicMock()
        config = [
            {
                "name": "downstairs",
                "zones": ["living_room", "kitchen"],
                "leader": "living_room"
            }
        ]

        manager = ThermalGroupManager(hass, config)

        # Follower should sync
        assert manager.should_sync_setpoint("kitchen", 21.0) is True

        # Leader should not sync to itself
        assert manager.should_sync_setpoint("living_room", 21.0) is False

        # Non-group zone should not sync
        assert manager.should_sync_setpoint("bedroom", 21.0) is False

    def test_leader_not_affected_by_own_changes(self):
        """Test that leader zone is not affected by its own setpoint changes."""
        hass = MagicMock()
        config = [
            {
                "name": "downstairs",
                "zones": ["living_room", "kitchen"],
                "leader": "living_room"
            }
        ]

        manager = ThermalGroupManager(hass, config)

        # Leader should not sync to its own setpoint
        assert manager.should_sync_setpoint("living_room", 21.0) is False
        assert manager.get_leader_zone("living_room") is None


class TestCrossGroupFeedforward:
    """Test cross-group heat transfer feedforward compensation."""

    def test_feedforward_calculation_with_transfer_factor(self):
        """Test heat transfer compensation calculated correctly."""
        from unittest.mock import patch

        hass = MagicMock()
        config = [
            {
                "name": "downstairs",
                "zones": ["living_room"],
                "leader": "living_room"
            },
            {
                "name": "upstairs",
                "zones": ["bedroom1"],
                "leader": "bedroom1",
                "receives_from": "downstairs",
                "transfer_factor": 0.3,
                "delay_minutes": 10
            }
        ]

        manager = ThermalGroupManager(hass, config)

        # Simulate heat output 10 minutes ago (matching delay)
        current_time = datetime(2024, 1, 15, 10, 0)
        with_delay = current_time - timedelta(minutes=10)
        manager._transfer_history["upstairs"].append(
            TransferHistory(
                source_group="downstairs",
                timestamp=with_delay,
                heat_output=50.0
            )
        )

        # Calculate feedforward for bedroom1
        with patch('custom_components.adaptive_thermostat.adaptive.thermal_groups.dt_util') as mock_dt_util:
            mock_dt_util.utcnow.return_value = current_time
            feedforward = manager.calculate_feedforward("bedroom1")

        # Should be 50% * 0.3 = 15%
        assert feedforward == pytest.approx(15.0, abs=0.01)

    def test_feedforward_respects_delay(self):
        """Test that delay is respected - no compensation before delay elapsed."""
        from unittest.mock import patch

        hass = MagicMock()
        config = [
            {
                "name": "downstairs",
                "zones": ["living_room"],
                "leader": "living_room"
            },
            {
                "name": "upstairs",
                "zones": ["bedroom1"],
                "leader": "bedroom1",
                "receives_from": "downstairs",
                "transfer_factor": 0.3,
                "delay_minutes": 10
            }
        ]

        manager = ThermalGroupManager(hass, config)

        current_time = datetime(2024, 1, 15, 10, 0)

        # Heat output only 2 minutes ago (less than 10 minute delay)
        recent = current_time - timedelta(minutes=2)
        manager._transfer_history["upstairs"].append(
            TransferHistory(
                source_group="downstairs",
                timestamp=recent,
                heat_output=50.0
            )
        )

        # Should return 0 because data is too recent (doesn't match delay)
        with patch('custom_components.adaptive_thermostat.adaptive.thermal_groups.dt_util') as mock_dt_util:
            mock_dt_util.utcnow.return_value = current_time
            feedforward = manager.calculate_feedforward("bedroom1")
            assert feedforward == 0.0

    def test_feedforward_no_history(self):
        """Test feedforward returns 0 when no history available."""
        hass = MagicMock()
        config = [
            {
                "name": "downstairs",
                "zones": ["living_room"],
                "leader": "living_room"
            },
            {
                "name": "upstairs",
                "zones": ["bedroom1"],
                "leader": "bedroom1",
                "receives_from": "downstairs",
                "transfer_factor": 0.3,
                "delay_minutes": 10
            }
        ]

        manager = ThermalGroupManager(hass, config)

        # No history recorded
        feedforward = manager.calculate_feedforward("bedroom1")
        assert feedforward == 0.0

    def test_feedforward_zone_not_receiving(self):
        """Test feedforward returns 0 for zones not receiving from other groups."""
        hass = MagicMock()
        config = [
            {
                "name": "downstairs",
                "zones": ["living_room"],
                "leader": "living_room"
            }
        ]

        manager = ThermalGroupManager(hass, config)

        feedforward = manager.calculate_feedforward("living_room")
        assert feedforward == 0.0

    def test_record_heat_output_updates_history(self):
        """Test that recording heat output updates transfer history."""
        from unittest.mock import patch

        hass = MagicMock()
        config = [
            {
                "name": "downstairs",
                "zones": ["living_room"],
                "leader": "living_room"
            },
            {
                "name": "upstairs",
                "zones": ["bedroom1"],
                "leader": "bedroom1",
                "receives_from": "downstairs",
                "transfer_factor": 0.3,
                "delay_minutes": 10
            }
        ]

        manager = ThermalGroupManager(hass, config)

        # Initially no history
        assert len(manager._transfer_history["upstairs"]) == 0

        # Record heat output for living_room (in downstairs group)
        current_time = datetime(2024, 1, 15, 10, 0)
        with patch('custom_components.adaptive_thermostat.adaptive.thermal_groups.dt_util') as mock_dt_util:
            mock_dt_util.utcnow.return_value = current_time
            manager.record_heat_output("living_room", 60.0)

        # History should be updated
        assert len(manager._transfer_history["upstairs"]) == 1
        assert manager._transfer_history["upstairs"][0].heat_output == 60.0
        assert manager._transfer_history["upstairs"][0].source_group == "downstairs"

    def test_history_limited_to_two_hours(self):
        """Test that history is pruned to last 2 hours."""
        from unittest.mock import patch

        hass = MagicMock()
        config = [
            {
                "name": "downstairs",
                "zones": ["living_room"],
                "leader": "living_room"
            },
            {
                "name": "upstairs",
                "zones": ["bedroom1"],
                "leader": "bedroom1",
                "receives_from": "downstairs",
                "transfer_factor": 0.3,
                "delay_minutes": 10
            }
        ]

        manager = ThermalGroupManager(hass, config)

        current_time = datetime(2024, 1, 15, 10, 0)

        # Add old entry (3 hours ago - should be pruned)
        old_entry = TransferHistory(
            source_group="downstairs",
            timestamp=current_time - timedelta(hours=3),
            heat_output=30.0
        )
        manager._transfer_history["upstairs"].append(old_entry)

        # Add recent entry (1 hour ago - should be kept)
        recent_entry = TransferHistory(
            source_group="downstairs",
            timestamp=current_time - timedelta(hours=1),
            heat_output=40.0
        )
        manager._transfer_history["upstairs"].append(recent_entry)

        # Record new heat output (triggers pruning)
        with patch('custom_components.adaptive_thermostat.adaptive.thermal_groups.dt_util') as mock_dt_util:
            mock_dt_util.utcnow.return_value = current_time
            manager.record_heat_output("living_room", 50.0)

        # Old entry should be removed, recent and new should remain
        assert len(manager._transfer_history["upstairs"]) == 2
        heat_outputs = [h.heat_output for h in manager._transfer_history["upstairs"]]
        assert 30.0 not in heat_outputs  # Old entry pruned
        assert 40.0 in heat_outputs  # Recent entry kept
        assert 50.0 in heat_outputs  # New entry kept

    def test_feedforward_time_tolerance(self):
        """Test feedforward only uses history within 5 minutes of target time."""
        from unittest.mock import patch

        hass = MagicMock()
        config = [
            {
                "name": "downstairs",
                "zones": ["living_room"],
                "leader": "living_room"
            },
            {
                "name": "upstairs",
                "zones": ["bedroom1"],
                "leader": "bedroom1",
                "receives_from": "downstairs",
                "transfer_factor": 0.3,
                "delay_minutes": 10
            }
        ]

        manager = ThermalGroupManager(hass, config)

        current_time = datetime(2024, 1, 15, 10, 0)

        # Heat output 16 minutes ago (6 minutes off from 10 minute delay - outside tolerance)
        off_target = current_time - timedelta(minutes=16)
        manager._transfer_history["upstairs"].append(
            TransferHistory(
                source_group="downstairs",
                timestamp=off_target,
                heat_output=50.0
            )
        )

        # Should return 0 because entry is outside 5-minute tolerance window
        with patch('custom_components.adaptive_thermostat.adaptive.thermal_groups.dt_util') as mock_dt_util:
            mock_dt_util.utcnow.return_value = current_time
            feedforward = manager.calculate_feedforward("bedroom1")
            assert feedforward == 0.0


class TestThermalGroupManagerIntegration:
    """Integration tests for ThermalGroupManager."""

    def test_manager_creation_and_zone_lookup(self):
        """Test manager creation and basic zone lookups."""
        hass = MagicMock()
        config = [
            {
                "name": "downstairs",
                "zones": ["living_room", "kitchen"],
                "leader": "living_room"
            },
            {
                "name": "upstairs",
                "zones": ["bedroom1", "bedroom2"],
                "leader": "bedroom1"
            }
        ]

        manager = ThermalGroupManager(hass, config)

        # Check group assignments
        assert manager.get_zone_group("living_room").name == "downstairs"
        assert manager.get_zone_group("kitchen").name == "downstairs"
        assert manager.get_zone_group("bedroom1").name == "upstairs"
        assert manager.get_zone_group("bedroom2").name == "upstairs"

        # Check non-existent zone
        assert manager.get_zone_group("nonexistent") is None

    def test_get_group_status(self):
        """Test getting status of all thermal groups."""
        hass = MagicMock()
        config = [
            {
                "name": "downstairs",
                "zones": ["living_room", "kitchen"],
                "leader": "living_room"
            },
            {
                "name": "upstairs",
                "zones": ["bedroom1"],
                "leader": "bedroom1",
                "receives_from": "downstairs",
                "transfer_factor": 0.3,
                "delay_minutes": 15
            }
        ]

        manager = ThermalGroupManager(hass, config)

        status = manager.get_group_status()

        # Check structure
        assert "groups" in status
        assert "zone_assignments" in status

        # Check groups
        assert "downstairs" in status["groups"]
        assert "upstairs" in status["groups"]

        # Check downstairs group
        downstairs = status["groups"]["downstairs"]
        assert downstairs["type"] == GROUP_TYPE_OPEN_PLAN
        assert set(downstairs["zones"]) == {"living_room", "kitchen"}
        assert downstairs["leader"] == "living_room"
        assert downstairs["receives_from"] is None

        # Check upstairs group
        upstairs = status["groups"]["upstairs"]
        assert upstairs["receives_from"] == "downstairs"
        assert upstairs["transfer_factor"] == 0.3
        assert upstairs["delay_minutes"] == 15
        assert upstairs["history_entries"] == 0

        # Check zone assignments
        assert status["zone_assignments"]["living_room"] == "downstairs"
        assert status["zone_assignments"]["kitchen"] == "downstairs"
        assert status["zone_assignments"]["bedroom1"] == "upstairs"

    def test_complex_multi_group_setup(self):
        """Test complex setup with multiple groups and cross-references."""
        hass = MagicMock()
        config = [
            {
                "name": "ground_floor",
                "zones": ["living_room", "kitchen", "dining"],
                "leader": "living_room"
            },
            {
                "name": "first_floor",
                "zones": ["bedroom1", "bedroom2", "bathroom"],
                "leader": "bedroom1",
                "receives_from": "ground_floor",
                "transfer_factor": 0.25,
                "delay_minutes": 20
            },
            {
                "name": "second_floor",
                "zones": ["attic"],
                "leader": "attic",
                "receives_from": "first_floor",
                "transfer_factor": 0.15,
                "delay_minutes": 30
            }
        ]

        manager = ThermalGroupManager(hass, config)

        # Verify all groups created
        assert len(manager._groups) == 3

        # Verify all zones assigned
        assert len(manager._zone_to_group) == 7

        # Verify leaders
        assert manager.is_leader_zone("living_room") is True
        assert manager.is_leader_zone("bedroom1") is True
        assert manager.is_leader_zone("attic") is True

        # Verify followers
        assert manager.get_leader_zone("kitchen") == "living_room"
        assert manager.get_leader_zone("bedroom2") == "bedroom1"

        # Verify transfer relationships
        first_floor = manager._groups["first_floor"]
        assert first_floor.receives_from == "ground_floor"
        assert first_floor.transfer_factor == 0.25

        second_floor = manager._groups["second_floor"]
        assert second_floor.receives_from == "first_floor"
        assert second_floor.transfer_factor == 0.15


class TestValidateThermalGroupsConfig:
    """Test standalone configuration validation function."""

    def test_validate_valid_config(self):
        """Test validation passes for valid config."""
        config = [
            {
                "name": "downstairs",
                "zones": ["living_room", "kitchen"],
                "leader": "living_room"
            }
        ]

        # Should not raise
        validate_thermal_groups_config(config)

    def test_validate_empty_list_fails(self):
        """Test validation fails for empty config list."""
        with pytest.raises(ValueError, match="cannot be empty"):
            validate_thermal_groups_config([])

    def test_validate_not_list_fails(self):
        """Test validation fails if config is not a list."""
        with pytest.raises(ValueError, match="must be a list"):
            validate_thermal_groups_config({"name": "test"})

    def test_validate_group_not_dict_fails(self):
        """Test validation fails if group is not a dictionary."""
        config = ["not_a_dict"]

        with pytest.raises(ValueError, match="must be a dictionary"):
            validate_thermal_groups_config(config)

    def test_validate_missing_name_fails(self):
        """Test validation fails if name is missing."""
        config = [
            {
                "zones": ["living_room"],
                "leader": "living_room"
            }
        ]

        with pytest.raises(ValueError, match="missing required field 'name'"):
            validate_thermal_groups_config(config)

    def test_validate_missing_zones_fails(self):
        """Test validation fails if zones is missing."""
        config = [
            {
                "name": "downstairs",
                "leader": "living_room"
            }
        ]

        with pytest.raises(ValueError, match="missing required field 'zones'"):
            validate_thermal_groups_config(config)

    def test_validate_duplicate_names_fails(self):
        """Test validation fails for duplicate group names."""
        config = [
            {
                "name": "downstairs",
                "zones": ["living_room"],
                "leader": "living_room"
            },
            {
                "name": "downstairs",  # Duplicate
                "zones": ["kitchen"],
                "leader": "kitchen"
            }
        ]

        with pytest.raises(ValueError, match="Duplicate group name"):
            validate_thermal_groups_config(config)

    def test_validate_zones_not_list_fails(self):
        """Test validation fails if zones is not a list."""
        config = [
            {
                "name": "downstairs",
                "zones": "living_room",  # Should be list
                "leader": "living_room"
            }
        ]

        with pytest.raises(ValueError, match="zones must be a list"):
            validate_thermal_groups_config(config)

    def test_validate_zone_in_multiple_groups_fails(self):
        """Test validation fails if zone is in multiple groups."""
        config = [
            {
                "name": "downstairs",
                "zones": ["living_room", "kitchen"],
                "leader": "living_room"
            },
            {
                "name": "upstairs",
                "zones": ["kitchen", "bedroom"],  # kitchen duplicated
                "leader": "bedroom"
            }
        ]

        with pytest.raises(ValueError, match="assigned to multiple groups"):
            validate_thermal_groups_config(config)

    def test_validate_receives_from_unknown_group_fails(self):
        """Test validation fails if receives_from references unknown group."""
        config = [
            {
                "name": "upstairs",
                "zones": ["bedroom"],
                "leader": "bedroom",
                "receives_from": "nonexistent"
            }
        ]

        with pytest.raises(ValueError, match="receives_from unknown group"):
            validate_thermal_groups_config(config)

    def test_validate_self_reference_fails(self):
        """Test validation fails if group receives from itself."""
        config = [
            {
                "name": "downstairs",
                "zones": ["living_room"],
                "leader": "living_room",
                "receives_from": "downstairs"  # Self-reference
            }
        ]

        with pytest.raises(ValueError, match="cannot receive from itself"):
            validate_thermal_groups_config(config)
