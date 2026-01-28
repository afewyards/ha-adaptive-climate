"""Cycle analysis functions and classes for Adaptive Thermostat.

This module provides tools for analyzing heating cycle performance metrics
including overshoot detection, undershoot calculation, oscillation counting,
and settling time analysis.
"""

from collections import deque
from datetime import datetime
from enum import Enum
from typing import List, Optional, Tuple, Deque
import logging

_LOGGER = logging.getLogger(__name__)


class InterruptionType(Enum):
    """Types of cycle interruptions."""

    SETPOINT_MAJOR = "setpoint_major"  # >0.5°C change with device inactive
    SETPOINT_MINOR = "setpoint_minor"  # ≤0.5°C change or device active
    MODE_CHANGE = "mode_change"         # HVAC mode changed (heat→off, cool→heat, etc.)
    CONTACT_SENSOR = "contact_sensor"   # Window/door opened
    TIMEOUT = "timeout"                 # Settling timeout reached
    EXTERNAL = "external"               # Other external interruption


class InterruptionClassifier:
    """Classifies and handles cycle interruptions with consistent logic."""

    # Classification thresholds
    SETPOINT_MAJOR_THRESHOLD = 0.5  # °C - setpoint changes above this are "major"
    CONTACT_GRACE_PERIOD = 300      # seconds - grace period for contact sensor

    @staticmethod
    def classify_setpoint_change(
        old_temp: float,
        new_temp: float,
        is_device_active: bool
    ) -> InterruptionType:
        """Classify a setpoint change interruption.

        Args:
            old_temp: Previous target temperature
            new_temp: New target temperature
            is_device_active: Whether heater/cooler is currently active

        Returns:
            InterruptionType.SETPOINT_MAJOR if device inactive and change >0.5°C
            InterruptionType.SETPOINT_MINOR if device active or change ≤0.5°C
        """
        delta = abs(new_temp - old_temp)

        # If device is active, classify as minor (continue tracking)
        if is_device_active:
            return InterruptionType.SETPOINT_MINOR

        # If device inactive, check magnitude
        if delta > InterruptionClassifier.SETPOINT_MAJOR_THRESHOLD:
            return InterruptionType.SETPOINT_MAJOR
        else:
            return InterruptionType.SETPOINT_MINOR

    @staticmethod
    def classify_mode_change(
        old_mode: str,
        new_mode: str,
        current_cycle_state: str
    ) -> Optional[InterruptionType]:
        """Classify a mode change interruption.

        Args:
            old_mode: Previous HVAC mode (heat, cool, off, etc.)
            new_mode: New HVAC mode
            current_cycle_state: Current cycle state (heating, cooling, settling)

        Returns:
            InterruptionType.MODE_CHANGE if mode change is incompatible
            None if mode change is compatible (no interruption)
        """
        # Map cycle states to compatible modes
        compatible_modes = {
            "heating": ["heat", "auto"],
            "cooling": ["cool", "auto"],
            "settling": ["heat", "cool", "auto"],  # Can settle from any active mode
        }

        # Check if new mode is compatible with current cycle state
        if current_cycle_state in compatible_modes:
            if new_mode not in compatible_modes[current_cycle_state]:
                return InterruptionType.MODE_CHANGE

        return None  # Compatible mode change, no interruption

    @staticmethod
    def classify_contact_sensor(
        contact_open_duration: float
    ) -> Optional[InterruptionType]:
        """Classify a contact sensor interruption.

        Args:
            contact_open_duration: Duration contact has been open in seconds

        Returns:
            InterruptionType.CONTACT_SENSOR if duration exceeds grace period
            None if within grace period (no interruption)
        """
        if contact_open_duration > InterruptionClassifier.CONTACT_GRACE_PERIOD:
            return InterruptionType.CONTACT_SENSOR
        return None  # Within grace period, no interruption


