"""Tests for manifold registry module."""

import pytest
from datetime import datetime, timedelta
from custom_components.adaptive_climate.adaptive.manifold_registry import (
    Manifold,
    ManifoldRegistry,
    manifolds_from_config,
)


def ground_floor() -> ManifoldRegistry:
    return ManifoldRegistry(
        [
            Manifold(
                name="Ground",
                pipe_volume=20.0,
                zones={"climate.living": 4.0, "climate.kitchen": 3.5, "climate.hall": 2.5},
            ),
            Manifold(name="First", pipe_volume=10.0, zones={"climate.bed": 2.0}),
        ]
    )


class TestManifoldDataclass:
    def test_manifold_holds_per_zone_flow(self):
        manifold = Manifold(
            name="2nd Floor", pipe_volume=20.0, zones={"climate.bathroom_2nd": 2.0, "climate.bedroom_2nd": 3.0}
        )
        assert manifold.name == "2nd Floor"
        assert manifold.pipe_volume == 20.0
        assert manifold.zones == {"climate.bathroom_2nd": 2.0, "climate.bedroom_2nd": 3.0}


class TestManifoldsFromConfig:
    def test_builds_manifolds_from_validated_config(self):
        config = [
            {
                "name": "Ground",
                "pipe_volume": 12,
                "zones": [{"entity": "climate.living", "flow": 4}, {"entity": "climate.kitchen", "flow": 3.5}],
            },
            {"name": "First", "pipe_volume": 8.0, "zones": [{"entity": "climate.bed", "flow": 2.0}]},
        ]
        manifolds = manifolds_from_config(config)
        assert [m.name for m in manifolds] == ["Ground", "First"]
        assert manifolds[0].pipe_volume == 12.0
        assert manifolds[0].zones == {"climate.living": 4.0, "climate.kitchen": 3.5}
        assert manifolds[1].zones == {"climate.bed": 2.0}

    def test_empty_config_builds_nothing(self):
        assert manifolds_from_config([]) == []


class TestManifoldRegistryLookup:
    """Test ManifoldRegistry zone to manifold lookup."""

    def test_registry_creation_empty(self):
        """Test registry creation with no manifolds."""
        registry = ManifoldRegistry([])

        assert registry.get_manifold_for_zone("climate.test") is None

    def test_registry_single_manifold(self):
        """Test registry with single manifold."""
        manifolds = [
            Manifold(
                name="2nd Floor",
                pipe_volume=20.0,
                zones={"climate.bathroom_2nd": 2.0, "climate.bedroom_2nd": 2.0},
            )
        ]
        registry = ManifoldRegistry(manifolds)

        manifold = registry.get_manifold_for_zone("climate.bathroom_2nd")
        assert manifold is not None
        assert manifold.name == "2nd Floor"

        manifold = registry.get_manifold_for_zone("climate.bedroom_2nd")
        assert manifold is not None
        assert manifold.name == "2nd Floor"

    def test_registry_multiple_manifolds(self):
        """Test registry with multiple manifolds."""
        manifolds = [
            Manifold(
                name="2nd Floor",
                pipe_volume=20.0,
                zones={"climate.bathroom_2nd": 2.0, "climate.bedroom_2nd": 2.0},
            ),
            Manifold(
                name="Ground Floor",
                pipe_volume=15.0,
                zones={"climate.living_room": 2.0, "climate.kitchen": 2.0},
            ),
        ]
        registry = ManifoldRegistry(manifolds)

        # Check 2nd floor zones
        manifold = registry.get_manifold_for_zone("climate.bathroom_2nd")
        assert manifold.name == "2nd Floor"

        # Check ground floor zones
        manifold = registry.get_manifold_for_zone("climate.living_room")
        assert manifold.name == "Ground Floor"

    def test_registry_unknown_zone(self):
        """Test lookup for zone not in any manifold."""
        manifolds = [Manifold(name="2nd Floor", pipe_volume=20.0, zones={"climate.bathroom_2nd": 2.0})]
        registry = ManifoldRegistry(manifolds)

        manifold = registry.get_manifold_for_zone("climate.unknown_zone")
        assert manifold is None


