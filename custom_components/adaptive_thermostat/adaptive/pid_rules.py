"""PID rule engine for adaptive thermostat tuning.

This module provides rule-based PID adjustment logic extracted from the AdaptiveLearner.
It includes rule definitions, evaluation, conflict detection, and resolution.
"""

from enum import Enum
from typing import Dict, List, NamedTuple, Optional, Set, Tuple
import logging

from ..const import (
    RULE_PRIORITY_OSCILLATION,
    RULE_PRIORITY_OVERSHOOT,
    RULE_PRIORITY_SLOW_RESPONSE,
    RULE_HYSTERESIS_BAND_PCT,
)

_LOGGER = logging.getLogger(__name__)


def calculate_pearson_correlation(x: List[float], y: List[float]) -> Optional[float]:
    """Calculate Pearson correlation coefficient between two variables.

    Args:
        x: First variable (e.g., rise times)
        y: Second variable (e.g., outdoor temperatures)

    Returns:
        Pearson correlation coefficient in range [-1, 1], or None if:
        - Insufficient data (< 2 samples)
        - Lists have different lengths
        - Standard deviation of either variable is zero
    """
    if len(x) < 2 or len(y) < 2 or len(x) != len(y):
        return None

    n = len(x)

    # Calculate means
    mean_x = sum(x) / n
    mean_y = sum(y) / n

    # Calculate covariance and standard deviations
    covariance = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n)) / n
    std_x = (sum((xi - mean_x) ** 2 for xi in x) / n) ** 0.5
    std_y = (sum((yi - mean_y) ** 2 for yi in y) / n) ** 0.5

    # Avoid division by zero
    if std_x == 0 or std_y == 0:
        return None

    # Pearson correlation: r = cov(X,Y) / (σ_x * σ_y)
    correlation = covariance / (std_x * std_y)

    # Clamp to [-1, 1] to handle floating point errors
    return max(-1.0, min(1.0, correlation))


class PIDRule(Enum):
    """PID adjustment rules with associated priority levels."""

    HIGH_OVERSHOOT = ("high_overshoot", RULE_PRIORITY_OVERSHOOT)
    MODERATE_OVERSHOOT = ("moderate_overshoot", RULE_PRIORITY_OVERSHOOT)
    SLOW_RESPONSE = ("slow_response", RULE_PRIORITY_SLOW_RESPONSE)
    UNDERSHOOT = ("undershoot", RULE_PRIORITY_SLOW_RESPONSE)
    MANY_OSCILLATIONS = ("many_oscillations", RULE_PRIORITY_OSCILLATION)
    SOME_OSCILLATIONS = ("some_oscillations", RULE_PRIORITY_OSCILLATION)
    SLOW_SETTLING = ("slow_settling", RULE_PRIORITY_SLOW_RESPONSE)

    def __init__(self, rule_name: str, priority: int):
        """Initialize rule with name and priority."""
        self.rule_name = rule_name
        self.priority = priority


class PIDRuleResult(NamedTuple):
    """Result of a single PID rule evaluation."""

    rule: PIDRule
    kp_factor: float  # Multiplier for Kp (1.0 = no change)
    ki_factor: float  # Multiplier for Ki (1.0 = no change)
    kd_factor: float  # Multiplier for Kd (1.0 = no change)
    reason: str       # Human-readable reason for adjustment


class RuleStateTracker:
    """Tracks rule activation state with hysteresis to prevent oscillation.

    Hysteresis prevents rules from rapidly activating/deactivating when metrics
    hover near thresholds, which would cause PID parameters to oscillate.

    Example: Overshoot rule with 0.2°C activation threshold and 20% hysteresis:
    - Inactive → Active when overshoot > 0.2°C (activation threshold)
    - Active → Inactive when overshoot < 0.16°C (release threshold = 0.2 * 0.8)
    - Between 0.16-0.2°C: maintains current state (hysteresis band)
    """

    def __init__(self, hysteresis_band_pct: float = RULE_HYSTERESIS_BAND_PCT):
        """Initialize the rule state tracker.

        Args:
            hysteresis_band_pct: Percentage hysteresis band (0.20 = 20%)
        """
        self._hysteresis_band_pct = hysteresis_band_pct
        self._rule_states: Dict[PIDRule, bool] = {}  # True = active, False = inactive

    def update_state(
        self,
        rule: PIDRule,
        metric_value: float,
        activation_threshold: float
    ) -> bool:
        """Update and return the state of a rule with hysteresis logic.

        Args:
            rule: The PID rule to check
            metric_value: Current metric value (e.g., overshoot in °C)
            activation_threshold: Threshold where rule activates

        Returns:
            True if rule is active, False if inactive
        """
        # Calculate release threshold (lower than activation for positive thresholds)
        release_threshold = activation_threshold * (1.0 - self._hysteresis_band_pct)

        # Get current state (default to inactive if never seen before)
        current_state = self._rule_states.get(rule, False)

        # State transition logic with hysteresis
        if not current_state:
            # Currently inactive: activate if metric exceeds activation threshold
            if metric_value > activation_threshold:
                self._rule_states[rule] = True
                _LOGGER.debug(
                    f"Rule '{rule.rule_name}' activated: {metric_value:.3f} > {activation_threshold:.3f}"
                )
                return True
        else:
            # Currently active: deactivate only if metric drops below release threshold
            if metric_value < release_threshold:
                self._rule_states[rule] = False
                _LOGGER.debug(
                    f"Rule '{rule.rule_name}' deactivated: {metric_value:.3f} < {release_threshold:.3f}"
                )
                return False

        # Maintain current state (in hysteresis band)
        return current_state

    def is_active(self, rule: PIDRule) -> bool:
        """Check if a rule is currently active.

        Args:
            rule: The PID rule to check

        Returns:
            True if active, False if inactive or never evaluated
        """
        return self._rule_states.get(rule, False)

    def reset(self):
        """Reset all rule states to inactive."""
        self._rule_states.clear()
        _LOGGER.debug("All rule states reset")


