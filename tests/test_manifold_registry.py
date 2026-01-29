"""Tests for manifold registry module."""
import pytest
from datetime import datetime, timedelta
from custom_components.adaptive_thermostat.adaptive.manifold_registry import (
    Manifold,
    ManifoldRegistry,
)


class TestManifoldDataclass:
    """Test Manifold dataclass creation and validation."""

    def test_manifold_creation_basic(self):
        """Test basic manifold creation with required fields."""
        manifold = Manifold(
            name="2nd Floor",
            zones=["climate.bathroom_2nd", "climate.bedroom_2nd"],
            pipe_volume=20.0,
            flow_per_loop=2.0
        )

        assert manifold.name == "2nd Floor"
        assert manifold.zones == ["climate.bathroom_2nd", "climate.bedroom_2nd"]
        assert manifold.pipe_volume == 20.0
        assert manifold.flow_per_loop == 2.0

    def test_manifold_creation_single_zone(self):
        """Test manifold with single zone."""
        manifold = Manifold(
            name="Garage",
            zones=["climate.garage"],
            pipe_volume=15.0,
            flow_per_loop=2.0
        )

        assert manifold.name == "Garage"
        assert manifold.zones == ["climate.garage"]
        assert len(manifold.zones) == 1

    def test_manifold_creation_many_zones(self):
        """Test manifold with multiple zones."""
        zones = [
            "climate.bedroom_1",
            "climate.bedroom_2",
            "climate.bathroom",
            "climate.hallway"
        ]
        manifold = Manifold(
            name="Upstairs",
            zones=zones,
            pipe_volume=25.0,
            flow_per_loop=2.5
        )

        assert manifold.name == "Upstairs"
        assert manifold.zones == zones
        assert len(manifold.zones) == 4

    def test_manifold_creation_custom_flow_rate(self):
        """Test manifold with custom flow per loop."""
        manifold = Manifold(
            name="Ground Floor",
            zones=["climate.living_room", "climate.kitchen"],
            pipe_volume=30.0,
            flow_per_loop=3.0  # Custom flow rate
        )

        assert manifold.flow_per_loop == 3.0

    def test_manifold_creation_large_pipe_volume(self):
        """Test manifold with large pipe volume (long distance)."""
        manifold = Manifold(
            name="Basement",
            zones=["climate.basement_rec"],
            pipe_volume=50.0,  # Long run from boiler
            flow_per_loop=2.0
        )

        assert manifold.pipe_volume == 50.0


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
                zones=["climate.bathroom_2nd", "climate.bedroom_2nd"],
                pipe_volume=20.0,
                flow_per_loop=2.0
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
                zones=["climate.bathroom_2nd", "climate.bedroom_2nd"],
                pipe_volume=20.0,
                flow_per_loop=2.0
            ),
            Manifold(
                name="Ground Floor",
                zones=["climate.living_room", "climate.kitchen"],
                pipe_volume=15.0,
                flow_per_loop=2.0
            )
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
        manifolds = [
            Manifold(
                name="2nd Floor",
                zones=["climate.bathroom_2nd"],
                pipe_volume=20.0,
                flow_per_loop=2.0
            )
        ]
        registry = ManifoldRegistry(manifolds)

        manifold = registry.get_manifold_for_zone("climate.unknown_zone")
        assert manifold is None