class PhaseAwareOvershootTracker:
    """
    Track temperature phases (rise vs settling) for accurate overshoot detection.

    Overshoot should only be measured in the settling phase, which begins after
    the temperature first crosses the setpoint. This prevents false overshoot
    readings during the rise phase.

    Time-window-based peak tracking: Only temperature readings within a specified
    time window after heater stops are considered for peak detection. This prevents
    late peaks caused by external factors (solar gain, occupancy) from being
    incorrectly attributed to overshoot.
    """

    # Phase constants
    PHASE_RISE = "rise"
    PHASE_SETTLING = "settling"

    def __init__(self, setpoint: float, tolerance: float = 0.05, peak_tracking_window_minutes: int = 45):
        """
        Initialize the phase-aware overshoot tracker.

        Args:
            setpoint: Target temperature in degrees C
            tolerance: Small tolerance band for detecting setpoint crossing (default 0.05C)
            peak_tracking_window_minutes: Time window after heater stops to track peaks (default 45 min)
        """
        self._setpoint = setpoint
        self._tolerance = tolerance
        self._peak_tracking_window_minutes = peak_tracking_window_minutes
        self._phase = self.PHASE_RISE
        self._setpoint_crossed = False
        self._crossing_timestamp: Optional[datetime] = None
        self._max_settling_temp: Optional[float] = None
        self._settling_temps: Deque[Tuple[datetime, float]] = deque(maxlen=1500)
        self._heater_stop_time: Optional[datetime] = None
        self._peak_window_closed = False

    @property
    def setpoint(self) -> float:
        """Get the current setpoint."""
        return self._setpoint

    @property
    def phase(self) -> str:
        """Get the current phase (rise or settling)."""
        return self._phase

    @property
    def setpoint_crossed(self) -> bool:
        """Check if the setpoint has been crossed."""
        return self._setpoint_crossed

    @property
    def crossing_timestamp(self) -> Optional[datetime]:
        """Get the timestamp when setpoint was first crossed."""
        return self._crossing_timestamp

    def reset(self, new_setpoint: Optional[float] = None) -> None:
        """
        Reset tracking state. Call when setpoint changes.

        Args:
            new_setpoint: Optional new setpoint value. If None, keeps current setpoint.
        """
        if new_setpoint is not None:
            self._setpoint = new_setpoint
        self._phase = self.PHASE_RISE
        self._setpoint_crossed = False
        self._crossing_timestamp = None
        self._max_settling_temp = None
        self._settling_temps.clear()
        self._heater_stop_time = None
        self._peak_window_closed = False
        _LOGGER.debug(f"Overshoot tracker reset, setpoint: {self._setpoint}°C")

    def on_heater_stopped(self, timestamp: datetime) -> None:
        """
        Mark when the heater stopped to begin peak tracking window.

        Args:
            timestamp: Time when heater was turned off
        """
        self._heater_stop_time = timestamp
        self._peak_window_closed = False
        _LOGGER.debug(
            f"Heater stopped at {timestamp}, starting {self._peak_tracking_window_minutes}-minute peak tracking window"
        )

    def update(self, timestamp: datetime, temperature: float) -> None:
        """
        Update tracker with a new temperature reading.

        Args:
            timestamp: Time of the reading
            temperature: Current temperature in degrees C
        """
        # Check for setpoint crossing (rise phase -> settling phase)
        if self._phase == self.PHASE_RISE:
            # Temperature has crossed or reached setpoint (with tolerance)
            if temperature >= self._setpoint - self._tolerance:
                self._phase = self.PHASE_SETTLING
                self._setpoint_crossed = True
                self._crossing_timestamp = timestamp
                _LOGGER.debug(
                    f"Setpoint crossed at {timestamp}, temp={temperature:.2f}°C, "
                    f"setpoint={self._setpoint:.2f}°C - entering settling phase"
                )

        # Track maximum temperature in settling phase
        if self._phase == self.PHASE_SETTLING:
            self._settling_temps.append((timestamp, temperature))

            # Only track peak if within time window after heater stopped
            if self._heater_stop_time is not None and not self._peak_window_closed:
                # Check if we're within the tracking window
                elapsed_minutes = (timestamp - self._heater_stop_time).total_seconds() / 60

                if elapsed_minutes <= self._peak_tracking_window_minutes:
                    # Within window - update peak
                    if self._max_settling_temp is None or temperature > self._max_settling_temp:
                        self._max_settling_temp = temperature
                        _LOGGER.debug(
                            f"Peak updated to {temperature:.2f}°C at {timestamp} "
                            f"({elapsed_minutes:.1f} min after heater stopped)"
                        )
                else:
                    # Window expired - close it
                    if not self._peak_window_closed:
                        self._peak_window_closed = True
                        _LOGGER.debug(
                            f"Peak tracking window closed at {timestamp} "
                            f"({elapsed_minutes:.1f} min after heater stopped), "
                            f"final peak: {self._max_settling_temp:.2f}°C"
                        )
            elif self._heater_stop_time is None:
                # Heater never stopped (still heating) - track peak normally
                if self._max_settling_temp is None or temperature > self._max_settling_temp:
                    self._max_settling_temp = temperature

    def get_overshoot(self) -> Optional[float]:
        """
        Calculate overshoot based on settling phase data.

        Returns:
            Overshoot in degrees C (positive values only), or None if:
            - Setpoint was never crossed (still in rise phase)
            - No settling phase data available
        """
        # No overshoot if setpoint was never reached
        if not self._setpoint_crossed:
            _LOGGER.debug("No overshoot: setpoint was never crossed")
            return None

        if self._max_settling_temp is None:
            return None

        overshoot = self._max_settling_temp - self._setpoint
        return max(0.0, overshoot)

    def get_settling_temps(self) -> List[Tuple[datetime, float]]:
        """
        Get all temperature readings from the settling phase.

        Returns:
            List of (timestamp, temperature) tuples from settling phase
        """
        return list(self._settling_temps)