def evaluate_pid_rules(
    avg_overshoot: float,
    avg_undershoot: float,
    avg_oscillations: float,
    avg_rise_time: float,
    avg_settling_time: float,
    recent_rise_times: Optional[List[float]] = None,
    recent_outdoor_temps: Optional[List[float]] = None,
    state_tracker: Optional[RuleStateTracker] = None,
    rule_thresholds: Optional[Dict[str, float]] = None,
    decay_contribution: Optional[float] = None,
    integral_at_tolerance_entry: Optional[float] = None,
) -> List[PIDRuleResult]:
    """
    Evaluate all PID tuning rules against current metrics.

    Args:
        avg_overshoot: Average overshoot in degrees C
        avg_undershoot: Average undershoot in degrees C
        avg_oscillations: Average number of oscillations
        avg_rise_time: Average rise time in minutes
        avg_settling_time: Average settling time in minutes
        recent_rise_times: List of recent rise times for correlation analysis (optional)
        recent_outdoor_temps: List of recent outdoor temps for correlation analysis (optional)
        state_tracker: Optional RuleStateTracker for hysteresis logic (optional, backward compatible)
        rule_thresholds: Optional dict of rule thresholds (optional, uses defaults if not provided).
            Supported keys: moderate_overshoot, high_overshoot, slow_response, slow_settling,
            undershoot, many_oscillations, some_oscillations
        decay_contribution: Integral contribution from settling/decay period (optional)
        integral_at_tolerance_entry: PID integral value when temp enters tolerance band (optional)

    Returns:
        List of applicable rule results (rules that would fire)
    """
    from ..const import MIN_OUTDOOR_TEMP_RANGE, SLOW_RESPONSE_CORRELATION_THRESHOLD

    # Default thresholds for backward compatibility
    if rule_thresholds is None:
        rule_thresholds = {
            'moderate_overshoot': 0.2,
            'high_overshoot': 1.0,
            'slow_response': 60,
            'slow_settling': 90,
            'undershoot': 0.3,
            'many_oscillations': 3,
            'some_oscillations': 1,
        }

    results: List[PIDRuleResult] = []

    # Helper to check if rule should fire with hysteresis
    def should_fire(rule: PIDRule, metric: float, threshold: float) -> bool:
        """Check if rule should fire, using hysteresis if tracker provided."""
        if state_tracker is not None:
            return state_tracker.update_state(rule, metric, threshold)
        else:
            # No hysteresis: simple threshold comparison (backward compatible)
            return metric > threshold

    # Rule 1: High overshoot (>1.0C) - Extreme case
    # Thermal lag is root cause, Kd addresses it. For extreme cases, also reduce Kp.
    if should_fire(PIDRule.HIGH_OVERSHOOT, avg_overshoot, rule_thresholds['high_overshoot']):
        results.append(PIDRuleResult(
            rule=PIDRule.HIGH_OVERSHOOT,
            kp_factor=0.90,  # Reduce Kp by 10% for extreme overshoot
            ki_factor=0.9,
            kd_factor=1.20,  # Increase Kd by 20% to handle thermal lag
            reason=f"Extreme overshoot ({avg_overshoot:.2f}°C, increase Kd+reduce Kp)"
        ))
    # Rule 2: Moderate overshoot (0.2-1.0C) - Increase Kd only
    # Thermal lag is root cause, Kd addresses it without touching Kp
    elif should_fire(PIDRule.MODERATE_OVERSHOOT, avg_overshoot, rule_thresholds['moderate_overshoot']):
        results.append(PIDRuleResult(
            rule=PIDRule.MODERATE_OVERSHOOT,
            kp_factor=1.0,
            ki_factor=1.0,
            kd_factor=1.20,  # Increase Kd by 20% to dampen overshoot
            reason=f"Moderate overshoot ({avg_overshoot:.2f}°C, increase Kd)"
        ))

    # Rule 3: Slow response (rise time >60 min)
    # Diagnose root cause: Ki (outdoor correlation) vs Kp (no correlation)
    if should_fire(PIDRule.SLOW_RESPONSE, avg_rise_time, rule_thresholds['slow_response']):
        # Default to old behavior (increase Kp)
        kp_factor = 1.10
        ki_factor = 1.0
        reason = f"Slow rise time ({avg_rise_time:.1f} min)"

        # Try to diagnose root cause if outdoor data available
        if (recent_rise_times is not None and recent_outdoor_temps is not None and
            len(recent_rise_times) >= 3 and len(recent_outdoor_temps) >= 3):

            # Check if outdoor temps have sufficient variation for correlation
            outdoor_range = max(recent_outdoor_temps) - min(recent_outdoor_temps)

            if outdoor_range >= MIN_OUTDOOR_TEMP_RANGE:
                # Calculate correlation between rise time and outdoor temp
                correlation = calculate_pearson_correlation(recent_rise_times, recent_outdoor_temps)

                if correlation is not None:
                    # Negative correlation means slower when colder -> Ki issue
                    # (integral takes too long to accumulate against outdoor losses)
                    if correlation < -SLOW_RESPONSE_CORRELATION_THRESHOLD:
                        # Diagnose LOW_KI: increase Ki up to 30%
                        kp_factor = 1.0
                        ki_factor = 1.30
                        reason = f"Slow rise time ({avg_rise_time:.1f} min, cold correlation r={correlation:.2f}, increase Ki)"
                    # Positive or no correlation means issue is not outdoor-dependent -> Kp issue
                    elif correlation >= -SLOW_RESPONSE_CORRELATION_THRESHOLD:
                        # Diagnose LOW_KP: increase Kp by 10% (default behavior)
                        kp_factor = 1.10
                        ki_factor = 1.0
                        reason = f"Slow rise time ({avg_rise_time:.1f} min, no cold correlation r={correlation:.2f}, increase Kp)"

        results.append(PIDRuleResult(
            rule=PIDRule.SLOW_RESPONSE,
            kp_factor=kp_factor,
            ki_factor=ki_factor,
            kd_factor=1.0,
            reason=reason
        ))

    # Rule 4: Undershoot (>0.3C)
    if should_fire(PIDRule.UNDERSHOOT, avg_undershoot, rule_thresholds['undershoot']):
        increase = min(1.0, avg_undershoot * 2.0)  # Up to 100% increase (doubling)

        # Calculate decay_ratio to scale Ki increase inversely
        decay_ratio = 0.0
        if (decay_contribution is not None and
            integral_at_tolerance_entry is not None and
            integral_at_tolerance_entry != 0.0):
            decay_ratio = min(1.0, max(0.0, decay_contribution / integral_at_tolerance_entry))

        # Scale increase inversely by (1 - decay_ratio)
        # If decay_ratio=0.0 (no decay), get full increase
        # If decay_ratio=1.0 (all integral from decay), get no increase
        scaled_increase = increase * (1.0 - decay_ratio)

        increase_pct = scaled_increase * 100.0
        results.append(PIDRuleResult(
            rule=PIDRule.UNDERSHOOT,
            kp_factor=1.0,
            ki_factor=1.0 + scaled_increase,
            kd_factor=1.0,
            reason=f"Undershoot ({avg_undershoot:.2f}°C, +{increase_pct:.0f}% Ki)"
        ))

    # Rule 5: Many oscillations (>3)
    if should_fire(PIDRule.MANY_OSCILLATIONS, avg_oscillations, rule_thresholds['many_oscillations']):
        results.append(PIDRuleResult(
            rule=PIDRule.MANY_OSCILLATIONS,
            kp_factor=0.90,
            ki_factor=1.0,
            kd_factor=1.20,
            reason=f"Many oscillations ({avg_oscillations:.1f})"
        ))
    # Rule 6: Some oscillations (>1, only if many didn't fire)
    elif should_fire(PIDRule.SOME_OSCILLATIONS, avg_oscillations, rule_thresholds['some_oscillations']):
        results.append(PIDRuleResult(
            rule=PIDRule.SOME_OSCILLATIONS,
            kp_factor=1.0,
            ki_factor=1.0,
            kd_factor=1.10,
            reason=f"Some oscillations ({avg_oscillations:.1f})"
        ))

    # Rule 7: Slow settling (>90 min)
    # Case 3: Sluggish system with low decay -> increase Ki by 10%
    # Case 5: High decay with good result -> no Ki change (Kd only)
    if should_fire(PIDRule.SLOW_SETTLING, avg_settling_time, rule_thresholds['slow_settling']):
        ki_factor = 1.0
        reason = f"Slow settling ({avg_settling_time:.1f} min)"

        # Calculate decay_ratio if decay data available
        decay_ratio = None
        if (decay_contribution is not None and
            integral_at_tolerance_entry is not None and
            integral_at_tolerance_entry != 0.0):
            decay_ratio = min(1.0, max(0.0, decay_contribution / integral_at_tolerance_entry))

        # Case 3: Low decay ratio (<0.2) indicates sluggish system -> increase Ki
        if decay_ratio is not None and decay_ratio < 0.2:
            ki_factor = 1.1
            reason = f"Slow settling ({avg_settling_time:.1f} min, low decay, +10% Ki, +15% Kd)"
        # Case 5: High decay ratio (>0.5) indicates decay is working well -> Kd only
        elif decay_ratio is not None and decay_ratio > 0.5:
            reason = f"Slow settling ({avg_settling_time:.1f} min, high decay, Kd only)"
        # Default: No decay data or moderate decay -> Kd only (original behavior)
        else:
            reason = f"Slow settling ({avg_settling_time:.1f} min, +15% Kd)"

        results.append(PIDRuleResult(
            rule=PIDRule.SLOW_SETTLING,
            kp_factor=1.0,
            ki_factor=ki_factor,
            kd_factor=1.15,
            reason=reason
        ))

    return results


