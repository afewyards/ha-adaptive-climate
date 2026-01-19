[
  {
    "id": "1.1",
    "category": "config",
    "description": "Add auto-apply configuration constants to const.py",
    "steps": [
      "Open custom_components/adaptive_thermostat/const.py",
      "Add CONF_AUTO_APPLY_PID = \"auto_apply_pid\" after existing CONF_ constants",
      "Add MAX_AUTO_APPLIES_PER_SEASON = 5 constant",
      "Add MAX_AUTO_APPLIES_LIFETIME = 20 constant",
      "Add MAX_CUMULATIVE_DRIFT_PCT = 50 constant",
      "Add PID_HISTORY_SIZE = 10 constant",
      "Add VALIDATION_CYCLE_COUNT = 5 constant",
      "Add VALIDATION_DEGRADATION_THRESHOLD = 0.30 constant",
      "Add SEASONAL_SHIFT_BLOCK_DAYS = 7 constant",
      "Add docstring comments for each constant explaining purpose",
      "VERIFY: grep -E '(MAX_AUTO_APPLIES|VALIDATION_|SEASONAL_SHIFT)' custom_components/adaptive_thermostat/const.py shows all new constants"
    ],
    "passes": true,
    "commitId": "bbf751143e7f48b7279e904046d3bf0baae3caec"
  },
  {
    "id": "1.2",
    "category": "config",
    "description": "Add heating-type-specific auto-apply thresholds dictionary",
    "steps": [
      "In const.py after validation constants, add AUTO_APPLY_THRESHOLDS = {} dict",
      "Add floor_hydronic entry: confidence_first=0.80, confidence_subsequent=0.90, min_cycles=8, cooldown_hours=96, cooldown_cycles=15",
      "Add radiator entry: confidence_first=0.70, confidence_subsequent=0.85, min_cycles=7, cooldown_hours=72, cooldown_cycles=12",
      "Add convector entry: confidence_first=0.60, confidence_subsequent=0.80, min_cycles=6, cooldown_hours=48, cooldown_cycles=10",
      "Add forced_air entry: confidence_first=0.60, confidence_subsequent=0.80, min_cycles=6, cooldown_hours=36, cooldown_cycles=8",
      "Add docstring explaining heating-type-specific behavior",
      "VERIFY: python -c \"from custom_components.adaptive_thermostat.const import AUTO_APPLY_THRESHOLDS; assert len(AUTO_APPLY_THRESHOLDS) == 4\""
    ],
    "passes": true,
    "commitId": "df724170b56cdbe7b9b8be94dcf9b97e2ac73abc"
  },
  {
    "id": "1.3",
    "category": "config",
    "description": "Add auto_apply_pid to PLATFORM_SCHEMA",
    "steps": [
      "Find PLATFORM_SCHEMA in const.py",
      "Add vol.Optional(CONF_AUTO_APPLY_PID, default=True): cv.boolean entry",
      "Place after other boolean config options for consistency",
      "VERIFY: grep 'CONF_AUTO_APPLY_PID' custom_components/adaptive_thermostat/const.py | grep PLATFORM_SCHEMA"
    ],
    "passes": true,
    "commitId": "7a4f90bb5e9debb351897ba0d6c9be8a9d1da236"
  },
  {
    "id": "1.4",
    "category": "config",
    "description": "Add entity attribute constants for auto-apply status",
    "steps": [
      "In const.py find ATTR_ attribute constants section",
      "Add ATTR_AUTO_APPLY_ENABLED = \"auto_apply_pid_enabled\"",
      "Add ATTR_AUTO_APPLY_COUNT = \"auto_apply_count\"",
      "Add ATTR_VALIDATION_MODE = \"validation_mode\"",
      "Add ATTR_PID_HISTORY = \"pid_history\"",
      "Add ATTR_PENDING_RECOMMENDATION = \"pending_recommendation\"",
      "VERIFY: grep 'ATTR_AUTO_APPLY' custom_components/adaptive_thermostat/const.py | wc -l shows 2 matches"
    ],
    "passes": true,
    "commitId": "b84900f"
  },
  {
    "id": "2.1",
    "category": "infrastructure",
    "description": "Add auto-apply tracking state to AdaptiveLearner.__init__()",
    "steps": [
      "Open custom_components/adaptive_thermostat/adaptive/learning.py",
      "Find AdaptiveLearner.__init__() method around line 94",
      "Add self._auto_apply_count: int = 0",
      "Add self._last_seasonal_shift: Optional[datetime] = None",
      "Add self._pid_history: List[Dict[str, Any]] = []",
      "Add self._physics_baseline_kp: Optional[float] = None",
      "Add self._physics_baseline_ki: Optional[float] = None",
      "Add self._physics_baseline_kd: Optional[float] = None",
      "Add self._validation_mode: bool = False",
      "Add self._validation_baseline_overshoot: Optional[float] = None",
      "Add self._validation_cycles: List[CycleMetrics] = []",
      "Add import for statistics module at top of file",
      "VERIFY: grep '_auto_apply_count\\|_validation_mode\\|_pid_history' custom_components/adaptive_thermostat/adaptive/learning.py | wc -l shows 3+ matches"
    ],
    "passes": true,
    "commitId": "15ea92f6329de9360c90d872de95c0905d5a1333"
  },
  {
    "id": "2.2",
    "category": "infrastructure",
    "description": "Implement record_pid_snapshot() method for PID history tracking",
    "steps": [
      "In learning.py after clear_history() method (line 471), add record_pid_snapshot() method",
      "Accept parameters: kp (float), ki (float), kd (float), reason (str), metrics (Optional[Dict[str, float]])",
      "Create snapshot dict with timestamp (datetime.now()), kp, ki, kd, reason, metrics",
      "Append snapshot to self._pid_history",
      "Implement FIFO eviction: if len > PID_HISTORY_SIZE, slice to keep last N",
      "Log debug message with PID values and reason",
      "Add docstring with Args and example reasons: auto_apply, manual, physics_reset, rollback",
      "VERIFY: grep -A 20 'def record_pid_snapshot' custom_components/adaptive_thermostat/adaptive/learning.py shows complete implementation"
    ],
    "passes": true,
    "commitId": "390e0fdaee3bb00ac053f68f383a31fa79e61d86"
  },
  {
    "id": "2.3",
    "category": "infrastructure",
    "description": "Implement get_previous_pid() method for rollback retrieval",
    "steps": [
      "In learning.py after record_pid_snapshot(), add get_previous_pid() method",
      "Return type: Optional[Dict[str, float]]",
      "Check if len(self._pid_history) < 2, return None",
      "Get second-to-last entry: prev = self._pid_history[-2]",
      "Return dict with kp, ki, kd, timestamp, reason from prev",
      "Add docstring explaining rollback retrieval logic",
      "VERIFY: grep -A 15 'def get_previous_pid' custom_components/adaptive_thermostat/adaptive/learning.py shows return statement"
    ],
    "passes": true,
    "commitId": "09b97ba"
  },
  {
    "id": "2.4",
    "category": "infrastructure",
    "description": "Implement get_pid_history() method for debugging access",
    "steps": [
      "In learning.py after get_previous_pid(), add get_pid_history() method",
      "Return type: List[Dict[str, Any]]",
      "Return self._pid_history.copy() to prevent external mutation",
      "Add docstring: Get full PID history for debugging",
      "VERIFY: grep 'def get_pid_history' custom_components/adaptive_thermostat/adaptive/learning.py"
    ],
    "passes": true,
    "commitId": "4cb569b"
  },
  {
    "id": "2.5",
    "category": "infrastructure",
    "description": "Implement set_physics_baseline() and calculate_drift_from_baseline() methods",
    "steps": [
      "In learning.py after get_pid_history(), add set_physics_baseline() method",
      "Accept parameters: kp (float), ki (float), kd (float)",
      "Set self._physics_baseline_kp, _ki, _kd to provided values",
      "Log info message with baseline values",
      "Add calculate_drift_from_baseline() method after set_physics_baseline()",
      "Accept parameters: current_kp, current_ki, current_kd",
      "Return 0.0 if self._physics_baseline_kp is None",
      "Calculate kp_drift = abs(current_kp - baseline_kp) / baseline_kp",
      "Calculate ki_drift and kd_drift similarly",
      "Return max(kp_drift, ki_drift, kd_drift)",
      "Add docstring explaining drift calculation as percentage",
      "VERIFY: grep -E '(set_physics_baseline|calculate_drift_from_baseline)' custom_components/adaptive_thermostat/adaptive/learning.py | wc -l shows 2"
    ],
    "passes": true,
    "commitId": "ff5b8aa2a6c8bfdf4c94161f83fa585d6ad54593"
  },
  {
    "id": "2.6",
    "category": "infrastructure",
    "description": "Implement validation mode methods: start, add_cycle, is_in",
    "steps": [
      "In learning.py add start_validation_mode(baseline_overshoot: float) method",
      "Set self._validation_mode = True",
      "Set self._validation_baseline_overshoot = baseline_overshoot",
      "Clear self._validation_cycles = []",
      "Log info message with baseline overshoot value",
      "Add add_validation_cycle(metrics: CycleMetrics) method returning Optional[str]",
      "Return None if not self._validation_mode",
      "Append metrics to self._validation_cycles",
      "If len < VALIDATION_CYCLE_COUNT, log debug and return None",
      "Calculate avg_overshoot from validation_cycles overshoot values",
      "If no overshoot data, log warning, set mode=False, return 'success'",
      "Calculate degradation_pct = (avg - baseline) / max(baseline, 0.1)",
      "If degradation_pct > VALIDATION_DEGRADATION_THRESHOLD, log warning, set mode=False, return 'rollback'",
      "Otherwise log success, set mode=False, return 'success'",
      "Add is_in_validation_mode() method returning bool: self._validation_mode",
      "VERIFY: grep -E '(start_validation_mode|add_validation_cycle|is_in_validation_mode)' custom_components/adaptive_thermostat/adaptive/learning.py | wc -l shows 3"
    ],
    "passes": true,
    "commitId": "674a7f331846d1b558df7264030de7f816559bf8"
  },
  {
    "id": "2.7",
    "category": "infrastructure",
    "description": "Implement check_auto_apply_limits() method for safety gates",
    "steps": [
      "In learning.py add check_auto_apply_limits(current_kp, current_ki, current_kd) method",
      "Return type: Optional[str] (None if OK, error message if blocked)",
      "Check lifetime limit: if self._auto_apply_count >= MAX_AUTO_APPLIES_LIFETIME, return error message",
      "Calculate seasonal limit: now = datetime.now(), cutoff = now - timedelta(days=90)",
      "Filter self._pid_history for reason=='auto_apply' and timestamp > cutoff",
      "If len(recent_applies) >= MAX_AUTO_APPLIES_PER_SEASON, return error message",
      "Check drift limit: drift_pct = self.calculate_drift_from_baseline(current_kp, current_ki, current_kd)",
      "If drift_pct > MAX_CUMULATIVE_DRIFT_PCT / 100, return error message",
      "Check seasonal shift: if self._last_seasonal_shift is not None, calculate days_since_shift",
      "If days_since_shift < SEASONAL_SHIFT_BLOCK_DAYS, return error message",
      "Return None if all checks pass",
      "Add docstring explaining all safety checks",
      "VERIFY: grep -A 40 'def check_auto_apply_limits' custom_components/adaptive_thermostat/adaptive/learning.py shows all 4 checks"
    ],
    "passes": true,
    "commitId": "cf4f751cfa20b6b896660975ff569b09f8c1fc8c"
  },
  {
    "id": "2.8",
    "category": "infrastructure",
    "description": "Implement seasonal shift recording and auto-apply count getter",
    "steps": [
      "In learning.py add record_seasonal_shift() method with no parameters",
      "Set self._last_seasonal_shift = datetime.now()",
      "Log warning message with SEASONAL_SHIFT_BLOCK_DAYS",
      "Add get_auto_apply_count() method returning int",
      "Return self._auto_apply_count",
      "Add docstring: Get number of times PID has been auto-applied",
      "VERIFY: grep -E '(record_seasonal_shift|get_auto_apply_count)' custom_components/adaptive_thermostat/adaptive/learning.py | wc -l shows 2"
    ],
    "passes": true,
    "commitId": "0d609df"
  },
  {
    "id": "2.9",
    "category": "infrastructure",
    "description": "Update clear_history() to reset validation state",
    "steps": [
      "Find clear_history() method in learning.py (line 467)",
      "Add self._convergence_confidence = 0.0 to reset confidence",
      "Add self._validation_mode = False",
      "Add self._validation_cycles = []",
      "Keep existing _cycle_history.clear(), _last_adjustment_time, _cycles_since_last_adjustment resets",
      "Update docstring to mention validation reset",
      "VERIFY: grep -A 10 'def clear_history' custom_components/adaptive_thermostat/adaptive/learning.py | grep validation_mode"
    ],
    "passes": true,
    "commitId": "fa64cd6920924a79148b8b5ba593c24b80c4b845"
  },
  {
    "id": "3.1",
    "category": "feature",
    "description": "Wire up confidence updates in cycle_tracker.py",
    "steps": [
      "Open custom_components/adaptive_thermostat/managers/cycle_tracker.py",
      "Find _finalize_cycle() method around line 701",
      "After existing self._adaptive_learner.update_convergence_tracking(metrics) call, add self._adaptive_learner.update_convergence_confidence(metrics)",
      "Add validation check: if self._adaptive_learner.is_in_validation_mode():",
      "Call validation_result = self._adaptive_learner.add_validation_cycle(metrics)",
      "If validation_result == 'rollback', call self._on_validation_failed() callback if set",
      "If validation_result == 'success', log info message",
      "VERIFY: grep -A 12 'update_convergence_tracking' custom_components/adaptive_thermostat/managers/cycle_tracker.py | grep 'update_convergence_confidence\\|validation_result'"
    ],
    "passes": true,
    "commitId": "80aad5cb34540685378453f07f7e6d0f0cdfe637"
  },
  {
    "id": "3.2",
    "category": "feature",
    "description": "Add on_validation_failed callback parameter to CycleTrackerManager",
    "steps": [
      "In cycle_tracker.py find CycleTrackerManager.__init__() method",
      "Add on_validation_failed: Optional[Callable[[], Awaitable[None]]] = None parameter",
      "In __init__ body, add self._on_validation_failed = on_validation_failed",
      "Update docstring with parameter description",
      "VERIFY: grep 'on_validation_failed' custom_components/adaptive_thermostat/managers/cycle_tracker.py | wc -l shows 2+ matches"
    ],
    "passes": true,
    "commitId": "6bfd052"
  },
  {
    "id": "3.3",
    "category": "feature",
    "description": "Add auto-apply safety checks to calculate_pid_adjustment()",
    "steps": [
      "Open custom_components/adaptive_thermostat/adaptive/learning.py",
      "Find calculate_pid_adjustment() method signature at line 230",
      "Add check_auto_apply: bool = False parameter",
      "Add outdoor_temp: Optional[float] = None parameter",
      "After Ke-First gate check (around line 276), add if check_auto_apply: block",
      "Check validation mode: if self._validation_mode, log debug and return None",
      "Check limits: limit_msg = self.check_auto_apply_limits(current_kp, current_ki, current_kd)",
      "If limit_msg, log warning and return None",
      "Check seasonal shift: if outdoor_temp and self.check_seasonal_shift(outdoor_temp), call record_seasonal_shift() and return None",
      "Get heating-type thresholds: thresholds = AUTO_APPLY_THRESHOLDS.get(self._heating_type, AUTO_APPLY_THRESHOLDS['convector'])",
      "Calculate confidence_threshold based on self._auto_apply_count (0 = first, else subsequent)",
      "If self._convergence_confidence < confidence_threshold, log debug and return None",
      "Override min_interval_hours, min_adjustment_cycles, min_cycles with heating-type values",
      "VERIFY: grep -A 50 'if check_auto_apply:' custom_components/adaptive_thermostat/adaptive/learning.py shows all safety checks"
    ],
    "passes": true,
    "commitId": "f5895b227239d44af3ae1e7c8cb2f83439aca13d"
  },
  {
    "id": "4.1",
    "category": "feature",
    "description": "Implement async_auto_apply_adaptive_pid() in PIDTuningManager",
    "steps": [
      "Open custom_components/adaptive_thermostat/managers/pid_tuning.py",
      "After async_apply_adaptive_pid() method, add async_auto_apply_adaptive_pid(outdoor_temp: Optional[float] = None) method",
      "Return type: Dict[str, Any] with keys: applied (bool), reason (str), recommendation (dict or None)",
      "Get coordinator from hass.data, return failure dict if None",
      "Find adaptive_learner from all_zones by matching climate_entity_id",
      "Get heating_type and thresholds from AUTO_APPLY_THRESHOLDS",
      "Calculate baseline_overshoot from last 6 cycles overshoot values using statistics.mean",
      "Call adaptive_learner.calculate_pid_adjustment() with check_auto_apply=True and heating-type thresholds",
      "If recommendation is None, return failure dict",
      "Record PID snapshot before applying with reason='before_auto_apply'",
      "Apply new PID values: set_kp/ki/kd, clear integral, set_pid_param",
      "Record PID snapshot after applying with reason='auto_apply'",
      "Clear learning history: adaptive_learner.clear_history()",
      "Increment adaptive_learner._auto_apply_count",
      "Start validation mode: adaptive_learner.start_validation_mode(baseline_overshoot)",
      "Log warning with before/after PID values and validation cycle count",
      "Call await self._async_control_heating(calc_pid=True) and await self._async_write_ha_state()",
      "Return success dict with applied=True, recommendation, old_values, new_values",
      "VERIFY: grep -A 80 'async def async_auto_apply_adaptive_pid' custom_components/adaptive_thermostat/managers/pid_tuning.py | grep 'start_validation_mode'"
    ],
    "passes": true,
    "commitId": "a43c9ab91411dce6ab3534c648ada5e502fe5bcc"
  },
  {
    "id": "4.2",
    "category": "feature",
    "description": "Implement async_rollback_pid() in PIDTuningManager",
    "steps": [
      "In pid_tuning.py after async_auto_apply_adaptive_pid(), add async_rollback_pid() method",
      "Return type: bool (True if success, False if no history)",
      "Get coordinator and adaptive_learner using same pattern as auto_apply",
      "Call previous_pid = adaptive_learner.get_previous_pid()",
      "If previous_pid is None, log warning and return False",
      "Store current PID values for logging",
      "Apply previous PID values: set_kp/ki/kd, clear integral, set_pid_param",
      "Record rollback snapshot: adaptive_learner.record_pid_snapshot(kp, ki, kd, reason='rollback')",
      "Clear history: adaptive_learner.clear_history()",
      "Log warning with before/after values and timestamp of previous config",
      "Call await self._async_control_heating(calc_pid=True) and await self._async_write_ha_state()",
      "Return True",
      "VERIFY: grep -A 50 'async def async_rollback_pid' custom_components/adaptive_thermostat/managers/pid_tuning.py | grep 'get_previous_pid'"
    ],
    "passes": true,
    "commitId": "ab78d883efcd15328e306176992b77cd2821a099"
  },
  {
    "id": "4.3",
    "category": "feature",
    "description": "Add PID snapshot recording to existing manual apply methods",
    "steps": [
      "In pid_tuning.py find async_apply_adaptive_pid() method",
      "After line 258 (after clearing integral), add coordinator access code",
      "Get adaptive_learner from all_zones by entity_id match",
      "If adaptive_learner exists, call record_pid_snapshot(kp, ki, kd, reason='manual_apply')",
      "Call adaptive_learner.clear_history() to reset learning after manual change",
      "In async_reset_pid_to_physics() after line 176, add similar code",
      "Get adaptive_learner, call set_physics_baseline(kp, ki, kd)",
      "Call record_pid_snapshot(kp, ki, kd, reason='physics_reset')",
      "VERIFY: grep -B 5 -A 5 \"reason='manual_apply'\" custom_components/adaptive_thermostat/managers/pid_tuning.py"
    ],
    "passes": true,
    "commitId": "984a1fdd4f6a517863962812965fb21ae83cf983"
  },
  {
    "id": "4.4",
    "category": "feature",
    "description": "Add _check_auto_apply_pid() and _handle_validation_failure() to climate.py",
    "steps": [
      "Open custom_components/adaptive_thermostat/climate.py",
      "In __init__() method, add self._auto_apply_pid = config.get(const.CONF_AUTO_APPLY_PID, True)",
      "Add async def _check_auto_apply_pid(self) method",
      "Early return if not self._auto_apply_pid or not self._pid_tuning_manager",
      "Get outdoor_temp from self._outdoor_sensor_entity_id state if available",
      "Call result = await self._pid_tuning_manager.async_auto_apply_adaptive_pid(outdoor_temp)",
      "If result['applied'], send persistent_notification with title, old/new PID values, validation count, rollback link",
      "Add async def _handle_validation_failure(self) method",
      "Early return if not self._pid_tuning_manager",
      "Call success = await self._pid_tuning_manager.async_rollback_pid()",
      "If success, send persistent_notification about automatic rollback",
      "VERIFY: grep -E '(_check_auto_apply_pid|_handle_validation_failure)' custom_components/adaptive_thermostat/climate.py | wc -l shows 2"
    ],
    "passes": true,
    "commitId": "b419acc"
  },
  {
    "id": "4.5",
    "category": "feature",
    "description": "Pass callbacks to CycleTrackerManager initialization",
    "steps": [
      "In climate.py find where self._cycle_tracker = CycleTrackerManager(...) is created",
      "Add on_auto_apply_check=self._check_auto_apply_pid parameter",
      "Add on_validation_failed=self._handle_validation_failure parameter",
      "Ensure callbacks are defined before CycleTrackerManager creation (move method definitions if needed)",
      "VERIFY: grep -A 3 'CycleTrackerManager' custom_components/adaptive_thermostat/climate.py | grep 'on_auto_apply_check'"
    ],
    "passes": true,
    "commitId": "1c8708f"
  },
  {
    "id": "4.6",
    "category": "feature",
    "description": "Add on_auto_apply_check callback parameter to CycleTrackerManager",
    "steps": [
      "Open custom_components/adaptive_thermostat/managers/cycle_tracker.py",
      "Find CycleTrackerManager.__init__() method",
      "Add on_auto_apply_check: Optional[Callable[[], Awaitable[None]]] = None parameter",
      "In __init__ body, add self._on_auto_apply_check = on_auto_apply_check",
      "In _finalize_cycle() at the end, add check: if self._on_auto_apply_check and self._climate._auto_apply_pid: await self._on_auto_apply_check()",
      "Update docstring with parameter description",
      "VERIFY: grep 'on_auto_apply_check' custom_components/adaptive_thermostat/managers/cycle_tracker.py | wc -l shows 2+ matches"
    ],
    "passes": true,
    "commitId": "1c8708f"
  },
  {
    "id": "5.1",
    "category": "feature",
    "description": "Add rollback_pid service to services.yaml",
    "steps": [
      "Open custom_components/adaptive_thermostat/services.yaml",
      "After apply_adaptive_pid service definition, add rollback_pid service",
      "Set name: Rollback PID",
      "Set description explaining rollback to previous config, clears history, exits validation",
      "Set target with entity domain=climate, integration=adaptive_thermostat",
      "VERIFY: grep -A 8 'rollback_pid:' custom_components/adaptive_thermostat/services.yaml | grep 'name: Rollback PID'"
    ],
    "passes": true,
    "commitId": "d465366"
  },
  {
    "id": "5.2",
    "category": "feature",
    "description": "Register rollback_pid service in climate.py",
    "steps": [
      "In climate.py find where services are registered with platform.async_register_entity_service (around line 350)",
      "Add platform.async_register_entity_service('rollback_pid', {}, 'async_rollback_pid')",
      "Add async def async_rollback_pid(self, **kwargs) method to climate entity class",
      "Method body: await self._pid_tuning_manager.async_rollback_pid()",
      "Add docstring: Service call handler for rollback_pid",
      "VERIFY: grep \"'rollback_pid'\" custom_components/adaptive_thermostat/climate.py | wc -l shows 2"
    ],
    "passes": true,
    "commitId": "dd88dc9"
  },
  {
    "id": "6.1",
    "category": "feature",
    "description": "Expose auto-apply status in entity attributes",
    "steps": [
      "Open custom_components/adaptive_thermostat/managers/state_attributes.py",
      "Find where attributes are being built (around line 250)",
      "Add attrs[const.ATTR_AUTO_APPLY_ENABLED] = getattr(climate, '_auto_apply_pid', False)",
      "If adaptive_learner exists, add attrs[const.ATTR_AUTO_APPLY_COUNT] = adaptive_learner.get_auto_apply_count()",
      "Add attrs[const.ATTR_VALIDATION_MODE] = adaptive_learner.is_in_validation_mode()",
      "Get pid_history = adaptive_learner.get_pid_history()",
      "If pid_history, format last 3 entries: timestamp (isoformat), round kp/ki/kd, reason",
      "Set attrs[const.ATTR_PID_HISTORY] to formatted list",
      "VERIFY: grep 'ATTR_AUTO_APPLY\\|ATTR_VALIDATION\\|ATTR_PID_HISTORY' custom_components/adaptive_thermostat/managers/state_attributes.py | wc -l shows 5"
    ],
    "passes": true,
    "commitId": "adb926a"
  },
  {
    "id": "7.1",
    "category": "feature",
    "description": "Set physics baseline during initialization in climate.py",
    "steps": [
      "Open custom_components/adaptive_thermostat/climate.py",
      "Find async_added_to_hass() method where physics-based PID is calculated",
      "After physics calculation (tau, kp, ki, kd from calculate_initial_pid), add physics baseline setting",
      "Check if self._adaptive_learner exists and area_m2 is set",
      "Call self._adaptive_learner.set_physics_baseline(kp, ki, kd)",
      "VERIFY: grep -A 5 'calculate_initial_pid' custom_components/adaptive_thermostat/climate.py | grep set_physics_baseline"
    ],
    "passes": true,
    "commitId": "c1bf5de"
  },
  {
    "id": "7.2",
    "category": "feature",
    "description": "Update physics baseline in async_reset_pid_to_physics()",
    "steps": [
      "In pid_tuning.py find async_reset_pid_to_physics() method",
      "After calculating kp, ki, kd (line 176), add coordinator access",
      "Get adaptive_learner from all_zones by entity_id match",
      "If adaptive_learner exists, call set_physics_baseline(kp, ki, kd)",
      "Call record_pid_snapshot(kp, ki, kd, reason='physics_reset')",
      "VERIFY: grep -A 10 'async_reset_pid_to_physics' custom_components/adaptive_thermostat/managers/pid_tuning.py | grep set_physics_baseline"
    ],
    "passes": true,
    "commitId": "984a1fd"
  },
  {
    "id": "8.1",
    "category": "test",
    "description": "Write unit tests for PID history and rollback",
    "steps": [
      "Create/update tests/test_auto_apply.py",
      "Write test_record_pid_snapshot_basic: create learner, record 3 snapshots, verify list length and FIFO",
      "Write test_record_pid_snapshot_fifo_eviction: record 11 snapshots (exceeding PID_HISTORY_SIZE=10), verify oldest evicted",
      "Write test_get_previous_pid_success: record 2 snapshots, call get_previous_pid(), verify returns second-to-last",
      "Write test_get_previous_pid_insufficient_history: record 1 snapshot, verify returns None",
      "Write test_calculate_drift_from_baseline: set baseline, calculate drift with 20% Kp change, verify 0.2 returned",
      "Write test_calculate_drift_no_baseline: calculate drift without baseline, verify 0.0 returned",
      "VERIFY: pytest tests/test_auto_apply.py::test_record_pid_snapshot_basic -v passes"
    ],
    "passes": true,
    "commitId": "6167004"
  },
  {
    "id": "8.2",
    "category": "test",
    "description": "Write unit tests for validation window",
    "steps": [
      "In tests/test_auto_apply.py add validation tests",
      "Write test_start_validation_mode: start validation, verify mode=True and baseline set",
      "Write test_add_validation_cycle_collecting: add 3 of 5 cycles, verify returns None (still collecting)",
      "Write test_add_validation_cycle_success: add 5 cycles with same overshoot, verify returns 'success'",
      "Write test_add_validation_cycle_degradation: add 5 cycles with 40% worse overshoot, verify returns 'rollback'",
      "Write test_validation_mode_reset_on_clear_history: start validation, clear history, verify mode=False",
      "VERIFY: pytest tests/test_auto_apply.py -k validation -v shows 5 tests passing"
    ],
    "passes": true,
    "commitId": "6167004"
  },
  {
    "id": "8.3",
    "category": "test",
    "description": "Write unit tests for auto-apply limits",
    "steps": [
      "In tests/test_auto_apply.py add limits tests",
      "Write test_check_auto_apply_limits_lifetime: set _auto_apply_count=20, call check_auto_apply_limits(), verify error message returned",
      "Write test_check_auto_apply_limits_seasonal: add 5 auto_apply snapshots within 90 days, verify blocked",
      "Write test_check_auto_apply_limits_drift: set baseline, check with 60% drifted values, verify blocked",
      "Write test_check_auto_apply_limits_seasonal_shift: record shift 3 days ago, verify blocked with days remaining message",
      "Write test_check_auto_apply_limits_all_pass: verify returns None when all checks pass",
      "VERIFY: pytest tests/test_auto_apply.py -k limits -v shows 5 tests passing"
    ],
    "passes": true,
    "commitId": "6167004"
  },
  {
    "id": "8.4",
    "category": "test",
    "description": "Write unit tests for heating-type-specific thresholds",
    "steps": [
      "In tests/test_auto_apply.py add heating-type tests",
      "Write test_auto_apply_threshold_floor_hydronic: create learner with heating_type='floor_hydronic', verify confidence_first=0.80",
      "Write test_auto_apply_threshold_forced_air: create learner with heating_type='forced_air', verify confidence_first=0.60, cooldown_hours=36",
      "Write test_auto_apply_threshold_unknown_defaults_to_convector: create learner with heating_type='unknown', verify uses convector thresholds",
      "Write test_threshold_lookup_in_calculate_pid_adjustment: mock calculate_pid_adjustment with check_auto_apply=True, verify uses heating-type thresholds",
      "VERIFY: pytest tests/test_auto_apply.py -k heating_type -v shows 4 tests passing"
    ],
    "passes": true,
    "commitId": "29e909c"
  },
  {
    "id": "9.1",
    "category": "test",
    "description": "Write integration test for full auto-apply flow",
    "steps": [
      "Create/update tests/test_integration_auto_apply.py",
      "Write test_full_auto_apply_flow: create thermostat with auto_apply_pid=True, heating_type='convector'",
      "Simulate 6 good cycles (overshoot < 0.2°C, oscillations < 2)",
      "Verify convergence_confidence reaches 0.6 (60%)",
      "Mock async_auto_apply_adaptive_pid to track calls",
      "Finalize 6th cycle, verify auto-apply triggered",
      "Verify validation_mode = True",
      "Verify PID snapshot recorded with reason='auto_apply'",
      "Verify learning history cleared",
      "VERIFY: pytest tests/test_integration_auto_apply.py::test_full_auto_apply_flow -v passes"
    ],
    "passes": true,
    "commitId": "f86494f"
  },
  {
    "id": "9.2",
    "category": "test",
    "description": "Write integration test for validation success scenario",
    "steps": [
      "In tests/test_integration_auto_apply.py add test_validation_success",
      "Trigger auto-apply (6 good cycles reaching 60% confidence)",
      "Verify validation mode started with baseline_overshoot",
      "Simulate 5 validation cycles with equal/better overshoot (e.g., 0.15°C baseline, 0.12°C validation)",
      "On 5th cycle, verify add_validation_cycle returns 'success'",
      "Verify validation_mode = False",
      "Verify auto_apply_count incremented to 1",
      "VERIFY: pytest tests/test_integration_auto_apply.py::test_validation_success -v passes"
    ],
    "passes": true,
    "commitId": "02626e4"
  },
  {
    "id": "9.3",
    "category": "test",
    "description": "Write integration test for validation failure and automatic rollback",
    "steps": [
      "In tests/test_integration_auto_apply.py add test_validation_failure_automatic_rollback",
      "Record initial PID values (kp=100, ki=0.01, kd=50)",
      "Trigger auto-apply, verify new PID applied (e.g., kp=90)",
      "Verify validation mode started",
      "Simulate 5 validation cycles with 40% worse overshoot (degradation)",
      "On 5th cycle, verify add_validation_cycle returns 'rollback'",
      "Verify _handle_validation_failure callback triggered",
      "Verify PID rolled back to previous values (kp=100)",
      "Verify learning history cleared",
      "Verify auto_apply_count NOT incremented (still 0)",
      "VERIFY: pytest tests/test_integration_auto_apply.py::test_validation_failure_automatic_rollback -v passes"
    ],
    "passes": true,
    "commitId": "5a9cf16"
  },
  {
    "id": "9.4",
    "category": "test",
    "description": "Write integration test for limit enforcement",
    "steps": [
      "In tests/test_integration_auto_apply.py add test_seasonal_limit_blocks_sixth_apply",
      "Trigger 5 successful auto-applies within 90 days",
      "Build confidence to 80% for 6th attempt",
      "Attempt 6th auto-apply, verify blocked",
      "Verify calculate_pid_adjustment returns None",
      "Verify log warning contains 'Seasonal limit reached'",
      "Write test_drift_limit_blocks_apply: set physics baseline, simulate 3 auto-applies creating 55% cumulative drift",
      "Attempt next auto-apply, verify blocked with drift error",
      "VERIFY: pytest tests/test_integration_auto_apply.py -k limit -v shows 2 tests passing"
    ],
    "passes": true,
    "commitId": "9271258d2624e7840eac8d8aa32517f034e513bf"
  },
  {
    "id": "9.5",
    "category": "test",
    "description": "Write integration test for seasonal shift blocking",
    "steps": [
      "In tests/test_integration_auto_apply.py add test_seasonal_shift_blocks_auto_apply",
      "Build confidence to 70%",
      "Set outdoor_temp history to stable 15°C average",
      "Simulate outdoor_temp drop to 3°C (12°C shift)",
      "Call calculate_pid_adjustment with outdoor_temp=3°C and check_auto_apply=True",
      "Verify check_seasonal_shift detects shift",
      "Verify record_seasonal_shift called",
      "Verify calculate_pid_adjustment returns None (blocked)",
      "Wait 8 days (mock time), attempt auto-apply again, verify unblocked",
      "VERIFY: pytest tests/test_integration_auto_apply.py::test_seasonal_shift_blocks_auto_apply -v passes"
    ],
    "passes": true,
    "commitId": "f6e9397"
  },
  {
    "id": "9.6",
    "category": "test",
    "description": "Write integration test for manual rollback service",
    "steps": [
      "In tests/test_integration_auto_apply.py add test_manual_rollback_service",
      "Set initial PID (kp=100, ki=0.01, kd=50)",
      "Trigger auto-apply to new PID (kp=90, ki=0.012, kd=55)",
      "Verify PID history has 2 entries",
      "Call adaptive_thermostat.rollback_pid service",
      "Verify async_rollback_pid called",
      "Verify PID reverted to kp=100, ki=0.01, kd=50",
      "Verify rollback snapshot recorded",
      "Verify learning history cleared",
      "Verify persistent notification sent about rollback",
      "VERIFY: pytest tests/test_integration_auto_apply.py::test_manual_rollback_service -v passes"
    ],
    "passes": true,
    "commitId": "83d6cb8"
  },
  {
    "id": "10.1",
    "category": "test",
    "description": "Write edge case test for HA restart during validation",
    "steps": [
      "In tests/test_integration_auto_apply.py add test_ha_restart_during_validation",
      "Trigger auto-apply, enter validation mode (3/5 cycles complete)",
      "Verify validation_mode = True and validation_cycles has 3 entries",
      "Simulate HA restart: clear all learner state except persisted data",
      "Note: validation state is NOT persisted currently (known issue)",
      "After restart, verify validation_mode = False (lost state)",
      "Document in test comments: validation state loss is acceptable, system will rebuild confidence",
      "VERIFY: pytest tests/test_integration_auto_apply.py::test_ha_restart_during_validation -v passes"
    ],
    "passes": true,
    "commitId": "31f9e65"
  },
  {
    "id": "10.2",
    "category": "test",
    "description": "Write edge case test for multiple zones auto-applying",
    "steps": [
      "In tests/test_integration_auto_apply.py add test_multiple_zones_auto_apply_simultaneously",
      "Create 2 thermostats: zone1 (convector), zone2 (radiator)",
      "Build confidence in both zones (60% zone1, 70% zone2)",
      "Finalize cycles in both zones in same event loop iteration",
      "Verify both _check_auto_apply_pid callbacks trigger",
      "Verify both zones apply PID independently",
      "Verify no interference (zone1 history not cleared by zone2 action)",
      "VERIFY: pytest tests/test_integration_auto_apply.py::test_multiple_zones_auto_apply_simultaneously -v passes"
    ],
    "passes": true,
    "commitId": "48a0a2b"
  },
  {
    "id": "10.3",
    "category": "test",
    "description": "Write edge case test for manual PID change during validation",
    "steps": [
      "In tests/test_integration_auto_apply.py add test_manual_pid_change_during_validation",
      "Trigger auto-apply, enter validation mode (2/5 cycles)",
      "User calls adaptive_thermostat.set_pid service manually",
      "Verify manual change records snapshot",
      "Verify clear_history called, which resets validation_mode=False",
      "Verify validation aborted (validation_cycles cleared)",
      "Continue with normal operation (no validation completion)",
      "VERIFY: pytest tests/test_integration_auto_apply.py::test_manual_pid_change_during_validation -v passes"
    ],
    "passes": true,
    "commitId": "d0397ca"
  },
  {
    "id": "10.4",
    "category": "test",
    "description": "Write edge case test for 20th lifetime auto-apply limit",
    "steps": [
      "In tests/test_integration_auto_apply.py add test_lifetime_limit_blocks_21st_apply",
      "Set learner._auto_apply_count = 19",
      "Trigger successful auto-apply (20th apply)",
      "Verify _auto_apply_count = 20",
      "Build confidence to 80% again",
      "Attempt 21st auto-apply",
      "Verify check_auto_apply_limits returns 'Lifetime limit reached' error",
      "Verify calculate_pid_adjustment returns None",
      "Verify log warning mentions manual review required",
      "VERIFY: pytest tests/test_integration_auto_apply.py::test_lifetime_limit_blocks_21st_apply -v passes"
    ],
    "passes": true,
    "commitId": "5f2a81c"
  },
  {
    "id": "11.1",
    "category": "test",
    "description": "Manual test: Create test zone and monitor auto-apply",
    "steps": [
      "Add test zone to HA config with auto_apply_pid: true, heating_type: convector",
      "Restart HA and verify zone loads without errors",
      "Monitor Developer Tools > States for climate.test_zone",
      "Check attributes: convergence_confidence_pct, auto_apply_enabled, auto_apply_count, validation_mode",
      "Simulate good heating cycles (adjust setpoint up/down)",
      "Watch for convergence_confidence_pct climbing to 60%",
      "Wait for auto-apply persistent notification",
      "Verify notification shows old→new PID values",
      "Verify validation_mode = true after auto-apply",
      "VERIFY: Auto-apply notification appears and attributes update correctly"
    ],
    "passes": false
  },
  {
    "id": "11.2",
    "category": "test",
    "description": "Manual test: Verify rollback service from Developer Tools",
    "steps": [
      "After auto-apply in test zone, check pid_history attribute (should show 2+ entries)",
      "Go to Developer Tools > Services",
      "Call adaptive_thermostat.rollback_pid with target entity_id: climate.test_zone",
      "Verify PID values revert to previous (check kp, ki, kd attributes)",
      "Verify persistent notification appears about rollback",
      "Verify validation_mode = false",
      "Verify auto_apply_count unchanged (rollback doesn't increment)",
      "VERIFY: Rollback service successfully reverts PID to previous values"
    ],
    "passes": false
  },
  {
    "id": "11.3",
    "category": "test",
    "description": "Manual test: Verify auto-apply disabled with config flag",
    "steps": [
      "Edit test zone config, add auto_apply_pid: false",
      "Restart HA",
      "Build confidence to 70% through good cycles",
      "Finalize 6th cycle",
      "Verify NO auto-apply notification",
      "Verify PID values unchanged",
      "Check attributes: auto_apply_enabled should be false",
      "VERIFY: Auto-apply successfully disabled when auto_apply_pid: false"
    ],
    "passes": false
  },
  {
    "id": "11.4",
    "category": "test",
    "description": "Manual test: Trigger validation failure and observe auto-rollback",
    "steps": [
      "After auto-apply, system enters validation mode",
      "Manually decrease heater power or introduce disturbance to cause overshoot",
      "Simulate 5 cycles with significantly worse overshoot (>30% degradation)",
      "On 5th validation cycle, verify automatic rollback notification appears",
      "Verify PID values reverted to pre-auto-apply values",
      "Verify validation_mode = false",
      "Check logs for 'Validation FAILED' message",
      "VERIFY: System automatically rolls back after detecting performance degradation"
    ],
    "passes": false
  }
]
