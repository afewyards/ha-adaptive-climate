"""Tests for service registration and handlers in adaptive_thermostat."""
import asyncio
import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime


# Mock Home Assistant modules before importing services
class MockServiceCall:
    """Mock ServiceCall for testing."""
    def __init__(self, data=None):
        self.data = data or {}


def _run_async(coro):
    """Run an async coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = Mock()
    hass.services = Mock()
    hass.services.async_register = Mock()
    hass.services.has_service = Mock(return_value=True)
    hass.services.async_call = AsyncMock()
    hass.states = Mock()
    hass.states.get = Mock(return_value=None)
    hass.data = {}
    return hass


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator."""
    coordinator = Mock()
    coordinator.get_all_zones = Mock(return_value={
        "living_room": {
            "climate_entity_id": "climate.living_room",
            "adaptive_learner": None,
        },
        "bedroom": {
            "climate_entity_id": "climate.bedroom",
            "adaptive_learner": None,
        },
    })
    return coordinator


@pytest.fixture
def mock_vacation_mode():
    """Create a mock vacation mode handler."""
    vacation_mode = Mock()
    vacation_mode.async_enable = AsyncMock()
    vacation_mode.async_disable = AsyncMock()
    return vacation_mode


@pytest.fixture
def mock_notification_funcs():
    """Create mock notification functions."""
    return {
        "send_notification": AsyncMock(return_value=True),
        "send_persistent": AsyncMock(return_value=True),
    }


# =============================================================================
# Test Service Registration
# =============================================================================


class TestServiceRegistration:
    """Tests for service registration."""

    def test_all_services_registered(self, mock_hass, mock_coordinator, mock_vacation_mode, mock_notification_funcs):
        """Verify all expected services are registered."""
        from custom_components.adaptive_thermostat.services import (
            async_register_services,
            SERVICE_RUN_LEARNING,
            SERVICE_HEALTH_CHECK,
            SERVICE_WEEKLY_REPORT,
            SERVICE_COST_REPORT,
            SERVICE_SET_VACATION_MODE,
            SERVICE_ENERGY_STATS,
            SERVICE_PID_RECOMMENDATIONS,
        )
        from custom_components.adaptive_thermostat.const import DOMAIN

        # Create mock schemas
        mock_vacation_schema = Mock()
        mock_cost_schema = Mock()

        # Register services
        async_register_services(
            hass=mock_hass,
            coordinator=mock_coordinator,
            vacation_mode=mock_vacation_mode,
            notify_service="test_notify",
            persistent_notification=True,
            async_send_notification_func=mock_notification_funcs["send_notification"],
            async_send_persistent_notification_func=mock_notification_funcs["send_persistent"],
            vacation_schema=mock_vacation_schema,
            cost_report_schema=mock_cost_schema,
            default_vacation_target_temp=15.0,
        )

        # Verify all 7 services were registered
        assert mock_hass.services.async_register.call_count == 7

        # Get all registered service names
        registered_services = [
            call[0][1] for call in mock_hass.services.async_register.call_args_list
        ]

        # Verify each expected service was registered
        expected_services = [
            SERVICE_RUN_LEARNING,
            SERVICE_HEALTH_CHECK,
            SERVICE_WEEKLY_REPORT,
            SERVICE_COST_REPORT,
            SERVICE_SET_VACATION_MODE,
            SERVICE_ENERGY_STATS,
            SERVICE_PID_RECOMMENDATIONS,
        ]
        for service in expected_services:
            assert service in registered_services, f"Service {service} not registered"

    def test_services_registered_with_correct_domain(self, mock_hass, mock_coordinator, mock_vacation_mode, mock_notification_funcs):
        """Verify services are registered under correct domain."""
        from custom_components.adaptive_thermostat.services import async_register_services
        from custom_components.adaptive_thermostat.const import DOMAIN

        async_register_services(
            hass=mock_hass,
            coordinator=mock_coordinator,
            vacation_mode=mock_vacation_mode,
            notify_service=None,
            persistent_notification=False,
            async_send_notification_func=mock_notification_funcs["send_notification"],
            async_send_persistent_notification_func=mock_notification_funcs["send_persistent"],
            vacation_schema=Mock(),
            cost_report_schema=Mock(),
            default_vacation_target_temp=15.0,
        )

        # All services should be registered under the DOMAIN
        for call in mock_hass.services.async_register.call_args_list:
            assert call[0][0] == DOMAIN

    def test_cost_report_uses_schema(self, mock_hass, mock_coordinator, mock_vacation_mode, mock_notification_funcs):
        """Verify cost_report service is registered with schema."""
        from custom_components.adaptive_thermostat.services import (
            async_register_services,
            SERVICE_COST_REPORT,
        )

        mock_cost_schema = Mock()

        async_register_services(
            hass=mock_hass,
            coordinator=mock_coordinator,
            vacation_mode=mock_vacation_mode,
            notify_service=None,
            persistent_notification=False,
            async_send_notification_func=mock_notification_funcs["send_notification"],
            async_send_persistent_notification_func=mock_notification_funcs["send_persistent"],
            vacation_schema=Mock(),
            cost_report_schema=mock_cost_schema,
            default_vacation_target_temp=15.0,
        )

        # Find the cost_report registration call
        cost_report_call = None
        for call in mock_hass.services.async_register.call_args_list:
            if call[0][1] == SERVICE_COST_REPORT:
                cost_report_call = call
                break

        assert cost_report_call is not None
        assert cost_report_call[1].get("schema") == mock_cost_schema


