"""Tests for comfort degradation detection."""

from unittest.mock import AsyncMock, MagicMock
import pytest

from custom_components.adaptive_climate.managers.comfort_degradation import (
    ComfortDegradationDetector,
    COMFORT_DEGRADATION_THRESHOLD,
    COMFORT_DROP_THRESHOLD,
    MIN_SAMPLES_FOR_DETECTION,
)


@pytest.fixture
def detector():
    return ComfortDegradationDetector(
        zone_id="office",
        zone_name="Office",
    )


def test_triggers_below_absolute_threshold(detector):
    """Score < 65 fires degradation."""
    for _ in range(50):
        detector.record_score(80.0)
    assert detector.check_degradation(60.0) is True


def test_triggers_on_large_drop(detector):
    """Drop >15 points from rolling avg fires."""
    for _ in range(50):
        detector.record_score(85.0)
    assert detector.check_degradation(68.0) is True


def test_no_trigger_normal_fluctuation(detector):
    """Small drops are ignored."""
    for _ in range(50):
        detector.record_score(80.0)
    assert detector.check_degradation(75.0) is False


def test_no_trigger_insufficient_data(detector):
    """No trigger when not enough samples for reliable average."""
    detector.record_score(80.0)
    assert detector.check_degradation(50.0) is False


def test_build_context_contact_pauses(detector):
    """Context includes contact pause count."""
    ctx = detector.build_context(contact_pauses=5, humidity_pauses=0)
    assert "5 contact" in ctx


def test_build_context_humidity_pauses(detector):
    """Context includes humidity pause count."""
    ctx = detector.build_context(contact_pauses=0, humidity_pauses=3)
    assert "3 humidity" in ctx


def test_build_context_no_causes(detector):
    """Context is empty string when no known causes."""
    ctx = detector.build_context(contact_pauses=0, humidity_pauses=0)
    assert ctx == ""


def test_build_context_both_causes(detector):
    """Context includes both when both present."""
    ctx = detector.build_context(contact_pauses=3, humidity_pauses=5)
    assert "3 contact" in ctx
    assert "5 humidity" in ctx


def test_rolling_average(detector):
    """Rolling average computed correctly."""
    for _ in range(10):
        detector.record_score(80.0)
    for _ in range(10):
        detector.record_score(90.0)
    avg = detector.rolling_average
    assert avg is not None
    assert 84.0 <= avg <= 86.0


def test_rolling_average_insufficient_data(detector):
    """Rolling average is None with insufficient data."""
    detector.record_score(80.0)
    assert detector.rolling_average is None


def test_max_samples_respected():
    """Buffer doesn't grow beyond max_samples."""
    detector = ComfortDegradationDetector(
        zone_id="test",
        zone_name="Test",
        max_samples=10,
    )
    for i in range(20):
        detector.record_score(float(i))
    assert len(detector._samples) == 10


def test_threshold_constants():
    """Verify threshold constants have expected values."""
    assert COMFORT_DEGRADATION_THRESHOLD == 65
    assert COMFORT_DROP_THRESHOLD == 15
    assert MIN_SAMPLES_FOR_DETECTION == 12
