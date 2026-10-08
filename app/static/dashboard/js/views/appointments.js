/**
 * Every appointment in a range of dates, grouped by day, with filters.
 * The filters live in the address, so a reload keeps them.
 */
import { api } from '../api.js';
import * as D from '../dates.js';
import { fmt, t } from '../i18n.js';
import { badge, empty, errorBox, field, fill, h, icon, ltr, spinner } from '../ui.js';
import { debounce, groupBy } from '../util.js';

const LIMIT = 1000;
const STATUSES = ['confirmed', 'cancelled'];

/** Whether an appointment matches what was typed: a name, a treatment, or part of a phone number. */
function matcher(query) {
  const text = query.trim().toLowerCase();
  if (!text) return () => true;
  const digits = text.replace(/\D/g, '');
  const stored = digits.startsWith('0') ? `972${digits.slice(1)}` : digits;
  return (a) => (a.customer_name || '').toLowerCase().includes(text)
    || (a.treatment || '').toLowerCase().includes(text)
    || (digits.length >= 3 && (a.customer_phone || '').includes(stored));
}

export default function appointmentsView(container, params, ctx) {
  const today = D.today(ctx.tz);
  const state = {
    from: D.isDate(params.from) ? params.from : today,
    to: D.isDate(params.to) ? params.to : D.addDays(today, 30),
    status: STATUSES.includes(params.status) ? params.status : '',
    therapist: params.therapist || '',
    q: params.q || '',
  };
  if (state.to < state.from) state.to = state.from;
  let rows = [];
  let sequence = 0;

  const from = h('input', { type: 'date', value: state.from });
  const to = h('input', { type: 'date', value: state.to });
  const status = h('select', {},
    h('option', { value: '' }, t('list.allStatuses')),
    h('option', { value: 'confirmed' }, t('status.confirmed')),
    h('option', { value: 'cancelled' }, t('status.cancelled')));
  status.value = state.status;
  const therapist = h('select', {},
    h('option', { value: '' }, t('list.everyone')),
    ctx.therapists.map((th) => h('option', { value: th.id }, th.is_active ? th.name : `${th.name} (${t('common.inactive')})`)));
  therapist.value = ctx.therapists.some((th) => th.id === state.therapist) ? state.therapist : '';
  state.therapist = therapist.value;
  const search = h('input', { type: 'search', placeholder: t('list.search'), value: state.q, autocomplete: 'off' });

  const presets = [
    ['list.today', today, today],
    ['list.next7', today, D.addDays(today, 6)],
    ['list.next30', today, D.addDays(today, 30)],
    ['list.past30', D.addDays(today, -30), D.addDays(today, -1)],
  ];
  const presetButtons = presets.map(([label, start, end]) => h('button', {
    type: 'button',
    class: 'chip',
    dataset: { from: start, to: end },
    onclick: () => {
      from.value = start;
      to.value = end;
      apply();
    },
  }, t(label)));

  const summary = h('p', { class: 'list-summary' });
  const results = h('div', { class: 'list' }, spinner());

  from.addEventListener('change', apply);
  to.addEventListener('change', apply);
  status.addEventListener('change', apply);
  therapist.addEventListener('change', apply);
  search.addEventListener('input', debounce(() => {
    state.q = search.value;
    remember();
    draw();
  }, 150));

  fill(container, h('section', { class: 'page' },
    h('div', { class: 'page-head' },
      h('h1', { class: 'page-title' }, t('list.title')),
      h('button', { class: 'btn btn-primary', type: 'button', onclick: () => ctx.openBooking({}) },
        icon('plus'), h('span', {}, t('cal.new')))),
    h('div', { class: 'filters' },
      h('div', { class: 'chips' }, presetButtons),
      h('div', { class: 'filter-fields' },
        field(t('list.from'), from),
        field(t('list.to'), to),
        field(t('list.status'), status),
        field(t('list.therapist'), therapist),
        field(t('list.searchLabel'), search, { className: 'field-search' }))),
    summary,
    results));
  load();

  return { refresh: load };

  function apply() {
    if (!D.isDate(from.value) || !D.isDate(to.value)) return;
    if (to.value < from.value) to.value = from.value;
    Object.assign(state, { from: from.value, to: to.value, status: status.value, therapist: therapist.value });
    remember();
    load();
  }

  function remember() {
    ctx.replaceParams('appointments', state);
    for (const button of presetButtons) {
      button.setAttribute('aria-pressed', String(button.dataset.from === state.from && button.dataset.to === state.to));
    }
  }

  async function load() {
    const mine = ++sequence;
    remember();
    results.classList.add('is-loading');
    try {
      const list = await api('/appointments', {
        query: { from_date: state.from, to_date: state.to, status: state.status, therapist_id: state.therapist, limit: LIMIT },
      });
      if (mine !== sequence) return;
      rows = list;
      draw();
    } catch (error) {
      if (mine !== sequence || error.status === 401) return;
      summary.textContent = '';
      fill(results, errorBox(error, load));
    } finally {
      if (mine === sequence) results.classList.remove('is-loading');
    }
  }

  function draw() {
    const shown = rows.filter(matcher(state.q));
    const revenue = shown.filter((a) => a.status === 'confirmed').reduce((sum, a) => sum + (a.price || 0), 0);
    summary.textContent = shown.length
      ? t('list.summary', { count: t('cal.count', { n: shown.length }), money: fmt.money(revenue) })
      : '';
    if (!shown.length) {
      fill(results, empty(t('list.empty')));
      return;
    }
    const days = [...groupBy(shown, (a) => a.date)];
    fill(results,
      rows.length >= LIMIT ? h('p', { class: 'hint' }, t('list.limit')) : null,
      ...days.map(([date, items]) => h('section', { class: 'day-group' },
        h('h2', { class: ['day-head', date === today && 'is-today'] },
          h('span', {}, fmt.dateLong(date)),
          h('span', { class: 'muted' }, t('cal.count', { n: items.length }))),
        items.map(row))));
  }

  function row(a) {
    return h('button', { type: 'button', class: ['list-row', a.status === 'cancelled' && 'is-cancelled'], onclick: () => ctx.openAppointment(a) },
      h('span', { class: 'lr-time' }, ltr(`${a.time}–${a.end_time}`)),
      h('span', { class: 'lr-who' },
        h('span', { class: 'lr-name' }, a.customer_name || t('appt.noName')),
        h('span', { class: 'lr-phone' }, ltr(fmt.phone(a.customer_phone)))),
      h('span', { class: 'lr-what' }, a.treatment),
      h('span', { class: 'lr-therapist', style: { '--c': ctx.therapistColor(a.therapist_id) } },
        h('span', { class: 'dot' }), h('span', { class: 'ellipsis' }, a.therapist)),
      h('span', { class: 'lr-price' }, a.price === null || a.price === undefined ? '' : fmt.money(a.price)),
      h('span', { class: 'lr-status' }, a.status === 'cancelled' ? badge(t('status.cancelled'), 'danger') : null));
  }
}