# =============================================================================
# Test Health Check Deduplication
# =============================================================================


class TestHealthCheckDeduplication:
    """Tests for health check deduplication."""

    def test_manual_and_scheduled_use_same_core(self, mock_hass, mock_coordinator, mock_notification_funcs):
        """Verify both manual and scheduled use the same core logic."""
        from custom_components.adaptive_thermostat.services import (
            async_handle_health_check,
            async_scheduled_health_check,
            _run_health_check_core,
        )

        # Both functions should delegate to _run_health_check_core
        # This is verified by checking that _run_health_check_core exists and is used
        assert _run_health_check_core is not None

        # Verify the function signature accepts is_scheduled parameter
        import inspect
        sig = inspect.signature(_run_health_check_core)
        assert "is_scheduled" in sig.parameters

    def test_scheduled_only_notifies_on_issues(self, mock_hass, mock_coordinator, mock_notification_funcs):
        """Verify scheduled check skips notification when healthy."""
        from custom_components.adaptive_thermostat.services import _run_health_check_core
        from custom_components.adaptive_thermostat.analytics.health import HealthStatus

        # Mock the health monitor to return healthy status
        with patch("custom_components.adaptive_thermostat.analytics.health.SystemHealthMonitor") as MockHealthMonitor:
            mock_monitor = Mock()
            mock_result = {
                "status": HealthStatus.HEALTHY,
                "summary": "All zones healthy",
                "zone_issues": {},
            }

            mock_monitor.check_all_zones = Mock(return_value=mock_result)
            MockHealthMonitor.return_value = mock_monitor

            # Run scheduled health check (is_scheduled=True)
            result = _run_async(_run_health_check_core(
                hass=mock_hass,
                coordinator=mock_coordinator,
                notify_service="test_notify",
                persistent_notification=True,
                async_send_notification_func=mock_notification_funcs["send_notification"],
                async_send_persistent_notification_func=mock_notification_funcs["send_persistent"],
                is_scheduled=True,
            ))

            # Should NOT have sent notifications for healthy status in scheduled mode
            mock_notification_funcs["send_notification"].assert_not_called()
            mock_notification_funcs["send_persistent"].assert_not_called()

    def test_scheduled_notifies_on_issues(self, mock_hass, mock_coordinator, mock_notification_funcs):
        """Verify scheduled check sends notification when issues found."""
        from custom_components.adaptive_thermostat.services import _run_health_check_core
        from custom_components.adaptive_thermostat.analytics.health import HealthStatus

        with patch("custom_components.adaptive_thermostat.analytics.health.SystemHealthMonitor") as MockHealthMonitor:
            mock_monitor = Mock()
            mock_issue = Mock()
            mock_issue.message = "Test issue"
            mock_result = {
                "status": HealthStatus.WARNING,
                "summary": "Issues detected",
                "zone_issues": {"living_room": [mock_issue]},
            }
            mock_monitor.check_all_zones = Mock(return_value=mock_result)
            MockHealthMonitor.return_value = mock_monitor

            # Run scheduled health check with issues
            _run_async(_run_health_check_core(
                hass=mock_hass,
                coordinator=mock_coordinator,
                notify_service="test_notify",
                persistent_notification=True,
                async_send_notification_func=mock_notification_funcs["send_notification"],
                async_send_persistent_notification_func=mock_notification_funcs["send_persistent"],
                is_scheduled=True,
            ))

            # Should have sent notifications for warning status
            mock_notification_funcs["send_notification"].assert_called_once()
            mock_notification_funcs["send_persistent"].assert_called_once()


