/**
 * How the clinic is doing over a range of dates: appointments, revenue,
 * cancellations, and who and what they were for.
 */
import { api } from '../api.js';
import * as D from '../dates.js';
import { fmt, t } from '../i18n.js';
import { empty, errorBox, field, fill, h, spinner } from '../ui.js';
import { groupBy } from '../util.js';

const PRESETS = {
  thisMonth: (today) => [D.startOfMonth(today), D.endOfMonth(today)],
  lastMonth: (today) => {
    const first = D.addMonths(D.startOfMonth(today), -1);
    return [first, D.endOfMonth(first)];
  },
  last30: (today) => [D.addDays(today, -29), today],
  next30: (today) => [today, D.addDays(today, 29)],
  thisYear: (today) => [`${today.slice(0, 4)}-01-01`, `${today.slice(0, 4)}-12-31`],
};
const PRESET_LABELS = {
  thisMonth: 'stats.thisMonth',
  lastMonth: 'stats.lastMonth',
  last30: 'stats.last30',
  next30: 'stats.next30',
  thisYear: 'stats.thisYear',
};
const MAX_DAYS = 366; // the server's limit
const WEEKLY_AFTER = 62; // past this many days, bars are weeks: daily ones get too thin to read

export default function statsView(container, params, ctx) {
  const today = D.today(ctx.tz);
  const state = { preset: 'thisMonth', from: '', to: '', metric: params.metric === 'revenue' ? 'revenue' : 'appointments' };
  if (D.isDate(params.from) && D.isDate(params.to) && params.from <= params.to) {
    Object.assign(state, { preset: '', from: params.from, to: params.to });
  } else {
    if (Object.hasOwn(PRESETS, params.preset)) state.preset = params.preset;
    [state.from, state.to] = PRESETS[state.preset](today);
  }
  let data = null;
  let sequence = 0;

  const presetButtons = Object.keys(PRESETS).map((key) => h('button', {
    type: 'button',
    class: 'chip',
    onclick: () => {
      state.preset = key;
      [state.from, state.to] = PRESETS[key](today);
      load();
    },
  }, t(PRESET_LABELS[key])));
  const from = h('input', { type: 'date' });
  const to = h('input', { type: 'date' });
  const rangeError = h('p', { class: 'form-error', role: 'alert' });
  const custom = () => {
    rangeError.textContent = '';
    if (!D.isDate(from.value) || !D.isDate(to.value)) return;
    if (to.value < from.value) {
      rangeError.textContent = t('stats.badRange');
      return;
    }
    if (D.daysBetween(from.value, to.value) > MAX_DAYS) {
      rangeError.textContent = t('stats.tooLong');
      return;
    }
    Object.assign(state, { preset: '', from: from.value, to: to.value });
    load();
  };
  from.addEventListener('change', custom);
  to.addEventListener('change', custom);

  const content = h('div', { class: 'stack' }, spinner());
  fill(container, h('section', { class: 'page' },
    h('div', { class: 'page-head' }, h('h1', { class: 'page-title' }, t('stats.title'))),
    h('div', { class: 'filters' },
      h('div', { class: 'chips' }, presetButtons),
      h('div', { class: 'filter-fields' }, field(t('list.from'), from), field(t('list.to'), to)),
      rangeError),
    content));
  load();

  return { refresh: load };

  function remember() {
    ctx.replaceParams('stats', state.preset
      ? { preset: state.preset, metric: state.metric }
      : { from: state.from, to: state.to, metric: state.metric });
    Object.keys(PRESETS).forEach((key, i) => presetButtons[i].setAttribute('aria-pressed', String(key === state.preset)));
    from.value = state.from;
    to.value = state.to;
  }

  async function load() {
    const mine = ++sequence;
    remember();
    content.classList.add('is-loading');
    try {
      const result = await api('/stats', { query: { from_date: state.from, to_date: state.to } });
      if (mine !== sequence) return;
      data = result;
      draw();
    } catch (error) {
      if (mine !== sequence || error.status === 401) return;
      fill(content, errorBox(error, load));
    } finally {
      if (mine === sequence) content.classList.remove('is-loading');
    }
  }

  function draw() {
    const kpis = h('div', { class: 'kpis' },
      kpi(t('stats.appointments'), fmt.number(data.appointments)),
      kpi(t('stats.revenue'), fmt.money(data.revenue)),
      kpi(t('stats.cancelled'), fmt.number(data.cancelled)),
      kpi(t('stats.rate'), fmt.percent(data.cancellation_rate)));

    if (!data.appointments && !data.cancelled) {
      fill(content, kpis, empty(t('stats.empty')));
      return;
    }

    const weekly = data.by_day.length > WEEKLY_AFTER;
    const buckets = weekly
      ? [...groupBy(data.by_day, (day) => D.startOfWeek(day.date))].map(([week, days]) => ({
        label: fmt.dayMonth(week),
        title: t('stats.weekOf', { date: fmt.dateLong(week) }),
        appointments: days.reduce((sum, day) => sum + day.appointments, 0),
        revenue: days.reduce((sum, day) => sum + day.revenue, 0),
      }))
      : data.by_day.map((day) => ({ label: fmt.dayMonth(day.date), title: fmt.dateLong(day.date), ...day }));

    const metricButtons = ['appointments', 'revenue'].map((metric) => h('button', {
      type: 'button',
      class: 'seg-btn',
      'aria-pressed': String(metric === state.metric),
      onclick: () => {
        state.metric = metric;
        remember();
        draw();
      },
    }, t(metric === 'revenue' ? 'stats.revenue' : 'stats.appointments')));

    const format = state.metric === 'revenue' ? fmt.money : fmt.number;
    fill(content,
      kpis,
      h('section', { class: 'card' },
        h('div', { class: 'section-head' },
          h('h2', { class: 'card-title' }, t(weekly ? 'stats.perWeek' : 'stats.perDay')),
          h('div', { class: 'seg', role: 'group' }, metricButtons)),
        chart(buckets, state.metric, format)),
      h('div', { class: 'stats-grid' },
        breakdown(t('stats.byTreatment'), Object.entries(data.by_treatment)),
        breakdown(t('stats.byTherapist'), Object.entries(data.by_therapist), (name) => {
          const therapist = ctx.therapists.find((th) => th.name === name);
          return therapist ? ctx.therapistColor(therapist.id) : undefined;
        })),
      h('p', { class: 'hint' }, t('stats.note')));
  }
}

