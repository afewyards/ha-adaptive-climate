"""Robust statistical functions for outlier detection and filtering.

This module provides robust statistics methods that are less sensitive to outliers
than traditional mean/stddev approaches. Used by AdaptiveLearner to filter invalid
cycles (e.g., sunny days causing solar gain) from PID tuning calculations.
"""

from typing import List, Tuple
import logging

_LOGGER = logging.getLogger(__name__)


def calculate_median(values: List[float]) -> float:
    """Calculate median of a list of values.

    Args:
        values: List of numeric values

    Returns:
        Median value

    Raises:
        ValueError: If values list is empty
    """
    if not values:
        raise ValueError("Cannot calculate median of empty list")

    sorted_values = sorted(values)
    n = len(sorted_values)

    if n % 2 == 0:
        # Even number of values - average the two middle values
        return (sorted_values[n // 2 - 1] + sorted_values[n // 2]) / 2.0
    else:
        # Odd number of values - return the middle value
        return sorted_values[n // 2]


def calculate_mad(values: List[float], median: float = None) -> float:
    """Calculate Median Absolute Deviation (MAD).

    MAD is a robust measure of variability that is less sensitive to outliers
    than standard deviation.

    Formula: MAD = median(|values - median(values)|)

    Args:
        values: List of numeric values
        median: Pre-calculated median (optional, will calculate if None)

    Returns:
        MAD value

    Raises:
        ValueError: If values list is empty
    """
    if not values:
        raise ValueError("Cannot calculate MAD of empty list")

    if median is None:
        median = calculate_median(values)

    # Calculate absolute deviations from median
    absolute_deviations = [abs(value - median) for value in values]

    # Return median of absolute deviations
    return calculate_median(absolute_deviations)


def detect_outliers_modified_zscore(
    values: List[float],
    threshold: float = 3.5
) -> Tuple[List[int], List[float]]:
    """Detect outliers using modified Z-score with MAD.

    The modified Z-score uses MAD instead of standard deviation, making it
    more robust to outliers. Values with modified Z-score > threshold are
    considered outliers.

    Modified Z-score formula:
        M_i = 0.6745 * (x_i - median) / MAD

    The constant 0.6745 is the 75th percentile of the standard normal distribution,
    making the modified Z-score roughly comparable to the traditional Z-score.

    Args:
        values: List of numeric values
        threshold: Modified Z-score threshold (default 3.5)

    Returns:
        Tuple of (outlier_indices, modified_zscores)

    Raises:
        ValueError: If values list is empty or has insufficient data
    """
    if not values:
        raise ValueError("Cannot detect outliers in empty list")

    if len(values) < 3:
        # Need at least 3 values for meaningful outlier detection
        return ([], [0.0] * len(values))

    median = calculate_median(values)
    mad = calculate_mad(values, median)

    if mad == 0:
        # All values are identical - no outliers
        return ([], [0.0] * len(values))

    # Calculate modified Z-scores
    modified_zscores = []
    outlier_indices = []

    for i, value in enumerate(values):
        modified_z = 0.6745 * abs(value - median) / mad
        modified_zscores.append(modified_z)

        if modified_z > threshold:
            outlier_indices.append(i)

    return (outlier_indices, modified_zscores)


def robust_average(
    values: List[float],
    max_outlier_fraction: float = 0.3,
    min_valid_count: int = 4,
    outlier_threshold: float = 3.5
) -> Tuple[float, List[int]]:
    """Calculate robust average using median with outlier removal.

    This function uses the modified Z-score method to detect and remove outliers,
    then returns the median of the remaining values. It includes safety checks to
    ensure we don't remove too many values.

    Args:
        values: List of numeric values
        max_outlier_fraction: Maximum fraction of values that can be removed (default 0.3)
        min_valid_count: Minimum number of valid values required after removal (default 4)
        outlier_threshold: Modified Z-score threshold for outlier detection (default 3.5)

    Returns:
        Tuple of (robust_average, outlier_indices)

    Raises:
        ValueError: If insufficient valid values after outlier removal
    """
    if not values:
        raise ValueError("Cannot calculate robust average of empty list")

    if len(values) < min_valid_count:
        # Not enough values - just return median without outlier detection
        _LOGGER.debug(
            "Insufficient values for outlier detection (%d < %d), using median",
            len(values),
            min_valid_count
        )
        return (calculate_median(values), [])

    # Detect outliers
    outlier_indices, modified_zscores = detect_outliers_modified_zscore(
        values, outlier_threshold
    )

    # Safety check: ensure we don't remove too many values
    max_outliers = int(len(values) * max_outlier_fraction)
    if len(outlier_indices) > max_outliers:
        _LOGGER.warning(
            "Too many outliers detected (%d > %d), using highest Z-score subset",
            len(outlier_indices),
            max_outliers
        )
        # Sort outliers by Z-score and only keep the most extreme ones
        outlier_data = [
            (idx, modified_zscores[idx])
            for idx in outlier_indices
        ]
        outlier_data.sort(key=lambda x: x[1], reverse=True)
        outlier_indices = [idx for idx, _ in outlier_data[:max_outliers]]

    # Ensure we have enough valid values after removal
    valid_count = len(values) - len(outlier_indices)
    if valid_count < min_valid_count:
        _LOGGER.warning(
            "Insufficient valid values after outlier removal (%d < %d), using all values",
            valid_count,
            min_valid_count
        )
        outlier_indices = []

    # Calculate median of valid (non-outlier) values
    valid_values = [
        value for i, value in enumerate(values)
        if i not in outlier_indices
    ]

    if outlier_indices:
        _LOGGER.debug(
            "Removed %d outliers from %d values (%.1f%%)",
            len(outlier_indices),
            len(values),
            100.0 * len(outlier_indices) / len(values)
        )

    return (calculate_median(valid_values), outlier_indices)