class TestTransportDelayColdManifold:
    def test_single_active_zone_uses_its_flow(self):
        assert ground_floor().get_transport_delay("climate.living", {"climate.living"}) == pytest.approx(5.0)

    def test_active_zones_on_the_manifold_add_their_flows(self):
        delay = ground_floor().get_transport_delay(
            "climate.living", {"climate.living", "climate.kitchen", "climate.hall"}
        )
        assert delay == pytest.approx(2.0)  # 20 / (4 + 3.5 + 2.5)

    def test_only_active_zones_count(self):
        delay = ground_floor().get_transport_delay("climate.living", {"climate.living", "climate.kitchen"})
        assert delay == pytest.approx(20.0 / 7.5)

    def test_active_zone_on_another_manifold_is_not_counted(self):
        delay = ground_floor().get_transport_delay("climate.living", {"climate.living", "climate.bed"})
        assert delay == pytest.approx(5.0)

    def test_no_active_zone_on_the_manifold_means_no_delay(self):
        assert ground_floor().get_transport_delay("climate.living", {"climate.bed"}) == 0.0

    def test_empty_active_set_means_no_delay(self):
        assert ground_floor().get_transport_delay("climate.living", set()) == 0.0


class TestTransportDelayWarmManifold:
    """Test get_transport_delay for warm manifolds (active < 5 min ago)."""

    def test_delay_recently_active_returns_zero(self):
        """Test that recently active manifold returns 0 delay."""
        from unittest.mock import patch

        manifolds = [Manifold(name="2nd Floor", pipe_volume=20.0, zones={"climate.bathroom_2nd": 2.0})]
        registry = ManifoldRegistry(manifolds)

        active_zones = {"climate.bathroom_2nd"}

        # First call - manifold is cold
        delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones)
        assert delay == 10.0  # 20L / 2 L/min = 10 min

        # Mark as recently active (simulate time passing < 5 min)
        current_time = datetime(2024, 1, 15, 10, 0)
        with patch("custom_components.adaptive_climate.adaptive.manifold_registry.dt_util") as mock_dt_util:
            mock_dt_util.utcnow.return_value = current_time
            registry.mark_manifold_active("climate.bathroom_2nd")

            # Second call - manifold is warm
            delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones)
            assert delay == 0.0

    def test_delay_warm_manifold_within_cooldown(self):
        """Test manifold stays warm within 5 minute cooldown period."""
        from unittest.mock import patch

        manifolds = [Manifold(name="2nd Floor", pipe_volume=20.0, zones={"climate.bathroom_2nd": 2.0})]
        registry = ManifoldRegistry(manifolds)

        active_zones = {"climate.bathroom_2nd"}

        current_time = datetime(2024, 1, 15, 10, 0)

        # Mark as active
        with patch("custom_components.adaptive_climate.adaptive.manifold_registry.dt_util") as mock_dt_util:
            mock_dt_util.utcnow.return_value = current_time
            registry.mark_manifold_active("climate.bathroom_2nd")

            # Check at various times within cooldown
            delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones)
            assert delay == 0.0

        # Simulate 2 minutes passing (still within 5 min cooldown)
        with patch("custom_components.adaptive_climate.adaptive.manifold_registry.dt_util") as mock_dt_util:
            mock_dt_util.utcnow.return_value = current_time
            registry._last_active_time["2nd Floor"] = current_time - timedelta(minutes=2)
            delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones)
            assert delay == 0.0

        # Simulate 4.5 minutes passing (still within cooldown)
        with patch("custom_components.adaptive_climate.adaptive.manifold_registry.dt_util") as mock_dt_util:
            mock_dt_util.utcnow.return_value = current_time
            registry._last_active_time["2nd Floor"] = current_time - timedelta(minutes=4.5)
            delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones)
            assert delay == 0.0

    def test_delay_manifold_cools_after_5_minutes(self):
        """Test manifold becomes cold after 5 minute cooldown expires."""
        from unittest.mock import patch

        manifolds = [Manifold(name="2nd Floor", pipe_volume=20.0, zones={"climate.bathroom_2nd": 2.0})]
        registry = ManifoldRegistry(manifolds)

        active_zones = {"climate.bathroom_2nd"}

        current_time = datetime(2024, 1, 15, 10, 0)

        # Mark as active
        with patch("custom_components.adaptive_climate.adaptive.manifold_registry.dt_util") as mock_dt_util:
            mock_dt_util.utcnow.return_value = current_time
            registry.mark_manifold_active("climate.bathroom_2nd")

            # Initially warm
            delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones)
            assert delay == 0.0

        # Simulate 6 minutes passing (beyond cooldown)
        with patch("custom_components.adaptive_climate.adaptive.manifold_registry.dt_util") as mock_dt_util:
            mock_dt_util.utcnow.return_value = current_time
            registry._last_active_time["2nd Floor"] = current_time - timedelta(minutes=6)
            delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones)
            assert delay == 10.0  # Back to cold calculation

    def test_delay_multiple_manifolds_independent_cooldown(self):
        """Test that different manifolds have independent cooldown tracking."""
        from unittest.mock import patch

        manifolds = [
            Manifold(name="2nd Floor", pipe_volume=20.0, zones={"climate.bathroom_2nd": 2.0}),
            Manifold(name="Ground Floor", pipe_volume=15.0, zones={"climate.living_room": 2.0}),
        ]
        registry = ManifoldRegistry(manifolds)

        # Mark only 2nd floor as active
        current_time = datetime(2024, 1, 15, 10, 0)
        with patch("custom_components.adaptive_climate.adaptive.manifold_registry.dt_util") as mock_dt_util:
            mock_dt_util.utcnow.return_value = current_time
            registry.mark_manifold_active("climate.bathroom_2nd")

            # 2nd floor should be warm
            active_zones_2nd = {"climate.bathroom_2nd"}
            delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones_2nd)
            assert delay == 0.0

        # Ground floor should still be cold
        active_zones_ground = {"climate.living_room"}
        delay = registry.get_transport_delay("climate.living_room", active_zones_ground)
        assert delay == 7.5  # 15L / 2 L/min = 7.5 min