class CycleMetrics:
    """Container for heating cycle performance metrics."""

    def __init__(
        self,
        overshoot: Optional[float] = None,
        undershoot: Optional[float] = None,
        settling_time: Optional[float] = None,
        oscillations: int = 0,
        rise_time: Optional[float] = None,
        disturbances: Optional[List[str]] = None,
        interruption_history: Optional[List[Tuple[datetime, str]]] = None,
        heater_cycles: int = 0,
        outdoor_temp_avg: Optional[float] = None,
        integral_at_tolerance_entry: Optional[float] = None,
        integral_at_setpoint_cross: Optional[float] = None,
        decay_contribution: Optional[float] = None,
        was_clamped: bool = False,
        end_temp: Optional[float] = None,
        settling_mae: Optional[float] = None,
        inter_cycle_drift: Optional[float] = None,
        dead_time: Optional[float] = None,
        mode: Optional[str] = None,
    ):
        """
        Initialize cycle metrics.

        Args:
            overshoot: Maximum overshoot in °C
            undershoot: Maximum undershoot in °C
            settling_time: Settling time in minutes
            oscillations: Number of temperature oscillations around target
            rise_time: Time to reach target from start in minutes
            disturbances: List of detected disturbance types (e.g., "solar_gain", "wind_loss")
            interruption_history: List of (timestamp, interruption_type) tuples for debugging
            heater_cycles: Number of heater on/off transitions (informational only)
            outdoor_temp_avg: Average outdoor temperature during cycle in °C
            integral_at_tolerance_entry: PID integral value when temp enters tolerance band
            integral_at_setpoint_cross: PID integral value when temp crosses setpoint
            decay_contribution: Integral contribution from settling/decay period
            was_clamped: Whether PID output was clamped during this cycle
            end_temp: Final temperature at cycle end in °C
            settling_mae: Mean absolute error during settling phase in °C
            inter_cycle_drift: Temperature drift between cycles in °C
            dead_time: Transport delay from heater to sensor in minutes
            mode: HVAC mode during cycle ("heating", "cooling", or None for backwards compatibility)
        """
        self.overshoot = overshoot
        self.undershoot = undershoot
        self.settling_time = settling_time
        self.oscillations = oscillations
        self.rise_time = rise_time
        self.disturbances = disturbances or []
        self.interruption_history = interruption_history or []
        self.heater_cycles = heater_cycles
        self.outdoor_temp_avg = outdoor_temp_avg
        self.integral_at_tolerance_entry = integral_at_tolerance_entry
        self.integral_at_setpoint_cross = integral_at_setpoint_cross
        self.decay_contribution = decay_contribution
        self.was_clamped = was_clamped
        self.end_temp = end_temp
        self.settling_mae = settling_mae
        self.inter_cycle_drift = inter_cycle_drift
        self.dead_time = dead_time
        self.mode = mode

    @property
    def is_disturbed(self) -> bool:
        """Check if this cycle had any disturbances.

        Returns:
            True if disturbances were detected, False otherwise
        """
        return len(self.disturbances) > 0

    @property
    def was_interrupted(self) -> bool:
        """Check if this cycle had any interruptions.

        Returns:
            True if interruptions were recorded, False otherwise
        """
        return len(self.interruption_history) > 0


