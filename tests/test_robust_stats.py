"""Tests for robust statistics functions."""

import pytest
from custom_components.adaptive_thermostat.adaptive.robust_stats import (
    calculate_median,
    calculate_mad,
    detect_outliers_modified_zscore,
    robust_average,
)


class TestCalculateMedian:
    """Tests for median calculation."""

    def test_median_odd_count(self):
        """Test median with odd number of values."""
        values = [1.0, 3.0, 5.0, 7.0, 9.0]
        assert calculate_median(values) == 5.0

    def test_median_even_count(self):
        """Test median with even number of values."""
        values = [1.0, 3.0, 5.0, 7.0]
        assert calculate_median(values) == 4.0  # (3 + 5) / 2

    def test_median_single_value(self):
        """Test median with single value."""
        values = [42.0]
        assert calculate_median(values) == 42.0

    def test_median_unsorted_values(self):
        """Test median with unsorted values."""
        values = [9.0, 1.0, 5.0, 3.0, 7.0]
        assert calculate_median(values) == 5.0

    def test_median_empty_list(self):
        """Test median with empty list raises ValueError."""
        with pytest.raises(ValueError):
            calculate_median([])


class TestCalculateMAD:
    """Tests for Median Absolute Deviation calculation."""

    def test_mad_basic(self):
        """Test MAD calculation with basic values."""
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        median = calculate_median(values)  # 3.0
        mad = calculate_mad(values, median)
        # Deviations: [2, 1, 0, 1, 2], median = 1.0
        assert mad == 1.0

    def test_mad_no_median_provided(self):
        """Test MAD calculates median when not provided."""
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        mad = calculate_mad(values)
        assert mad == 1.0

    def test_mad_all_identical(self):
        """Test MAD with all identical values."""
        values = [5.0, 5.0, 5.0, 5.0]
        mad = calculate_mad(values)
        assert mad == 0.0

    def test_mad_with_outlier(self):
        """Test MAD is robust to outliers."""
        values = [1.0, 2.0, 3.0, 4.0, 100.0]  # 100 is outlier
        median = 3.0
        mad = calculate_mad(values, median)
        # Deviations: [2, 1, 0, 1, 97], sorted: [0, 1, 1, 2, 97], median = 1.0 (robust!)
        assert mad == 1.0

    def test_mad_empty_list(self):
        """Test MAD with empty list raises ValueError."""
        with pytest.raises(ValueError):
            calculate_mad([])


class TestDetectOutliersModifiedZScore:
    """Tests for outlier detection using modified Z-score."""

    def test_no_outliers_normal_distribution(self):
        """Test no outliers detected in normal distribution."""
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        outlier_indices, zscores = detect_outliers_modified_zscore(values)
        assert outlier_indices == []
        assert len(zscores) == 5
        assert all(z < 3.5 for z in zscores)

    def test_single_outlier_detected(self):
        """Test single outlier is detected."""
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 100.0]  # 100 is outlier
        outlier_indices, zscores = detect_outliers_modified_zscore(values, threshold=3.5)
        assert 5 in outlier_indices  # Index 5 (value 100) is outlier
        assert len(outlier_indices) == 1

    def test_multiple_outliers_detected(self):
        """Test multiple outliers detected."""
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 100.0, 200.0]
        outlier_indices, zscores = detect_outliers_modified_zscore(values, threshold=3.5)
        assert 5 in outlier_indices  # 100
        assert 6 in outlier_indices  # 200
        assert len(outlier_indices) == 2

    def test_all_identical_no_outliers(self):
        """Test all identical values have no outliers."""
        values = [5.0, 5.0, 5.0, 5.0]
        outlier_indices, zscores = detect_outliers_modified_zscore(values)
        assert outlier_indices == []
        assert all(z == 0.0 for z in zscores)

    def test_insufficient_data(self):
        """Test insufficient data (< 3 values) returns no outliers."""
        values = [1.0, 2.0]
        outlier_indices, zscores = detect_outliers_modified_zscore(values)
        assert outlier_indices == []
        assert zscores == [0.0, 0.0]

    def test_custom_threshold(self):
        """Test custom threshold affects detection."""
        values = [1.0, 2.0, 3.0, 4.0, 10.0]
        # Strict threshold (lower) detects more outliers
        outliers_strict, _ = detect_outliers_modified_zscore(values, threshold=2.0)
        # Relaxed threshold (higher) detects fewer outliers
        outliers_relaxed, _ = detect_outliers_modified_zscore(values, threshold=5.0)
        assert len(outliers_strict) >= len(outliers_relaxed)

    def test_empty_list(self):
        """Test empty list raises ValueError."""
        with pytest.raises(ValueError):
            detect_outliers_modified_zscore([])