function kpi(label, value) {
  return h('div', { class: 'kpi' }, h('div', { class: 'kpi-label' }, label), h('div', { class: 'kpi-value' }, value));
}

function chart(buckets, metric, format) {
  const max = Math.max(0, ...buckets.map((bucket) => bucket[metric]));
  // Label about eight bars, or four on a phone, so the dates never run together.
  const labels = window.matchMedia('(max-width: 560px)').matches ? 4 : 8;
  const every = Math.max(1, Math.ceil(buckets.length / labels));
  return h('figure', { class: 'chart' },
    h('div', { class: 'chart-scale' }, h('span', {}, format(max)), h('span', {}, format(0))),
    h('div', { class: 'chart-plot' },
      h('div', { class: 'chart-bars' }, buckets.map((bucket) => h('div', {
        class: 'chart-col',
        title: `${bucket.title}: ${format(bucket[metric])}`,
      }, h('div', {
        class: ['chart-bar', bucket[metric] > 0 && 'has-value'],
        style: { height: max ? `${(bucket[metric] / max) * 100}%` : '0%' },
      })))),
      h('div', { class: 'chart-axis', 'aria-hidden': 'true' },
        buckets.map((bucket, i) => h('span', { class: 'chart-label' }, i % every === 0 ? bucket.label : '')))));
}

function breakdown(title, entries, colorOf) {
  const max = Math.max(0, ...entries.map(([, count]) => count));
  return h('section', { class: 'card' },
    h('h2', { class: 'card-title' }, title),
    entries.length
      ? h('div', { class: 'hbars' }, entries.map(([name, count]) => h('div', { class: 'hbar' },
        h('span', { class: 'hbar-label', title: name }, name),
        h('span', { class: 'hbar-track' },
          h('span', { class: 'hbar-fill', style: { width: `${max ? (count / max) * 100 : 0}%`, '--c': colorOf ? colorOf(name) : undefined } })),
        h('span', { class: 'hbar-value' }, fmt.number(count)))))
      : h('p', { class: 'muted' }, t('stats.none')));
}
