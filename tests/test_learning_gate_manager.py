"""Tests for LearningGateManager class."""

import pytest
from unittest.mock import Mock

from custom_components.adaptive_climate.managers.learning_gate import (
    LearningGateManager,
)
from custom_components.adaptive_climate.const import HeatingType


class TestLearningGateManager:
    """Tests for LearningGateManager class."""

    @pytest.fixture
    def mock_night_setback_controller(self):
        """Create mock night setback controller."""
        mock = Mock()
        mock.in_learning_grace_period = False
        return mock

    @pytest.fixture
    def mock_contact_sensor_handler(self):
        """Create mock contact sensor handler."""
        mock = Mock()
        mock.is_any_contact_open.return_value = False
        return mock

    @pytest.fixture
    def mock_humidity_detector(self):
        """Create mock humidity detector."""
        mock = Mock()
        mock.should_pause.return_value = False
        return mock

    @pytest.fixture
    def mock_adaptive_learner(self):
        """Create mock adaptive learner."""
        mock = Mock()
        mock.get_cycle_count.return_value = 0
        mock.get_convergence_confidence.return_value = 0.0
        return mock

    @pytest.fixture
    def get_adaptive_learner(self, mock_adaptive_learner):
        """Create callable that returns adaptive learner."""
        return lambda: mock_adaptive_learner

    def test_returns_none_when_night_setback_not_enabled(
        self,
        mock_contact_sensor_handler,
        mock_humidity_detector,
        get_adaptive_learner,
    ):
        """Test returns None when night setback controller is None."""
        manager = LearningGateManager(
            night_setback_controller=None,
            contact_sensor_handler=mock_contact_sensor_handler,
            humidity_detector=mock_humidity_detector,
            get_adaptive_learner=get_adaptive_learner,
            heating_type=HeatingType.CONVECTOR,
        )

        result = manager.get_allowed_delta()
        assert result is None

    def test_returns_zero_when_contact_sensor_open(
        self,
        mock_night_setback_controller,
        mock_contact_sensor_handler,
        mock_humidity_detector,
        get_adaptive_learner,
    ):
        """Test returns 0.0 when contact sensor is open (fully suppressed)."""
        mock_contact_sensor_handler.is_any_contact_open.return_value = True

        manager = LearningGateManager(
            night_setback_controller=mock_night_setback_controller,
            contact_sensor_handler=mock_contact_sensor_handler,
            humidity_detector=mock_humidity_detector,
            get_adaptive_learner=get_adaptive_learner,
            heating_type=HeatingType.CONVECTOR,
        )

        result = manager.get_allowed_delta()
        assert result == 0.0

    def test_returns_zero_when_humidity_spike_detected(
        self,
        mock_night_setback_controller,
        mock_contact_sensor_handler,
        mock_humidity_detector,
        get_adaptive_learner,
    ):
        """Test returns 0.0 when humidity spike detected (fully suppressed)."""
        mock_humidity_detector.should_pause.return_value = True

        manager = LearningGateManager(
            night_setback_controller=mock_night_setback_controller,
            contact_sensor_handler=mock_contact_sensor_handler,
            humidity_detector=mock_humidity_detector,
            get_adaptive_learner=get_adaptive_learner,
            heating_type=HeatingType.CONVECTOR,
        )

        result = manager.get_allowed_delta()
        assert result == 0.0

    def test_returns_zero_when_learning_grace_period(
        self,
        mock_night_setback_controller,
        mock_contact_sensor_handler,
        mock_humidity_detector,
        get_adaptive_learner,
    ):
        """Test returns 0.0 when in learning grace period (fully suppressed)."""
        mock_night_setback_controller.in_learning_grace_period = True

        manager = LearningGateManager(
            night_setback_controller=mock_night_setback_controller,
            contact_sensor_handler=mock_contact_sensor_handler,
            humidity_detector=mock_humidity_detector,
            get_adaptive_learner=get_adaptive_learner,
            heating_type=HeatingType.CONVECTOR,
        )

        result = manager.get_allowed_delta()
        assert result == 0.0

    def test_graduated_delta_idle_status(
        self,
        mock_night_setback_controller,
        mock_contact_sensor_handler,
        mock_humidity_detector,
        mock_adaptive_learner,
        get_adaptive_learner,
    ):
        """Test returns 0.0 for idle status (< 3 cycles)."""
        mock_adaptive_learner.get_cycle_count.return_value = 2
        mock_adaptive_learner.get_convergence_confidence.return_value = 0.0

        manager = LearningGateManager(
            night_setback_controller=mock_night_setback_controller,
            contact_sensor_handler=mock_contact_sensor_handler,
            humidity_detector=mock_humidity_detector,
            get_adaptive_learner=get_adaptive_learner,
            heating_type=HeatingType.CONVECTOR,
        )

        result = manager.get_allowed_delta()
        assert result == 0.0

    def test_graduated_delta_collecting_status(
        self,
        mock_night_setback_controller,
        mock_contact_sensor_handler,
        mock_humidity_detector,
        mock_adaptive_learner,
        get_adaptive_learner,
    ):
        """Test returns 0.5 for collecting status (>= 3 cycles, below tier 1)."""
        mock_adaptive_learner.get_cycle_count.return_value = 3
        # Convergence confidence below tier 1 (40% * 1.0 = 0.40)
        mock_adaptive_learner.get_convergence_confidence.return_value = 0.35

        manager = LearningGateManager(
            night_setback_controller=mock_night_setback_controller,
            contact_sensor_handler=mock_contact_sensor_handler,
            humidity_detector=mock_humidity_detector,
            get_adaptive_learner=get_adaptive_learner,
            heating_type=HeatingType.CONVECTOR,
        )

        result = manager.get_allowed_delta()
        assert result == 0.5

    def test_graduated_delta_stable_status(
        self,
        mock_night_setback_controller,
        mock_contact_sensor_handler,
        mock_humidity_detector,
        mock_adaptive_learner,
        get_adaptive_learner,
    ):
        """Test returns 1.0 for stable status (>= tier 1, < tier 2)."""
        mock_adaptive_learner.get_cycle_count.return_value = 10
        # Convergence confidence between tier 1 (0.40) and tier 2 (0.70)
        mock_adaptive_learner.get_convergence_confidence.return_value = 0.55

        manager = LearningGateManager(
            night_setback_controller=mock_night_setback_controller,
            contact_sensor_handler=mock_contact_sensor_handler,
            humidity_detector=mock_humidity_detector,
            get_adaptive_learner=get_adaptive_learner,
            heating_type=HeatingType.CONVECTOR,
        )

        result = manager.get_allowed_delta()
        assert result == 1.0

    def test_graduated_delta_tuned_status(
        self,
        mock_night_setback_controller,
        mock_contact_sensor_handler,
        mock_humidity_detector,
        mock_adaptive_learner,
        get_adaptive_learner,
    ):
        """Test returns None for tuned status (>= tier 2, < tier 3)."""
        mock_adaptive_learner.get_cycle_count.return_value = 15
        # Convergence confidence between tier 2 (0.70) and tier 3 (0.95)
        mock_adaptive_learner.get_convergence_confidence.return_value = 0.80

        manager = LearningGateManager(
            night_setback_controller=mock_night_setback_controller,
            contact_sensor_handler=mock_contact_sensor_handler,
            humidity_detector=mock_humidity_detector,
            get_adaptive_learner=get_adaptive_learner,
            heating_type=HeatingType.CONVECTOR,
        )

        result = manager.get_allowed_delta()
        assert result is None

    def test_graduated_delta_optimized_status(
        self,
        mock_night_setback_controller,
        mock_contact_sensor_handler,
        mock_humidity_detector,
        mock_adaptive_learner,
        get_adaptive_learner,
    ):
        """Test returns None for optimized status (>= tier 3)."""
        mock_adaptive_learner.get_cycle_count.return_value = 20
        # Convergence confidence >= tier 3 (0.95)
        mock_adaptive_learner.get_convergence_confidence.return_value = 0.95

        manager = LearningGateManager(
            night_setback_controller=mock_night_setback_controller,
            contact_sensor_handler=mock_contact_sensor_handler,
            humidity_detector=mock_humidity_detector,
            get_adaptive_learner=get_adaptive_learner,
            heating_type=HeatingType.CONVECTOR,
        )

        result = manager.get_allowed_delta()
        assert result is None

    def test_heating_type_scaling_floor_hydronic(
        self,
        mock_night_setback_controller,
        mock_contact_sensor_handler,
        mock_humidity_detector,
        mock_adaptive_learner,
        get_adaptive_learner,
    ):
        """Test confidence thresholds are scaled for floor_hydronic (0.8x)."""
        mock_adaptive_learner.get_cycle_count.return_value = 10
        # Convergence confidence = 0.35 (between scaled tier 1 0.32 and tier 2 0.56)
        mock_adaptive_learner.get_convergence_confidence.return_value = 0.35

        manager = LearningGateManager(
            night_setback_controller=mock_night_setback_controller,
            contact_sensor_handler=mock_contact_sensor_handler,
            humidity_detector=mock_humidity_detector,
            get_adaptive_learner=get_adaptive_learner,
            heating_type=HeatingType.FLOOR_HYDRONIC,  # 0.8x scaling
        )

        # Should be stable (1.0) because 0.35 > (0.40 * 0.8 = 0.32)
        result = manager.get_allowed_delta()
        assert result == 1.0

    def test_heating_type_scaling_forced_air(
        self,
        mock_night_setback_controller,
        mock_contact_sensor_handler,
        mock_humidity_detector,
        mock_adaptive_learner,
        get_adaptive_learner,
    ):
        """Test confidence thresholds are scaled for forced_air (1.1x)."""
        mock_adaptive_learner.get_cycle_count.return_value = 10
        # Convergence confidence = 0.45 (between scaled tier 1 0.44 and tier 2 0.77)
        mock_adaptive_learner.get_convergence_confidence.return_value = 0.45

        manager = LearningGateManager(
            night_setback_controller=mock_night_setback_controller,
            contact_sensor_handler=mock_contact_sensor_handler,
            humidity_detector=mock_humidity_detector,
            get_adaptive_learner=get_adaptive_learner,
            heating_type=HeatingType.FORCED_AIR,  # 1.1x scaling
        )

        # Should be stable (1.0) because 0.45 > (0.40 * 1.1 = 0.44)
        result = manager.get_allowed_delta()
        assert result == 1.0

    def test_is_learning_suppressed_true_when_contact_open(
        self,
        mock_night_setback_controller,
        mock_contact_sensor_handler,
        mock_humidity_detector,
        get_adaptive_learner,
    ):
        """Test is_learning_suppressed returns True when contact sensor open."""
        mock_contact_sensor_handler.is_any_contact_open.return_value = True

        manager = LearningGateManager(
            night_setback_controller=mock_night_setback_controller,
            contact_sensor_handler=mock_contact_sensor_handler,
            humidity_detector=mock_humidity_detector,
            get_adaptive_learner=get_adaptive_learner,
            heating_type=HeatingType.CONVECTOR,
        )

        assert manager.is_learning_suppressed() is True

    def test_is_learning_suppressed_true_when_humidity_spike(
        self,
        mock_night_setback_controller,
        mock_contact_sensor_handler,
        mock_humidity_detector,
        get_adaptive_learner,
    ):
        """Test is_learning_suppressed returns True when humidity spike detected."""
        mock_humidity_detector.should_pause.return_value = True

        manager = LearningGateManager(
            night_setback_controller=mock_night_setback_controller,
            contact_sensor_handler=mock_contact_sensor_handler,
            humidity_detector=mock_humidity_detector,
            get_adaptive_learner=get_adaptive_learner,
            heating_type=HeatingType.CONVECTOR,
        )

        assert manager.is_learning_suppressed() is True

    def test_is_learning_suppressed_true_when_grace_period(
        self,
        mock_night_setback_controller,
        mock_contact_sensor_handler,
        mock_humidity_detector,
        get_adaptive_learner,
    ):
        """Test is_learning_suppressed returns True when in learning grace period."""
        mock_night_setback_controller.in_learning_grace_period = True

        manager = LearningGateManager(
            night_setback_controller=mock_night_setback_controller,
            contact_sensor_handler=mock_contact_sensor_handler,
            humidity_detector=mock_humidity_detector,
            get_adaptive_learner=get_adaptive_learner,
            heating_type=HeatingType.CONVECTOR,
        )

        assert manager.is_learning_suppressed() is True

    def test_is_learning_suppressed_false_when_no_suppression(
        self,
        mock_night_setback_controller,
        mock_contact_sensor_handler,
        mock_humidity_detector,
        get_adaptive_learner,
    ):
        """Test is_learning_suppressed returns False when no suppression conditions."""
        manager = LearningGateManager(
            night_setback_controller=mock_night_setback_controller,
            contact_sensor_handler=mock_contact_sensor_handler,
            humidity_detector=mock_humidity_detector,
            get_adaptive_learner=get_adaptive_learner,
            heating_type=HeatingType.CONVECTOR,
        )

        assert manager.is_learning_suppressed() is False

    def test_returns_zero_when_no_adaptive_learner(
        self,
        mock_night_setback_controller,
        mock_contact_sensor_handler,
        mock_humidity_detector,
    ):
        """Test returns 0.0 when adaptive learner is None."""
        manager = LearningGateManager(
            night_setback_controller=mock_night_setback_controller,
            contact_sensor_handler=mock_contact_sensor_handler,
            humidity_detector=mock_humidity_detector,
            get_adaptive_learner=lambda: None,  # Returns None
            heating_type=HeatingType.CONVECTOR,
        )

        result = manager.get_allowed_delta()
        assert result == 0.0

    def test_handles_contact_sensor_handler_none(
        self,
        mock_night_setback_controller,
        mock_humidity_detector,
        mock_adaptive_learner,
        get_adaptive_learner,
    ):
        """Test handles contact_sensor_handler being None."""
        mock_adaptive_learner.get_cycle_count.return_value = 10
        mock_adaptive_learner.get_convergence_confidence.return_value = 0.55

        manager = LearningGateManager(
            night_setback_controller=mock_night_setback_controller,
            contact_sensor_handler=None,  # No contact sensor
            humidity_detector=mock_humidity_detector,
            get_adaptive_learner=get_adaptive_learner,
            heating_type=HeatingType.CONVECTOR,
        )

        # Should work normally and return stable (1.0)
        result = manager.get_allowed_delta()
        assert result == 1.0

    def test_handles_humidity_detector_none(
        self,
        mock_night_setback_controller,
        mock_contact_sensor_handler,
        mock_adaptive_learner,
        get_adaptive_learner,
    ):
        """Test handles humidity_detector being None."""
        mock_adaptive_learner.get_cycle_count.return_value = 10
        mock_adaptive_learner.get_convergence_confidence.return_value = 0.55

        manager = LearningGateManager(
            night_setback_controller=mock_night_setback_controller,
            contact_sensor_handler=mock_contact_sensor_handler,
            humidity_detector=None,  # No humidity detector
            get_adaptive_learner=get_adaptive_learner,
            heating_type=HeatingType.CONVECTOR,
        )

        # Should work normally and return stable (1.0)
        result = manager.get_allowed_delta()
        assert result == 1.0

    def test_handles_exception_from_contact_sensor(
        self,
        mock_night_setback_controller,
        mock_contact_sensor_handler,
        mock_humidity_detector,
        mock_adaptive_learner,
        get_adaptive_learner,
    ):
        """Test gracefully handles exceptions from contact sensor handler."""
        mock_contact_sensor_handler.is_any_contact_open.side_effect = TypeError("test error")
        mock_adaptive_learner.get_cycle_count.return_value = 10
        mock_adaptive_learner.get_convergence_confidence.return_value = 0.55

        manager = LearningGateManager(
            night_setback_controller=mock_night_setback_controller,
            contact_sensor_handler=mock_contact_sensor_handler,
            humidity_detector=mock_humidity_detector,
            get_adaptive_learner=get_adaptive_learner,
            heating_type=HeatingType.CONVECTOR,
        )

        # Should continue and return stable (1.0), ignoring the error
        result = manager.get_allowed_delta()
        assert result == 1.0

    def test_handles_exception_from_humidity_detector(
        self,
        mock_night_setback_controller,
        mock_contact_sensor_handler,
        mock_humidity_detector,
        mock_adaptive_learner,
        get_adaptive_learner,
    ):
        """Test gracefully handles exceptions from humidity detector."""
        mock_humidity_detector.should_pause.side_effect = AttributeError("test error")
        mock_adaptive_learner.get_cycle_count.return_value = 10
        mock_adaptive_learner.get_convergence_confidence.return_value = 0.55

        manager = LearningGateManager(
            night_setback_controller=mock_night_setback_controller,
            contact_sensor_handler=mock_contact_sensor_handler,
            humidity_detector=mock_humidity_detector,
            get_adaptive_learner=get_adaptive_learner,
            heating_type=HeatingType.CONVECTOR,
        )

        # Should continue and return stable (1.0), ignoring the error
        result = manager.get_allowed_delta()
        assert result == 1.0

    def test_tier_3_always_95_percent(
        self,
        mock_night_setback_controller,
        mock_contact_sensor_handler,
        mock_humidity_detector,
        mock_adaptive_learner,
        get_adaptive_learner,
    ):
        """Test tier 3 threshold is always 0.95 (not scaled by heating type)."""
        mock_adaptive_learner.get_cycle_count.return_value = 20

        # Test with floor_hydronic (0.8x scale)
        mock_adaptive_learner.get_convergence_confidence.return_value = 0.94
        manager = LearningGateManager(
            night_setback_controller=mock_night_setback_controller,
            contact_sensor_handler=mock_contact_sensor_handler,
            humidity_detector=mock_humidity_detector,
            get_adaptive_learner=get_adaptive_learner,
            heating_type=HeatingType.FLOOR_HYDRONIC,
        )
        # Should be tuned (None), not optimized, because 0.94 < 0.95
        assert manager.get_allowed_delta() is None

        # Now at exactly 0.95 - should still be optimized (None)
        mock_adaptive_learner.get_convergence_confidence.return_value = 0.95
        assert manager.get_allowed_delta() is None

    def test_priority_of_suppression_checks(
        self,
        mock_night_setback_controller,
        mock_contact_sensor_handler,
        mock_humidity_detector,
        mock_adaptive_learner,
        get_adaptive_learner,
    ):
        """Test suppression checks are evaluated in correct priority order."""
        # Set up all suppression conditions
        mock_night_setback_controller.in_learning_grace_period = True
        mock_contact_sensor_handler.is_any_contact_open.return_value = True
        mock_humidity_detector.should_pause.return_value = True
        mock_adaptive_learner.get_cycle_count.return_value = 10
        mock_adaptive_learner.get_convergence_confidence.return_value = 0.80

        manager = LearningGateManager(
            night_setback_controller=mock_night_setback_controller,
            contact_sensor_handler=mock_contact_sensor_handler,
            humidity_detector=mock_humidity_detector,
            get_adaptive_learner=get_adaptive_learner,
            heating_type=HeatingType.CONVECTOR,
        )

        # Should return 0.0 (suppressed) even though confidence would allow unlimited
        result = manager.get_allowed_delta()
        assert result == 0.0

        # When all suppression cleared, should return unlimited
        mock_night_setback_controller.in_learning_grace_period = False
        mock_contact_sensor_handler.is_any_contact_open.return_value = False
        mock_humidity_detector.should_pause.return_value = False

        result = manager.get_allowed_delta()
        assert result is None