class TestTransportDelayColdManifold:
    """Test get_transport_delay for cold manifolds (>5 min since last active)."""

    def test_delay_single_loop_active(self):
        """Test delay calculation with single loop active."""
        manifolds = [
            Manifold(
                name="2nd Floor",
                zones=["climate.bathroom_2nd"],
                pipe_volume=20.0,
                flow_per_loop=2.0
            )
        ]
        registry = ManifoldRegistry(manifolds)

        # 1 loop × 2 L/min = 2 L/min total flow
        # 20L / 2 L/min = 10 min delay
        active_zones = {"climate.bathroom_2nd": 1}  # 1 loop
        delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones)

        assert delay == 10.0

    def test_delay_two_loops_one_zone(self):
        """Test delay calculation with 2 loops in same zone."""
        manifolds = [
            Manifold(
                name="2nd Floor",
                zones=["climate.bathroom_2nd"],
                pipe_volume=20.0,
                flow_per_loop=2.0
            )
        ]
        registry = ManifoldRegistry(manifolds)

        # 2 loops × 2 L/min = 4 L/min total flow
        # 20L / 4 L/min = 5 min delay
        active_zones = {"climate.bathroom_2nd": 2}  # 2 loops
        delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones)

        assert delay == 5.0

    def test_delay_multiple_zones_same_manifold(self):
        """Test delay calculation with multiple zones active on same manifold."""
        manifolds = [
            Manifold(
                name="2nd Floor",
                zones=["climate.bathroom_2nd", "climate.bedroom_2nd"],
                pipe_volume=20.0,
                flow_per_loop=2.0
            )
        ]
        registry = ManifoldRegistry(manifolds)

        # bathroom: 2 loops, bedroom: 3 loops
        # (2 + 3) loops × 2 L/min = 10 L/min total flow
        # 20L / 10 L/min = 2 min delay
        active_zones = {
            "climate.bathroom_2nd": 2,
            "climate.bedroom_2nd": 3
        }
        delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones)

        assert delay == 2.0

        # Same delay for bedroom (same manifold)
        delay = registry.get_transport_delay("climate.bedroom_2nd", active_zones)
        assert delay == 2.0

    def test_delay_example_from_spec(self):
        """Test exact example from spec: 2 loops, 20L, 2 L/min → 5 min."""
        manifolds = [
            Manifold(
                name="Test",
                zones=["climate.test"],
                pipe_volume=20.0,
                flow_per_loop=2.0
            )
        ]
        registry = ManifoldRegistry(manifolds)

        # Example: 2 loops active, pipe_volume=20L, flow_per_loop=2 L/min
        # Expected: 20/(2×2) = 5 min
        active_zones = {"climate.test": 2}
        delay = registry.get_transport_delay("climate.test", active_zones)

        assert delay == 5.0

    def test_delay_large_pipe_volume(self):
        """Test delay with large pipe volume (long distance)."""
        manifolds = [
            Manifold(
                name="Basement",
                zones=["climate.basement"],
                pipe_volume=50.0,  # Long run
                flow_per_loop=2.0
            )
        ]
        registry = ManifoldRegistry(manifolds)

        # 1 loop × 2 L/min = 2 L/min
        # 50L / 2 L/min = 25 min delay
        active_zones = {"climate.basement": 1}
        delay = registry.get_transport_delay("climate.basement", active_zones)

        assert delay == 25.0

    def test_delay_high_flow_rate(self):
        """Test delay with higher flow per loop."""
        manifolds = [
            Manifold(
                name="Ground Floor",
                zones=["climate.living_room"],
                pipe_volume=20.0,
                flow_per_loop=4.0  # Higher flow
            )
        ]
        registry = ManifoldRegistry(manifolds)

        # 1 loop × 4 L/min = 4 L/min
        # 20L / 4 L/min = 5 min delay
        active_zones = {"climate.living_room": 1}
        delay = registry.get_transport_delay("climate.living_room", active_zones)

        assert delay == 5.0


