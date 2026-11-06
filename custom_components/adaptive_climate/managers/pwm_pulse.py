"""Prepare effective pulse reservations without spending duty credit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .heater_controller import HeaterController


@dataclass(frozen=True)
class Pulse:
    """Electrical duration plus raw credit to debit after a successful start."""

    seconds: float
    accumulator_debit: float = 0.0
    continuous: bool = False


def prepare_pulse(heater: HeaterController, output: float, now: float) -> Pulse:
    """Use the existing PWM timing model once per new allocation.

    The runtime freezes this result until output/target changes or a new period
    begins. Replanning phases alone must never call this function again.
    """
    pwm = heater._pwm_controller
    if pwm is None or output <= 0:
        return Pulse(0.0)
    if output >= heater._difference:
        return Pulse(float(heater._pwm), continuous=True)
    heat = pwm._calculate_heat_duration(output, heater._difference)
    if heat < pwm.min_open_time:
        if heater.is_active("heat"):
            return Pulse(0.0)
        if pwm.duty_accumulator_seconds >= pwm.min_open_time:
            return Pulse(heater.effective_min_open_time, pwm.min_open_time)
        previous = pwm._last_accumulator_calc_time
        if previous is not None:
            pwm.set_duty_accumulator(pwm.duty_accumulator_seconds + max(0.0, now - previous) * heat / heater._pwm)
        pwm._last_accumulator_calc_time = now
        # Like legacy PWM, eligibility is tested before adding this tick's credit.
        return Pulse(0.0)
    pwm.reset_duty_accumulator()
    adjusted = pwm.calculate_adjusted_on_time(output, heater._difference)
    if adjusted <= 0:
        return Pulse(0.0)
    off = heater._pwm - adjusted
    if 0 < off < heater._min_closed_time:
        adjusted = min(adjusted * heater._min_closed_time / off, heater._pwm - heater._min_closed_time)
    return Pulse(max(heater.effective_min_open_time, adjusted - pwm.get_close_command_offset()))