def detect_rule_conflicts(
    rule_results: List[PIDRuleResult]
) -> List[Tuple[PIDRuleResult, PIDRuleResult, str]]:
    """
    Detect conflicts between applicable rules.

    A conflict occurs when two rules adjust the same parameter in opposite directions
    (one increases, one decreases).

    Args:
        rule_results: List of applicable rule results

    Returns:
        List of (rule1, rule2, parameter_name) tuples for each conflict
    """
    conflicts: List[Tuple[PIDRuleResult, PIDRuleResult, str]] = []

    for i, r1 in enumerate(rule_results):
        for r2 in rule_results[i + 1:]:
            # Check Kp conflicts (one increases, one decreases)
            if (r1.kp_factor > 1.0 and r2.kp_factor < 1.0) or \
               (r1.kp_factor < 1.0 and r2.kp_factor > 1.0):
                conflicts.append((r1, r2, "kp"))

            # Check Ki conflicts
            if (r1.ki_factor > 1.0 and r2.ki_factor < 1.0) or \
               (r1.ki_factor < 1.0 and r2.ki_factor > 1.0):
                conflicts.append((r1, r2, "ki"))

            # Check Kd conflicts
            if (r1.kd_factor > 1.0 and r2.kd_factor < 1.0) or \
               (r1.kd_factor < 1.0 and r2.kd_factor > 1.0):
                conflicts.append((r1, r2, "kd"))

    return conflicts