class TestTransportDelayWarmManifold:
    """Test get_transport_delay for warm manifolds (active < 5 min ago)."""

    def test_delay_recently_active_returns_zero(self):
        """Test that recently active manifold returns 0 delay."""
        from unittest.mock import patch

        manifolds = [
            Manifold(
                name="2nd Floor",
                zones=["climate.bathroom_2nd"],
                pipe_volume=20.0,
                flow_per_loop=2.0
            )
        ]
        registry = ManifoldRegistry(manifolds)

        active_zones = {"climate.bathroom_2nd": 1}

        # First call - manifold is cold
        delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones)
        assert delay == 10.0  # 20L / (1 × 2) = 10 min

        # Mark as recently active (simulate time passing < 5 min)
        current_time = datetime(2024, 1, 15, 10, 0)
        with patch('custom_components.adaptive_thermostat.adaptive.manifold_registry.dt_util') as mock_dt_util:
            mock_dt_util.utcnow.return_value = current_time
            registry.mark_manifold_active("climate.bathroom_2nd")

            # Second call - manifold is warm
            delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones)
            assert delay == 0.0

    def test_delay_warm_manifold_within_cooldown(self):
        """Test manifold stays warm within 5 minute cooldown period."""
        from unittest.mock import patch

        manifolds = [
            Manifold(
                name="2nd Floor",
                zones=["climate.bathroom_2nd"],
                pipe_volume=20.0,
                flow_per_loop=2.0
            )
        ]
        registry = ManifoldRegistry(manifolds)

        active_zones = {"climate.bathroom_2nd": 1}

        current_time = datetime(2024, 1, 15, 10, 0)

        # Mark as active
        with patch('custom_components.adaptive_thermostat.adaptive.manifold_registry.dt_util') as mock_dt_util:
            mock_dt_util.utcnow.return_value = current_time
            registry.mark_manifold_active("climate.bathroom_2nd")

            # Check at various times within cooldown
            delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones)
            assert delay == 0.0

        # Simulate 2 minutes passing (still within 5 min cooldown)
        with patch('custom_components.adaptive_thermostat.adaptive.manifold_registry.dt_util') as mock_dt_util:
            mock_dt_util.utcnow.return_value = current_time
            registry._last_active_time["2nd Floor"] = current_time - timedelta(minutes=2)
            delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones)
            assert delay == 0.0

        # Simulate 4.5 minutes passing (still within cooldown)
        with patch('custom_components.adaptive_thermostat.adaptive.manifold_registry.dt_util') as mock_dt_util:
            mock_dt_util.utcnow.return_value = current_time
            registry._last_active_time["2nd Floor"] = current_time - timedelta(minutes=4.5)
            delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones)
            assert delay == 0.0

    def test_delay_manifold_cools_after_5_minutes(self):
        """Test manifold becomes cold after 5 minute cooldown expires."""
        from unittest.mock import patch

        manifolds = [
            Manifold(
                name="2nd Floor",
                zones=["climate.bathroom_2nd"],
                pipe_volume=20.0,
                flow_per_loop=2.0
            )
        ]
        registry = ManifoldRegistry(manifolds)

        active_zones = {"climate.bathroom_2nd": 1}

        current_time = datetime(2024, 1, 15, 10, 0)

        # Mark as active
        with patch('custom_components.adaptive_thermostat.adaptive.manifold_registry.dt_util') as mock_dt_util:
            mock_dt_util.utcnow.return_value = current_time
            registry.mark_manifold_active("climate.bathroom_2nd")

            # Initially warm
            delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones)
            assert delay == 0.0

        # Simulate 6 minutes passing (beyond cooldown)
        with patch('custom_components.adaptive_thermostat.adaptive.manifold_registry.dt_util') as mock_dt_util:
            mock_dt_util.utcnow.return_value = current_time
            registry._last_active_time["2nd Floor"] = current_time - timedelta(minutes=6)
            delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones)
            assert delay == 10.0  # Back to cold calculation

    def test_delay_multiple_manifolds_independent_cooldown(self):
        """Test that different manifolds have independent cooldown tracking."""
        from unittest.mock import patch

        manifolds = [
            Manifold(
                name="2nd Floor",
                zones=["climate.bathroom_2nd"],
                pipe_volume=20.0,
                flow_per_loop=2.0
            ),
            Manifold(
                name="Ground Floor",
                zones=["climate.living_room"],
                pipe_volume=15.0,
                flow_per_loop=2.0
            )
        ]
        registry = ManifoldRegistry(manifolds)

        # Mark only 2nd floor as active
        current_time = datetime(2024, 1, 15, 10, 0)
        with patch('custom_components.adaptive_thermostat.adaptive.manifold_registry.dt_util') as mock_dt_util:
            mock_dt_util.utcnow.return_value = current_time
            registry.mark_manifold_active("climate.bathroom_2nd")

            # 2nd floor should be warm
            active_zones_2nd = {"climate.bathroom_2nd": 1}
            delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones_2nd)
            assert delay == 0.0

        # Ground floor should still be cold
        active_zones_ground = {"climate.living_room": 1}
        delay = registry.get_transport_delay("climate.living_room", active_zones_ground)
        assert delay == 7.5  # 15L / (1 × 2) = 7.5 min


