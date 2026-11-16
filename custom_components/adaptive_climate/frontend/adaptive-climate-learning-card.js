(() => {
const TAG = 'adaptive-climate-learning-card';
const DASH = '–';
const WIDE_FROM = 520;
const RATE_FLOOR = 0.75;
const SCALE = { rate: 2, overshoot: 0.5, settling: 120 };
const RATE_HEADROOM = 1.25;
const PHYSICS_FLAG = 1.5;
const SHOWN_CHANGES = 3;
const GAINS = [['kp', 'Kp'], ['ki', 'Ki'], ['kd', 'Kd']];
const FAILS = [
  ['undershoot', 'ended too low'],
  ['overshoot', 'overshot'],
  ['settling', 'settled too slowly'],
  ['oscillation', 'swung too much'],
  ['rise_time', 'rose too slowly'],
];
const REASONS = {
  undershoot_ki_boost: 'undershoot boost',
  auto_apply: 'auto-tune',
  rollback: 'rolled back',
  physics_reset: 'reset to physics',
  physics_init: 'set from physics',
  restore: 'restored at start',
  manual_apply: 'applied by hand',
};
const TUNE_BLOCKED = {
  lifetime_limit: 'lifetime tuning limit reached',
  seasonal_limit: 'seasonal tuning limit reached',
  drift_limit: 'drift limit reached',
  seasonal_shift: 'seasonal shift detected',
  disabled: 'auto-tuning is disabled',
  no_room_size: 'no room size set',
  no_baseline: 'no starting values yet',
};
const LAST_CHECK = {
  converged: 'the room is already on target',
  no_rules_triggered: 'nothing to adjust',
  no_change: 'the gains would not change',
  insufficient_usable_data: 'too few usable cycles',
};
const LAST_HIDDEN = ['rate_limited', 'insufficient_data', 'gates_not_passed'];

const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const clamp01 = (v) => Math.min(Math.max(v, 0), 1);
const cycles = (n) => `${n} ${n === 1 ? 'cycle' : 'cycles'}`;
const trim = (v, d) => String(Math.round(v * 10 ** d) / 10 ** d);
const words = (s) => String(s).replace(/_/g, ' ');
let LOCALE = {};
const day = (iso) => {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  try {
    return d.toLocaleDateString(LOCALE.language, { day: 'numeric', month: 'short', timeZone: LOCALE.timeZone });
  } catch (e) {
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  }
};
const gain = (v, k) => (k === 'ki' && v < 1 ? v.toPrecision(3) : v.toFixed(2));

function bar(key, label, value, ref, scale, unit, d, isOff, severity, say) {
  const v = num(value);
  const r = num(ref) !== null && ref > 0 ? ref : null;
  const off = v !== null && r !== null && isOff(v, r);
  return {
    key,
    label,
    text: v === null ? DASH : trim(v, d) + unit,
    frac: v === null ? 0 : clamp01(v / scale),
    tick: r === null ? null : clamp01(r / scale),
    off,
    severity: off ? severity(v, r) : 0,
    say: off ? say(v, r) : '',
    sr: r === null ? '' : key === 'rate'
      ? `, expected ${trim(r, d)}${unit}, ${off ? 'below expected' : 'as expected'}`
      : `, limit ${trim(r, d)}${unit}, ${off ? 'over the limit' : 'within the limit'}`,
  };
}

function measures(a, rate = SCALE.rate) {
  const rows = [
    bar('undershoot', 'undershoot', a.undershoot, a.undershoot_limit, SCALE.overshoot, '°', 2, (v, r) => v > r,
      (v, r) => v / r, (v) => `Ends ${trim(v, 1)}° below target`),
    bar('rate', 'heats at', a.heating_rate, a.heating_rate_expected, rate, '°/h', 1,
      (v, r) => Math.round((v / r) * 100) < RATE_FLOOR * 100, (v, r) => (RATE_FLOOR * r) / v,
      (v, r) => `Heats at ${Math.round((v / r) * 100)}% of the expected rate`),
    bar('overshoot', 'overshoot', a.overshoot, a.overshoot_limit, SCALE.overshoot, '°', 2, (v, r) => v > r,
      (v, r) => v / r, (v) => `Overshoots by ${trim(v, 2)}°`),
    bar('settling', 'settles in', a.settling_time, a.settling_time_limit, SCALE.settling, ' min', 0, (v, r) => v > r,
      (v, r) => v / r, (v) => `Takes ${trim(v, 0)} min to settle`),
  ];
  return [...rows.filter((r) => r.off), ...rows.filter((r) => !r.off)];
}

const hasMeasurement = (a) => ['undershoot', 'heating_rate', 'overshoot', 'settling_time'].some((k) => num(a[k]) !== null);

const gainsOf = (h) => GAINS.map(([k]) => num(h && h[k]));

function timeline(a, history) {
  const hist = Array.isArray(history) ? history : [];
  const entries = [];
  let prev = null;
  hist.forEach((h) => {
    const now = gainsOf(h);
    if (now.some((g) => g === null)) return;
    const was = prev;
    prev = now;
    const shown = GAINS.map(([k, label], i) => {
      const to = gain(now[i], k);
      if (!was) return `${label} ${to}`;
      const from = gain(was[i], k);
      return from === to ? null : `${label} ${from} → ${to}`;
    }).filter(Boolean);
    if (!shown.length) return;
    const reason = typeof h.reason === 'string' ? h.reason : '';
    entries.push({ date: day(h.timestamp), text: shown.join(', '), why: REASONS[reason] || words(reason) });
  });
  const t = a.tune;
  const last = t && (t.state === 'ready' || t.state === 'waiting') && typeof t.last === 'string' && t.last && !LAST_HIDDEN.includes(t.last)
    ? `Last check: ${LAST_CHECK[t.last] || words(t.last)}` : null;
  return { next: tuneText(t), last, entries: entries.reverse() };
}

function tuneText(t) {
  if (!t || typeof t !== 'object') return null;
  if (t.state === 'ready') return 'Tune check at the next cycle';
  if (t.state === 'waiting') {
    if (num(t.cycles_left) > 0) {
      const more = `Tune check after ${t.cycles_left} more ${t.cycles_left === 1 ? 'cycle' : 'cycles'}`;
      return num(t.hours_left) > 0 ? `${more}, not before ${t.hours_left} h` : more;
    }
    if (num(t.hours_left) > 0) return `Tune check in ${t.hours_left} h`;
    return 'Tune check waiting';
  }
  if (t.state === 'checking') return `Tune check running, ${num(t.done) ?? 0} of ${num(t.of) ?? '?'} cycles`;
  if (t.state === 'blocked' || t.state === 'off') return `No tuning: ${TUNE_BLOCKED[t.reason] || words(t.reason || 'unknown')}`;
  return null;
}

function sentence(a, history) {
  if (a.tune && a.tune.state === 'checking') {
    const tuned = (Array.isArray(history) ? history : []).filter((h) => h && h.reason === 'auto_apply').pop();
    const when = tuned && day(tuned.timestamp);
    return `Checking a tune${when ? ` from ${when}` : ''}`;
  }
  const worst = measures(a).filter((m) => m.off).sort((x, y) => y.severity - x.severity)[0];
  if (worst) return worst.say;
  return hasMeasurement(a) ? 'On track' : 'Still collecting';
}

function factsLine(a) {
  const status = typeof a.status === 'string' && a.status ? a.status[0].toUpperCase() + a.status.slice(1) : '';
  const head = [status, num(a.cycles) !== null ? cycles(a.cycles) : ''].filter(Boolean).join(', ');
  let tail = '';
  if (typeof a.recent === 'string' && a.recent) {
    const good = [...a.recent].filter((c) => c === 'g').length;
    const fails = a.recent_fails && typeof a.recent_fails === 'object' ? a.recent_fails : {};
    let top = null;
    FAILS.forEach(([k, text]) => {
      if (num(fails[k]) > 0 && (!top || fails[k] > top.n)) top = { n: fails[k], text };
    });
    tail = `${good} of the last ${a.recent.length} were good${top ? `; ${top.n} ${top.text}` : ''}`;
  }
  return [head, tail].filter(Boolean).map((s) => `${s}.`).join(' ');
}

function holds(a) {
  const out = [];
  if (a.ki_boost_capped === true) out.push('Ki boost is at its cap.');
  if (a.tune && a.tune.state === 'checking') out.push('No Ki boost while the check runs.');
  if (num(a.setback_limit) === 0) out.push('Night setback off for now.');
  else if (num(a.setback_limit) !== null) {
    out.push(`Night setback held at ${trim(a.setback_limit, 1)}°${num(a.setback_until) !== null ? ` until ${a.setback_until}%` : ''}.`);
  }
  return out.join(' ');
}

function gains(a) {
  const now = a.gains && typeof a.gains === 'object' ? a.gains : null;
  const phys = a.gains_physics && typeof a.gains_physics === 'object' ? a.gains_physics : {};
  if (!now) return [];
  return GAINS.filter(([k]) => num(now[k]) !== null).map(([k, label]) => {
    const ratio = num(phys[k]) > 0 ? now[k] / phys[k] : null;
    const flag = ratio !== null && (ratio >= PHYSICS_FLAG || ratio <= 1 / PHYSICS_FLAG) ? `${trim(ratio, 1)}× physics` : '';
    return { label, text: gain(now[k], k), flag };
  });
}

function rateScale(objs) {
  const expected = objs.map((o) => num(o && o.attributes && o.attributes.learning && o.attributes.learning.heating_rate_expected) || 0);
  return Math.max(SCALE.rate, RATE_HEADROOM * Math.max(0, ...expected));
}

function zoneProfile(id, stateObj, rate = SCALE.rate) {
  const learning = stateObj && stateObj.attributes && stateObj.attributes.learning;
  if (!stateObj || stateObj.state === 'unavailable' || !learning || typeof learning !== 'object') {
    return { id, name: id, confidence: null, available: false, say: 'Not available', bad: false, bars: [] };
  }
  const a = learning;
  const ph = stateObj.attributes.pid_history;
  const history = ph && Array.isArray(ph.heating) ? ph.heating : [];
  const bars = measures(a, rate);
  return {
    id,
    name: (stateObj.attributes.friendly_name || id) + '',
    confidence: num(a.confidence),
    available: true,
    say: sentence(a, history),
    bad: bars.some((b) => b.off),
    strip: typeof a.recent === 'string' ? a.recent.slice(-20) : '',
    facts: factsLine(a),
    bars,
    gains: gains(a),
    log: timeline(a, history),
    holds: holds(a),
  };
}

const offCount = (z) => z.bars.filter((b) => b.off).length;

function attention(zones) {
  const live = zones.filter((z) => z.available);
  const n = live.filter((z) => z.bad).length;
  if (!live.length) return '';
  if (!n) return 'All rooms on track';
  return `${n} of ${live.length} ${live.length === 1 ? 'room needs' : 'rooms need'} attention`;
}

function sortZones(zones) {
  return [...zones].sort(
    (x, y) => offCount(y) - offCount(x) || (x.confidence ?? 0) - (y.confidence ?? 0) || x.name.localeCompare(y.name),
  );
}

const STYLE = `
:host { display: block; }
.wrap { container-type: inline-size; padding: 16px 16px 10px; color: var(--primary-text-color); font-variant-numeric: tabular-nums; --tick: var(--secondary-text-color); }
@supports (color: color-mix(in srgb, red, blue)) {
  .wrap { --tick: color-mix(in srgb, var(--secondary-text-color) 60%, var(--divider-color)); }
}
.top { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; margin-bottom: 8px; }
.top b { font-size: 16px; font-weight: 500; }
.top span, .empty { font-size: 12.5px; color: var(--secondary-text-color); }
.empty { padding: 10px 0; font-size: 14px; }
.room { border-top: 1px solid var(--divider-color); }
.row { all: unset; box-sizing: border-box; width: 100%; cursor: pointer; padding: 10px 0;
  display: grid; grid-template-columns: minmax(0, 1fr) auto 44px; gap: 1px 12px; align-items: center; }
.row.na { cursor: default; }
.row:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; border-radius: 4px; }
.name { font-weight: 500; grid-column: 1; overflow-wrap: anywhere; }
.say { grid-column: 1; grid-row: 2; font-size: 12.5px; color: var(--secondary-text-color); }
.bad .say { color: var(--primary-text-color); }
.bad .say::before { content: ""; display: inline-block; width: 7px; height: 7px; border-radius: 50%;
  background: var(--warning-color); margin-right: 6px; vertical-align: 1px; }
.strip { grid-column: 2; grid-row: 1 / 3; display: flex; align-items: flex-end; gap: 2px; height: 16px; }
.strip i { width: 3px; height: 16px; border-radius: 1px; background: var(--tick); }
.strip i.p { height: 6px; background: var(--warning-color); }
.pct { grid-column: 3; grid-row: 1 / 3; text-align: right; font-size: 15px; font-weight: 500; }
.more { display: none; padding: 2px 0 16px; }
.room.open .more { display: block; }
.room.open .row { padding-bottom: 6px; }
.facts { font-size: 12.5px; color: var(--secondary-text-color); margin: 0 0 12px; }
.m { display: grid; grid-template-columns: 82px 62px minmax(0, 1fr); gap: 7px 10px; align-items: center; font-size: 13px; }
.m .k { color: var(--secondary-text-color); }
.m .off { font-weight: 600; }
.track { position: relative; height: 5px; border-radius: 3px; background: var(--divider-color); }
.track b { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 3px; background: var(--tick); }
.track.off b { background: var(--warning-color); }
.track u { position: absolute; top: -4px; width: 2px; height: 13px; background: var(--primary-color); }
.gains { display: flex; gap: 6px 18px; flex-wrap: wrap; font-size: 13px; margin: 16px 0 0; }
.gains span { white-space: nowrap; }
.gains em { font-style: normal; color: var(--secondary-text-color); margin-right: 5px; }
.gains small { font-size: 11.5px; font-weight: 600; margin-left: 5px; color: var(--warning-color); }
.log { list-style: none; margin: 16px 0 0; padding: 0; font-size: 12.5px; }
.log li { display: grid; grid-template-columns: 54px minmax(0, 1fr); gap: 0 10px; padding: 3px 0 3px 14px; position: relative; }
.log li::before { content: ""; position: absolute; left: 0; top: 8px; width: 7px; height: 7px; border-radius: 50%; background: var(--tick); }
.log li::after { content: ""; position: absolute; left: 3px; top: 17px; bottom: -6px; width: 1px; background: var(--divider-color); }
.log li:last-child::after { display: none; }
.log li.next::before { background: transparent; box-shadow: inset 0 0 0 1.5px var(--primary-color); }
.log li.sub::before { display: none; }
.log .dim { color: var(--secondary-text-color); }
.log li:focus { outline: none; }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
.log .d, .log .why { color: var(--secondary-text-color); }
.log .why { margin-left: 6px; }
.log li.old { display: none; }
.log.all li.old { display: grid; }
.older { all: unset; cursor: pointer; font-size: 12.5px; color: var(--primary-color); margin: 4px 0 0 14px; }
.older:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; border-radius: 3px; }
.hold { font-size: 12.5px; color: var(--secondary-text-color); margin: 12px 0 0; }
.ft { border-top: 1px solid var(--divider-color); padding-top: 9px; font-size: 11.5px; color: var(--secondary-text-color); display: flex; gap: 16px; flex-wrap: wrap; align-items: center; }
.ft .strip { height: 12px; display: inline-flex; vertical-align: -1px; margin-right: 6px; }
.ft .strip i { height: 12px; }
.ft .strip i.p { height: 5px; }
@container (min-width: ${WIDE_FROM}px) {
  .row { grid-template-columns: 130px minmax(0, 1fr) auto 44px; }
  .say { grid-column: 2; grid-row: 1; font-size: 13px; }
  .strip { grid-column: 3; grid-row: 1; }
  .pct { grid-column: 4; grid-row: 1; }
  .more { padding-left: 142px; }
}`;

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function pct(z) {
  const e = el('span', 'pct', z.confidence === null ? DASH : `${z.confidence}%`);
  e.append(el('span', 'sr', ' confidence'));
  return e;
}

function strip(s) {
  const e = el('span', 'strip');
  e.setAttribute('aria-hidden', 'true');
  [...s].forEach((c) => e.append(el('i', c === 'p' ? 'p' : '')));
  return e;
}

class AdaptiveClimateLearningCard extends HTMLElement {
  constructor() {
    super();
    this._config = {};
    this._seen = null;
    this._open = null;
    this._older = new Set();
    this.attachShadow({ mode: 'open' });
  }

  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    const cfg = { ...(config || {}) };
    if (cfg.entities !== undefined && !Array.isArray(cfg.entities)) throw new Error('entities must be a list of climate entity ids');
    if (cfg.entities) cfg.entities = cfg.entities.map((e) => (e && e.entity) ?? e);
    this._config = cfg;
    this._seen = null;
    if (this._hass) this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 1 + (this._count || 0);
  }

  _ids() {
    const given = this._config.entities;
    if (Array.isArray(given)) return given;
    return Object.keys(this._hass.states).filter((id) => {
      const l = id.startsWith('climate.') && this._hass.states[id].attributes.learning;
      return l && typeof l === 'object' && 'confidence' in l;
    });
  }

  _render() {
    if (!this._hass || !this._hass.states) return;
    const ids = this._ids();
    const objs = ids.map((id) => this._hass.states[id]);
    const seen = this._seen;
    if (seen && seen.ids.length === ids.length && ids.every((id, i) => id === seen.ids[i] && objs[i] === seen.objs[i])) return;
    this._seen = { ids, objs };

    LOCALE = { language: this._hass.locale && this._hass.locale.language, timeZone: this._hass.config && this._hass.config.time_zone };
    const rate = rateScale(objs);
    let zones = ids.map((id, i) => zoneProfile(id, objs[i], rate));
    if (!Array.isArray(this._config.entities)) zones = sortZones(zones);
    this._count = zones.length;

    const focused = this.shadowRoot.activeElement;
    const focusKey = focused && focused.dataset.k;

    const card = el('ha-card');
    const wrap = el('div', 'wrap');
    const top = el('div', 'top');
    top.append(el('b', '', this._config.title || 'What the system knows'), el('span', '', attention(zones)));
    wrap.append(top);

    if (!zones.length) wrap.append(el('div', 'empty', 'No Adaptive Climate zones found.'));
    zones.forEach((z) => wrap.append(this._room(z)));

    if (zones.some((z) => z.strip)) {
      const ft = el('div', 'ft');
      const legend = el('span');
      legend.append(strip('gpg'), 'last 20 cycles: tall good, short poor');
      ft.append(legend, el('span', '', '% is confidence'));
      wrap.append(ft);
    }

    card.append(wrap);
    this.shadowRoot.replaceChildren(el('style', '', STYLE), card);
    if (focusKey) [...this.shadowRoot.querySelectorAll('[data-k]')].find((e) => e.dataset.k === focusKey)?.focus();
  }

  _room(z) {
    const room = el('div', 'room');
    room.dataset.id = z.id;
    if (!z.available) {
      const row = el('div', 'row na');
      row.append(el('span', 'name', z.name), el('span', 'say', z.say));
      room.append(row);
      return room;
    }
    const open = this._open === z.id;
    room.classList.toggle('open', open);
    const row = el('button', z.bad ? 'row bad' : 'row');
    row.type = 'button';
    row.dataset.k = `r:${z.id}`;
    row.setAttribute('aria-expanded', String(open));
    row.append(el('span', 'name', z.name), el('span', 'say', z.say), strip(z.strip), pct(z));
    row.onclick = () => {
      this._open = this._open === z.id ? null : z.id;
      this.shadowRoot.querySelectorAll('.room').forEach((r) => {
        const on = r.dataset.id === this._open;
        r.classList.toggle('open', on);
        const b = r.querySelector('button.row');
        if (b) b.setAttribute('aria-expanded', String(on));
      });
    };

    const more = el('div', 'more');
    if (z.facts) more.append(el('p', 'facts', z.facts));
    const m = el('div', 'm');
    z.bars.forEach((b) => {
      const track = el('div', b.off ? 'track off' : 'track');
      const fill = el('b');
      fill.style.width = `${b.frac * 100}%`;
      track.append(fill);
      if (b.tick !== null) {
        const u = el('u');
        u.style.left = `calc(${b.tick * 100}% - 1px)`;
        track.append(u);
      }
      const val = el('span', b.off ? 'off' : '', b.text);
      if (b.sr) val.append(el('span', 'sr', b.sr));
      m.append(el('span', 'k', b.label), val, track);
    });
    more.append(m);
    if (z.gains.length) {
      const g = el('div', 'gains');
      z.gains.forEach((x) => {
        const s = el('span');
        s.append(el('em', '', x.label), x.text);
        if (x.flag) s.append(el('small', '', x.flag));
        g.append(s);
      });
      more.append(g);
    }
    const { next, entries } = z.log;
    if (next || entries.length) {
      const log = el('ul', 'log');
      if (next) {
        const li = el('li', 'next');
        li.append(el('span', 'd', 'next'), el('span', '', next));
        log.append(li);
        if (z.log.last) {
          const sub = el('li', 'sub');
          sub.append(el('span', 'd'), el('span', 'dim', z.log.last));
          log.append(sub);
        }
      }
      entries.forEach((e, i) => {
        const li = el('li', i >= SHOWN_CHANGES ? 'old' : '');
        li.tabIndex = i >= SHOWN_CHANGES ? -1 : li.tabIndex;
        const what = el('span', '', e.text);
        what.append(el('span', 'why', e.why));
        li.append(el('span', 'd', e.date), what);
        log.append(li);
      });
      more.append(log);
      const hidden = entries.length - SHOWN_CHANGES;
      if (hidden > 0 && this._older.has(z.id)) log.classList.add('all');
      else if (hidden > 0) {
        const btn = el('button', 'older', `Show ${hidden} older ${hidden === 1 ? 'change' : 'changes'}`);
        btn.type = 'button';
        btn.dataset.k = `o:${z.id}`;
        btn.onclick = () => {
          this._older.add(z.id);
          log.classList.add('all');
          btn.remove();
          log.querySelector('li.old').focus();
        };
        more.append(btn);
      }
    }
    if (z.holds) more.append(el('p', 'hold', z.holds));
    room.append(row, more);
    return room;
  }
}

window.AdaptiveClimateLearningCard = { zoneProfile, sortZones, rateScale, sentence, factsLine, timeline, tuneText, holds, gains, attention };
if (!customElements.get(TAG)) customElements.define(TAG, AdaptiveClimateLearningCard);
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === TAG)) {
  window.customCards.push({
    type: TAG,
    name: 'Adaptive Climate learning',
    description: 'What the system has learned about each zone, against what was expected.',
  });
}
})();
