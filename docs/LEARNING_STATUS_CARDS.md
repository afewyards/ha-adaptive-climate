# Learning Status Dashboard Cards

This guide shows how to display the adaptive learning status in Home Assistant dashboards using the new state attributes.

## Available Attributes

The following attributes are exposed on each `climate.*` entity:

| Attribute | Type | Values | Description |
|-----------|------|--------|-------------|
| `learning_status` | string | `collecting`, `ready`, `active`, `converged` | Current learning phase |
| `cycles_collected` | integer | 0+ | Number of completed cycles |
| `cycles_required_for_learning` | integer | 6 | Minimum cycles needed |
| `convergence_confidence_pct` | integer | 0-100 | Tuning confidence percentage |
| `current_cycle_state` | string | `idle`, `heating`, `settling`, `cooling` | Real-time cycle state |
| `last_cycle_interrupted` | string/null | `setpoint_change`, `mode_change`, `contact_sensor`, `null` | Last interruption reason |
| `last_pid_adjustment` | string/null | ISO 8601 timestamp | Last PID adjustment time |

## Learning Status Values

- **collecting** - Gathering initial data, fewer than 6 cycles completed
- **ready** - Minimum cycles collected, learning can begin
- **active** - Actively making PID adjustments with moderate confidence (≥50%)
- **converged** - System is well-tuned (3+ consecutive good cycles)

## Basic Examples

### 1. Simple Entities Card

Shows all learning attributes in a clean list:

```yaml
type: entities
title: Living Room Thermostat Learning
entities:
  - entity: climate.living_room
    type: attribute
    attribute: learning_status
    name: Learning Status
  - entity: climate.living_room
    type: attribute
    attribute: cycles_collected
    name: Cycles Collected
  - entity: climate.living_room
    type: attribute
    attribute: cycles_required_for_learning
    name: Cycles Required
  - entity: climate.living_room
    type: attribute
    attribute: convergence_confidence_pct
    name: Confidence
    suffix: "%"
  - entity: climate.living_room
    type: attribute
    attribute: current_cycle_state
    name: Current Cycle
  - entity: climate.living_room
    type: attribute
    attribute: last_cycle_interrupted
    name: Last Interruption
  - entity: climate.living_room
    type: attribute
    attribute: last_pid_adjustment
    name: Last Adjustment
```

### 2. Compact Status Card (Markdown)

Displays learning progress in a compact format:

```yaml
type: markdown
title: Learning Progress
content: |
  ## {{ state_attr('climate.living_room', 'friendly_name') }}

  **Status:** {{ state_attr('climate.living_room', 'learning_status') | title }}

  **Progress:** {{ state_attr('climate.living_room', 'cycles_collected') }} /
  {{ state_attr('climate.living_room', 'cycles_required_for_learning') }} cycles

  **Confidence:** {{ state_attr('climate.living_room', 'convergence_confidence_pct') }}%

  **Current State:** {{ state_attr('climate.living_room', 'current_cycle_state') | title }}

  {% set interrupted = state_attr('climate.living_room', 'last_cycle_interrupted') %}
  {% if interrupted %}
  ⚠️ Last cycle interrupted: {{ interrupted.replace('_', ' ') | title }}
  {% endif %}

  {% set last_adj = state_attr('climate.living_room', 'last_pid_adjustment') %}
  {% if last_adj %}
  🔧 Last PID adjustment: {{ relative_time(strptime(last_adj, '%Y-%m-%dT%H:%M:%S')) }}
  {% endif %}
```

## Progress Visualization

### 3. Progress Bar (Custom Bar Card)

Shows cycle collection progress:

```yaml
type: custom:bar-card
entities:
  - entity: climate.living_room
    attribute: cycles_collected
    name: Learning Progress
    max: 6
    positions:
      icon: inside
      name: inside
    severity:
      - color: '#f44336'
        from: 0
        to: 2
      - color: '#ff9800'
        from: 3
        to: 4
      - color: '#4caf50'
        from: 5
        to: 6
```

### 4. Gauge Card - Convergence Confidence

Visual gauge showing tuning confidence:

```yaml
type: gauge
entity: climate.living_room
name: Tuning Confidence
unit: "%"
min: 0
max: 100
severity:
  green: 80
  yellow: 50
  red: 0
attribute: convergence_confidence_pct
needle: true
```

## Status-Based Conditional Cards

### 5. Show Only When Collecting Data

Displays helpful message during initial learning phase:

