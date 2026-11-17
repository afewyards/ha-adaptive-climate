"""Tests for ClimateControlMixin.set_control_value call ordering.

Regression tests for H05: zone demand must be updated BEFORE querying transport
delay so that the first cold-manifold heating cycle receives the correct delay
instead of 0.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# Minimal stub that exercises set_control_value() without HA infrastructure
# ---------------------------------------------------------------------------


class _MinimalControlStub:
    """Minimal stub implementing just enough for set_control_value().

    We inherit from ClimateControlMixin directly so the real method under
    test is used — no reimplementation.
    """

    def __init__(self, coordinator: MagicMock) -> None:
        from custom_components.adaptive_climate.const import DOMAIN

        self.entity_id = "climate.living_room"
        self._zone_id = "living_room"

        # HVAC mode (value attribute accessed for hvac_mode_str)
        mock_hvac = MagicMock()
        mock_hvac.value = "heat"
        self._hvac_mode = mock_hvac
        self.hvac_mode = mock_hvac  # property alias

        # hass — only data dict needed
        self.hass = MagicMock()
        self.hass.data = {DOMAIN: {"coordinator": coordinator}}

        # HeaterController stub
        hc = MagicMock()
        hc.update_open_closed_times = MagicMock()
        hc.set_transport_delay = MagicMock()
        hc.async_set_control_value = AsyncMock()
        hc.is_active = MagicMock(return_value=True)
        self._heater_controller = hc

        # Required attributes
        min_open = MagicMock()
        min_open.seconds = 300
        self._min_open_time = min_open
        min_closed = MagicMock()
        min_closed.seconds = 300
        self._min_closed_time = min_closed
        self._control_output = 75.0
        self._time_changed = 0.0
        self._force_on = False
        self._force_off = False

        # Callbacks used by async_set_control_value
        self._set_is_heating = MagicMock()
        self._set_last_heat_cycle_time = MagicMock()
        self._set_time_changed = MagicMock()
        self._set_force_on = MagicMock()
        self._set_force_off = MagicMock()

    def _get_cycle_start_time(self) -> float:
        return 0.0

    @property
    def _is_device_active(self) -> bool:
        return self._heater_controller.is_active(self.hvac_mode)


def _make_stub_with_ordered_coordinator() -> tuple[_MinimalControlStub, list[str], MagicMock]:
    """Return (stub, call_log, coordinator) where call_log records coordinator method order."""
    call_log: list[str] = []

    coordinator = MagicMock()

    def _update_demand(*_: object) -> None:
        call_log.append("update_zone_demand")

    def _get_delay(*_: object) -> float:
        call_log.append("get_transport_delay_for_zone")
        return 2.0  # 2 minutes transport delay

    coordinator.update_zone_demand = MagicMock(side_effect=_update_demand)
    coordinator.get_transport_delay_for_zone = MagicMock(side_effect=_get_delay)

    from custom_components.adaptive_climate.climate_control import ClimateControlMixin

    class _Stub(_MinimalControlStub, ClimateControlMixin):
        pass

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    stub = _Stub(coordinator)
    return stub, call_log, coordinator


def _run(stub: _MinimalControlStub) -> None:
    """Run set_control_value() in a fresh event loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(stub.set_control_value())
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSetControlValueDemandOrder:
    """H05 regression: update_zone_demand must precede get_transport_delay_for_zone.

    When a zone first starts heating on a cold manifold, the coordinator must
    know the zone is active before it can return the correct transport delay.
    """

    def test_demand_updated_before_delay_query(self) -> None:
        """update_zone_demand is called before get_transport_delay_for_zone."""
        stub, call_log, _ = _make_stub_with_ordered_coordinator()
        _run(stub)

        assert "update_zone_demand" in call_log, "update_zone_demand was never called"
        assert "get_transport_delay_for_zone" in call_log, "get_transport_delay_for_zone was never called"

        demand_idx = call_log.index("update_zone_demand")
        delay_idx = call_log.index("get_transport_delay_for_zone")
        assert demand_idx < delay_idx, (
            f"update_zone_demand (call #{demand_idx}) must precede "
            f"get_transport_delay_for_zone (call #{delay_idx}). "
            f"Full call order: {call_log}"
        )

    def test_transport_delay_applied_to_heater_controller(self) -> None:
        """Heater controller receives coordinator's transport delay converted to seconds."""
        stub, _, _ = _make_stub_with_ordered_coordinator()
        _run(stub)

        # Coordinator returns 2 minutes; heater controller must receive 120 seconds
        stub._heater_controller.set_transport_delay.assert_called_once_with(2.0 * 60)

    def test_update_zone_demand_called_with_correct_args(self) -> None:
        """update_zone_demand receives zone_id, active state, and hvac mode string."""
        stub, _, coordinator = _make_stub_with_ordered_coordinator()
        _run(stub)

        coordinator.update_zone_demand.assert_called_once_with(
            stub._zone_id,
            True,  # _is_device_active
            "heat",  # _hvac_mode.value
        )