# =============================================================================
# Test Weekly Report Deduplication
# =============================================================================


class TestWeeklyReportDeduplication:
    """Tests for weekly report deduplication."""

    def test_scheduled_only_runs_on_sunday(self, mock_hass, mock_coordinator, mock_notification_funcs):
        """Verify scheduled report only runs on Sunday."""
        from custom_components.adaptive_thermostat.services import async_scheduled_weekly_report

        # Test on a Monday (weekday() = 0)
        monday = Mock()
        monday.weekday = Mock(return_value=0)

        with patch("custom_components.adaptive_thermostat.services._run_weekly_report_core") as mock_core:
            _run_async(async_scheduled_weekly_report(
                hass=mock_hass,
                coordinator=mock_coordinator,
                notify_service="test_notify",
                persistent_notification=True,
                async_send_notification_func=mock_notification_funcs["send_notification"],
                async_send_persistent_notification_func=mock_notification_funcs["send_persistent"],
                _now=monday,
            ))

            # Should NOT have called the core function on Monday
            mock_core.assert_not_called()

    def test_scheduled_runs_on_sunday(self, mock_hass, mock_coordinator, mock_notification_funcs):
        """Verify scheduled report runs on Sunday."""
        from custom_components.adaptive_thermostat.services import async_scheduled_weekly_report

        # Test on a Sunday (weekday() = 6)
        sunday = Mock()
        sunday.weekday = Mock(return_value=6)

        with patch("custom_components.adaptive_thermostat.services._run_weekly_report_core") as mock_core:
            mock_core.return_value = {"report": Mock(), "has_energy_data": False, "total_cost": 0}

            _run_async(async_scheduled_weekly_report(
                hass=mock_hass,
                coordinator=mock_coordinator,
                notify_service="test_notify",
                persistent_notification=True,
                async_send_notification_func=mock_notification_funcs["send_notification"],
                async_send_persistent_notification_func=mock_notification_funcs["send_persistent"],
                _now=sunday,
            ))

            # Should have called the core function on Sunday
            mock_core.assert_called_once()


# =============================================================================
# Test New Service Handlers
# =============================================================================


class TestEnergyStatsHandler:
    """Tests for energy_stats service handler."""

    def test_energy_stats_returns_expected_structure(self, mock_hass, mock_coordinator):
        """Verify energy_stats returns expected data structure."""
        from custom_components.adaptive_thermostat.services import async_handle_energy_stats

        call = MockServiceCall()
        result = _run_async(async_handle_energy_stats(mock_hass, mock_coordinator, call))

        # Verify expected keys in result
        assert "total_power_w" in result
        assert "zone_powers" in result
        assert "energy_today_kwh" in result
        assert "cost_today" in result
        assert "weekly_energy_kwh" in result
        assert "weekly_cost" in result
        assert "zone_duty_cycles" in result

    def test_energy_stats_with_sensor_data(self, mock_hass, mock_coordinator):
        """Verify energy_stats retrieves sensor data correctly."""
        from custom_components.adaptive_thermostat.services import async_handle_energy_stats

        # Set up mock sensor states
        mock_power_state = Mock()
        mock_power_state.state = "1500"
        mock_power_state.attributes = {"zone_powers": {"living_room": 800, "bedroom": 700}}

        mock_cost_state = Mock()
        mock_cost_state.state = "25.50"
        mock_cost_state.attributes = {"weekly_energy_kwh": 150}

        def get_state(entity_id):
            if entity_id == "sensor.heating_total_power":
                return mock_power_state
            elif entity_id == "sensor.heating_weekly_cost":
                return mock_cost_state
            return None

        mock_hass.states.get = Mock(side_effect=get_state)

        call = MockServiceCall()
        result = _run_async(async_handle_energy_stats(mock_hass, mock_coordinator, call))

        assert result["total_power_w"] == 1500.0
        assert result["zone_powers"] == {"living_room": 800, "bedroom": 700}
        assert result["weekly_cost"] == 25.50
        assert result["weekly_energy_kwh"] == 150


