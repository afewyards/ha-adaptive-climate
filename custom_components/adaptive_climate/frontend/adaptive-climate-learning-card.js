(() => {
const TAG = 'adaptive-climate-learning-card';
const DASH = '–';
const NARROW_UNDER = 560;
const RATE_FLOOR = 0.75;
const SCALE = { rate: 2, overshoot: 0.5, settling: 120 };
const RATE_HEADROOM = 1.25;

const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const clamp01 = (v) => Math.min(Math.max(v, 0), 1);
const cycles = (n) => `${n} ${n === 1 ? 'cycle' : 'cycles'}`;
const trim = (v, d) => String(Math.round(v * 10 ** d) / 10 ** d);

function bar(label, value, ref, scale, unit, d, isOff) {
  const v = num(value);
  const r = num(ref) !== null && ref > 0 ? ref : null;
  return {
    label,
    text: v === null ? DASH : trim(v, d) + unit,
    frac: v === null ? 0 : clamp01(v / scale),
    tick: r === null ? null : clamp01(r / scale),
    off: v !== null && r !== null && isOff(v, r),
  };
}

function rateScale(objs) {
  const expected = objs.map((o) => num(o && o.attributes && o.attributes.learning && o.attributes.learning.heating_rate_expected) || 0);
  return Math.max(SCALE.rate, RATE_HEADROOM * Math.max(0, ...expected));
}

function zoneProfile(id, stateObj, rate = SCALE.rate) {
  const learning = stateObj && stateObj.attributes && stateObj.attributes.learning;
  if (!stateObj || stateObj.state === 'unavailable' || !learning || typeof learning !== 'object') {
    return { id, name: id, meta: '', confidence: null, status: '', cycles: null, available: false, bars: [], reasons: ['Not available'] };
  }
  const a = learning;
  const name = (stateObj.attributes.friendly_name || id) + '';
  const meta = [
    typeof a.heating_type === 'string' && a.heating_type ? a.heating_type.replace(/_/g, ' ') : null,
    num(a.area_m2) !== null ? `${trim(a.area_m2, 1)} m²` : null,
  ].filter(Boolean).join(', ');
  const bars = [
    bar('heats at', a.heating_rate, a.heating_rate_expected, rate, '°/h', 1, (v, r) => Math.round((v / r) * 100) < RATE_FLOOR * 100),
    bar('overshoot', a.overshoot, a.overshoot_limit, SCALE.overshoot, '°', 2, (v, r) => v > r),
    bar('settles in', a.settling_time, a.settling_time_limit, SCALE.settling, ' min', 0, (v, r) => v > r),
  ];
  if (num(a.heating_rate) !== null && num(a.heating_rate_expected) > 0) {
    bars[0].sub = `${Math.round((a.heating_rate / a.heating_rate_expected) * 100)}%`;
  }
  const reasons = [];
  if (bars[0].off) reasons.push(`Heats at ${bars[0].sub} of the expected rate.`);
  if (bars[1].off) reasons.push(`Overshoots by ${trim(a.overshoot, 2)}°, limit ${trim(a.overshoot_limit, 2)}°.`);
  if (bars[2].off) reasons.push(`Takes ${trim(a.settling_time, 0)} min to settle, limit ${trim(a.settling_time_limit, 0)} min.`);
  const measured = [a.heating_rate, a.overshoot, a.settling_time].some((v) => num(v) !== null);
  return {
    id,
    name,
    meta,
    confidence: num(a.confidence),
    status: typeof a.status === 'string' ? a.status : '',
    cycles: num(a.cycles),
    available: true,
    bars: measured ? bars : [],
    reasons: measured ? reasons : ['Still collecting'],
  };
}

const offCount = (z) => z.bars.filter((b) => b.off).length;

function sortZones(zones) {
  return [...zones].sort(
    (x, y) => offCount(y) - offCount(x) || (x.confidence ?? 0) - (y.confidence ?? 0) || x.name.localeCompare(y.name),
  );
}

const STYLE = `
:host { display: block; }
.wrap { container-type: inline-size; padding: 16px 16px 12px; color: var(--primary-text-color); font-variant-numeric: tabular-nums; }
.top { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px; }
.top b { font-size: 16px; font-weight: 500; }
.top span, .dim { font-size: 12.5px; color: var(--secondary-text-color); }
.room { border-top: 1px solid var(--divider-color); padding: 10px 0 9px; }
.hd { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
.n { font-weight: 500; min-width: 0; overflow-wrap: anywhere; }
.n small { font-weight: 400; font-size: 12px; color: var(--secondary-text-color); margin-left: 6px; }
.s { font-size: 12.5px; color: var(--secondary-text-color); text-align: right; }
.s b { color: var(--primary-text-color); font-weight: 500; }
.bars { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; margin-top: 7px; }
.bu { position: relative; height: 34px; }
.l { font-size: 11px; color: var(--secondary-text-color); display: block; line-height: 1; }
.v { font-size: 13.5px; display: block; line-height: 1.35; white-space: nowrap; }
.v small { color: var(--secondary-text-color); font-size: 11.5px; margin-left: 3px; }
.tr, .fill { position: absolute; left: 0; bottom: 0; height: 5px; border-radius: 3px; }
.tr { right: 0; background: var(--divider-color); }
.fill { background: var(--secondary-text-color); }
.ex { position: absolute; bottom: -4px; width: 2px; height: 13px; background: var(--primary-color); }
.off .v { font-weight: 600; }
.off .fill { background: var(--warning-color); }
.note { font-size: 12.5px; color: var(--primary-text-color); border-left: 3px solid var(--warning-color); padding-left: 6px; margin-top: 7px; }
.note.plain { color: var(--secondary-text-color); border-left: 0; padding-left: 0; }
.empty { padding: 10px 0; color: var(--secondary-text-color); }
.ft { border-top: 1px solid var(--divider-color); padding-top: 9px; font-size: 11.5px; color: var(--secondary-text-color); display: flex; gap: 14px; flex-wrap: wrap; }
.ft i { display: inline-block; vertical-align: middle; margin-right: 5px; }
@container (min-width: ${NARROW_UNDER}px) {
  .room { display: grid; grid-template-columns: 150px 1fr 150px; gap: 4px 16px; align-items: center; }
  .hd { display: contents; }
  .n small { display: block; margin: 0; }
  .n { grid-column: 1; grid-row: 1; }
  .s { grid-column: 3; grid-row: 1; }
  .s b { display: block; font-size: 15px; }
  .bars { margin: 0; grid-column: 2; grid-row: 1; }
  .note { grid-column: 2 / -1; margin-top: 0; }
  .hd + .note { grid-column: 2; grid-row: 1; }
}`;

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

class AdaptiveClimateLearningCard extends HTMLElement {
  constructor() {
    super();
    this._config = {};
    this._seen = null;
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

    const rate = rateScale(objs);
    let zones = ids.map((id, i) => zoneProfile(id, objs[i], rate));
    if (!Array.isArray(this._config.entities)) zones = sortZones(zones);
    this._count = zones.length;

    const card = el('ha-card');
    const wrap = el('div', 'wrap');
    const total = zones.reduce((n, z) => n + (z.cycles || 0), 0);
    const top = el('div', 'top');
    top.append(el('b', '', this._config.title || 'What the system knows'), zones.length ? el('span', '', cycles(total)) : '');
    wrap.append(top);

    if (!zones.length) wrap.append(el('div', 'empty', 'No Adaptive Climate zones found.'));
    zones.forEach((z) => wrap.append(this._room(z)));

    const ft = el('div', 'ft');
    const learned = el('span');
    const sw = el('i');
    sw.style.cssText = 'width:14px;height:5px;border-radius:3px;background:var(--secondary-text-color)';
    learned.append(sw, 'learned');
    const exp = el('span');
    const tk = el('i');
    tk.style.cssText = 'width:2px;height:12px;background:var(--primary-color)';
    exp.append(tk, 'expected, or the limit');
    ft.append(learned, exp);
    wrap.append(ft);

    card.append(wrap);
    this.shadowRoot.replaceChildren(el('style', '', STYLE), card);
  }

  _room(z) {
    const room = el('div', 'room');
    const hd = el('div', 'hd');
    const n = el('span', 'n', z.name);
    if (z.meta) n.append(el('small', '', z.meta));
    hd.append(n);
    if (z.available) {
      const s = el('span', 's');
      const status = [z.status, z.cycles !== null ? cycles(z.cycles) : null].filter(Boolean).join(', ');
      s.append(el('b', '', z.confidence === null ? DASH : `${z.confidence}%`), status ? ` ${status}` : '');
      hd.append(s);
    }
    room.append(hd);
    if (z.bars.length) {
      const bars = el('div', 'bars');
      z.bars.forEach((b) => {
        const bu = el('div', b.off ? 'bu off' : 'bu');
        const v = el('span', 'v', b.text);
        if (b.sub) v.append(el('small', '', b.sub));
        const fill = el('div', 'fill');
        fill.style.width = `${b.frac * 100}%`;
        bu.append(el('span', 'l', b.label), v, el('div', 'tr'), fill);
        if (b.tick !== null) {
          const ex = el('div', 'ex');
          ex.style.left = `calc(${b.tick * 100}% - 1px)`;
          bu.append(ex);
        }
        bars.append(bu);
      });
      room.append(bars);
    }
    const plain = !z.bars.length;
    z.reasons.forEach((r) => room.append(el('div', plain ? 'note plain' : 'note', r)));
    return room;
  }
}

window.AdaptiveClimateLearningCard = { zoneProfile, sortZones, rateScale };
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