class TestTransportDelayUnknownZone:
    """Test get_transport_delay for unknown zones."""

    def test_delay_unknown_zone_returns_zero(self):
        """Test that unknown zone returns 0 delay without error."""
        manifolds = [
            Manifold(
                name="2nd Floor",
                zones=["climate.bathroom_2nd"],
                pipe_volume=20.0,
                flow_per_loop=2.0
            )
        ]
        registry = ManifoldRegistry(manifolds)

        active_zones = {"climate.unknown": 1}

        # Should return 0 for unknown zone (silently)
        delay = registry.get_transport_delay("climate.unknown", active_zones)
        assert delay == 0.0

    def test_delay_unknown_zone_empty_registry(self):
        """Test unknown zone with empty registry."""
        registry = ManifoldRegistry([])

        active_zones = {"climate.test": 1}
        delay = registry.get_transport_delay("climate.test", active_zones)

        assert delay == 0.0

    def test_delay_zone_not_in_active_zones(self):
        """Test delay calculation when requesting zone not in active_zones dict."""
        manifolds = [
            Manifold(
                name="2nd Floor",
                zones=["climate.bathroom_2nd"],
                pipe_volume=20.0,
                flow_per_loop=2.0
            )
        ]
        registry = ManifoldRegistry(manifolds)

        # Request zone exists but no active zones reported
        active_zones = {}
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
                zones=["climate.bathroom_2nd", "climate.bedroom_2nd"],
                pipe_volume=20.0,
                flow_per_loop=2.0
            )
        ]
        registry = ManifoldRegistry(manifolds)

        # Scenario: Bathroom zone turns on heater first
        active_zones_bathroom = {"climate.bathroom_2nd": 2}

        # First heating cycle - manifold is cold, should have delay
        delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones_bathroom)
        assert delay == 5.0  # 20L / (2 × 2) = 5 min

        # Simulate production code: heater turns on → mark manifold active
        current_time = datetime(2024, 1, 15, 10, 0)
        with patch('custom_components.adaptive_thermostat.adaptive.manifold_registry.dt_util') as mock_dt_util:
            mock_dt_util.utcnow.return_value = current_time
            registry.mark_manifold_active("climate.bathroom_2nd")

            # Bedroom zone turns on shortly after (within 5 min)
            active_zones_both = {
                "climate.bathroom_2nd": 2,
                "climate.bedroom_2nd": 1
            }

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
                zones=["climate.living_room", "climate.kitchen", "climate.dining"],
                pipe_volume=30.0,
                flow_per_loop=2.0
            )
        ]
        registry = ManifoldRegistry(manifolds)

        # First zone activates - manifold cold
        active_zones = {"climate.living_room": 2}
        delay = registry.get_transport_delay("climate.living_room", active_zones)
        assert delay == 7.5  # 30L / (2 × 2) = 7.5 min

        # Mark as active (simulating production heater turn-on)
        current_time = datetime(2024, 1, 15, 10, 0)
        with patch('custom_components.adaptive_thermostat.adaptive.manifold_registry.dt_util') as mock_dt_util:
            mock_dt_util.utcnow.return_value = current_time
            registry.mark_manifold_active("climate.living_room")

            # Second zone activates shortly after
            active_zones = {"climate.living_room": 2, "climate.kitchen": 1}
            delay = registry.get_transport_delay("climate.kitchen", active_zones)
            assert delay == 0.0  # Manifold warm

            # Third zone activates
            active_zones = {
                "climate.living_room": 2,
                "climate.kitchen": 1,
                "climate.dining": 1
            }
            delay = registry.get_transport_delay("climate.dining", active_zones)
            assert delay == 0.0  # Still warm

    def test_different_manifolds_independent_marking(self):
        """Test that marking one manifold active doesn't affect other manifolds."""
        from unittest.mock import patch

        manifolds = [
            Manifold(
                name="2nd Floor",
                zones=["climate.bathroom_2nd"],
                pipe_volume=20.0,
                flow_per_loop=2.0
            ),
            Manifold(
                name="Ground Floor",
                zones=["climate.living_room"],
                pipe_volume=15.0,
                flow_per_loop=2.0
            )
        ]
        registry = ManifoldRegistry(manifolds)

        # Activate 2nd floor zone and mark manifold active
        active_zones_2nd = {"climate.bathroom_2nd": 1}
        delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones_2nd)
        assert delay == 10.0  # Cold initially

        current_time = datetime(2024, 1, 15, 10, 0)
        with patch('custom_components.adaptive_thermostat.adaptive.manifold_registry.dt_util') as mock_dt_util:
            mock_dt_util.utcnow.return_value = current_time
            registry.mark_manifold_active("climate.bathroom_2nd")

            # 2nd floor should be warm
            delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones_2nd)
            assert delay == 0.0

        # Ground floor should still be cold
        active_zones_ground = {"climate.living_room": 1}
        delay = registry.get_transport_delay("climate.living_room", active_zones_ground)
        assert delay == 7.5  # 15L / (1 × 2) = 7.5 min (still cold)