```yaml
type: conditional
conditions:
  - entity: climate.living_room
    state_not: "off"
  - entity: climate.living_room
    attribute: learning_status
    state: "collecting"
card:
  type: markdown
  content: |
    # 📚 Collecting Initial Data

    Your thermostat is learning your home's heating characteristics.

    **Progress:** {{ state_attr('climate.living_room', 'cycles_collected') }}/6 cycles

    **Tips:**
    - Avoid frequent setpoint changes
    - Keep windows/doors closed during heating
    - Let cycles complete naturally

    Once 6 cycles are collected, automatic PID tuning will begin.
```

### 6. Alert When Cycle Interrupted

Shows warning when cycles are being interrupted:

```yaml
type: conditional
conditions:
  - entity: climate.living_room
    attribute: last_cycle_interrupted
    state_not: "null"
card:
  type: markdown
  content: |
    ### ⚠️ Cycle Interrupted

    **Reason:** {{ state_attr('climate.living_room', 'last_cycle_interrupted').replace('_', ' ') | title }}

    {% if state_attr('climate.living_room', 'last_cycle_interrupted') == 'setpoint_change' %}
    Frequent setpoint changes interrupt learning. Try to set your desired temperature and let the system stabilize.
    {% elif state_attr('climate.living_room', 'last_cycle_interrupted') == 'contact_sensor' %}
    A window or door was left open for too long. The system pauses heating for safety and energy efficiency.
    {% elif state_attr('climate.living_room', 'last_cycle_interrupted') == 'mode_change' %}
    The thermostat mode was changed during a cycle. This is normal if you manually adjusted it.
    {% endif %}
```

## Multi-Zone Dashboards

### 7. Multi-Zone Status Grid (Mushroom Cards)

Perfect for homes with multiple zones:

```yaml
type: grid
columns: 2
square: false
cards:
  - type: custom:mushroom-template-card
    primary: Living Room
    secondary: |
      {{ state_attr('climate.living_room', 'learning_status') | title }}
      · {{ state_attr('climate.living_room', 'cycles_collected') }}/6 cycles
    icon: mdi:school
    icon_color: |
      {% set status = state_attr('climate.living_room', 'learning_status') %}
      {% if status == 'converged' %}
        green
      {% elif status == 'active' %}
        blue
      {% elif status == 'ready' %}
        orange
      {% else %}
        grey
      {% endif %}
    tap_action:
      action: more-info

  - type: custom:mushroom-template-card
    primary: Bedroom
    secondary: |
      {{ state_attr('climate.bedroom', 'learning_status') | title }}
      · {{ state_attr('climate.bedroom', 'cycles_collected') }}/6 cycles
    icon: mdi:school
    icon_color: |
      {% set status = state_attr('climate.bedroom', 'learning_status') %}
      {% if status == 'converged' %}
        green
      {% elif status == 'active' %}
        blue
      {% elif status == 'ready' %}
        orange
      {% else %}
        grey
      {% endif %}
    tap_action:
      action: more-info

  - type: custom:mushroom-template-card
    primary: Office
    secondary: |
      {{ state_attr('climate.office', 'learning_status') | title }}
      · {{ state_attr('climate.office', 'cycles_collected') }}/6 cycles
    icon: mdi:school
    icon_color: |
      {% set status = state_attr('climate.office', 'learning_status') %}
      {% if status == 'converged' %}
        green
      {% elif status == 'active' %}
        blue
      {% elif status == 'ready' %}
        orange
      {% else %}
        grey
      {% endif %}
    tap_action:
      action: more-info
```

### 8. Multi-Zone Comparison Table

Shows all zones in a compact table:

```yaml
type: markdown
content: |
  # Thermostat Learning Status

  | Zone | Status | Cycles | Confidence | State |
  |------|--------|--------|------------|-------|
  | Living Room | {{ state_attr('climate.living_room', 'learning_status') }} | {{ state_attr('climate.living_room', 'cycles_collected') }}/6 | {{ state_attr('climate.living_room', 'convergence_confidence_pct') }}% | {{ state_attr('climate.living_room', 'current_cycle_state') }} |
  | Bedroom | {{ state_attr('climate.bedroom', 'learning_status') }} | {{ state_attr('climate.bedroom', 'cycles_collected') }}/6 | {{ state_attr('climate.bedroom', 'convergence_confidence_pct') }}% | {{ state_attr('climate.bedroom', 'current_cycle_state') }} |
  | Office | {{ state_attr('climate.office', 'learning_status') }} | {{ state_attr('climate.office', 'cycles_collected') }}/6 | {{ state_attr('climate.office', 'convergence_confidence_pct') }}% | {{ state_attr('climate.office', 'current_cycle_state') }} |
```