class TestTransportDelayUnknownZone:
    """Test get_transport_delay for unknown zones."""

    def test_delay_unknown_zone_returns_zero(self):
        """Test that unknown zone returns 0 delay without error."""
        manifolds = [Manifold(name="2nd Floor", pipe_volume=20.0, zones={"climate.bathroom_2nd": 2.0})]
        registry = ManifoldRegistry(manifolds)

        active_zones = {"climate.unknown"}

        # Should return 0 for unknown zone (silently)
        delay = registry.get_transport_delay("climate.unknown", active_zones)
        assert delay == 0.0

    def test_delay_unknown_zone_empty_registry(self):
        """Test unknown zone with empty registry."""
        registry = ManifoldRegistry([])

        active_zones = {"climate.test"}
        delay = registry.get_transport_delay("climate.test", active_zones)

        assert delay == 0.0

    def test_delay_zone_not_in_active_zones(self):
        """Test delay calculation when requesting zone not in active_zones."""
        manifolds = [Manifold(name="2nd Floor", pipe_volume=20.0, zones={"climate.bathroom_2nd": 2.0})]
        registry = ManifoldRegistry(manifolds)

        # Request zone exists but no active zones reported
        active_zones = set()
        delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones)

        # No active zones means no flow, should handle gracefully
        # Implementation should return 0 or some sensible default
        assert delay >= 0