def calculate_overshoot(
    temperature_history: List[Tuple[datetime, float]],
    target_temp: float,
    phase_aware: bool = True
) -> Optional[float]:
    """
    Calculate maximum overshoot beyond target temperature.

    Args:
        temperature_history: List of (timestamp, temperature) tuples
        target_temp: Target temperature in °C
        phase_aware: If True, only calculate overshoot from settling phase
                    (after setpoint is first crossed). Default True.

    Returns:
        Overshoot in °C (positive values only), or None if:
        - No data provided
        - phase_aware=True and setpoint was never reached
    """
    if not temperature_history:
        return None

    if not phase_aware:
        # Legacy behavior: max temp minus setpoint
        max_temp = max(temp for _, temp in temperature_history)
        overshoot = max_temp - target_temp
        return max(0.0, overshoot)

    # Phase-aware calculation: only consider temps after setpoint crossing
    tracker = PhaseAwareOvershootTracker(target_temp)

    for timestamp, temp in temperature_history:
        tracker.update(timestamp, temp)

    return tracker.get_overshoot()


def calculate_undershoot(
    temperature_history: List[Tuple[datetime, float]], target_temp: float
) -> Optional[float]:
    """
    Calculate maximum undershoot below target temperature.

    Args:
        temperature_history: List of (timestamp, temperature) tuples
        target_temp: Target temperature in °C

    Returns:
        Undershoot in °C (positive values only), or None if no data
    """
    if not temperature_history:
        return None

    min_temp = min(temp for _, temp in temperature_history)
    undershoot = target_temp - min_temp

    return max(0.0, undershoot)


def count_oscillations(
    temperature_history: List[Tuple[datetime, float]],
    target_temp: float,
    threshold: float = 0.1,
) -> int:
    """
    Count number of temperature oscillations around target temperature.

    This function counts temperature crossings of the setpoint, NOT heater on/off cycles.
    In PWM mode, the heater may cycle on/off frequently (expected behavior), but this
    function only counts actual temperature oscillations around the target.

    Args:
        temperature_history: List of (timestamp, temperature) tuples
        target_temp: Target temperature in °C
        threshold: Hysteresis threshold in °C to avoid counting noise

    Returns:
        Number of temperature oscillations (crossings of target), not heater cycles
    """
    if len(temperature_history) < 2:
        return 0

    # Track state: None, 'above', or 'below'
    state = None
    crossings = 0

    for _, temp in temperature_history:
        # Determine if above or below target (with threshold)
        if temp > target_temp + threshold:
            new_state = "above"
        elif temp < target_temp - threshold:
            new_state = "below"
        else:
            # Within threshold band, maintain current state
            new_state = state

        # Check for state change (crossing)
        if state is not None and new_state != state and new_state is not None:
            crossings += 1

        if new_state is not None:
            state = new_state

    return crossings


