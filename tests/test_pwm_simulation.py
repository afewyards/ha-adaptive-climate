"""24-hour replay of real legacy PWM and the production group runtime.

The model supplies already-effective durations, zero actuator travel and zero
minimum times. It is a scheduling/accounting comparison, not thermal feedback.
Run with pytest -s to print measured JSON including per-zone delivery/debt.
"""

import json
from types import SimpleNamespace
import time

import pytest

from test_pwm_load_balancing import Simulation
from custom_components.adaptive_climate.managers import pwm_controller as pwm_module


class LegacyReplay:
    """Drive the actual independent PWM algorithm every 30 simulated seconds."""

    def __init__(self, sim):
        self.clock = sim
        self.zones = []
        for n, phase in enumerate((0, 900, 1800, 2700)):
            state = SimpleNamespace(on=False, changed=-float(phase), delivered=0.0, transitions=[], mode="heat")

            def active(mode, zone=state):
                return zone.on

            async def turn_on(*, zone=state, **kwargs):
                if not zone.on:
                    zone.on = True
                    zone.transitions.append((self.clock.now, True))

            async def turn_off(*, zone=state, **kwargs):
                if zone.on:
                    zone.on = False
                    zone.transitions.append((self.clock.now, False))

            heater = SimpleNamespace(
                is_active=active,
                get_entities=lambda _: [],
                async_turn_on=turn_on,
                async_turn_off=turn_off,
                _has_demand=True,
            )
            thermostat = SimpleNamespace(entity_id=f"climate.legacy_{n}", _current_temp=19, _target_temp=22)
            pwm = pwm_module.PWMController(thermostat, 3600, 100, 0, 0)
            self.zones.append((state, heater, pwm))

    async def tick(self, durations, urgent):
        for n, (state, heater, pwm) in enumerate(self.zones):
            await pwm.async_pwm_switch(
                control_output=durations[n] / 36 if state.mode == "heat" else 0,
                hvac_mode="heat",
                heater_controller=heater,
                get_cycle_start_time=lambda: 0,
                set_is_heating=lambda _: None,
                set_last_heat_cycle_time=lambda _: None,
                time_changed=state.changed,
                set_time_changed=lambda t, zone=state: setattr(zone, "changed", t),
                force_on=n in urgent,
                force_off=False,
                set_force_on=lambda _: None,
                set_force_off=lambda _: None,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["minimal", "maximal"])
@pytest.mark.parametrize("interactive", [False, True])
@pytest.mark.parametrize("duties", [(1440, 1080, 1080, 1440), (720, 720, 720, 720)])
async def test_24h_legacy_comparison(monkeypatch, strategy, interactive, duties):
    sim = Simulation(durations=duties, strategy=strategy)
    monkeypatch.setattr(pwm_module, "time", SimpleNamespace(monotonic=lambda: sim.now))
    monkeypatch.setattr(pwm_module, "HVACMode", SimpleNamespace(HEAT="heat", COOL="cool"))
    legacy = LegacyReplay(sim)
    starts = {"legacy": 0, "coordinated": 0}
    union = {"legacy": 0, "coordinated": 0}
    previous = {"legacy": False, "coordinated": False}
    delivered = [0.0] * 4
    latencies = {"legacy": [], "coordinated": []}
    cpu_start = time.perf_counter()
    for instant in range(0, 86400, 30):
        await sim.advance(float(instant))
        urgent = set()
        if interactive and instant == 6 * 3600:
            sim.zones[3].mode = "off"
            legacy.zones[3][0].mode = "off"
            sim.manager.invalidate(sim.zones[3].entity_id)
        if interactive and instant == 8 * 3600 + 1020:
            sim.zones[3].mode = "heat"
            legacy.zones[3][0].mode = "heat"
            urgent.add(3)
        if interactive and instant == 12 * 3600 + 1020:
            sim.zones[0].target += 1
            sim.zones[0].seconds = 1800
            urgent.add(0)
        await sim.update()
        await legacy.tick([z.seconds for z in sim.zones], urgent)
        for n in urgent:
            for label, on in (("legacy", legacy.zones[n][0].on), ("coordinated", sim.zones[n].on)):
                latencies[label].append(0 if on else None)
        current = {"legacy": any(s.on for s, _, _ in legacy.zones), "coordinated": any(z.on for z in sim.zones)}
        for label, active in current.items():
            starts[label] += int(active and not previous[label])
            union[label] += 30 * int(active)
        previous = current
        for n, zone in enumerate(sim.zones):
            delivered[n] += 30 * int(zone.on)
            legacy.zones[n][0].delivered += 30 * int(legacy.zones[n][0].on)
    await sim.advance(86400 - 0.001)
    for zone in sim.manager._zones.values():
        sim.manager._sample(zone, sim.now)
        assert zone.requested == pytest.approx(zone.delivered + zone.credit + zone.cancelled_balance, abs=0.01)
    report = {
        "strategy": strategy,
        "interactive_events": interactive,
        "initial_effective_durations_seconds": duties,
        "simulated_seconds": 86400,
        "estimated_starts": starts,
        "estimated_union_seconds_sampled_30s": union,
        "urgent_command_latency_seconds": latencies,
        "legacy_electrical_seconds_sampled_30s": [s.delivered for s, _, _ in legacy.zones],
        "coordinated_electrical_seconds_exact": [round(z.delivered, 3) for z in sim.manager._zones.values()],
        "coordinated_debt_seconds": [round(z.credit, 3) for z in sim.manager._zones.values()],
        "coordinated_cancelled_balance_seconds": [round(z.cancelled_balance, 3) for z in sim.manager._zones.values()],
        "max_ordinary_deferral_seconds": max(z.max_deferral for z in sim.manager._zones.values()),
        "coordinated_relay_transitions": [len(z.transitions) for z in sim.zones],
        "legacy_relay_transitions": [len(s.transitions) for s, _, _ in legacy.zones],
        "cpu_seconds": round(time.perf_counter() - cpu_start, 4),
    }
    print(json.dumps(report, sort_keys=True))
    expected = [0, 0] if interactive else []
    assert latencies == {"legacy": expected, "coordinated": expected}
    if not interactive:
        for n, zone in enumerate(sim.manager._zones.values()):
            assert zone.delivered + zone.credit == pytest.approx(legacy.zones[n][0].delivered, abs=0.01)
        if strategy == "minimal":
            assert starts["coordinated"] <= starts["legacy"]
            if duties == (720, 720, 720, 720):
                assert starts["coordinated"] < starts["legacy"]
    assert sim.manager.mode == "active"