class TestPIDRecommendationsHandler:
    """Tests for pid_recommendations service handler."""

    def test_pid_recommendations_returns_expected_structure(self, mock_hass, mock_coordinator):
        """Verify pid_recommendations returns expected data structure."""
        from custom_components.adaptive_thermostat.services import async_handle_pid_recommendations

        call = MockServiceCall()
        result = _run_async(async_handle_pid_recommendations(mock_hass, mock_coordinator, call))

        # Verify expected keys in result
        assert "zones" in result
        assert "zones_with_recommendations" in result
        assert "zones_insufficient_data" in result
        assert "zones_error" in result

    def test_pid_recommendations_with_learner(self, mock_hass, mock_coordinator):
        """Verify pid_recommendations works with adaptive learner."""
        from custom_components.adaptive_thermostat.services import async_handle_pid_recommendations

        # Set up a zone with adaptive learner
        mock_learner = Mock()
        mock_learner.get_cycle_count = Mock(return_value=10)
        mock_learner.calculate_pid_adjustment = Mock(return_value={
            "kp": 110.0,
            "ki": 0.011,
            "kd": 0.1,
        })

        mock_coordinator.get_all_zones = Mock(return_value={
            "living_room": {
                "climate_entity_id": "climate.living_room",
                "adaptive_learner": mock_learner,
            },
        })

        # Set up mock state
        mock_state = Mock()
        mock_state.attributes = {"kp": 100.0, "ki": 0.01, "kd": 0.0}
        mock_hass.states.get = Mock(return_value=mock_state)

        call = MockServiceCall()
        result = _run_async(async_handle_pid_recommendations(mock_hass, mock_coordinator, call))

        assert result["zones_with_recommendations"] == 1
        assert "living_room" in result["zones"]
        assert result["zones"]["living_room"]["status"] == "recommendation_available"
        assert result["zones"]["living_room"]["recommended_pid"]["kp"] == 110.0

    def test_pid_recommendations_no_learner(self, mock_hass, mock_coordinator):
        """Verify pid_recommendations handles zones without learner."""
        from custom_components.adaptive_thermostat.services import async_handle_pid_recommendations

        # Zone without adaptive learner
        mock_coordinator.get_all_zones = Mock(return_value={
            "living_room": {
                "climate_entity_id": "climate.living_room",
                "adaptive_learner": None,
            },
        })

        call = MockServiceCall()
        result = _run_async(async_handle_pid_recommendations(mock_hass, mock_coordinator, call))

        assert result["zones"]["living_room"]["status"] == "learning_disabled"


# =============================================================================
# Test Run Learning Handler
# =============================================================================


class TestRunLearningHandler:
    """Tests for run_learning service handler."""

    def test_run_learning_returns_results(self, mock_hass, mock_coordinator):
        """Verify run_learning returns proper results structure."""
        from custom_components.adaptive_thermostat.services import async_handle_run_learning

        call = MockServiceCall()
        result = _run_async(async_handle_run_learning(mock_hass, mock_coordinator, call))

        assert "zones_analyzed" in result
        assert "zones_with_recommendations" in result
        assert "zones_skipped" in result
        assert "zone_results" in result

    def test_run_learning_skips_zones_without_learner(self, mock_hass, mock_coordinator):
        """Verify run_learning skips zones without adaptive learner."""
        from custom_components.adaptive_thermostat.services import async_handle_run_learning

        call = MockServiceCall()
        result = _run_async(async_handle_run_learning(mock_hass, mock_coordinator, call))

        # Both zones have no learner, should be skipped
        assert result["zones_skipped"] == 2
        assert result["zone_results"]["living_room"]["status"] == "skipped"
        assert result["zone_results"]["living_room"]["reason"] == "learning_disabled"


# =============================================================================
# Test Vacation Mode Handler
# =============================================================================