def resolve_rule_conflicts(
    rule_results: List[PIDRuleResult],
    conflicts: List[Tuple[PIDRuleResult, PIDRuleResult, str]]
) -> List[PIDRuleResult]:
    """
    Resolve conflicts by applying higher priority rules.

    For each conflict, the lower priority rule's adjustment for that parameter
    is neutralized (set to 1.0).

    Args:
        rule_results: List of applicable rule results
        conflicts: List of detected conflicts from detect_rule_conflicts()

    Returns:
        Modified list of rule results with conflicts resolved
    """
    # Track which rules have parameters suppressed
    suppressed: Dict[PIDRule, Set[str]] = {}

    for r1, r2, param in conflicts:
        # Determine winner (higher priority)
        winner = r1 if r1.rule.priority >= r2.rule.priority else r2
        loser = r2 if winner == r1 else r1

        _LOGGER.info(
            f"PID rule conflict on {param}: '{winner.rule.rule_name}' "
            f"(priority {winner.rule.priority}) takes precedence over "
            f"'{loser.rule.rule_name}' (priority {loser.rule.priority})"
        )

        # Mark loser's parameter as suppressed
        if loser.rule not in suppressed:
            suppressed[loser.rule] = set()
        suppressed[loser.rule].add(param)

    # Build resolved results
    resolved: List[PIDRuleResult] = []
    for result in rule_results:
        if result.rule in suppressed:
            # Create new result with suppressed parameters neutralized
            suppressed_params = suppressed[result.rule]
            resolved.append(PIDRuleResult(
                rule=result.rule,
                kp_factor=1.0 if "kp" in suppressed_params else result.kp_factor,
                ki_factor=1.0 if "ki" in suppressed_params else result.ki_factor,
                kd_factor=1.0 if "kd" in suppressed_params else result.kd_factor,
                reason=result.reason + " (partially suppressed)"
            ))
        else:
            resolved.append(result)

    return resolved