## Advanced Layouts

### 9. Detailed Status Card (Custom Button Card)

Rich status display with color coding:

```yaml
type: custom:button-card
entity: climate.living_room
name: Living Room Learning Status
show_state: false
styles:
  card:
    - height: 120px
  grid:
    - grid-template-areas: '"n" "status" "progress" "details"'
    - grid-template-rows: 1fr 1fr 1fr 1fr
custom_fields:
  status:
    card:
      type: markdown
      content: |
        **Status:** {{ state_attr('climate.living_room', 'learning_status') | upper }}
      style: |
        ha-card {
          box-shadow: none;
          font-size: 14px;
          text-align: center;
        }
  progress:
    card:
      type: markdown
      content: |
        {{ state_attr('climate.living_room', 'cycles_collected') }}/6 cycles ·
        {{ state_attr('climate.living_room', 'convergence_confidence_pct') }}% confidence
      style: |
        ha-card {
          box-shadow: none;
          font-size: 12px;
          text-align: center;
          color: grey;
        }
  details:
    card:
      type: markdown
      content: |
        Cycle: {{ state_attr('climate.living_room', 'current_cycle_state') }}
        {% set interrupted = state_attr('climate.living_room', 'last_cycle_interrupted') %}
        {% if interrupted %} · ⚠️ {{ interrupted }}{% endif %}
      style: |
        ha-card {
          box-shadow: none;
          font-size: 11px;
          text-align: center;
          color: grey;
        }
color: |
  [[[
    const status = entity.attributes.learning_status;
    if (status === 'converged') return 'green';
    if (status === 'active') return 'blue';
    if (status === 'ready') return 'orange';
    return 'grey';
  ]]]
```

### 10. Full Dashboard Section

Complete learning status section combining multiple cards:

```yaml
type: vertical-stack
cards:
  - type: markdown
    content: |
      # 🎓 Adaptive Learning Status

  # Progress bar
  - type: custom:bar-card
    entities:
      - entity: climate.living_room
        attribute: cycles_collected
        name: Cycles Collected
        max: 6
    severity:
      - color: red
        from: 0
        to: 2
      - color: orange
        from: 3
        to: 4
      - color: green
        from: 5
        to: 6

  # Status badges
  - type: horizontal-stack
    cards:
      - type: custom:mushroom-template-card
        primary: "{{ state_attr('climate.living_room', 'learning_status') | title }}"
        secondary: Status
        icon: mdi:progress-check
        icon_color: |
          {% set status = state_attr('climate.living_room', 'learning_status') %}
          {% if status == 'converged' %}
            green
          {% elif status == 'active' %}
            blue
          {% else %}
            orange
          {% endif %}

      - type: custom:mushroom-template-card
        primary: "{{ state_attr('climate.living_room', 'convergence_confidence_pct') }}%"
        secondary: Confidence
        icon: mdi:gauge
        icon_color: |
          {% set conf = state_attr('climate.living_room', 'convergence_confidence_pct') %}
          {% if conf >= 80 %}
            green
          {% elif conf >= 50 %}
            orange
          {% else %}
            red
          {% endif %}

      - type: custom:mushroom-template-card
        primary: "{{ state_attr('climate.living_room', 'current_cycle_state') | title }}"
        secondary: Cycle State
        icon: mdi:sync
        icon_color: |
          {% if state_attr('climate.living_room', 'current_cycle_state') == 'heating' %}
            red
          {% elif state_attr('climate.living_room', 'current_cycle_state') == 'settling' %}
            orange
          {% else %}
            grey
          {% endif %}

  # Details
  - type: entities
    entities:
      - entity: climate.living_room
        type: attribute
        attribute: last_cycle_interrupted
        name: Last Interruption
      - entity: climate.living_room
        type: attribute
        attribute: last_pid_adjustment
        name: Last PID Adjustment
```

## Automations & Notifications

### 11. Notify When Learning Complete

Send notification when thermostat reaches convergence:

