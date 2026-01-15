"""PID rule engine for adaptive thermostat tuning.

This module provides rule-based PID adjustment logic extracted from the AdaptiveLearner.
It includes rule definitions, evaluation, conflict detection, and resolution.
"""

from enum import Enum
from typing import Dict, List, NamedTuple, Set, Tuple
import logging

from ..const import (
    RULE_PRIORITY_OSCILLATION,
    RULE_PRIORITY_OVERSHOOT,
    RULE_PRIORITY_SLOW_RESPONSE,
)

_LOGGER = logging.getLogger(__name__)


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


def evaluate_pid_rules(
    avg_overshoot: float,
    avg_undershoot: float,
    avg_oscillations: float,
    avg_rise_time: float,
    avg_settling_time: float,
) -> List[PIDRuleResult]:
    """
    Evaluate all PID tuning rules against current metrics.

    Args:
        avg_overshoot: Average overshoot in degrees C
        avg_undershoot: Average undershoot in degrees C
        avg_oscillations: Average number of oscillations
        avg_rise_time: Average rise time in minutes
        avg_settling_time: Average settling time in minutes

    Returns:
        List of applicable rule results (rules that would fire)
    """
    results: List[PIDRuleResult] = []

    # Rule 1: High overshoot (>0.5C)
    if avg_overshoot > 0.5:
        reduction = min(0.15, avg_overshoot * 0.2)  # Up to 15% reduction
        results.append(PIDRuleResult(
            rule=PIDRule.HIGH_OVERSHOOT,
            kp_factor=1.0 - reduction,
            ki_factor=0.9,
            kd_factor=1.0,
            reason=f"High overshoot ({avg_overshoot:.2f}°C)"
        ))
    # Rule 2: Moderate overshoot (>0.2C, only if high overshoot didn't fire)
    elif avg_overshoot > 0.2:
        results.append(PIDRuleResult(
            rule=PIDRule.MODERATE_OVERSHOOT,
            kp_factor=0.95,
            ki_factor=1.0,
            kd_factor=1.0,
            reason=f"Moderate overshoot ({avg_overshoot:.2f}°C)"
        ))

    # Rule 3: Slow response (rise time >60 min)
    if avg_rise_time > 60:
        results.append(PIDRuleResult(
            rule=PIDRule.SLOW_RESPONSE,
            kp_factor=1.10,
            ki_factor=1.0,
            kd_factor=1.0,
            reason=f"Slow rise time ({avg_rise_time:.1f} min)"
        ))

    # Rule 4: Undershoot (>0.3C)
    if avg_undershoot > 0.3:
        increase = min(1.0, avg_undershoot * 2.0)  # Up to 100% increase (doubling)
        increase_pct = increase * 100.0
        results.append(PIDRuleResult(
            rule=PIDRule.UNDERSHOOT,
            kp_factor=1.0,
            ki_factor=1.0 + increase,
            kd_factor=1.0,
            reason=f"Undershoot ({avg_undershoot:.2f}°C, +{increase_pct:.0f}% Ki)"
        ))

    # Rule 5: Many oscillations (>3)
    if avg_oscillations > 3:
        results.append(PIDRuleResult(
            rule=PIDRule.MANY_OSCILLATIONS,
            kp_factor=0.90,
            ki_factor=1.0,
            kd_factor=1.20,
            reason=f"Many oscillations ({avg_oscillations:.1f})"
        ))
    # Rule 6: Some oscillations (>1, only if many didn't fire)
    elif avg_oscillations > 1:
        results.append(PIDRuleResult(
            rule=PIDRule.SOME_OSCILLATIONS,
            kp_factor=1.0,
            ki_factor=1.0,
            kd_factor=1.10,
            reason=f"Some oscillations ({avg_oscillations:.1f})"
        ))

    # Rule 7: Slow settling (>90 min)
    if avg_settling_time > 90:
        results.append(PIDRuleResult(
            rule=PIDRule.SLOW_SETTLING,
            kp_factor=1.0,
            ki_factor=1.0,
            kd_factor=1.15,
            reason=f"Slow settling ({avg_settling_time:.1f} min)"
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
