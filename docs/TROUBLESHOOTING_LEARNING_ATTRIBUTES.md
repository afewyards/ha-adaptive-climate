# Troubleshooting Learning Status Attributes

If the learning status attributes aren't appearing on your climate entity, follow these steps:

## Step 1: Check if Attributes Exist

In Home Assistant, go to **Developer Tools** → **States** and search for your climate entity (e.g., `climate.gf`).

Look for these attributes in the attribute list:
- `learning_status`
- `cycles_collected`
- `cycles_required_for_learning`
- `convergence_confidence_pct`
- `current_cycle_state`
- `last_cycle_interrupted`
- `last_pid_adjustment`

**If these attributes are missing**, continue to Step 2.

**If they show as "none" or "null"**, that's normal for a new installation - they'll populate as the system runs.

## Step 2: Verify Zone Registration

The attributes only appear if the zone is properly registered with the coordinator.

Check your logs for messages like:
```
CycleTrackerManager initialized for zone <zone_id>
```

If you don't see this, the zone might not be registered.

## Step 3: Check Configuration

Verify your climate entity has the required configuration in `configuration.yaml`:

```yaml
climate:
  - platform: adaptive_thermostat
    name: "Ground Floor"
    # ... other config ...
```

## Step 4: Restart Home Assistant

After upgrading, you need to **fully restart Home Assistant** (not just reload integrations):

1. Go to **Settings** → **System** → **Restart**
2. Click **Restart Home Assistant**
3. Wait for restart to complete

## Step 5: Check Coordinator Setup

The attributes are only added if:
1. The coordinator exists in HASS data
2. The zone is registered in the coordinator
3. Both `adaptive_learner` and `cycle_tracker` are set for the zone

To verify, check logs for:
```
AdaptiveThermostatCoordinator: Zone <zone_id> registered
```

## Step 6: Verify Entity ID Matching

The code matches zones by `climate_entity_id` in the coordinator. Make sure:
- Your entity ID is correct (e.g., `climate.gf` not `climate.ground_floor`)
- The entity is properly initialized

## Quick Test Card

Try this simple card to test if attributes exist:

```yaml
type: entities
entities:
  - entity: climate.gf
  - entity: climate.gf
    type: attribute
    attribute: learning_status
  - entity: climate.gf
    type: attribute
    attribute: cycles_collected
```

If the attribute rows show "Unknown attribute" or are blank, the attributes aren't being set.

## Common Issues

### Issue 1: Old Version Still Running

**Symptom:** Attributes don't exist after update

**Solution:**
1. Clear browser cache (Ctrl+F5)
2. Restart Home Assistant completely
3. Check you're running the latest commit

### Issue 2: Zone Not Registered

**Symptom:** Other zones work, but one specific zone doesn't

**Solution:**
- Check the zone's configuration is valid
- Look for error messages in logs about that specific zone
- Verify the zone's heater/cooler entities exist and are accessible

### Issue 3: Coordinator Not Initialized

**Symptom:** No attributes on any zones

**Solution:**
- Check for errors during startup related to `adaptive_thermostat` domain
- Verify you have `adaptive_thermostat:` in your `configuration.yaml`
- Check if domain-level configuration is required (main heater, etc.)

## Debug Mode

Enable debug logging to see what's happening:

```yaml
logger:
  default: info
  logs:
    custom_components.adaptive_thermostat: debug
    custom_components.adaptive_thermostat.managers.state_attributes: debug
    custom_components.adaptive_thermostat.managers.cycle_tracker: debug
    custom_components.adaptive_thermostat.adaptive.learning: debug
```

After adding this, restart Home Assistant and check `home-assistant.log` for messages.

## What to Look For in Logs

**Good signs:**
```
CycleTrackerManager initialized for zone gf
AdaptiveThermostatCoordinator: Zone gf registered
```

**Bad signs:**
```
KeyError: 'adaptive_learner'
KeyError: 'cycle_tracker'
AttributeError: ...
```

If you see errors, please share them for further help.

## Manual Verification Script

You can also check via Home Assistant's Python console (Developer Tools → Template):

```jinja2
{{ state_attr('climate.gf', 'learning_status') }}
{{ state_attr('climate.gf', 'cycles_collected') }}
{{ state_attr('climate.gf', 'convergence_confidence_pct') }}
```

If these return empty/null, the attributes aren't set.

## Last Resort: Check Code Version

Verify you have the latest code by checking the file:

```bash
# On HAOS, access via SSH
cat /config/custom_components/adaptive_thermostat/managers/state_attributes.py | grep "ATTR_LEARNING_STATUS"
```

You should see:
```python
ATTR_LEARNING_STATUS = "learning_status"
```

If not found, the code didn't update properly.
