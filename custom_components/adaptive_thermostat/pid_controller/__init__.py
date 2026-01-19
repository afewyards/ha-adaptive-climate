import logging
import math
from time import time

_LOGGER = logging.getLogger(__name__)

# Minimum time delta (seconds) required for integral/derivative updates
# Rationale: 5s allows 5s sensor intervals, provides 5:1 SNR for 0.1°C noise, 102x safety margin vs 0.049s spike
# Balances responsiveness with noise rejection for fast sensor update rates
MIN_DT_FOR_DERIVATIVE = 5.0


# Based on Arduino PID Library
# See https://github.com/br3ttb/Arduino-PID-Library
class PID:
    error: float

    def __init__(self, kp, ki, kd, ke=0, ke_wind=0.02, out_min=float('-inf'), out_max=float('+inf'),
                 sampling_period=0, cold_tolerance=0.3, hot_tolerance=0.3, derivative_filter_alpha=0.15,
                 outdoor_temp_lag_tau=4.0):
        """A proportional-integral-derivative controller using P-on-M (proportional-on-measurement).
            :param kp: Proportional coefficient.
            :type kp: float
            :param ki: Integral coefficient in units of %/(°C·hour).
            :type ki: float
            :param kd: Derivative coefficient in units of %/(°C/hour).
            :type kd: float
            :param ke: Outdoor temperature compensation coefficient.
            :type ke: float
            :param ke_wind: Wind speed compensation coefficient (per m/s).
            :type ke_wind: float
            :param out_min: Lower output limit.
            :type out_min: float
            :param out_max: Upper output limit.
            :type out_max: float
            :param sampling_period: time period between two PID calculations in seconds
            :type sampling_period: float
            :param cold_tolerance: Temperature below setpoint to trigger heating when PID mode is OFF.
            :type cold_tolerance: float
            :param hot_tolerance: Temperature above setpoint to trigger cooling when PID mode is OFF.
            :type hot_tolerance: float
            :param derivative_filter_alpha: EMA filter alpha for derivative term (0.0-1.0).
                                           Lower values = more filtering. 1.0 = no filter.
            :type derivative_filter_alpha: float
            :param outdoor_temp_lag_tau: Time constant in hours for outdoor temperature EMA filter.
                                        Larger values = slower response to outdoor temp changes.
            :type outdoor_temp_lag_tau: float
        """
        if kp is None:
            raise ValueError('kp must be specified')
        if ki is None:
            raise ValueError('ki must be specified')
        if kd is None:
            raise ValueError('kd must be specified')
        if out_min >= out_max:
            raise ValueError('out_min must be less than out_max')

        self._Kp = kp
        self._Ki = ki
        self._Kd = kd
        self._Ke = ke
        self._Ke_wind = ke_wind
        self._out_min = out_min
        self._out_max = out_max
        self._proportional = 0.0
        self._integral = 0.0
        self._derivative = 0.0
        self._derivative_filtered = 0.0  # EMA-filtered derivative value
        self._derivative_filter_alpha = derivative_filter_alpha
        self._last_set_point = 0
        self._set_point = 0
        self._input = None
        self._input_time = None
        self._last_input = None
        self._last_input_time = None
        self._error = 0
        self._input_diff = 0
        self._dext = 0
        self._dt = 0
        self._last_output = 0
        self._output = 0
        self._proportional = 0
        self._derivative = 0
        self._external = 0
        self._feedforward = 0.0
        self._mode = 'AUTO'
        self._sampling_period = sampling_period
        self._cold_tolerance = cold_tolerance
        self._hot_tolerance = hot_tolerance
        self._outdoor_temp_lag_tau = outdoor_temp_lag_tau  # Time constant in hours
        self._outdoor_temp_lagged = None  # Will be initialized on first outdoor temp reading
        self._last_output_before_off = None  # Stores output before switching to OFF mode for bumpless transfer
        self._wind_speed = 0.0  # Current wind speed in m/s (defaults to 0 if unavailable)

    @property
    def mode(self):
        return self._mode

    @mode.setter
    def mode(self, mode):
        assert mode.upper() in ['AUTO', 'OFF']
        new_mode = mode.upper()
        # Store output before switching to OFF for bumpless transfer
        if self._mode == 'AUTO' and new_mode == 'OFF':
            self._last_output_before_off = self._output
        # Clear samples when switching from OFF to AUTO to prevent stale data
        if self._mode == 'OFF' and new_mode == 'AUTO':
            self.clear_samples()
        self._mode = new_mode

    @property
    def out_max(self):
        return self._out_max

    @out_max.setter
    def out_max(self, out_max):
        self._out_max = out_max

    @property
    def out_min(self):
        return self._out_min

    @out_min.setter
    def out_min(self, out_min):
        self._out_min = out_min

    @property
    def sampling_period(self):
        return self._sampling_period

    @property
    def error(self):
        return self._error

    @property
    def proportional(self):
        return self._proportional

    @property
    def integral(self):
        return self._integral

    @integral.setter
    def integral(self, i):
        assert isinstance(i, float), "Integral should be a float"
        self._integral = i

    @property
    def derivative(self):
        return self._derivative

    @property
    def external(self):
        return self._external

    @property
    def feedforward(self):
        """Get the current feedforward term value."""
        return self._feedforward

    def set_feedforward(self, ff):
        """Set the feedforward term for thermal coupling compensation.

        The feedforward term is subtracted from the PID output to reduce
        heating demand when neighboring zones are providing thermal coupling.

        Args:
            ff: Feedforward value in % (0-100). Positive values reduce output.
        """
        self._feedforward = ff

    @property
    def dt(self):
        return self._dt

    @property
    def outdoor_temp_lagged(self):
        """Get the lagged (EMA-filtered) outdoor temperature."""
        return self._outdoor_temp_lagged

    @outdoor_temp_lagged.setter
    def outdoor_temp_lagged(self, value):
        """Set the lagged outdoor temperature (for state restoration)."""
        self._outdoor_temp_lagged = value

    @property
    def outdoor_temp_lag_tau(self):
        """Get the outdoor temperature lag time constant in hours."""
        return self._outdoor_temp_lag_tau

    @property
    def has_transfer_state(self):
        """Check if bumpless transfer state is available."""
        return self._last_output_before_off is not None

    def prepare_bumpless_transfer(self):
        """Prepare for bumpless transfer by setting integral to maintain continuity.

        This method calculates the integral term needed to maintain the same output
        as before the mode was switched to OFF, preventing sudden output jumps when
        switching back to AUTO mode.

        Should be called on first calc() after OFF→AUTO transition, and only if:
        - Setpoint hasn't changed significantly (< 2°C)
        - Error is not too large (< 2°C)
        """
        if not self.has_transfer_state:
            return

        # Skip transfer if setpoint changed significantly or error is large
        if abs(self._set_point - self._last_set_point) > 2.0:
            _LOGGER.debug("Bumpless transfer skipped: setpoint changed by %.2f°C",
                         abs(self._set_point - self._last_set_point))
            self._last_output_before_off = None
            return

        if abs(self._error) > 2.0:
            _LOGGER.debug("Bumpless transfer skipped: error too large (%.2f°C)", abs(self._error))
            self._last_output_before_off = None
            return

        # Calculate required integral to match last output
        # Output = P + I + D + E - F, so I = Output - P - E + F (D=0 on first calc after OFF)
        required_integral = self._last_output_before_off - self._proportional - self._external + self._feedforward

        # Clamp to valid range accounting for external and feedforward terms
        required_integral = max(
            min(required_integral, self._out_max - self._external - self._feedforward),
            self._out_min - self._external - self._feedforward
        )

        self._integral = required_integral
        _LOGGER.debug("Bumpless transfer: set integral to %.2f%% to maintain output %.2f%%",
                     self._integral, self._last_output_before_off)

        # Clear the transfer state after use
        self._last_output_before_off = None

    def set_pid_param(self, kp=None, ki=None, kd=None, ke=None):
        """Set PID parameters."""
        if kp is not None and isinstance(kp, (int, float)):
            self._Kp = kp
        if ki is not None and isinstance(ki, (int, float)):
            self._Ki = ki
        if kd is not None and isinstance(kd, (int, float)):
            self._Kd = kd
        if ke is not None and isinstance(ke, (int, float)):
            self._Ke = ke

    def clear_samples(self):
        """Clear the samples values and timestamp to restart PID from clean state after
        a switch off of the thermostat"""
        self._input = None
        self._input_time = None
        self._last_input = None
        self._last_input_time = None
        self._derivative_filtered = 0.0
        self._outdoor_temp_lagged = None
        
    def calc(self, input_val, set_point, input_time=None, last_input_time=None, ext_temp=None, wind_speed=None):
        """Adjusts and holds the given setpoint.

        Args:
            input_val (float): The input value.
            set_point (float): The target value.
            input_time (float): The timestamp in seconds of the input value to compute dt
            last_input_time (float): The timestamp in seconds of the previous input value to
            compute dt
            ext_temp (float): The outdoor temperature value.
            wind_speed (float): The wind speed in m/s (optional).

        Returns:
            A value between `out_min` and `out_max`.
        """
        # Validate inputs for NaN and Inf values
        if math.isnan(input_val) or math.isinf(input_val):
            _LOGGER.warning("Invalid input_val received: %s. Returning cached output.", input_val)
            return self._output, False
        if math.isnan(set_point) or math.isinf(set_point):
            _LOGGER.warning("Invalid set_point received: %s. Returning cached output.", set_point)
            return self._output, False
        if ext_temp is not None and (math.isnan(ext_temp) or math.isinf(ext_temp)):
            _LOGGER.warning("Invalid ext_temp received: %s. Returning cached output.", ext_temp)
            return self._output, False

        if self._sampling_period != 0 and self._last_input_time is not None and \
                time() - self._last_input_time < self._sampling_period:
            return self._output, False  # If last sample is too young, keep last output value

        self._last_input = self._input
        if self._sampling_period == 0:
            self._last_input_time = last_input_time
        else:
            self._last_input_time = self._input_time
        self._last_output = self._output

        # Refresh with actual values
        self._input = input_val
        if self._sampling_period == 0:
            if input_time is None:
                _LOGGER.warning(
                    "PID controller in event-driven mode (sampling_period=0) but no "
                    "input_time provided. Using current time as fallback."
                )
                self._input_time = time()
            else:
                self._input_time = input_time
        else:
            self._input_time = time()
        self._last_set_point = self._set_point
        self._set_point = set_point

        if self.mode == 'OFF':  # If PID is off, simply switch between min and max output
            if input_val <= set_point - self._cold_tolerance:
                self._output = self._out_max
                _LOGGER.debug("PID is off and input lower than set point: heater ON")
                return self._output, True
            elif input_val >= set_point + self._hot_tolerance:
                self._output = self._out_min
                _LOGGER.debug("PID is off and input higher than set point: heater OFF")
                return self._output, True
            else:
                return self._output, False

        # Compute all the working error variables
        self._error = set_point - input_val
        if self._last_input is not None:
            self._input_diff = self._input - self._last_input
        else:
            self._input_diff = 0
        if self._last_input_time is not None:
            self._dt = self._input_time - self._last_input_time
        else:
            self._dt = 0

        # Apply EMA filter to outdoor temperature to model thermal lag
        if ext_temp is not None:
            if self._outdoor_temp_lagged is None:
                # Initialize with first reading (no warmup needed)
                self._outdoor_temp_lagged = ext_temp
            else:
                # Apply EMA filter: alpha = dt / (tau * 3600)
                # tau is in hours, dt is in seconds, so convert tau to seconds
                alpha = self._dt / (self._outdoor_temp_lag_tau * 3600.0)
                # Clamp alpha to [0, 1] for numerical stability
                alpha = max(0.0, min(1.0, alpha))
                self._outdoor_temp_lagged = alpha * ext_temp + (1.0 - alpha) * self._outdoor_temp_lagged

            self._dext = set_point - self._outdoor_temp_lagged
        else:
            self._dext = 0

        # Update wind speed (treat None as 0)
        if wind_speed is not None:
            self._wind_speed = wind_speed
        else:
            self._wind_speed = 0.0

        # Compensate losses due to external temperature and wind
        # Formula: external = Ke * dext + Ke_wind * wind_speed * dext
        # Wind increases heat loss proportionally to temperature difference
        self._external = self._Ke * self._dext + self._Ke_wind * self._wind_speed * self._dext

        # Calculate proportional term using P-on-M (proportional-on-measurement)
        # P-on-M: proportional term based on negative derivative of measurement
        # This eliminates output spikes when setpoint changes
        if self._last_input is not None and self._dt != 0:
            self._proportional = -self._Kp * self._input_diff
        else:
            self._proportional = 0.0

        # Apply bumpless transfer if transitioning from OFF to AUTO
        # This must be done after P and E terms are calculated but before integral updates
        if self.has_transfer_state:
            self.prepare_bumpless_transfer()

        # Apply timing threshold to prevent derivative spikes from rapid non-sensor calls
        # Only update integral and derivative if dt >= MIN_DT_FOR_DERIVATIVE
        if self._dt >= MIN_DT_FOR_DERIVATIVE:
            # Back-calculation anti-windup: prevent windup when saturated AND error drives further saturation
            # Allow wind-down when error opposes saturation (e.g., saturated high but error negative)
            # P-on-M: integrate continuously, no reset on setpoint change
            # Directional saturation check: only block integration when saturated AND error drives further saturation
            saturated_high = self._last_output >= self._out_max and self._error > 0
            saturated_low = self._last_output <= self._out_min and self._error < 0
            if not (saturated_high or saturated_low):
                # Convert dt from seconds to hours for dimensional correctness
                # Ki has units of %/(°C·hour), so dt must be in hours
                dt_hours = self._dt / 3600.0
                self._integral += self._Ki * self._error * dt_hours

            # Integral clamping accounts for external and feedforward terms to ensure total output respects bounds
            # Formula: I_max = out_max - E - F, I_min = out_min - E - F
            # This ensures P + I + D + E - F stays within [out_min, out_max]
            # After v0.7.0 Ke reduction (100x), E typically <1%, leaving >99% headroom for integral
            # Feedforward (F) reduces available headroom when thermal coupling is active
            # Note: Clamping always runs (not just when accumulating) to handle feedforward changes
            self._integral = max(
                min(self._integral, self._out_max - self._external - self._feedforward),
                self._out_min - self._external - self._feedforward
            )

            # Calculate derivative
            # Convert dt to hours for dimensional correctness
            # Kd has units of %/(°C/hour), so dt must be in hours
            dt_hours = self._dt / 3600.0
            raw_derivative = -(self._Kd * self._input_diff) / dt_hours

            # Apply EMA filter to reduce sensor noise amplification
            # Formula: filtered = alpha * raw + (1 - alpha) * prev_filtered
            # alpha = 1.0 disables filter (no filtering)
            # alpha = 0.0 gives maximum filtering (derivative becomes constant)
            self._derivative_filtered = (
                self._derivative_filter_alpha * raw_derivative +
                (1.0 - self._derivative_filter_alpha) * self._derivative_filtered
            )
            self._derivative = self._derivative_filtered

        elif self._dt > 0:
            # dt is positive but below threshold - freeze I and D at last values
            # This prevents derivative spikes from rapid non-sensor calls (external sensor, contact sensor, periodic loop)
            # Integral and derivative remain unchanged from previous calculation
            _LOGGER.debug(
                "PID: dt=%.3fs < %.1fs threshold, freeze I=%.2f D=%.2f (rapid non-sensor call)",
                self._dt, MIN_DT_FOR_DERIVATIVE, self._integral, self._derivative_filtered
            )
        else:
            # First call (dt=0) - initialize I and D to zero
            self._integral = 0.0
            self._derivative = 0.0
            self._derivative_filtered = 0.0

        # Compute PID Output
        # Formula: output = P + I + D + E - F
        # Feedforward (F) is subtracted to reduce output when thermal coupling provides heat
        output = self._proportional + self._integral + self._derivative + self._external - self._feedforward
        self._output = max(min(output, self._out_max), self._out_min)
        return self._output, True