class TestMultipleZonesActiveSameManifold:
    """Test correct total flow calculation with multiple active zones."""

    def test_two_zones_different_loop_counts(self):
        """Test flow calculation with zones having different loop counts."""
        manifolds = [
            Manifold(
                name="2nd Floor",
                zones=["climate.bathroom_2nd", "climate.bedroom_2nd"],
                pipe_volume=20.0,
                flow_per_loop=2.0
            )
        ]
        registry = ManifoldRegistry(manifolds)

        # Bathroom: 2 loops, Bedroom: 3 loops
        # Total: 5 loops × 2 L/min = 10 L/min
        # Delay: 20L / 10 L/min = 2 min
        active_zones = {
            "climate.bathroom_2nd": 2,
            "climate.bedroom_2nd": 3
        }

        delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones)
        assert delay == 2.0

    def test_three_zones_active(self):
        """Test flow calculation with three zones active."""
        manifolds = [
            Manifold(
                name="Ground Floor",
                zones=["climate.living_room", "climate.kitchen", "climate.dining"],
                pipe_volume=30.0,
                flow_per_loop=2.0
            )
        ]
        registry = ManifoldRegistry(manifolds)

        # Living: 3 loops, Kitchen: 2 loops, Dining: 1 loop
        # Total: 6 loops × 2 L/min = 12 L/min
        # Delay: 30L / 12 L/min = 2.5 min
        active_zones = {
            "climate.living_room": 3,
            "climate.kitchen": 2,
            "climate.dining": 1
        }

        delay = registry.get_transport_delay("climate.living_room", active_zones)
        assert delay == 2.5

    def test_only_some_zones_active(self):
        """Test that only active zones on manifold contribute to flow."""
        manifolds = [
            Manifold(
                name="2nd Floor",
                zones=["climate.bathroom_2nd", "climate.bedroom_2nd", "climate.hallway"],
                pipe_volume=25.0,
                flow_per_loop=2.0
            )
        ]
        registry = ManifoldRegistry(manifolds)

        # Only bathroom and bedroom active, hallway off
        # Total: 5 loops × 2 L/min = 10 L/min
        # Delay: 25L / 10 L/min = 2.5 min
        active_zones = {
            "climate.bathroom_2nd": 2,
            "climate.bedroom_2nd": 3
            # hallway not included
        }

        delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones)
        assert delay == 2.5

    def test_active_zone_from_different_manifold_not_counted(self):
        """Test that zones from other manifolds don't affect delay."""
        manifolds = [
            Manifold(
                name="2nd Floor",
                zones=["climate.bathroom_2nd"],
                pipe_volume=20.0,
                flow_per_loop=2.0
            ),
            Manifold(
                name="Ground Floor",
                zones=["climate.living_room"],
                pipe_volume=15.0,
                flow_per_loop=2.0
            )
        ]
        registry = ManifoldRegistry(manifolds)

        # Both zones active but on different manifolds
        active_zones = {
            "climate.bathroom_2nd": 1,
            "climate.living_room": 3  # Different manifold
        }

        # 2nd floor delay should only count its own zone
        # 1 loop × 2 L/min = 2 L/min
        # 20L / 2 L/min = 10 min
        delay = registry.get_transport_delay("climate.bathroom_2nd", active_zones)
        assert delay == 10.0

        # Ground floor delay should only count its own zone
        # 3 loops × 2 L/min = 6 L/min
        # 15L / 6 L/min = 2.5 min
        delay = registry.get_transport_delay("climate.living_room", active_zones)
        assert delay == 2.5

    def test_all_zones_on_manifold_active(self):
        """Test delay when all zones on a manifold are active."""
        manifolds = [
            Manifold(
                name="Ground Floor",
                zones=["climate.living_room", "climate.kitchen", "climate.dining", "climate.hallway"],
                pipe_volume=40.0,
                flow_per_loop=2.0
            )
        ]
        registry = ManifoldRegistry(manifolds)

        # All 4 zones active with varying loop counts
        # Total: (2+3+1+2) = 8 loops × 2 L/min = 16 L/min
        # Delay: 40L / 16 L/min = 2.5 min
        active_zones = {
            "climate.living_room": 2,
            "climate.kitchen": 3,
            "climate.dining": 1,
            "climate.hallway": 2
        }

        delay = registry.get_transport_delay("climate.living_room", active_zones)
        assert delay == 2.5


