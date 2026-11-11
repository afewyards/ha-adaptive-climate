# Pump-aware PWM coordination

[English](#english) · [Deutsch](#deutsch)

## English

### Purpose and scope

Independent zone PWM ignores a pump shared through an existing electrical OR:
any active zone energizes it. Phase coordination can join genuine heating
pulses into longer group runs or concentrate them into a shorter energized
window. This feature schedules zone outputs; it does not command a pump relay,
change requested heat, tune PID gains or infer hydraulic flow.

Coordination is **disabled by default**. Supported groups contain 2–16 HEAT-only
PWM zones with the same positive period and one non-inverted `switch.*` or
`input_boolean.*` output per zone. Outputs must be distinct and zone membership
must be unique. Analog valves, COOL/HEAT_COOL, multiple outputs, separate demand
switches, incompatible periods or impossible minimum times are not coordinated.

### Configuration

Merge this example into the existing domain block, using your actual entity IDs.
The IDs, weights and settings below are synthetic examples, not a site layout.
Each group must correspond to one shared pump in the existing installation.

```yaml
adaptive_climate:
  pwm_coordination:
    enabled: true
    mode: load_balancing
    groups:
      - name: shared_pump
        overlap_strategy: minimal
        handover_overlap_seconds: 240
        max_deferral_seconds: 3600
        zones:
          - entity: climate.zone_a
            hydraulic_weight: 3.0
          - entity: climate.zone_b
            hydraulic_weight: 1.0
          - entity: climate.zone_c
            hydraulic_weight: 1.0
```

| Setting | Default / valid values | Meaning |
| --- | --- | --- |
| `enabled` | `false`, Boolean | Explicitly enable coordination. |
| `mode` | `load_balancing` only | Group scheduling mode. |
| `groups[].name` | Required, unique, nonempty | Stable group/journal identity. |
| `overlap_strategy` | `minimal` or `maximal`; default `minimal` | Phase objective described below. |
| `handover_overlap_seconds` | 240; finite 0–1800 | Desired electrical handover overlap within genuine demand. |
| `max_deferral_seconds` | 3600; finite 0–7200 | Bound for ordinary scheduling; not an urgent-demand delay. |
| `zones` | 2–16 explicit climate IDs | Unique across all groups. |
| `hydraulic_weight` | 1; finite, strictly positive | Relative preference for solo time; not measured flow. |

Unknown options, invalid IDs, duplicate membership and nonfinite numbers are
rejected. Actual controllers, periods, outputs and readiness are checked at
runtime. Configuration changes require a Home Assistant restart.

### Two different objectives

`minimal` spreads ordinary pulses into a contiguous chain with short handovers,
favoring solo time for the highest-weight zone and assigning unavoidable longer
overlaps to smaller zones. It can reduce repeated starts but can energize the
pump longer than concentrated scheduling. Genuine demand over 100% can make
longer overlap unavoidable.

`maximal` aligns ordinary starts to concentrate the same ON time into a shorter
group run. This favors shorter energized runtime; it can increase starts versus
an already continuous workload. Neither strategy guarantees both fewer starts
and less runtime for every demand trace. No heat is invented to fill a gap.

The cyclic planner supplies advisory phases. The executable `minimal` plan uses
remaining signed credit, actual active intervals, minimum ON/OFF constraints,
ordinary deadlines and advisory next-period opportunities. Active and priority
starts stay fixed. A forecast does not grant future heat or guarantee continuity.

### Demand, safety and accounting

Eligible setpoint increases and OFF→HEAT are evaluated immediately rather than
waiting for a coordinated slot. Existing minimum switching times, availability,
pauses and genuine demand still apply. Low duty must earn its normal accumulator
credit; priority cannot manufacture a minimum pulse. Direct 100% demand remains
continuous while eligible.

Ordinary pulses have at most one OFF→ON start per zone and stable group-origin
epoch. PID noise, minimum-OFF expiry and replanning cannot reopen a completed
ordinary allocation. Deliberate effective target/mode changes retain priority.
Demand may revise an active pulse; closed allocations settle revisions without
ratcheting invented debt. Observed ON seconds pay the signed delivery ledger.
Actual overrun offsets later demand; cancelled credit is reported separately.

Commands go through `HeaterController` using a serialized, result-bearing helper
with bounded confirmation, generation checks and cancellation reconciliation.
Rejected/failed commands do not consume delivery credit or emit successful
heating-start events. Timers/listeners run on HA's event loop; queued stale
callbacks are invalidated. Each active group owns one next-event timer.

Durable journals use the existing HA Store, with write-ahead ON intent and
post-confirmation checkpoints. They retain budgets, ordinary-start allowance,
low-duty credit, known output edges and priority/deadline state. Unknown offline
delivery is reported as uncertain, not claimed as measured heat. Failed durable
reservation blocks coordinated ON and requests safety OFF through the controller.
Partial startup retains unbound journal rows. On first upgrade there is no past
journal; startup ON with unknown elapsed time falls back conservatively.

Unconfigured/disabled zones use independent control. Unsupported or unavailable
groups release scheduling ownership with a diagnostic reason; fallback is
latched until restart. Storage/reconciliation safety failures are treated more
strictly and must not bypass the failed reservation through an independent ON.
Mode OFF and safety pauses never become filler heat.

An `input_boolean` confirms helper state, not a downstream relay, network,
valve or pump. Even a switch state is an electrical-state estimate. Pump starts,
energized seconds and overlap are **estimates from output states**, not measured
flow, energy savings, valve position or compressor starts. The existing pump
wiring and any downstream device protection remain authoritative.

### Diagnostics and rollout

The climate `pwm_coordination` attribute reports group mode/fallback reason,
strategy, estimated starts/runtime/weighted overlap, requested and observed
seconds, cancelled/uncertain balances, remaining credit, ordinary epochs and
suppression counts, priority latency/blocks, next transition and `runtime_*`
plan fields. Cyclic projections and executable forecasts describe different
horizons; neither is a hardware measurement.

1. Back up integration, configuration and learning storage; preserve the
   existing domain and installation method.
2. Check topology, periods and minimum times. Begin the first monitored enable
   with participating thermostats and outputs OFF, then restart HA.
3. Resume HEAT. Verify active group mode, minimum times, accounting and fallback.
4. Exercise setpoint increase, OFF→HEAT, pauses, output unavailability and a
   restart with a trustworthy journal. Observe actual equipment separately.
5. To disable, remove the block or set `enabled: false` and restart. Preserve
   storage backups for a code rollback; do not reset learned PID data.

See [the test plan](pump-aware-pwm-test-plan.md) for automated coverage and
supervised acceptance criteria. No optimizer package or additional dependency
is required. Independent learning reliability fixes are submitted separately.

## Deutsch

### Zweck und Konfiguration

Unabhängige PWM-Zonen berücksichtigen eine gemeinsam über elektrisches ODER
geschaltete Pumpe nicht. Die Koordination verbindet echte Heizimpulse zu
längeren Gruppenläufen oder konzentriert sie in ein kürzeres Zeitfenster.
Sie steuert Zonenzeiten, verändert weder angeforderte Wärme noch PID-Werte
und misst weder Durchfluss noch Pumpenleistung.

Die Funktion ist standardmäßig deaktiviert. Unterstützt werden 2–16 reine
HEAT-PWM-Zonen mit gleicher positiver Periode und je einem unterschiedlichen,
nicht invertierten `switch.*`- oder `input_boolean.*`-Ausgang. Analoge Ventile,
Kühlung/Mischbetrieb, mehrere Ausgänge, separate Bedarfsschalter und
unvereinbare Mindestzeiten werden nicht koordiniert.

Das obige YAML enthält ausschließlich synthetische Beispiele. In den
vorhandenen Domain-Block übernehmen und echte Entity-IDs einsetzen. Eine
Gruppe muss einer gemeinsam geschalteten Pumpe entsprechen. Die gemeinsame
Tabelle oben nennt alle Standardwerte und Grenzen. Gewichte sind relative
Prioritäten für Alleinlauf, keine gemessenen Durchflüsse. Ungültige Optionen,
Mehrfachzuordnungen und nicht endliche Werte werden abgelehnt. Änderungen
der Konfiguration erfordern einen HA-Neustart.

### Strategien und Grenzen

`minimal` verteilt normale Impulse zu einer zusammenhängenden Kette mit kurzen
Übergängen. Die höchstgewichtete Zone erhält bevorzugt Alleinlauf; längere
unvermeidliche Überlappung liegt bevorzugt bei kleineren Zonen. Das kann Starts
reduzieren, benötigt aber gegebenenfalls mehr Pumpenlaufzeit als konzentriertes
Schalten. Bei mehr als 100% Summenbedarf ist Überlappung teilweise unvermeidlich.

`maximal` legt normale Starts zusammen und konzentriert dieselbe Einschaltzeit
in einen kürzeren Gruppenlauf. Gegenüber bereits durchgehendem Betrieb können
dadurch mehr Starts entstehen. Keine Strategie garantiert für jeden Verlauf
gleichzeitig weniger Starts und kürzere Laufzeit. Es wird keine Wärme erfunden,
um Lücken zu schließen.

Der ausführbare Minimalplan berücksichtigt Restguthaben, aktive Intervalle,
Mindestzeiten, Fristen und unverbindliche Chancen der nächsten Periode. Aktive
und priorisierte Starts bleiben fest. Eine Prognose bewilligt keine künftige
Wärme und garantiert keinen lückenlosen Betrieb.

### Bedarf, Sicherheit und Persistenz

Sollwerterhöhung und OFF→HEAT werden sofort ausgewertet; Mindestzeiten,
Pausen, Verfügbarkeit und echter Bedarf gelten weiterhin. Kleine Leistungen
müssen das vorhandene Akkumulatorguthaben verdienen. Normaler Bedarf erlaubt
höchstens einen OFF→ON-Start pro Zone und stabiler Gruppenperiode. PID-Rauschen,
Neuplanung oder abgelaufene Mindest-AUS-Zeit öffnen keinen zweiten normalen
Impuls. Bewusste Ziel-/Modusänderungen behalten Priorität; direkter Vollbedarf
bleibt bei erfüllten Bedingungen eingeschaltet.

Gemeldete EIN-Sekunden begleichen das vorzeichenbehaftete Zeitkonto. Wirkliche
Überläufe mindern späteren Bedarf; zurückgezogene und unsichere Zeit wird
getrennt ausgewiesen. Alle Befehle laufen serialisiert mit Ergebnis,
Bestätigung und Versionsprüfung durch `HeaterController`. Abgelehnte Befehle
verbrauchen kein Guthaben und erzeugen keine erfolgreichen Heizstart-Ereignisse.
Veraltete Timer werden entwertet; jede Gruppe hat einen nächsten Ereignistimer.

Das Journal im vorhandenen HA Store sichert Budgets, Startberechtigungen,
Kleinstlastguthaben, bekannte Ausgangsflanken und Prioritätsfristen. Vor EIN
wird die Absicht gespeichert, nach Bestätigung erneut gesichert. Unbekannte
Offline-Lieferung zählt nicht als gemessene Wärme. Speicherfehler sperren
koordiniertes EIN und fordern Sicherheits-AUS über den Controller an.
Beim ersten Upgrade ohne Journal führt unbekanntes bereits aktives EIN
konservativ zum Rückfall.

Deaktivierte/nicht konfigurierte Zonen behalten unabhängige Steuerung.
Ungeeignete oder ausgefallene Gruppen geben die Planung mit Diagnosegrund ab;
der Rückfall bleibt bis zum Neustart bestehen. Speicher-/Abgleichfehler dürfen
nicht über unabhängiges EIN umgangen werden. OFF und Sicherheitspausen werden
niemals mit zusätzlichem Heizen überbrückt.

Ein Helper bestätigt nur seinen eigenen HA-Zustand. Auch Switch-Zustände sind
keine Messung von Ventilposition, Durchfluss oder Pumpen-/Kompressorstarts.
Angezeigte Starts, Laufzeit und Überlappung sind Schätzungen aus Zonen-Ausgängen.
Bestehende Verdrahtung und Geräteschutz bleiben maßgeblich.

### Inbetriebnahme und Diagnose

Vorhandene Installation, Konfiguration und Lernspeicher sichern. Topologie,
Perioden und Mindestzeiten prüfen. Erstes Aktivieren überwacht mit zuvor
ausgeschalteten Zonen/Ausgängen und anschließendem Neustart durchführen. Dann
HEAT einschalten und Gruppenstatus, Zeitkonten, Mindestzeiten, Prioritätswechsel,
Pausen, Ausfälle und Journal-Neustart beobachten; echte Geräte separat prüfen.
Zum Deaktivieren Block entfernen oder `enabled: false` setzen und neu starten.
Bei Code-Rollback Sicherung behalten; keine Lerndaten löschen.

Das Attribut `pwm_coordination` enthält unter anderem Status/Rückfallgrund,
Zeitkonten, Schätzwerte, Startberechtigungen, Prioritätslatenz und `runtime_*`-
Planwerte. Zyklische Projektion und ausführbarer Plan haben unterschiedliche
Zeithorizonte. Der [Testplan](pump-aware-pwm-test-plan.md#deutsch) nennt
automatisierte Nachweise und Abnahmekriterien. Keine neue Optimierungsbibliothek
ist erforderlich. Allgemeine Lernkorrekturen werden separat eingereicht.