def calculate_settling_time(
    temperature_history: List[Tuple[datetime, float]],
    target_temp: float,
    tolerance: float = 0.2,
    reference_time: Optional[datetime] = None,
) -> Optional[float]:
    """
    Calculate time required for temperature to settle within tolerance band.

    Args:
        temperature_history: List of (timestamp, temperature) tuples
        target_temp: Target temperature in °C
        tolerance: Tolerance band in °C (±)
        reference_time: Optional reference time to calculate settling from.
                       If provided, settling time is measured from this time.
                       If None, uses first sample timestamp (legacy behavior).
                       If before first sample, uses first sample timestamp.

    Returns:
        Settling time in minutes from reference_time (or first sample), or None if never settles
    """
    if len(temperature_history) < 2:
        return None

    # Determine start time for settling calculation
    if reference_time is not None:
        # Use reference_time, but clamp to first sample if reference is earlier
        start_time = max(reference_time, temperature_history[0][0])
    else:
        # Legacy behavior: use first sample timestamp
        start_time = temperature_history[0][0]

    settle_index = None

    # Find first entry into tolerance band that persists, starting from start_time
    for i, (timestamp, temp) in enumerate(temperature_history):
        # Skip samples before start_time
        if timestamp < start_time:
            continue

        within_tolerance = abs(temp - target_temp) <= tolerance

        if within_tolerance:
            # Check if it stays within tolerance
            # Need at least 3 more samples or until the end
            remaining = temperature_history[i:]
            if len(remaining) >= 3:
                # Check if next samples stay within tolerance
                stays_settled = all(
                    abs(t - target_temp) <= tolerance for _, t in remaining[:3]
                )
                if stays_settled:
                    settle_index = i
                    break
            elif len(remaining) > 0:
                # At end of history, check if all remaining stay within tolerance
                stays_settled = all(
                    abs(t - target_temp) <= tolerance for _, t in remaining
                )
                if stays_settled:
                    settle_index = i
                    break

    if settle_index is None:
        return None

    settle_time = temperature_history[settle_index][0]
    settling_minutes = (settle_time - start_time).total_seconds() / 60

    return settling_minutes


def calculate_rise_time(
    temperature_history: List[Tuple[datetime, float]],
    start_temp: float,
    target_temp: float,
    threshold: float = 0.05,
) -> Optional[float]:
    """
    Calculate time required for temperature to rise from start to target.

    Rise time measures how quickly the heating system brings the temperature
    from the initial value to the target setpoint. This is useful for
    evaluating system responsiveness.

    Args:
        temperature_history: List of (timestamp, temperature) tuples
        start_temp: Starting temperature in °C
        target_temp: Target temperature in °C
        threshold: Tolerance for detecting target (default 0.05°C)

    Returns:
        Rise time in minutes from first reading to reaching target,
        or None if:
        - Insufficient data (< 2 samples)
        - Target temperature never reached
        - Already at target (start_temp >= target_temp - threshold)

    Example:
        >>> history = [
        ...     (datetime(2024, 1, 1, 10, 0), 18.0),
        ...     (datetime(2024, 1, 1, 10, 15), 19.5),
        ...     (datetime(2024, 1, 1, 10, 30), 21.0),
        ... ]
        >>> calculate_rise_time(history, 18.0, 21.0)
        30.0
    """
    if len(temperature_history) < 2:
        return None

    # If already at or above target, no rise time needed
    if start_temp >= target_temp - threshold:
        return None

    start_time = temperature_history[0][0]

    # Find first temperature reading that reaches target
    for timestamp, temp in temperature_history:
        if temp >= target_temp - threshold:
            rise_minutes = (timestamp - start_time).total_seconds() / 60
            return rise_minutes

    # Target never reached
    return None


def calculate_settling_mae(
    temperature_history: List[Tuple[datetime, float]],
    target_temp: float,
    settling_start_time: Optional[datetime] = None,
) -> Optional[float]:
    """Calculate Mean Absolute Error during settling phase.

    Args:
        temperature_history: List of (timestamp, temperature) tuples
        target_temp: The target temperature
        settling_start_time: When settling phase started (heater turned off)

    Returns:
        MAE during settling phase, or None if no settling data
    """
    if not temperature_history or settling_start_time is None:
        return None

    # Filter to temps after settling_start_time
    settling_temps = [
        temp for timestamp, temp in temperature_history
        if timestamp >= settling_start_time
    ]

    if not settling_temps:
        return None

    # Calculate mean absolute error from target
    errors = [abs(temp - target_temp) for temp in settling_temps]
    return sum(errors) / len(errors)
