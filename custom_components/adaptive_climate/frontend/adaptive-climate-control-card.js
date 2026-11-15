(() => {
const TAG = 'adaptive-climate-control-card';
const PAUSE = 600;
const DASH = '–';
const SLOTS = ['eco', 'comfort', 'sleep', 'away'];
const PRESETS = ['away', 'eco', 'boost', 'comfort', 'home', 'sleep', 'activity'];
const DEPTH = { min: 0.5, max: 5, step: 0.5 };

function num(v) {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

function isObj(v) {
  return !!v && typeof v === 'object' && !Array.isArray(v);
}

function deg(v) {
  return v === null ? DASH : `${v.toFixed(1)}°`;
}

function hhmm(v) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(typeof v === 'string' ? v : '');
  return m ? `${m[1].padStart(2, '0')}:${m[2]}` : null;
}

function nightOf(z) {
  return z.settings && isObj(z.settings.night_setback) ? z.settings.night_setback : null;
}

function zoneOf(id, s) {
  const a = (s && s.attributes) || {};
  const temps = {};
  PRESETS.forEach((p) => { temps[p] = num(a[`${p}_temp`]); });
  return {
    id,
    ok: !!s && s.state !== 'unavailable',
    name: String(a.friendly_name || id),
    off: !!s && s.state === 'off',
    heating: a.hvac_action === 'heating',
    cur: num(a.current_temperature),
    target: num(a.temperature),
    min: num(a.min_temp) ?? 7,
    max: num(a.max_temp) ?? 35,
    step: num(a.target_temp_step) || 0.5,
    preset: a.preset_mode,
    presets: Array.isArray(a.preset_modes) ? a.preset_modes : [],
    temps,
    settings: isObj(a.settings) ? a.settings : null,
  };
}

const STYLE = `
:host { display: block; }
.wrap { padding: 16px 16px 10px; color: var(--primary-text-color); font-size: 14px; line-height: 1.4; font-variant-numeric: tabular-nums; }
button, select, input { font: inherit; color: inherit; background: var(--divider-color); border: 0; border-radius: 8px; }
button { cursor: pointer; }
option { background: var(--card-background-color); }
button:focus-visible, select:focus-visible, input:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
:disabled { opacity: .4; cursor: default; }
.top { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
.top b { font-size: 16px; font-weight: 500; }
.house { display: flex; gap: 6px; margin-left: auto; }
.house > * { padding: 5px 10px; font-size: 12.5px; }
.pill[aria-checked=true] { background: var(--primary-color); color: var(--card-background-color); }
.room { border-top: 1px solid var(--divider-color); padding: 10px 0; }
.row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 10px; }
.n { flex: 1 1 120px; min-width: 0; background: none; padding: 2px 0; text-align: left; font-weight: 500; overflow-wrap: anywhere; }
.n:disabled { opacity: 1; }
.n i { display: inline-block; font-style: normal; color: var(--secondary-text-color); margin-left: 4px; transition: transform .12s; }
.n[aria-expanded=true] i { transform: rotate(180deg); }
.n small { display: block; font-weight: 400; font-size: 12px; color: var(--secondary-text-color); }
.n small.heat { color: var(--primary-text-color); }
.n small.heat::before { content: ""; display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: var(--warning-color); margin-right: 5px; vertical-align: 1px; }
.ctl { display: flex; align-items: center; gap: 8px; margin-left: auto; }
.step { display: flex; align-items: center; gap: 2px; }
.step button { width: 30px; height: 30px; padding: 0; font-size: 17px; line-height: 1; }
.step output { min-width: 50px; text-align: center; font-size: 15.5px; font-weight: 500; }
.ctl select { padding: 6px 8px; font-size: 12.5px; min-width: 74px; }
.sw { width: 34px; height: 20px; border-radius: 10px; position: relative; padding: 0; flex: none; }
.sw::after { content: ""; position: absolute; top: 3px; left: 3px; width: 14px; height: 14px; border-radius: 50%; background: var(--secondary-text-color); transition: left .12s; }
.sw[aria-checked=true] { background: var(--primary-color); }
.sw[aria-checked=true]::after { left: 17px; background: var(--card-background-color); }
.offz .step, .offz .ctl select { opacity: .4; }
.more { margin-top: 9px; padding: 8px 10px; border: 1px solid var(--divider-color); border-radius: 8px; display: grid; gap: 4px; font-size: 12.5px; }
.more h4 { margin: 4px 0 0; font-size: 12.5px; font-weight: 500; }
.ln { display: grid; grid-template-columns: minmax(0, 1fr) auto 26px; align-items: center; gap: 6px; min-height: 30px; }
.ln > span:first-child { color: var(--secondary-text-color); }
.ln > :nth-child(2) { justify-self: end; }
.more .step output { min-width: 56px; font-size: 13.5px; }
.more input { padding: 4px 6px; }
.more .x { width: 26px; height: 26px; padding: 0; }
.more .act { padding: 4px 8px; font-size: 12px; margin-left: 6px; }
.dim { color: var(--secondary-text-color); }
.reset { justify-self: start; margin-top: 4px; padding: 5px 9px; font-size: 12px; }
.err { color: var(--error-color); font-size: 12.5px; margin-top: 6px; }
.empty { padding: 10px 0; color: var(--secondary-text-color); }`;

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function ctl(tag, key, label, z, cls, text) {
  const e = el(tag, cls, text);
  e.dataset.k = key;
  if (label) e.setAttribute('aria-label', label);
  if (z && !z.ok) e.disabled = true;
  return e;
}

class AdaptiveClimateControlCard extends HTMLElement {
  constructor() {
    super();
    this._config = {};
    this._known = new Set();
    this._pend = {};
    this._errors = {};
    this._clock = new Set();
    this._open = null;
    this.attachShadow({ mode: 'open' });
    this.shadowRoot.addEventListener('focusout', () => setTimeout(() => this._refresh()));
    this.shadowRoot.addEventListener('focusin', (e) => {
      if (e.target !== this._committed) this._committed = null;
    });
    // A redraw between press and release would replace the button and the browser would drop the click.
    // Buttons only: a select's native popup can swallow the pointerup.
    this.shadowRoot.addEventListener('pointerdown', (e) => {
      if (!e.target.closest('button')) return;
      this._pressing = true;
      const release = () => {
        window.removeEventListener('pointerup', release, true);
        window.removeEventListener('pointercancel', release, true);
        setTimeout(() => {
          this._pressing = false;
          this._refresh();
        });
      };
      window.addEventListener('pointerup', release, true);
      window.addEventListener('pointercancel', release, true);
    });
  }

  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    const cfg = { ...(config || {}) };
    if (cfg.entities !== undefined && !Array.isArray(cfg.entities)) throw new Error('entities must be a list of climate entity ids');
    if (cfg.entities) cfg.entities = cfg.entities.map((e) => (e && e.entity) ?? e);
    this._config = cfg;
    this._stale = true;
    this._refresh();
  }

  set hass(hass) {
    this._hass = hass;
    this._refresh();
  }

  getCardSize() {
    return 1 + (this._count || 0) + (this._open ? 4 : 0);
  }

  _ids() {
    const st = this._hass.states;
    if (Array.isArray(this._config.entities)) return this._config.entities;
    // Unavailable entities lose their extra attributes, so remember zones once seen.
    Object.keys(st).forEach((id) => { if (id.startsWith('climate.') && isObj(st[id].attributes.settings)) this._known.add(id); });
    const name = (id) => String(st[id].attributes.friendly_name || id);
    return [...this._known].filter((id) => st[id]).sort((x, y) => name(x).localeCompare(name(y)));
  }

  _busy() {
    const a = this.shadowRoot.activeElement;
    const editing = !!a && a !== this._committed && a.matches('input, select');
    return this._pressing || Object.keys(this._pend).length > 0 || editing;
  }

  _refresh() {
    if (!this._hass || !this._hass.states) return;
    const ids = this._ids();
    const seen = this._seen;
    const same = seen && seen.length === ids.length && ids.every((id, i) => id === seen[i].id && this._hass.states[id] === seen[i].obj);
    if (same && !this._stale) return;
    if (this._busy()) {
      this._stale = true;
      return;
    }
    this._draw();
  }

  _draw(failedKey, focusKey) {
    const root = this.shadowRoot;
    const active = root.activeElement;
    const k = focusKey || (active && active.dataset.k);
    const committed = !!active && active === this._committed;
    const typed = active && !committed && active.matches('input, select') && active.dataset.k !== failedKey ? active.value : null;

    const ids = this._ids();
    this._seen = ids.map((id) => ({ id, obj: this._hass.states[id] }));
    this._stale = false;
    const zones = ids.map((id) => zoneOf(id, this._hass.states[id]));
    if (!zones.some((z) => z.id === this._open && z.ok)) this._open = null;
    this._count = zones.length;

    const card = el('ha-card');
    const wrap = el('div', 'wrap');
    const top = el('div', 'top');
    top.append(el('b', '', this._config.title || 'Heating'), this._house(zones));
    wrap.append(top);
    const headErr = this._err('');
    if (headErr) wrap.append(headErr);
    if (!zones.length) wrap.append(el('div', 'empty', 'No Adaptive Climate zones found.'));
    zones.forEach((z, i) => wrap.append(this._room(z, i)));
    card.append(wrap);
    this._drawing = true;
    root.replaceChildren(el('style', '', STYLE), card);
    this._drawing = false;

    const again = k && [...root.querySelectorAll('[data-k]')].find((e) => e.dataset.k === k);
    if (again) {
      if (typed !== null && !focusKey) again.value = typed;
      again.focus();
      if (committed) this._committed = again;
    }
  }

  _err(id) {
    const e = this._errors[id];
    if (!e) return null;
    const div = el('div', 'err', e.msg);
    // Only announce once; later redraws re-insert the same text silently.
    if (e.fresh) div.setAttribute('role', 'alert');
    e.fresh = false;
    return div;
  }

  _zone(id) {
    return zoneOf(id, this._hass.states[id]);
  }

  async _call(id, service, data, key) {
    const [domain, name] = service.split('.');
    const p = this._pend[key];
    if (p) p.calls++;
    const hadError = id in this._errors;
    delete this._errors[id];
    let failed = false;
    try {
      await this._hass.callService(domain, name, data);
    } catch (err) {
      failed = true;
      this._errors[id] = { msg: (err && err.message) || String(err), fresh: true };
    }
    if (p) {
      p.calls--;
      if (failed) {
        clearTimeout(p.timer);
        p.timer = 0;
      }
      if (!p.calls && !p.timer && this._pend[key] === p) delete this._pend[key];
    }
    if (failed) {
      this._draw(key);
      return;
    }
    // Without an error to clear, wait for the state push instead of redrawing the old value.
    if (hadError) this._stale = true;
    this._refresh();
  }

  _house(zones) {
    const house = el('span', 'house');
    const live = zones.filter((z) => z.ok);
    const union = [...new Set(live.flatMap((z) => z.presets))];
    if (union.some((p) => p !== 'none')) {
      const all = ctl('select', 'all', 'Preset for all rooms');
      const first = el('option', '', 'All rooms');
      first.value = '';
      first.disabled = true;
      all.append(first, ...union.map((p) => el('option', '', p)));
      all.value = '';
      all.addEventListener('change', () => {
        const preset = all.value;
        all.value = '';
        this._committed = all;
        live.filter((z) => z.presets.includes(preset))
          .forEach((z) => this._call(z.id, 'climate.set_preset_mode', { entity_id: z.id, preset_mode: preset }));
      });
      house.append(all);
    }
    const lead = live.find((z) => z.settings);
    if (lead) {
      const on = !!lead.settings.vacation;
      const v = ctl('button', 'vacation', null, null, 'pill', 'Vacation');
      v.setAttribute('role', 'switch');
      v.setAttribute('aria-checked', String(on));
      v.addEventListener('click', () => {
        v.setAttribute('aria-checked', String(!on));
        this._call('', 'adaptive_climate.set_vacation_mode', { enabled: !on }, 'vacation');
      });
      house.append(v);
    }
    return house;
  }

  _room(z, i) {
    const room = el('div', z.off ? 'room offz' : 'room');
    const row = el('div', 'row');
    const open = this._open === z.id;

    const n = ctl('button', `${z.id}|open`, null, z, 'n', z.name);
    n.setAttribute('aria-expanded', String(open));
    n.setAttribute('aria-controls', `more${i}`);
    const chev = el('i', '', '▾');
    chev.setAttribute('aria-hidden', 'true');
    const status = !z.ok ? 'unavailable' : `${deg(z.cur)} now${z.off ? ', off' : z.heating ? ', heating' : ''}`;
    n.append(chev, el('small', z.ok && !z.off && z.heating ? 'heat' : '', status));
    n.addEventListener('click', () => {
      this._open = open ? null : z.id;
      this._draw();
    });

    const c = el('span', 'ctl');
    c.append(this._stepper(z, `${z.id}|target`, `${z.name} target`, (q) => q.target, { min: z.min, max: z.max, step: z.step },
      (v) => ['climate.set_temperature', { entity_id: z.id, temperature: v }]));
    if (z.ok && z.presets.some((p) => p !== 'none')) {
      const sel = ctl('select', `${z.id}|preset`, `${z.name} preset`, z);
      sel.append(...z.presets.map((p) => el('option', '', p)));
      sel.value = z.preset;
      sel.addEventListener('change', () => {
        this._committed = sel;
        this._call(z.id, 'climate.set_preset_mode', { entity_id: z.id, preset_mode: sel.value }, sel.dataset.k);
      });
      c.append(sel);
    }
    const sw = ctl('button', `${z.id}|power`, `${z.name} heating`, z, 'sw');
    sw.setAttribute('role', 'switch');
    sw.setAttribute('aria-checked', String(z.ok && !z.off));
    sw.addEventListener('click', () => {
      sw.setAttribute('aria-checked', String(z.off));
      this._call(z.id, z.off ? 'climate.turn_on' : 'climate.turn_off', { entity_id: z.id }, sw.dataset.k);
    });
    c.append(sw);

    row.append(n, c);
    room.append(row);
    if (open) room.append(this._more(z, i));
    const err = this._err(z.id);
    if (err) room.append(err);
    return room;
  }

  _stepper(z, key, label, pick, o, send) {
    const step = el('span', 'step');
    const p = this._pend[key];
    const value = pick(z);
    const out = el('output', '', z.ok ? (p ? deg(p.value) : value === null ? 'not set' : deg(value)) : DASH);
    out.setAttribute('aria-label', label);
    const btn = (dir) => {
      const b = ctl('button', `${key}${dir > 0 ? '+' : '-'}`, `${dir > 0 ? 'Raise' : 'Lower'} ${label}`, z, '', dir > 0 ? '+' : '−');
      b.addEventListener('click', () => this._press(z, key, pick, dir, o, out, send));
      return b;
    };
    step.append(btn(-1), out, btn(1));
    return step;
  }

  _press(z, key, pick, dir, o, out, send) {
    const p = this._pend[key] || { calls: 0, timer: 0, value: null };
    // Step from the latest state: a push may have been held back while this row was drawn.
    const fresh = this._zone(z.id);
    const from = p.value ?? pick(fresh) ?? (o.base ? o.base(fresh) : null) ?? o.min;
    const next = Math.min(o.max, Math.max(o.min, Math.round((from + dir * o.step) / o.step) * o.step));
    const v = Math.round(next * 100) / 100;
    if (v === from) return;
    this._pend[key] = p;
    p.value = v;
    out.textContent = deg(v);
    clearTimeout(p.timer);
    p.timer = setTimeout(() => {
      p.timer = 0;
      this._call(z.id, ...send(p.value), key);
    }, PAUSE);
  }

  _more(z, i) {
    const more = el('div', 'more');
    more.id = `more${i}`;
    more.append(el('h4', '', 'Night setback'), ...this._setback(z), el('h4', '', 'Preset temperatures'));

    const slots = [...SLOTS, ...PRESETS.filter((p) => !SLOTS.includes(p) && z.temps[p] !== null)];
    slots.forEach((preset) => {
      const label = `${z.name} ${preset} temperature`;
      const send = (v) => ['adaptive_climate.set_preset_temperature', { entity_id: z.id, preset, temperature: v }];
      const ln = el('div', 'ln');
      ln.append(el('span', '', preset), this._stepper(z, `${z.id}|${preset}`, label, (q) => q.temps[preset], { min: z.min, max: z.max, step: z.step, base: (q) => q.target }, send));
      if (z.temps[preset] !== null) {
        const x = ctl('button', `${z.id}|${preset}x`, `Clear ${label}`, z, 'x', '×');
        x.addEventListener('click', () => this._call(z.id, ...send(null), x.dataset.k));
        ln.append(x);
      }
      more.append(ln);
    });

    if (z.settings && z.settings.changed) {
      const reset = ctl('button', `${z.id}|reset`, `Back to config for ${z.name}`, z, 'reset', 'Back to config');
      reset.addEventListener('click', () => {
        if (confirm(`Put ${z.name} back to its config? Every change made here for this room is dropped.`)) {
          this._call(z.id, 'adaptive_climate.reset_zone_settings', { entity_id: z.id }, reset.dataset.k);
        }
      });
      more.append(reset);
    }
    return more;
  }

  _setback(z) {
    const ns = nightOf(z);
    if (!ns) return [el('div', 'dim', 'Night setback is not set up for this room in the config.')];
    const line = (label, ...value) => {
      const ln = el('div', 'ln');
      ln.append(el('span', '', label), ...value);
      return ln;
    };
    const depth = num(ns.delta);
    if (!ns.editable) {
      return [
        line('starts at', el('span', '', String(ns.start ?? DASH))),
        line('lowers by', el('span', '', deg(depth))),
        line('warm again by', el('span', '', hhmm(ns.recovery_deadline) || DASH)),
        el('div', 'dim', 'set in the config'),
      ];
    }
    const send = (field, v) => ['adaptive_climate.set_night_setback', { entity_id: z.id, [field]: v }];
    const time = (field, label, value) => {
      const t = ctl('input', `${z.id}|${field}`, `${z.name} ${label}`, z);
      t.type = 'time';
      t.value = value || '';
      let sent = null;
      // Sent once the edit is done: change fires per segment, so a half-typed time would be saved.
      const commit = () => {
        if (this._drawing) return;
        const current = hhmm((nightOf(this._zone(z.id)) || {})[field]);
        if (!t.value) t.value = current || '';
        if (!t.value || t.value === current || t.value === sent) return;
        sent = t.value;
        this._call(z.id, ...send(field, t.value), t.dataset.k);
      };
      t.addEventListener('blur', commit);
      t.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') commit();
      });
      return t;
    };
    const start = hhmm(ns.start);
    if (start) this._clock.delete(z.id);
    let startCtl;
    if (start || this._clock.has(z.id)) {
      startCtl = time('start', 'night setback starts at', start);
    } else {
      startCtl = el('span', '', String(ns.start ?? DASH));
      const set = ctl('button', `${z.id}|clock`, `Set a time for ${z.name} night setback start`, z, 'act', 'set a time');
      set.addEventListener('click', () => {
        this._clock.add(z.id);
        this._draw(null, `${z.id}|start`);
      });
      startCtl.append(set);
    }
    return [
      line('starts at', startCtl),
      line('lowers by', this._stepper(z, `${z.id}|delta`, `${z.name} night setback depth`, (q) => num((nightOf(q) || {}).delta), DEPTH, (v) => send('delta', v))),
      line('warm again by', time('recovery_deadline', 'warm again by', hhmm(ns.recovery_deadline))),
    ];
  }
}

if (!customElements.get(TAG)) customElements.define(TAG, AdaptiveClimateControlCard);
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === TAG)) {
  window.customCards.push({
    type: TAG,
    name: 'Adaptive Climate control',
    description: 'Change each zone’s target, preset, night setback and preset temperatures.',
  });
}
})();