class TestMarkManifoldActiveProductionScenario:
    """Test mark_manifold_active in production scenarios simulating heater turn-on."""

    def test_heater_turn_on_marks_manifold_active(self):
        """Test that marking manifold active after heater turn-on prevents delay for adjacent zones."""
        from unittest.mock import patch

        manifolds = [
            Manifold(
                name="2nd Floor",
                pipe_volume=20.0,
                zones={"climate.bathroom_2nd": 2.0, "climate.bedroom_2nd": 2.0},
            )
        ]
        registry = ManifoldRegistry(manifolds)

        # Scenario: Bathroom zone turns on heater first
        active_zones_bathroom = {"climate.bathroom_2nd"}

        # First heating cycle - manifold is cold, should have delay
        delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones_bathroom)
        assert delay == 10.0  # 20L / 2 L/min = 10 min

        # Simulate production code: heater turns on → mark manifold active
        current_time = datetime(2024, 1, 15, 10, 0)
        with patch("custom_components.adaptive_climate.adaptive.manifold_registry.dt_util") as mock_dt_util:
            mock_dt_util.utcnow.return_value = current_time
            registry.mark_manifold_active("climate.bathroom_2nd")

            # Bedroom zone turns on shortly after (within 5 min)
            active_zones_both = {"climate.bathroom_2nd", "climate.bedroom_2nd"}

            # Bedroom should get 0 delay because manifold is now warm
            delay = registry.get_transport_delay("climate.bedroom_2nd", active_zones_both)
            assert delay == 0.0  # Manifold is warm, no transport delay

            # Bathroom should also get 0 delay
            delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones_both)
            assert delay == 0.0

    def test_sequential_zone_activation_on_same_manifold(self):
        """Test that sequential zone activations on same manifold benefit from mark_manifold_active."""
        from unittest.mock import patch

        manifolds = [
            Manifold(
                name="Ground Floor",
                pipe_volume=30.0,
                zones={"climate.living_room": 2.0, "climate.kitchen": 2.0, "climate.dining": 2.0},
            )
        ]
        registry = ManifoldRegistry(manifolds)

        # First zone activates - manifold cold
        active_zones = {"climate.living_room"}
        delay = registry.get_transport_delay("climate.living_room", active_zones)
        assert delay == 15.0  # 30L / 2 L/min = 15 min

        # Mark as active (simulating production heater turn-on)
        current_time = datetime(2024, 1, 15, 10, 0)
        with patch("custom_components.adaptive_climate.adaptive.manifold_registry.dt_util") as mock_dt_util:
            mock_dt_util.utcnow.return_value = current_time
            registry.mark_manifold_active("climate.living_room")

            # Second zone activates shortly after
            active_zones = {"climate.living_room", "climate.kitchen"}
            delay = registry.get_transport_delay("climate.kitchen", active_zones)
            assert delay == 0.0  # Manifold warm

            # Third zone activates
            active_zones = {"climate.living_room", "climate.kitchen", "climate.dining"}
            delay = registry.get_transport_delay("climate.dining", active_zones)
            assert delay == 0.0  # Still warm

    def test_different_manifolds_independent_marking(self):
        """Test that marking one manifold active doesn't affect other manifolds."""
        from unittest.mock import patch

        manifolds = [
            Manifold(name="2nd Floor", pipe_volume=20.0, zones={"climate.bathroom_2nd": 2.0}),
            Manifold(name="Ground Floor", pipe_volume=15.0, zones={"climate.living_room": 2.0}),
        ]
        registry = ManifoldRegistry(manifolds)

        # Activate 2nd floor zone and mark manifold active
        active_zones_2nd = {"climate.bathroom_2nd"}
        delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones_2nd)
        assert delay == 10.0  # Cold initially

        current_time = datetime(2024, 1, 15, 10, 0)
        with patch("custom_components.adaptive_climate.adaptive.manifold_registry.dt_util") as mock_dt_util:
            mock_dt_util.utcnow.return_value = current_time
            registry.mark_manifold_active("climate.bathroom_2nd")

            # 2nd floor should be warm
            delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones_2nd)
            assert delay == 0.0

        # Ground floor should still be cold
        active_zones_ground = {"climate.living_room"}
        delay = registry.get_transport_delay("climate.living_room", active_zones_ground)
        assert delay == 7.5  # 15L / 2 L/min = 7.5 min (still cold)