```yaml
automation:
  - alias: "Thermostat Learning Complete"
    trigger:
      - platform: state
        entity_id: climate.living_room
        attribute: learning_status
        to: "converged"
    action:
      - service: notify.mobile_app_iphone
        data:
          title: "🎓 Thermostat Tuned"
          message: >
            {{ trigger.to_state.attributes.friendly_name }} has completed learning with
            {{ state_attr(trigger.entity_id, 'convergence_confidence_pct') }}% confidence
            after {{ state_attr(trigger.entity_id, 'cycles_collected') }} cycles.
          data:
            url: /lovelace/climate
            tag: "learning_complete"
```

### 12. Alert on Frequent Interruptions

Warn if cycles keep getting interrupted:

```yaml
automation:
  - alias: "Alert - Frequent Cycle Interruptions"
    trigger:
      - platform: state
        entity_id: climate.living_room
        attribute: last_cycle_interrupted
        to:
          - "setpoint_change"
          - "mode_change"
          - "contact_sensor"
    condition:
      # Only alert if we're still collecting (learning not complete)
      - condition: state
        entity_id: climate.living_room
        attribute: learning_status
        state: "collecting"
    action:
      - service: persistent_notification.create
        data:
          title: "Thermostat Learning Interrupted"
          message: >
            {{ trigger.to_state.attributes.friendly_name }}'s learning cycle was interrupted due to
            {{ state_attr(trigger.entity_id, 'last_cycle_interrupted').replace('_', ' ') }}.

            Current progress: {{ state_attr(trigger.entity_id, 'cycles_collected') }}/6 cycles.

            Tip: Let the thermostat run uninterrupted to complete learning faster.
```

### 13. Daily Learning Summary

Send daily summary of learning progress:

```yaml
automation:
  - alias: "Daily Thermostat Learning Summary"
    trigger:
      - platform: time
        at: "20:00:00"
    condition:
      # Only send if still learning (not converged)
      - condition: template
        value_template: >
          {{ state_attr('climate.living_room', 'learning_status') != 'converged' }}
    action:
      - service: notify.mobile_app_iphone
        data:
          title: "📊 Daily Learning Summary"
          message: >
            Living Room: {{ state_attr('climate.living_room', 'cycles_collected') }}/6 cycles,
            {{ state_attr('climate.living_room', 'convergence_confidence_pct') }}% confident

            Bedroom: {{ state_attr('climate.bedroom', 'cycles_collected') }}/6 cycles,
            {{ state_attr('climate.bedroom', 'convergence_confidence_pct') }}% confident
```

## Helper Templates

### 14. Template Sensors for Advanced Use

Create template sensors for easier automation:

```yaml
template:
  - sensor:
      - name: "Living Room Learning Progress"
        unique_id: living_room_learning_progress
        unit_of_measurement: "%"
        state: >
          {{ (state_attr('climate.living_room', 'cycles_collected') / 6 * 100) | round(0) }}
        icon: mdi:progress-clock

      - name: "Living Room Learning Status Icon"
        unique_id: living_room_learning_status_icon
        state: "{{ state_attr('climate.living_room', 'learning_status') }}"
        icon: >
          {% set status = state_attr('climate.living_room', 'learning_status') %}
          {% if status == 'converged' %}
            mdi:check-circle
          {% elif status == 'active' %}
            mdi:cog-sync
          {% elif status == 'ready' %}
            mdi:play-circle
          {% else %}
            mdi:dots-horizontal-circle
          {% endif %}

  - binary_sensor:
      - name: "Living Room Learning Active"
        unique_id: living_room_learning_active
        state: >
          {{ state_attr('climate.living_room', 'current_cycle_state') in ['heating', 'settling'] }}
        device_class: running
```

## Tips for Best Results

1. **Avoid frequent setpoint changes** during the collecting phase - each interruption delays learning
2. **Keep windows/doors closed** during heating cycles - contact sensor interruptions abort cycles
3. **Let cycles complete** - don't change modes or turn off the thermostat mid-cycle
4. **Monitor confidence** - values above 80% indicate excellent tuning
5. **Check interruptions** - if `last_cycle_interrupted` shows frequent issues, investigate the cause

## Color Coding Reference

Use these colors for consistent visual feedback:

| Status | Color | Meaning |
|--------|-------|---------|
| Collecting | Grey | Initial data gathering |
| Ready | Orange | Minimum data collected, ready to learn |
| Active | Blue | Actively tuning PID values |
| Converged | Green | Well-tuned, high confidence |

| Confidence | Color | Meaning |
|------------|-------|---------|
| 0-49% | Red | Low confidence, needs more cycles |
| 50-79% | Orange/Yellow | Moderate confidence, tuning in progress |
| 80-100% | Green | High confidence, excellent tuning |
