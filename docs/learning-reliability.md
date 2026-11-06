# Learning reliability and diagnostics

[English](#english) · [Deutsch](#deutsch)

## English

### Problem and behavior

Heating relay cycles and completed learning cycles measure different things.
Restoring an old `cycle_active` flag into a newly idle cycle tracker can prevent
the next real heating session from emitting `CYCLE_STARTED`. Restart now clears
only transient session/demand flags; saved gains, integral, completed learner
cycles, confidence and actuator wear counts remain available.

For PWM, zero and small positive demand below the existing 2% threshold now
share one continuous two-period settling deadline. For example,
`0 → 0.1 → 0 → 1.5%` no longer keeps moving that deadline. Demand reaching 2%
still cancels it. Forced OFF ends a transient session even if its relay is
already OFF. There is no new maximum session duration or change to PID terms.
Repeated maintenance demand at or above 2% remains outside this fix.

`run_learning`, PID recommendation services and daily analysis use the HEAT
gains manager as the source of current gains. An absent manager is reported
instead of substituting plausible-looking defaults. Diagnostics distinguish
missing cycles, cooldown gates, unusable cycles, convergence and filtered rules.
Displaying/calculating recommendations does not consume cooldown or cycle
credit. Only successful gain application commits the adjustment timestamp and
resets the counter, after the existing intentional history reset. Failed gain
application retains this credit.

Learner payload format 12 persists `cycles_since_last_adjustment`. Format 11
and older payloads without it migrate conservatively to zero once. This does
not erase existing cycle history or confidence, and no manual reset is needed.
Malformed counters are sanitized without discarding stored cycles. Older code
cannot preserve the new counter on a subsequent save; retain the usual backup
before upgrading or rolling back.

Parallel zone setup now shares one initialization lock. Storage, manifold and
water-temperature restoration finish before the shared store is published and
the learning-window number is requested once. Failure/cancellation releases
the lock without exposing incomplete storage, so later setup may retry.

### Verification and regression plan

Run `python -m pytest tests/test_learning_hotfixes.py tests/test_learning_startup.py -q`.
The tests use synthetic entities, gains and deterministic clocks. They cover:

- Stale restart flags while retaining wear counters, gains and integral.
- One uninterrupted low-demand timer, forced OFF and a subsequent new session.
- Repeated recommendation reads, real gains, explicit gates and daily analysis.
- Successful/failed manual and automatic application, counter persistence,
  old-format migration and malformed payloads.
- Concurrent storage/manifold/water-temperature loading, failure/retry and
  cancellation with waiting zones; one shared store and number discovery.

Run the full suite, Ruff check/format and Pyright before merging. During a
supervised HA check, preserve learning storage; restart multiple zones, verify
one learning-window entity, then inspect recommendation responses twice without
applying gains. Neither read should change the last adjustment time/counter.
Observe real session start/settling events independently of relay pulse counts.
Do not expect completed cycles while valid maintenance demand stays above 2%.

This fix needs no YAML change, pump coordination, new dependency, version bump,
standalone patch script or learning-data reset.

## Deutsch

### Problem und Verhalten

Relais-Schaltzyklen und abgeschlossene Lernzyklen sind unterschiedliche Größen.
Ein aus der alten Sitzung wiederhergestelltes `cycle_active` kann den neuen,
zunächst inaktiven Lerntracker blockieren. Beim Neustart werden deshalb nur
vorübergehende Sitzungs-/Bedarfsflags verworfen. PID-Werte, Integral,
abgeschlossene Lernzyklen, Konfidenz und Verschleißzähler bleiben erhalten.

Null und kleiner positiver PWM-Bedarf unter der bestehenden Zwei-Prozent-
Schwelle teilen sich nun eine durchgehende Beruhigungsfrist von zwei Perioden.
Wechsel wie `0 → 0,1 → 0 → 1,5%` verschieben diese Frist nicht mehr. Ab 2%
wird sie weiterhin abgebrochen. Erzwungenes OFF beendet die Sitzung auch bei
bereits ausgeschaltetem Relais. Eine neue maximale Sitzungsdauer oder andere
PID-Terme sind nicht enthalten. Wiederkehrender Maintenance-Bedarf ab 2%
kann einen Abschluss weiterhin verhindern.

Lerndienste und tägliche Analyse lesen die wirksamen HEAT-PID-Werte aus dem
Gains Manager. Fehlende Werte werden ausdrücklich gemeldet. Diagnosen
unterscheiden fehlende/verwendbare Zyklen, Cooldown, Konvergenz und gefilterte
Regeln. Empfehlungen anzusehen verbraucht weder Wartezeit noch Zyklenguthaben.
Erst erfolgreiches Anwenden setzt Zeitpunkt und Zähler nach dem vorhandenen
bewussten Historien-Reset zurück. Fehlgeschlagenes Anwenden verbraucht das
Guthaben nicht.

Lernerformat 12 speichert `cycles_since_last_adjustment`. Ältere Daten ohne
diesen Zähler beginnen einmal konservativ bei null. Historie und Konfidenz
bleiben erhalten; fehlerhafte Zähler löschen keine Zyklen. Kein manueller Reset
ist erforderlich. Vor Upgrade/Rollback wie üblich sichern: ältere Software
bewahrt den neuen Zähler beim Speichern nicht.

Parallele Zonenstarts verwenden ein gemeinsames Initialisierungs-Lock. Erst
nach Laden von Speicher, Verteiler- und Wassertemperaturzustand wird der Store
veröffentlicht und die globale Lernfenster-Entity einmal angefordert. Fehler
und Abbruch geben das Lock frei; ein späterer Start darf erneut versuchen.

### Prüfung

Der oben angegebene Pytest-Aufruf prüft mit synthetischen Daten die
Neustartflags, Niedrigbedarfsfrist, OFF, echten PID-Werte, wiederholten
Empfehlungen, manuelle/automatische Anwendung, Migration sowie parallelen
Start mit Fehlern und Abbruch. Vor Übernahme zusätzlich Gesamtsuite, Ruff und
Pyright ausführen. In HA ohne Löschen der Lerndaten mehrere Zonen neu starten,
eine Lernfenster-Entity kontrollieren und Empfehlungen zweimal ohne Anwenden
aufrufen. Anpassungszeit und Zähler dürfen sich dadurch nicht ändern.
Lernsitzungen getrennt von Relaisimpulsen beobachten; oberhalb 2% ist kein
erzwungener Abschluss vorgesehen.

Keine YAML-Änderung, Pumpenkoordination, zusätzliche Abhängigkeit,
Versionsanhebung oder separates Patch-Skript ist erforderlich.