class TestRobustAverage:
    """Tests for robust average calculation."""

    def test_robust_average_no_outliers(self):
        """Test robust average with no outliers returns median."""
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        avg, outliers = robust_average(values)
        assert avg == 3.0  # Median
        assert outliers == []

    def test_robust_average_ignores_outliers(self):
        """Test robust average removes outliers (sunny day scenario)."""
        # 5 normal cycles + 1 outlier (solar gain)
        values = [0.3, 0.4, 0.35, 0.38, 0.42, 2.5]  # 2.5 is outlier
        avg, outliers = robust_average(values)
        # Should remove outlier and return median of [0.3, 0.35, 0.38, 0.4, 0.42]
        assert 5 in outliers  # Index 5 is outlier
        assert len(outliers) == 1
        # Median of remaining values should be around 0.38
        assert 0.35 <= avg <= 0.42

    def test_robust_average_max_outlier_fraction(self):
        """Test max 30% outliers can be removed."""
        # 10 values, max 3 outliers (30%)
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 100.0, 200.0, 300.0]
        avg, outliers = robust_average(values, max_outlier_fraction=0.3)
        assert len(outliers) <= 3

    def test_robust_average_min_valid_count(self):
        """Test minimum 4 valid values required after removal."""
        # Only 5 values total, if we remove 2, we have 3 left (< 4 min)
        values = [1.0, 2.0, 3.0, 100.0, 200.0]
        avg, outliers = robust_average(values, min_valid_count=4)
        # Max 30% = 1 outlier allowed, so remove the highest Z-score (200)
        # This leaves 4 values which meets the min requirement
        assert len(outliers) <= 1  # At most 1 outlier removed (30% of 5)

    def test_robust_average_insufficient_data(self):
        """Test insufficient data returns median without outlier detection."""
        values = [1.0, 2.0, 3.0]
        avg, outliers = robust_average(values, min_valid_count=4)
        assert avg == 2.0  # Median
        assert outliers == []

    def test_robust_average_empty_list(self):
        """Test empty list raises ValueError."""
        with pytest.raises(ValueError):
            robust_average([])

    def test_robust_average_sunny_day_scenario(self):
        """Test realistic sunny day scenario with 1 outlier in 6 cycles."""
        # 6 cycles: 5 normal (0.2-0.5°C overshoot) + 1 solar gain (3.0°C)
        overshoot_values = [0.3, 0.4, 0.25, 0.35, 0.45, 3.0]
        avg, outliers = robust_average(overshoot_values)
        # Should detect index 5 (3.0) as outlier
        assert 5 in outliers
        assert len(outliers) == 1
        # Average should be ~0.35, not influenced by 3.0
        assert 0.25 <= avg <= 0.45

    def test_robust_average_custom_threshold(self):
        """Test custom outlier threshold."""
        values = [1.0, 2.0, 3.0, 4.0, 10.0]
        # Strict threshold detects more outliers
        avg_strict, outliers_strict = robust_average(
            values, outlier_threshold=2.0
        )
        # Relaxed threshold detects fewer outliers
        avg_relaxed, outliers_relaxed = robust_average(
            values, outlier_threshold=5.0
        )
        assert len(outliers_strict) >= len(outliers_relaxed)


def test_robust_stats_module_exists():
    """Marker test to verify robust_stats module exists and functions are importable."""
    assert callable(calculate_median)
    assert callable(calculate_mad)
    assert callable(detect_outliers_modified_zscore)
    assert callable(robust_average)