class TestManifoldPersistence:
    """Test manifold state persistence methods."""

    def test_get_state_for_persistence_empty(self):
        """Test get_state_for_persistence with no active manifolds."""
        manifolds = [
            Manifold(
                name="2nd Floor",
                zones=["climate.bathroom_2nd"],
                pipe_volume=20.0,
                flow_per_loop=2.0
            )
        ]
        registry = ManifoldRegistry(manifolds)

        state = registry.get_state_for_persistence()
        assert state == {}

    def test_get_state_for_persistence_single_manifold(self):
        """Test get_state_for_persistence with one active manifold."""
        from unittest.mock import patch

        manifolds = [
            Manifold(
                name="2nd Floor",
                zones=["climate.bathroom_2nd"],
                pipe_volume=20.0,
                flow_per_loop=2.0
            )
        ]
        registry = ManifoldRegistry(manifolds)

        # Mark manifold as active
        current_time = datetime(2024, 1, 15, 10, 30, 0)
        with patch('custom_components.adaptive_thermostat.adaptive.manifold_registry.dt_util') as mock_dt_util:
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
            Manifold(
                name="2nd Floor",
                zones=["climate.bathroom_2nd"],
                pipe_volume=20.0,
                flow_per_loop=2.0
            ),
            Manifold(
                name="Ground Floor",
                zones=["climate.living_room"],
                pipe_volume=15.0,
                flow_per_loop=2.0
            )
        ]
        registry = ManifoldRegistry(manifolds)

        # Mark both manifolds as active at different times
        time_2nd = datetime(2024, 1, 15, 10, 30, 0)
        time_ground = datetime(2024, 1, 15, 10, 45, 0)

        with patch('custom_components.adaptive_thermostat.adaptive.manifold_registry.dt_util') as mock_dt_util:
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
        manifolds = [
            Manifold(
                name="2nd Floor",
                zones=["climate.bathroom_2nd"],
                pipe_volume=20.0,
                flow_per_loop=2.0
            )
        ]
        registry = ManifoldRegistry(manifolds)

        # Restore empty state
        registry.restore_state({})

        # Should have no active manifolds
        assert len(registry._last_active_time) == 0

    def test_restore_state_single_manifold(self):
        """Test restore_state with one manifold."""
        manifolds = [
            Manifold(
                name="2nd Floor",
                zones=["climate.bathroom_2nd"],
                pipe_volume=20.0,
                flow_per_loop=2.0
            )
        ]
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
            Manifold(
                name="2nd Floor",
                zones=["climate.bathroom_2nd"],
                pipe_volume=20.0,
                flow_per_loop=2.0
            ),
            Manifold(
                name="Ground Floor",
                zones=["climate.living_room"],
                pipe_volume=15.0,
                flow_per_loop=2.0
            )
        ]
        registry = ManifoldRegistry(manifolds)

        # Restore state (use UTC timezone-aware datetimes)
        state = {
            "2nd Floor": "2024-01-15T10:30:00+00:00",
            "Ground Floor": "2024-01-15T10:45:00+00:00"
        }
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
        manifolds = [
            Manifold(
                name="2nd Floor",
                zones=["climate.bathroom_2nd"],
                pipe_volume=20.0,
                flow_per_loop=2.0
            )
        ]
        registry = ManifoldRegistry(manifolds)

        # Restore with invalid timestamp - should skip silently
        state = {"2nd Floor": "invalid-timestamp"}
        registry.restore_state(state)

        # Should not have any restored manifolds (parse_datetime returns None for invalid)
        assert len(registry._last_active_time) == 0

    def test_restore_state_mixed_valid_invalid(self):
        """Test restore_state with mix of valid and invalid timestamps."""
        manifolds = [
            Manifold(
                name="2nd Floor",
                zones=["climate.bathroom_2nd"],
                pipe_volume=20.0,
                flow_per_loop=2.0
            ),
            Manifold(
                name="Ground Floor",
                zones=["climate.living_room"],
                pipe_volume=15.0,
                flow_per_loop=2.0
            )
        ]
        registry = ManifoldRegistry(manifolds)

        # Restore with one valid and one invalid timestamp (use UTC timezone-aware)
        state = {
            "2nd Floor": "2024-01-15T10:30:00+00:00",
            "Ground Floor": "invalid-timestamp"
        }
        registry.restore_state(state)

        # Should only have the valid one
        assert len(registry._last_active_time) == 1
        assert "2nd Floor" in registry._last_active_time
        assert "Ground Floor" not in registry._last_active_time

    def test_persistence_roundtrip(self):
        """Test full persistence roundtrip: save state and restore it."""
        from unittest.mock import patch

        manifolds = [
            Manifold(
                name="2nd Floor",
                zones=["climate.bathroom_2nd"],
                pipe_volume=20.0,
                flow_per_loop=2.0
            ),
            Manifold(
                name="Ground Floor",
                zones=["climate.living_room"],
                pipe_volume=15.0,
                flow_per_loop=2.0
            )
        ]

        # First registry - mark manifolds active
        registry1 = ManifoldRegistry(manifolds)

        time_2nd = datetime(2024, 1, 15, 10, 30, 0)
        time_ground = datetime(2024, 1, 15, 10, 45, 0)

        with patch('custom_components.adaptive_thermostat.adaptive.manifold_registry.dt_util') as mock_dt_util:
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
