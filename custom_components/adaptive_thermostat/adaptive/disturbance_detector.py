"""Disturbance detection for filtering invalid learning cycles."""
import logging
from typing import List, Optional
from datetime import datetime

_LOGGER = logging.getLogger(__name__)


class DisturbanceDetector:
    """Detects environmental disturbances that invalidate PID learning cycles.

    Disturbances include:
    - Solar gain: Rapid temperature rise correlated with sun/solar sensor
    - Wind loss: Temperature drop with stable outdoor temp but high wind
    - Outdoor temperature swings: Large outdoor temp changes during cycle
    - Occupancy: Temperature rise without heater activity
    """

    def __init__(self):
        """Initialize the disturbance detector."""
        self._logger = _LOGGER

    def detect_disturbances(
        self,
        temperature_history: List[tuple[datetime, float]],
        heater_active_periods: List[tuple[datetime, datetime]],
        outdoor_temps: Optional[List[tuple[datetime, float]]] = None,
        solar_values: Optional[List[tuple[datetime, float]]] = None,
        wind_speeds: Optional[List[tuple[datetime, float]]] = None,
    ) -> List[str]:
        """Detect all disturbances for a heating cycle.

        Args:
            temperature_history: Indoor temperature readings (timestamp, temp)
            heater_active_periods: List of (start_time, end_time) when heater was on
            outdoor_temps: Outdoor temperature readings (timestamp, temp)
            solar_values: Solar irradiance or illuminance readings (timestamp, value)
            wind_speeds: Wind speed readings (timestamp, speed_m_s)

        Returns:
            List of disturbance type strings (empty if no disturbances detected)
        """
        disturbances = []

        # Check for solar gain
        if solar_values and self._detect_solar_gain(temperature_history, solar_values, heater_active_periods):
            disturbances.append("solar_gain")

        # Check for wind loss
        if outdoor_temps and wind_speeds and self._detect_wind_loss(
            temperature_history, outdoor_temps, wind_speeds, heater_active_periods
        ):
            disturbances.append("wind_loss")

        # Check for outdoor temperature swings
        if outdoor_temps and self._detect_outdoor_temp_swing(outdoor_temps):
            disturbances.append("outdoor_temp_swing")

        # Check for occupancy effects
        if self._detect_occupancy(temperature_history, heater_active_periods):
            disturbances.append("occupancy")

        return disturbances

    def _detect_solar_gain(
        self,
        temperature_history: List[tuple[datetime, float]],
        solar_values: List[tuple[datetime, float]],
        heater_active_periods: List[tuple[datetime, datetime]],
    ) -> bool:
        """Detect solar gain: temperature rise correlated with solar increase.

        Args:
            temperature_history: Indoor temperature readings
            solar_values: Solar sensor readings
            heater_active_periods: When heater was active

        Returns:
            True if solar gain detected
        """
        if len(temperature_history) < 3 or len(solar_values) < 3:
            return False

        # Calculate temperature rise rate (°C per hour)
        first_temp = temperature_history[0][1]
        last_temp = temperature_history[-1][1]
        duration_hours = (temperature_history[-1][0] - temperature_history[0][0]).total_seconds() / 3600.0

        if duration_hours < 0.1:
            return False

        temp_rise_rate = (last_temp - first_temp) / duration_hours

        # Calculate solar increase
        first_solar = solar_values[0][1]
        last_solar = solar_values[-1][1]
        solar_increase = last_solar - first_solar

        # Detect solar gain if:
        # 1. Temperature rising faster than 0.5°C/h during settling (heater off)
        # 2. Solar values increased significantly (>100 W/m² or >1000 lux)
        if temp_rise_rate > 0.5 and solar_increase > 100:
            # Check if this occurred during settling phase (heater off)
            if heater_active_periods:
                last_heater_stop = heater_active_periods[-1][1]
                settling_temps = [
                    (ts, temp) for ts, temp in temperature_history
                    if ts > last_heater_stop
                ]
                if len(settling_temps) >= 2:
                    settling_rise = settling_temps[-1][1] - settling_temps[0][1]
                    if settling_rise > 0.3:  # >0.3°C rise during settling
                        self._logger.info(
                            "Solar gain detected: temp rose %.2f°C during settling "
                            "with solar increase %.1f",
                            settling_rise, solar_increase
                        )
                        return True

        return False

    def _detect_wind_loss(
        self,
        temperature_history: List[tuple[datetime, float]],
        outdoor_temps: List[tuple[datetime, float]],
        wind_speeds: List[tuple[datetime, float]],
        heater_active_periods: List[tuple[datetime, datetime]],
    ) -> bool:
        """Detect wind-driven heat loss: indoor temp drops with stable outdoor but high wind.

        Args:
            temperature_history: Indoor temperature readings
            outdoor_temps: Outdoor temperature readings
            wind_speeds: Wind speed readings
            heater_active_periods: When heater was active

        Returns:
            True if wind loss detected
        """
        if len(temperature_history) < 3 or len(outdoor_temps) < 2 or len(wind_speeds) < 2:
            return False

        # Check outdoor temperature stability (< 2°C change)
        outdoor_range = max(t for _, t in outdoor_temps) - min(t for _, t in outdoor_temps)
        if outdoor_range > 2.0:
            return False  # Outdoor temp not stable

        # Check for high wind (>5 m/s average)
        avg_wind = sum(speed for _, speed in wind_speeds) / len(wind_speeds)
        if avg_wind < 5.0:
            return False  # Wind not high enough

        # Check for indoor temperature drop during heater-off period
        if heater_active_periods:
            last_heater_stop = heater_active_periods[-1][1]
            settling_temps = [
                (ts, temp) for ts, temp in temperature_history
                if ts > last_heater_stop
            ]
            if len(settling_temps) >= 2:
                duration_hours = (settling_temps[-1][0] - settling_temps[0][0]).total_seconds() / 3600.0
                if duration_hours < 0.1:
                    return False

                settling_drop = settling_temps[0][1] - settling_temps[-1][1]
                # Calculate drop rate to distinguish from normal cooling
                drop_rate = settling_drop / duration_hours if duration_hours > 0 else 0

                # Wind loss should cause faster-than-normal cooling (>1.0°C/hour)
                if settling_drop > 0.5 and drop_rate > 1.0:
                    self._logger.info(
                        "Wind loss detected: temp dropped %.2f°C (%.2f°C/h) during settling "
                        "with avg wind %.1f m/s",
                        settling_drop, drop_rate, avg_wind
                    )
                    return True

        return False

    def _detect_outdoor_temp_swing(
        self,
        outdoor_temps: List[tuple[datetime, float]],
    ) -> bool:
        """Detect large outdoor temperature swing during cycle.

        Args:
            outdoor_temps: Outdoor temperature readings

        Returns:
            True if outdoor swing >5°C detected
        """
        if len(outdoor_temps) < 2:
            return False

        outdoor_range = max(t for _, t in outdoor_temps) - min(t for _, t in outdoor_temps)

        if outdoor_range > 5.0:
            self._logger.info(
                "Outdoor temperature swing detected: %.2f°C change during cycle",
                outdoor_range
            )
            return True

        return False

    def _detect_occupancy(
        self,
        temperature_history: List[tuple[datetime, float]],
        heater_active_periods: List[tuple[datetime, datetime]],
    ) -> bool:
        """Detect occupancy-driven temperature rise without heater.

        Args:
            temperature_history: Indoor temperature readings
            heater_active_periods: When heater was active

        Returns:
            True if occupancy effect detected
        """
        if len(temperature_history) < 3 or not heater_active_periods:
            return False

        # Check for temperature rise during heater-off period
        last_heater_stop = heater_active_periods[-1][1]
        settling_temps = [
            (ts, temp) for ts, temp in temperature_history
            if ts > last_heater_stop
        ]

        if len(settling_temps) >= 3:  # Need at least 3 samples to avoid false positives
            duration_hours = (settling_temps[-1][0] - settling_temps[0][0]).total_seconds() / 3600.0
            if duration_hours < 0.25:  # Need at least 15 minutes
                return False

            temp_rise = settling_temps[-1][1] - settling_temps[0][1]
            rise_rate = temp_rise / duration_hours

            # Detect if temperature rising >0.5°C/h during settling (heater off)
            # This suggests internal heat gains (people, cooking, electronics)
            # Raised threshold from 0.3 to 0.5 to reduce false positives
            if rise_rate > 0.5:
                self._logger.info(
                    "Occupancy detected: temp rose %.2f°C (%.2f°C/h) during heater-off period",
                    temp_rise, rise_rate
                )
                return True

        return False