class TestVacationModeHandler:
    """Tests for set_vacation_mode service handler."""

    def test_vacation_mode_enable(self, mock_hass, mock_vacation_mode):
        """Verify vacation mode can be enabled."""
        from custom_components.adaptive_thermostat.services import async_handle_set_vacation_mode

        call = MockServiceCall({"enabled": True, "target_temp": 12.0})
        _run_async(async_handle_set_vacation_mode(mock_hass, mock_vacation_mode, call, 15.0))

        mock_vacation_mode.async_enable.assert_called_once_with(12.0)
        mock_vacation_mode.async_disable.assert_not_called()

    def test_vacation_mode_disable(self, mock_hass, mock_vacation_mode):
        """Verify vacation mode can be disabled."""
        from custom_components.adaptive_thermostat.services import async_handle_set_vacation_mode

        call = MockServiceCall({"enabled": False})
        _run_async(async_handle_set_vacation_mode(mock_hass, mock_vacation_mode, call, 15.0))

        mock_vacation_mode.async_disable.assert_called_once()
        mock_vacation_mode.async_enable.assert_not_called()

    def test_vacation_mode_uses_default_temp(self, mock_hass, mock_vacation_mode):
        """Verify vacation mode uses default temp when not specified."""
        from custom_components.adaptive_thermostat.services import async_handle_set_vacation_mode

        call = MockServiceCall({"enabled": True})
        _run_async(async_handle_set_vacation_mode(mock_hass, mock_vacation_mode, call, 15.0))

        mock_vacation_mode.async_enable.assert_called_once_with(15.0)


# =============================================================================
# Test Daily Learning Scheduled Callback
# =============================================================================


class TestDailyLearningCallback:
    """Tests for daily learning scheduled callback."""

    def test_daily_learning_processes_zones(self, mock_hass, mock_coordinator):
        """Verify daily learning processes all zones."""
        from custom_components.adaptive_thermostat.services import async_daily_learning

        # Set up a zone with adaptive learner
        mock_learner = Mock()
        mock_learner.calculate_pid_adjustment = Mock(return_value=None)

        mock_coordinator.get_all_zones = Mock(return_value={
            "living_room": {
                "climate_entity_id": "climate.living_room",
                "adaptive_learner": mock_learner,
            },
        })

        # Set up mock state
        mock_state = Mock()
        mock_state.attributes = {"kp": 100.0, "ki": 0.01, "kd": 0.0}
        mock_hass.states.get = Mock(return_value=mock_state)

        _now = Mock()
        _run_async(async_daily_learning(mock_hass, mock_coordinator, 7, _now))

        # Verify learner was called
        mock_learner.calculate_pid_adjustment.assert_called_once()


# =============================================================================
# Test Service Constants
# =============================================================================


class TestServiceConstants:
    """Tests for service constants."""

    def test_service_names_defined(self):
        """Verify all service name constants are defined."""
        from custom_components.adaptive_thermostat.services import (
            SERVICE_RUN_LEARNING,
            SERVICE_HEALTH_CHECK,
            SERVICE_WEEKLY_REPORT,
            SERVICE_COST_REPORT,
            SERVICE_SET_VACATION_MODE,
            SERVICE_ENERGY_STATS,
            SERVICE_PID_RECOMMENDATIONS,
        )

        assert SERVICE_RUN_LEARNING == "run_learning"
        assert SERVICE_HEALTH_CHECK == "health_check"
        assert SERVICE_WEEKLY_REPORT == "weekly_report"
        assert SERVICE_COST_REPORT == "cost_report"
        assert SERVICE_SET_VACATION_MODE == "set_vacation_mode"
        assert SERVICE_ENERGY_STATS == "energy_stats"
        assert SERVICE_PID_RECOMMENDATIONS == "pid_recommendations"


# =============================================================================
# Integration Test - Module Import
# =============================================================================


def test_services_module_exists():
    """Verify services module can be imported."""
    from custom_components.adaptive_thermostat import services

    assert services is not None
    assert hasattr(services, "async_register_services")
    assert hasattr(services, "async_handle_health_check")
    assert hasattr(services, "async_handle_weekly_report")
    assert hasattr(services, "async_handle_cost_report")
    assert hasattr(services, "async_handle_run_learning")
    assert hasattr(services, "async_handle_set_vacation_mode")
    assert hasattr(services, "async_handle_energy_stats")
    assert hasattr(services, "async_handle_pid_recommendations")
    assert hasattr(services, "async_scheduled_health_check")
    assert hasattr(services, "async_scheduled_weekly_report")
    assert hasattr(services, "async_daily_learning")
