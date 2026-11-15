# CLAUDE.md

## Overview

Adaptive Climate: HA custom component with PID thermostat, adaptive learning, multi-zone coordination, physics-based init. Fork of Smart Thermostat PID.

## Commands

```bash
pytest                                    # all tests
pytest tests/test_pid_controller.py       # specific file
pytest --cov=custom_components/adaptive_climate  # coverage
ruff check custom_components/ tests/       # lint
ruff format custom_components/ tests/       # format
pyright custom_components/adaptive_climate/ # type check
pre-commit run --all-files                  # all checks
```

## Code Style

- **Max file length:** 800 lines - extract into modules when exceeded
- **Documentation:** Update GitHub wiki alongside any doc changes
- **Naming:** `*Controller` for hardware-actuating classes (HeaterController, PWMController), `*Manager` for pure-logic orchestration (CycleTrackerManager, PIDTuningManager, KeManager, NightSetbackManager)
- **Type annotations:** Use `X | None` (PEP 604), not `Optional[X]`
- **Heating types:** `HeatingType(StrEnum)` in `const.py` — never raw strings
- **Timestamps:** `homeassistant.util.dt.utcnow()` for wall-clock, `time.monotonic()` for elapsed durations — never `datetime.now()` or `time.time()`
- **Assertions:** Never use `assert` in production code — raise `ValueError`/`TypeError`
- **Entity IDs:** Use `split_entity_id()` — never string slicing
- **HA decorators:** `@callback` only on synchronous functions — never on `async def`
- **Manager interfaces:** Managers receive a `ThermostatState` Protocol, not raw callbacks or thermostat references
- **Coordinator access:** Use `self._coordinator` cached property — never inline `hass.data.get(DOMAIN, {}).get("coordinator")`
- **Persistence:** HA Store API only (`async_save_zone`) — no legacy file-based `save()`
- **Linting:** Ruff enforces style (line-length 120, import sorting, pyupgrade)
- **Type checking:** Pyright strict on source, excluded from tests
- **Type ignores:** Use `# type: ignore[specific-code]` with comment for HA boundaries — never bare `# type: ignore`

## Architecture

**See also:** `docs/architecture/manager-communication.md` for detailed explanation of communication patterns between thermostat and managers (Events, Callbacks, Protocols, Direct Reference).

Module map: `docs/architecture/modules.md`. Subsystem behaviour, tuning tables and config examples: `docs/subsystems/` (one file per subsystem).

### Data Flow

```
Temp → TemperatureManager → PID → HeaterController → CycleTracker → Coordinator → CentralController
```

### Config Levels

1. **Domain** (`adaptive_climate:`): house_energy_rating, main_heater, sync_modes, presets, manifolds, thermal_groups
2. **Entity** (`climate:` platform): heater, target_sensor, heating_type, area_m2, night_setback

## Caveats

- **Service calls:** Use `hass.services.async_call()` - never manipulate states directly
- **PWM vs Valve:** `pwm > 0` = on/off cycling; `pwm = 0` = direct 0-100%
- **State persistence:** RestoreEntity - PID integral and gains persist
- **HAOS sensors:** Only update on change; large `sensor_dt` normal when stable
