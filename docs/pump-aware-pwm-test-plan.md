# Pump-aware PWM verification plan

[English](#english) · [Deutsch](#deutsch)

## English

### Automated verification

Run the complete suite with `python -m pytest tests/ -q`, then
`ruff check custom_components/ tests/`, `ruff format --check custom_components/ tests/`
and `pyright custom_components/adaptive_climate/`.

| Regression module | Acceptance condition |
| --- | --- |
| `test_pwm_load_balancing.py` | Deterministic bounded phases, valid schema, genuine demand, signed ledger, priority, safety and bounded scheduling. |
| `test_pwm_coordination_prerequisites.py` | Serialized actuator, rejected/failed commands retain credit, stale callbacks harmless, OFF wins over an in-flight ON. |
| `test_pwm_ha_integration.py` | Real adapter boundaries, supported binary outputs, disabled path, readiness, fallback ownership and observed timing. |
| `test_pwm_callback_safety.py` | Timers/listeners/confirmation execute on the modeled HA event loop. |
| `test_pwm_restart.py` | Durable intent/checkpoints, uncertain offline delivery, partial startup, minimum times, failed storage and priority deadlines. |
| `test_pwm_ordinary_entitlement.py` | One ordinary start per epoch; no false debt from repeated PID revisions; persistence and noise replays. |
| `test_pwm_runtime_handover.py` | Repair executable handovers without moving fixed starts, changing durations or violating timing bounds; deterministic bounded planning. |
| `test_pwm_simulation.py` | Eight 24h electrical replays against the real independent PWM controller, both strategies and interactive demand. |

One late-demand regression compares executable repair with the same coordinated
runtime using nominal phases only. Over a synthetic 24h trace plus 1800s drain,
starts fall from 49 to 25 while the two zones retain exactly 30000 and 14400
observed ON seconds with zero ending credit. This isolates handover repair;
it is **not** an independent-PWM or physical-equipment baseline. Other replay
tests compare against the actual independent controller. All models distinguish
estimated electrical behavior from delivered heat and physical pump energy.

The integrated feature was reported working in one real installation. This is
qualitative field feedback, not a controlled energy study or a guarantee for
other wiring, demand traces, helper/network latency or hydraulic equipment.
Exact branch CI results are recorded in the pull request, rather than copying
test totals from historical combined releases.

### Supervised Home Assistant acceptance

Use suitable test equipment or a supervised heating installation. Back up
configuration and storage; preserve existing protective controls.

1. With coordination absent/disabled, confirm independent behavior and no group
   timers/listeners. Check the supported topology before explicitly enabling.
2. Start with outputs OFF, enable a group and restart. Verify `mode: active`,
   expected membership and no fallback reason or scheduler-thread errors.
3. At stable sub-100% demand, observe whole PWM periods. Check one ordinary start
   per zone/epoch, minimum ON/OFF times and ledger conservation. Do not mistake
   priority starts for duplicate ordinary starts.
4. Compare both strategies with equivalent demand over complete periods; include
   boundary/drain time. Inspect runtime and starts separately and account for
   remaining/cancelled/uncertain credit. Never infer savings from an incomplete
   interval or a cyclic projection alone.
5. Raise a setpoint mid-period and perform OFF→HEAT. Check immediate evaluation,
   first minimum-time-eligible response, and no fabricated low-duty pulse.
6. Exercise OFF, contact/humidity pauses and output unavailability. Ensure stale
   ON callbacks cannot resurrect demand; check explicit fallback ownership.
7. Restart with known OFF and journaled ON. Check remaining budget, ordinary
   allowance, minimum protection, pending priority and separate uncertainty.
   Simulate failed persistence only in a safe test environment: no ON may bypass
   the failed durable reservation.
8. Disable and restart; confirm independent control. For helper proxies, inspect
   actual downstream relay behavior separately from the HA helper state.

Accept only if comfort, protective limits, requested heat and accounting remain
correct. Physical flow, wear, energy and pump/heat-source starts require their
own measurements; the integration does not provide those measurements here.

## Deutsch

### Automatisierte Prüfung

Gesamtsuite, Ruff-Prüfung/Formatprüfung und Pyright mit den oben genannten
Befehlen ausführen. Die gemeinsame Tabelle ordnet alle Regressionen den
Abnahmekriterien zu: reine Planung und Schema, serialisierte Aktorbefehle,
HA-Anbindung, Event-Loop, Neustartjournal, normale Startberechtigung,
ausführbare Übergänge und acht 24-Stunden-Vergleiche mit dem unabhängigen PWM.

Ein synthetischer Spätbedarfsfall isoliert die Übergangskorrektur: 24 Stunden
plus 1800 Sekunden Nachlauf, 49 statt 25 Gruppenstarts ohne/mit Korrektur,
jeweils exakt 30000 und 14400 EIN-Sekunden bei null Restguthaben. Das ist ein
Vergleich innerhalb derselben koordinierten Laufzeitlogik, keine physische
Messung und kein Vergleich gegen unabhängiges PWM. Andere Tests verwenden
den echten unabhängigen Controller als Referenz.

Die integrierte Funktion wurde aus einer realen Installation als funktionierend
bestätigt. Diese qualitative Rückmeldung ersetzt keine Energiestudie und
beweist keine universelle Kompatibilität. Der PR dokumentiert die Prüfungen
seines getrennten Branches; historische Gesamtzahlen werden nicht übertragen.

### Überwachte HA-Abnahme

Mit Sicherung und geeigneter Überwachung zunächst deaktiviertes Verhalten
prüfen. Topologie kontrollieren, mit Ausgängen AUS aktivieren und neu starten.
Aktiven Gruppenstatus, Mitglieder und fehlende Fehlergründe prüfen. Über ganze
Perioden normale Starts, Mindestzeiten und Zeitkonten beobachten. Beide
Strategien nur bei vergleichbarem Bedarf und vollständigen Zeitfenstern samt
Nachlauf bewerten; Laufzeit und Starts getrennt auswerten.

Sollwerterhöhung und OFF→HEAT während der Periode testen: sofortige Auswertung,
frühestmögliche zulässige Reaktion, kein erfundenes Kleinstlastguthaben. OFF,
Pausen und Ausfälle müssen veraltete Einschaltungen entwerten und eindeutige
Zuständigkeit behalten. Neustarts mit AUS und journalisiertem EIN auf Budget,
Startberechtigung, Mindestzeiten, Priorität und Unsicherheit prüfen.
Speicherfehler ausschließlich sicher simulieren; fehlgeschlagene Reservierungen
dürfen nicht über unabhängiges EIN umgangen werden. Deaktivieren und Neustart
müssen unabhängigen Betrieb wiederherstellen. Bei Helpern nachgeschaltete
Relais separat beobachten.

Abnahme nur bei korrektem Komfort, Geräteschutz, Wärmebedarf und Zeitkonto.
Durchfluss, Energie, Verschleiß sowie echte Pumpen-/Wärmeerzeugerstarts
benötigen eigene Messungen.
