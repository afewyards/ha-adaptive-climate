"""Source guards for pump flow wiring that the mocked HA test harness can't exercise."""

from pathlib import Path

PACKAGE = Path(__file__).parent.parent / "custom_components" / "adaptive_climate"


def test_zone_data_carries_valve_actuation_time():
    source = (PACKAGE / "climate_setup.py").read_text()
    assert '"valve_actuation_time": valve_actuation_seconds,' in source


def test_domain_schema_accepts_pump_flow_control():
    source = (PACKAGE / "__init__.py").read_text()
    assert "vol.Optional(CONF_PUMP_FLOW_CONTROL): PUMP_FLOW_CONTROL_SCHEMA" in source
