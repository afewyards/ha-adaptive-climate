# Auto-Apply PID Dashboard Cards

Dashboard card examples for monitoring automatic PID application status and history.

Replace `climate.living_room` with your entity ID.

## Simple Markdown Card (no custom cards needed)

```yaml
type: markdown
title: PID History
content: |
  {% set history = state_attr('climate.living_room', 'pid_history') or [] %}
  {% if history | length == 0 %}
  *No PID history yet*
  {% else %}
  | When | Kp | Ki | Kd | Reason |
  |------|---:|---:|---:|--------|
  {% for entry in history | reverse %}
  | {{ as_timestamp(entry.timestamp) | timestamp_custom('%b %d %H:%M') }} | {{ entry.kp | round(1) }} | {{ entry.ki | round(4) }} | {{ entry.kd | round(1) }} | {{ entry.reason | replace('_', ' ') | title }} |
  {% endfor %}
  {% endif %}
```

## With Mushroom + Card-mod (nicer styling)

Requires [mushroom-cards](https://github.com/piitaya/lovelace-mushroom) and [card-mod](https://github.com/thomasloven/lovelace-card-mod).

```yaml
type: custom:stack-in-card
title: PID History
cards:
  - type: custom:mushroom-chips-card
    chips:
      - type: template
        icon: mdi:history
        content: >-
          {{ state_attr('climate.living_room', 'auto_apply_count') or 0 }} auto-applies
      - type: template
        icon: >-
          {{ 'mdi:flask' if state_attr('climate.living_room', 'validation_mode') else 'mdi:check-circle' }}
        content: >-
          {{ 'Validating' if state_attr('climate.living_room', 'validation_mode') else 'Stable' }}
        icon_color: >-
          {{ 'orange' if state_attr('climate.living_room', 'validation_mode') else 'green' }}
  - type: markdown
    content: |
      {% set history = state_attr('climate.living_room', 'pid_history') or [] %}
      {% if history | length == 0 %}
      *No PID changes recorded yet*
      {% else %}
      {% for entry in history | reverse %}
      **{{ entry.reason | replace('_', ' ') | title }}** · {{ as_timestamp(entry.timestamp) | timestamp_custom('%b %d, %H:%M') }}
      `Kp={{ entry.kp | round(1) }}` `Ki={{ entry.ki | round(4) }}` `Kd={{ entry.kd | round(1) }}`
      {% if not loop.last %}---{% endif %}
      {% endfor %}
      {% endif %}
card_mod:
  style: |
    ha-card { padding: 12px; }
    ha-markdown { font-size: 14px; }
```

## Compact Entities Card

Requires [fold-entity-row](https://github.com/thomasloven/lovelace-fold-entity-row) for collapsible history section.

```yaml
type: entities
title: Auto-Apply Status
entities:
  - type: attribute
    entity: climate.living_room
    attribute: auto_apply_pid_enabled
    name: Auto-Apply Enabled
    icon: mdi:auto-fix
  - type: attribute
    entity: climate.living_room
    attribute: auto_apply_count
    name: Apply Count
    icon: mdi:counter
  - type: attribute
    entity: climate.living_room
    attribute: validation_mode
    name: Validation Mode
    icon: mdi:flask-outline
  - type: attribute
    entity: climate.living_room
    attribute: convergence_confidence_pct
    name: Confidence
    icon: mdi:percent
    suffix: "%"
  - type: custom:fold-entity-row
    head:
      type: section
      label: PID History
    entities:
      - type: custom:template-entity-row
        name: Latest
        state: >-
          {% set h = state_attr('climate.living_room', 'pid_history') %}
          {% if h %}{{ h[-1].reason | replace('_',' ') | title }} ({{ h[-1].kp | round(1) }}/{{ h[-1].ki | round(4) }}/{{ h[-1].kd | round(1) }}){% else %}None{% endif %}
```

## Full Auto-Apply Dashboard

Complete card combining status, confidence gauge, and history.

```yaml
type: vertical-stack
cards:
  - type: custom:mushroom-climate-card
    entity: climate.living_room
    show_temperature_control: true
  - type: gauge
    entity: climate.living_room
    attribute: convergence_confidence_pct
    name: Learning Confidence
    min: 0
    max: 100
    severity:
      green: 70
      yellow: 40
      red: 0
    needle: true
  - type: entities
    entities:
      - type: attribute
        entity: climate.living_room
        attribute: auto_apply_pid_enabled
        name: Auto-Apply
        icon: mdi:auto-fix
      - type: attribute
        entity: climate.living_room
        attribute: auto_apply_count
        name: Times Applied
        icon: mdi:counter
      - type: attribute
        entity: climate.living_room
        attribute: validation_mode
        name: Validating
        icon: mdi:flask
      - type: attribute
        entity: climate.living_room
        attribute: learning_status
        name: Status
        icon: mdi:school
  - type: markdown
    title: PID History
    content: |
      {% set history = state_attr('climate.living_room', 'pid_history') or [] %}
      {% if history | length == 0 %}
      *No PID history yet*
      {% else %}
      | When | Kp | Ki | Kd | Reason |
      |------|---:|---:|---:|--------|
      {% for entry in history | reverse %}
      | {{ as_timestamp(entry.timestamp) | timestamp_custom('%b %d %H:%M') }} | {{ entry.kp | round(1) }} | {{ entry.ki | round(4) }} | {{ entry.kd | round(1) }} | {{ entry.reason | replace('_', ' ') | title }} |
      {% endfor %}
      {% endif %}
```

## Available Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `auto_apply_pid_enabled` | bool | Whether auto-apply is enabled |
| `auto_apply_count` | int | Number of times PID has been auto-applied |
| `validation_mode` | bool | Currently validating new PID values |
| `pid_history` | list | Last 3 PID configs (timestamp, kp, ki, kd, reason) |
| `convergence_confidence_pct` | float | Learning confidence percentage (0-100) |
| `learning_status` | string | Current learning status |