class TestManifoldPersistence:
    """Test manifold state persistence methods."""

    def test_get_state_for_persistence_empty(self):
        """Test get_state_for_persistence with no active manifolds."""
        manifolds = [Manifold(name="2nd Floor", pipe_volume=20.0, zones={"climate.bathroom_2nd": 2.0})]
        registry = ManifoldRegistry(manifolds)

        state = registry.get_state_for_persistence()
        assert state == {}

    def test_get_state_for_persistence_single_manifold(self):
        """Test get_state_for_persistence with one active manifold."""
        from unittest.mock import patch

        manifolds = [Manifold(name="2nd Floor", pipe_volume=20.0, zones={"climate.bathroom_2nd": 2.0})]
        registry = ManifoldRegistry(manifolds)

        # Mark manifold as active
        current_time = datetime(2024, 1, 15, 10, 30, 0)
        with patch("custom_components.adaptive_climate.adaptive.manifold_registry.dt_util") as mock_dt_util:
            mock_dt_util.utcnow.return_value = current_time
            registry.mark_manifold_active("climate.bathroom_2nd")

        # Get state for persistence
        state = registry.get_state_for_persistence()

        assert len(state) == 1
        assert "2nd Floor" in state
        assert state["2nd Floor"] == "2024-01-15T10:30:00"

    def test_get_state_for_persistence_multiple_manifolds(self):
        """Test get_state_for_persistence with multiple active manifolds."""
        from unittest.mock import patch

        manifolds = [
            Manifold(name="2nd Floor", pipe_volume=20.0, zones={"climate.bathroom_2nd": 2.0}),
            Manifold(name="Ground Floor", pipe_volume=15.0, zones={"climate.living_room": 2.0}),
        ]
        registry = ManifoldRegistry(manifolds)

        # Mark both manifolds as active at different times
        time_2nd = datetime(2024, 1, 15, 10, 30, 0)
        time_ground = datetime(2024, 1, 15, 10, 45, 0)

        with patch("custom_components.adaptive_climate.adaptive.manifold_registry.dt_util") as mock_dt_util:
            mock_dt_util.utcnow.return_value = time_2nd
            registry.mark_manifold_active("climate.bathroom_2nd")

            mock_dt_util.utcnow.return_value = time_ground
            registry.mark_manifold_active("climate.living_room")

        # Get state for persistence
        state = registry.get_state_for_persistence()

        assert len(state) == 2
        assert state["2nd Floor"] == "2024-01-15T10:30:00"
        assert state["Ground Floor"] == "2024-01-15T10:45:00"

    def test_restore_state_empty(self):
        """Test restore_state with empty state dict."""
        manifolds = [Manifold(name="2nd Floor", pipe_volume=20.0, zones={"climate.bathroom_2nd": 2.0})]
        registry = ManifoldRegistry(manifolds)

        # Restore empty state
        registry.restore_state({})

        # Should have no active manifolds
        assert len(registry._last_active_time) == 0

    def test_restore_state_single_manifold(self):
        """Test restore_state with one manifold."""
        manifolds = [Manifold(name="2nd Floor", pipe_volume=20.0, zones={"climate.bathroom_2nd": 2.0})]
        registry = ManifoldRegistry(manifolds)

        # Restore state (use UTC timezone-aware datetime)
        state = {"2nd Floor": "2024-01-15T10:30:00+00:00"}
        registry.restore_state(state)

        # Check restored timestamp
        assert "2nd Floor" in registry._last_active_time
        restored_time = registry._last_active_time["2nd Floor"]
        assert restored_time.year == 2024
        assert restored_time.month == 1
        assert restored_time.day == 15
        assert restored_time.hour == 10
        assert restored_time.minute == 30

    def test_restore_state_multiple_manifolds(self):
        """Test restore_state with multiple manifolds."""
        manifolds = [
            Manifold(name="2nd Floor", pipe_volume=20.0, zones={"climate.bathroom_2nd": 2.0}),
            Manifold(name="Ground Floor", pipe_volume=15.0, zones={"climate.living_room": 2.0}),
        ]
        registry = ManifoldRegistry(manifolds)

        # Restore state (use UTC timezone-aware datetimes)
        state = {"2nd Floor": "2024-01-15T10:30:00+00:00", "Ground Floor": "2024-01-15T10:45:00+00:00"}
        registry.restore_state(state)

        # Check restored timestamps
        assert len(registry._last_active_time) == 2

        time_2nd = registry._last_active_time["2nd Floor"]
        assert time_2nd.hour == 10
        assert time_2nd.minute == 30

        time_ground = registry._last_active_time["Ground Floor"]
        assert time_ground.hour == 10
        assert time_ground.minute == 45

    def test_restore_state_invalid_timestamp(self):
        """Test restore_state handles invalid timestamp gracefully."""
        manifolds = [Manifold(name="2nd Floor", pipe_volume=20.0, zones={"climate.bathroom_2nd": 2.0})]
        registry = ManifoldRegistry(manifolds)

        # Restore with invalid timestamp - should skip silently
        state = {"2nd Floor": "invalid-timestamp"}
        registry.restore_state(state)

        # Should not have any restored manifolds (parse_datetime returns None for invalid)
        assert len(registry._last_active_time) == 0

    def test_restore_state_mixed_valid_invalid(self):
        """Test restore_state with mix of valid and invalid timestamps."""
        manifolds = [
            Manifold(name="2nd Floor", pipe_volume=20.0, zones={"climate.bathroom_2nd": 2.0}),
            Manifold(name="Ground Floor", pipe_volume=15.0, zones={"climate.living_room": 2.0}),
        ]
        registry = ManifoldRegistry(manifolds)

        # Restore with one valid and one invalid timestamp (use UTC timezone-aware)
        state = {"2nd Floor": "2024-01-15T10:30:00+00:00", "Ground Floor": "invalid-timestamp"}
        registry.restore_state(state)

        # Should only have the valid one
        assert len(registry._last_active_time) == 1
        assert "2nd Floor" in registry._last_active_time
        assert "Ground Floor" not in registry._last_active_time

    def test_persistence_roundtrip(self):
        """Test full persistence roundtrip: save state and restore it."""
        from unittest.mock import patch

        manifolds = [
            Manifold(name="2nd Floor", pipe_volume=20.0, zones={"climate.bathroom_2nd": 2.0}),
            Manifold(name="Ground Floor", pipe_volume=15.0, zones={"climate.living_room": 2.0}),
        ]

        # First registry - mark manifolds active
        registry1 = ManifoldRegistry(manifolds)

        time_2nd = datetime(2024, 1, 15, 10, 30, 0)
        time_ground = datetime(2024, 1, 15, 10, 45, 0)

        with patch("custom_components.adaptive_climate.adaptive.manifold_registry.dt_util") as mock_dt_util:
            mock_dt_util.utcnow.return_value = time_2nd
            registry1.mark_manifold_active("climate.bathroom_2nd")

            mock_dt_util.utcnow.return_value = time_ground
            registry1.mark_manifold_active("climate.living_room")

        # Save state
        state = registry1.get_state_for_persistence()

        # Second registry - restore state
        registry2 = ManifoldRegistry(manifolds)
        registry2.restore_state(state)

        # Verify state is identical
        assert len(registry2._last_active_time) == 2
        assert registry2._last_active_time["2nd Floor"].hour == 10
        assert registry2._last_active_time["2nd Floor"].minute == 30
        assert registry2._last_active_time["Ground Floor"].hour == 10
        assert registry2._last_active_time["Ground Floor"].minute == 45


class TestWorstCaseTransportDelay:
    """Test get_worst_case_transport_delay for preheat scheduling."""

    def test_worst_case_is_the_zone_alone(self):
        assert ground_floor().get_worst_case_transport_delay("climate.hall") == pytest.approx(8.0)  # 20 / 2.5

    def test_worst_case_unknown_zone_is_zero(self):
        assert ground_floor().get_worst_case_transport_delay("climate.garage") == 0.0

    def test_worst_case_empty_registry_is_zero(self):
        assert ManifoldRegistry([]).get_worst_case_transport_delay("climate.living") == 0.0

    def test_worst_case_is_never_shorter_than_the_live_delay(self):
        registry = ground_floor()
        live = registry.get_transport_delay("climate.living", {"climate.living", "climate.kitchen"})
        assert registry.get_worst_case_transport_delay("climate.living") >= live


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
