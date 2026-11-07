/**
 * The calendar: a day with a column per therapist, a week with a column
 * per day, or a month at a glance. Clicking free time starts a booking at
 * that time; clicking an appointment opens it.
 */
import { api } from '../api.js';
import * as D from '../dates.js';
import { fmt, t } from '../i18n.js';
import { empty, errorBox, fill, h, icon, ltr, spinner, toast } from '../ui.js';
import { groupBy } from '../util.js';

const PX_PER_MINUTE = 1.2; // an hour is 72px tall
const CLICK_STEP = 30; // a click on free time rounds down to this many minutes
const MONTH_CHIPS = 3; // appointments named in a month cell before "+N more"
const LIMIT = 1000; // the most the server returns at once
const VIEWS = ['day', 'week', 'month'];
const VIEW_LABELS = { day: 'cal.day', week: 'cal.week', month: 'cal.month' };
const FALLBACK_HOURS = [8 * 60, 20 * 60];

const startOf = (a) => D.toMinutes(a.time);
const endOf = (a) => startOf(a) + (a.duration_minutes || Math.max(D.toMinutes(a.end_time) - startOf(a), 15));

/** The hours a therapist works on a date, as [start, end] minutes, or null on a day off. */
function workingHours(therapist, date) {
  const days = (therapist.working_days || '').split(',').map((day) => day.trim());
  if (!days.includes(D.DAY_NAMES[D.weekday(date)])) return null;
  const start = D.toMinutes(therapist.working_hours_start);
  const end = D.toMinutes(therapist.working_hours_end);
  return start !== null && end !== null && end > start ? [start, end] : null;
}

/**
 * Lay overlapping appointments side by side. Each takes the first lane free
 * at its start, and everything in one run of overlaps shares its width.
 */
function pack(appointments) {
  const items = appointments
    .map((appointment) => ({ appointment, from: startOf(appointment), to: endOf(appointment) }))
    .sort((a, b) => a.from - b.from || b.to - a.to);
  const placed = [];
  let run = [];
  let laneEnds = [];
  let runEnd = -Infinity;
  const finishRun = () => {
    for (const item of run) item.lanes = laneEnds.length;
    placed.push(...run);
    run = [];
    laneEnds = [];
  };
  for (const item of items) {
    if (run.length && item.from >= runEnd) {
      finishRun();
      runEnd = -Infinity;
    }
    let lane = laneEnds.findIndex((end) => end <= item.from);
    if (lane === -1) {
      lane = laneEnds.length;
      laneEnds.push(item.to);
    } else {
      laneEnds[lane] = item.to;
    }
    item.lane = lane;
    run.push(item);
    runEnd = Math.max(runEnd, item.to);
  }
  finishRun();
  return placed;
}

export default function calendar(container, params, ctx) {
  const narrow = window.matchMedia('(max-width: 860px)').matches;
  const state = {
    view: VIEWS.includes(params.view) ? params.view : (narrow ? 'day' : 'week'),
    date: D.isDate(params.date) ? params.date : D.today(ctx.tz),
  };
  let appointments = [];
  let shown = null; // which span is on screen, to keep its scroll position on refresh
  let sequence = 0;

  const title = h('h1', { class: 'page-title cal-title' });
  const viewButtons = VIEWS.map((view) => h('button', {
    type: 'button', class: 'seg-btn', onclick: () => go({ view }),
  }, t(VIEW_LABELS[view])));
  const toolbar = h('div', { class: 'toolbar' },
    h('div', { class: 'toolbar-group' },
      h('button', { class: 'btn', type: 'button', onclick: () => go({ date: D.today(ctx.tz) }) }, t('cal.today')),
      h('div', { class: 'stepper' },
        h('button', { class: 'btn-icon', type: 'button', title: t('cal.prev'), 'aria-label': t('cal.prev'), onclick: () => step(-1) },
          icon('chevron', 'rtl-mirror')),
        h('button', { class: 'btn-icon', type: 'button', title: t('cal.next'), 'aria-label': t('cal.next'), onclick: () => step(1) },
          icon('chevron', 'ltr-mirror'))),
      title),
    h('div', { class: 'toolbar-group' },
      h('div', { class: 'seg', role: 'group', 'aria-label': t('cal.viewLabel') }, viewButtons),
      h('button', { class: 'btn btn-primary', type: 'button', onclick: newBooking }, icon('plus'), h('span', {}, t('cal.new')))));
  const legend = h('div', { class: 'cal-legend' });
  const body = h('div', { class: 'cal-body' }, spinner());

  fill(container, h('section', { class: 'page page-calendar' }, toolbar, legend, body));
  load();

  return { refresh: load };

  function go(change) {
    Object.assign(state, change);
    ctx.replaceParams('calendar', state);
    load();
  }

  function step(direction) {
    const { view, date } = state;
    if (view === 'day') go({ date: D.addDays(date, direction) });
    else if (view === 'week') go({ date: D.addDays(date, 7 * direction) });
    else go({ date: D.addMonths(date, direction) });
  }

  function span() {
    if (state.view === 'day') return [state.date, state.date];
    if (state.view === 'week') {
      const from = D.startOfWeek(state.date);
      return [from, D.addDays(from, 6)];
    }
    return [D.startOfWeek(D.startOfMonth(state.date)), D.addDays(D.startOfWeek(D.endOfMonth(state.date)), 6)];
  }

  function newBooking() {
    const today = D.today(ctx.tz);
    ctx.openBooking({ date: state.date > today ? state.date : today });
  }

  async function load() {
    const mine = ++sequence;
    const [from, to] = span();
    heading(from, to);
    body.classList.add('is-loading');
    try {
      const list = await api('/appointments', {
        query: { from_date: from, to_date: to, status: 'confirmed', limit: LIMIT },
      });
      if (mine !== sequence) return;
      appointments = list;
      draw(from, to);
    } catch (error) {
      if (mine !== sequence || error.status === 401) return;
      shown = null;
      fill(body, errorBox(error, load));
    } finally {
      if (mine === sequence) body.classList.remove('is-loading');
    }
  }

  function heading(from, to) {
    if (state.view === 'day') title.textContent = fmt.dateLong(state.date);
    else if (state.view === 'week') title.textContent = fmt.dateRange(from, to);
    else title.textContent = fmt.monthYear(state.date);
    viewButtons.forEach((button, i) => button.setAttribute('aria-pressed', String(VIEWS[i] === state.view)));

    const people = state.view === 'day' ? [] : ctx.activeTherapists().map((therapist) =>
      h('span', { class: 'legend-item', style: { '--c': ctx.therapistColor(therapist.id) } },
        h('span', { class: 'dot' }), therapist.name));
    fill(legend, ...people,
      h('span', { class: 'legend-hint' }, t(state.view === 'month' ? 'cal.hintMonth' : 'cal.hint')));
  }

  function draw(from, to) {
    const key = `${state.view}|${from}`;
    const old = body.querySelector('.cal-scroll');
    const keep = old && key === shown ? [old.scrollTop, old.scrollLeft] : null;
    shown = key;

    if (!ctx.therapists.length) {
      fill(body, empty(t('cal.noTherapists'),
        h('a', { class: 'btn btn-primary', href: '#/team' }, t('cal.addTherapist'))));
      return;
    }

    const view = state.view === 'month' ? month(D.range(from, to)) : grid(state.view === 'day' ? [state.date] : D.range(from, to));
    fill(body,
      appointments.length >= LIMIT ? h('p', { class: 'hint' }, t('cal.limit')) : null,
      view.el);
    if (view.scroller) {
      if (keep) [view.scroller.scrollTop, view.scroller.scrollLeft] = keep;
      else view.scroller.scrollTop = view.top;
    }
  }

  /** The time grid shared by the day and week views. */
  function grid(days) {
    const dayView = state.view === 'day';
    const today = D.today(ctx.tz);
    const now = D.nowMinutes(ctx.tz);
    const byDay = groupBy(appointments, (a) => a.date);

    let columns;
    if (dayView) {
      const date = days[0];
      const booked = byDay.get(date) || [];
      const busy = new Set(booked.map((a) => a.therapist_id));
      columns = ctx.therapists
        .filter((therapist) => therapist.is_active || busy.has(therapist.id))
        .map((therapist) => ({
          date,
          therapist,
          hours: workingHours(therapist, date),
          events: booked.filter((a) => a.therapist_id === therapist.id),
        }));
      if (!columns.length) {
        return { el: empty(t('cal.noTherapists'), h('a', { class: 'btn btn-primary', href: '#/team' }, t('cal.addTherapist'))) };
      }
    } else {
      columns = days.map((date) => ({ date, hours: clinicHours(date), events: byDay.get(date) || [] }));
    }

    // From opening to closing, widened to fit anyone's hours and anything booked outside them.
    let [start, end] = openingHours();
    for (const column of columns) {
      if (column.hours) {
        start = Math.min(start, column.hours[0]);
        end = Math.max(end, column.hours[1]);
      }
      for (const a of column.events) {
        start = Math.min(start, startOf(a));
        end = Math.max(end, endOf(a));
      }
    }
    start = Math.floor(start / 60) * 60;
    end = Math.min(24 * 60, Math.ceil(end / 60) * 60);
    const y = (minutes) => `${(minutes - start) * PX_PER_MINUTE}px`;
    const tall = (minutes) => `${minutes * PX_PER_MINUTE}px`;

    const cells = [h('div', { class: 'cal-corner' })];
    for (const column of columns) cells.push(head(column));
    const gutter = h('div', { class: 'cal-gutter', 'aria-hidden': 'true' });
    for (let minute = start; minute < end; minute += 60) {
      gutter.append(h('span', { class: 'cal-hour', style: { top: y(minute) } }, D.fromMinutes(minute)));
    }
    cells.push(gutter);
    for (const column of columns) cells.push(columnBody(column));

    const gridEl = h('div', {
      class: ['cal-grid', dayView ? 'is-day' : 'is-week'],
      style: { '--cols': columns.length, '--hour': tall(60), '--height': tall(end - start) },
    }, cells);
    const scroller = h('div', { class: 'cal-scroll' }, gridEl);

    // Open where there is something to see: the hour before now, or the first appointment.
    let focus = start;
    if (days.includes(today)) focus = now - 60;
    else {
      const first = Math.min(...columns.flatMap((column) => column.events.map(startOf)));
      if (Number.isFinite(first)) focus = first - 30;
    }
    return { el: scroller, scroller, top: Math.max(0, (focus - start) * PX_PER_MINUTE) };

    function head(column) {
      if (dayView) {
        const { therapist } = column;
        return h('div', { class: 'cal-head', style: { '--c': ctx.therapistColor(therapist.id) } },
          h('span', { class: 'cal-head-name' }, h('span', { class: 'dot' }), h('span', { class: 'ellipsis' }, therapist.name)),
          h('span', { class: 'cal-head-sub' }, column.hours
            ? ltr(`${D.fromMinutes(column.hours[0])}–${D.fromMinutes(column.hours[1])}`)
            : t('cal.dayOff')));
      }
      return h('button', {
        type: 'button',
        class: ['cal-head', 'is-link', column.date === today && 'is-today'],
        'aria-label': fmt.dateLong(column.date),
        onclick: () => go({ view: 'day', date: column.date }),
      },
      h('span', { class: 'cal-head-sub' }, fmt.weekday(column.date, 'short')),
      h('span', { class: 'cal-head-num' }, fmt.dayNumber(column.date)));
    }

    function columnBody(column) {
      const col = h('div', { class: ['cal-col', column.date < today && 'is-past'] });

      // Hours nobody works are shaded, so free time stands out.
      if (!column.hours) {
        col.append(h('div', { class: 'cal-off', style: { top: '0px', height: tall(end - start) } },
          h('span', {}, t(dayView ? 'cal.dayOff' : 'cal.closed'))));
      } else {
        if (column.hours[0] > start) col.append(h('div', { class: 'cal-off', style: { top: '0px', height: tall(column.hours[0] - start) } }));
        if (column.hours[1] < end) col.append(h('div', { class: 'cal-off', style: { top: y(column.hours[1]), height: tall(end - column.hours[1]) } }));
      }
      if (column.date === today && now > start) {
        col.append(h('div', { class: 'cal-past', style: { top: '0px', height: tall(Math.min(now, end) - start) } }));
      }

      for (const item of pack(column.events)) col.append(event(item));

      if (column.date === today && now >= start && now <= end) {
        col.append(h('div', { class: ['cal-now', (!dayView || column === columns[0]) && 'with-dot'], style: { top: y(now) } }));
      }

      col.addEventListener('click', (e) => {
        if (e.target.closest('.cal-event')) return;
        const offset = e.clientY - col.getBoundingClientRect().top;
        const minute = start + Math.floor(offset / PX_PER_MINUTE / CLICK_STEP) * CLICK_STEP;
        book(column.date, minute, column.therapist && column.therapist.id);
      });
      return col;
    }

    function event({ appointment: a, lane, lanes, from, to }) {
      const height = Math.max((to - from) * PX_PER_MINUTE, 22);
      const who = a.customer_name || fmt.phone(a.customer_phone);
      return h('button', {
        type: 'button',
        class: ['cal-event', height < 46 && 'is-short'],
        style: {
          top: y(from),
          height: `${height - 2}px`,
          insetInlineStart: `calc(${(lane / lanes) * 100}% + 2px)`,
          width: `calc(${100 / lanes}% - 4px)`,
          '--c': ctx.therapistColor(a.therapist_id),
        },
        title: `${a.time}–${a.end_time} · ${who} · ${a.treatment} · ${a.therapist}`,
        onclick: () => ctx.openAppointment(a),
      },
      h('span', { class: 'ev-time' }, ltr(a.time)),
      h('span', { class: 'ev-who' }, who),
      h('span', { class: 'ev-what' }, dayView ? a.treatment : `${a.treatment} · ${a.therapist}`));
    }
  }

  function month(days) {
    const today = D.today(ctx.tz);
    const current = state.date.slice(0, 7);
    const byDay = groupBy(appointments, (a) => a.date);

    const cells = days.slice(0, 7).map((date) => h('div', { class: 'month-head' }, fmt.weekday(date, 'short')));
    for (const date of days) {
      const items = byDay.get(date) || [];
      const cell = h('div', {
        class: ['month-cell', date.slice(0, 7) !== current && 'is-other', date === today && 'is-today', date < today && 'is-past'],
      },
      h('div', { class: 'month-top' },
        // Keyboard users reach the day through this; the click itself is the cell's.
        h('button', { type: 'button', class: 'month-day', 'aria-label': `${fmt.dateLong(date)} · ${t('cal.count', { n: items.length })}` },
          fmt.dayNumber(date)),
        items.length ? h('span', { class: 'month-count' }, String(items.length)) : null),
      h('div', { class: 'month-chips' },
        items.slice(0, MONTH_CHIPS).map((a) => h('button', {
          type: 'button',
          class: 'month-chip',
          style: { '--c': ctx.therapistColor(a.therapist_id) },
          title: `${a.time} · ${a.customer_name || fmt.phone(a.customer_phone)} · ${a.treatment} · ${a.therapist}`,
          onclick: (e) => {
            e.stopPropagation();
            ctx.openAppointment(a);
          },
        }, ltr(a.time), ' ', a.customer_name || fmt.phone(a.customer_phone))),
        items.length > MONTH_CHIPS ? h('span', { class: 'month-more' }, t('cal.more', { n: items.length - MONTH_CHIPS })) : null));
      cell.addEventListener('click', () => go({ view: 'day', date }));
      cells.push(cell);
    }
    return { el: h('div', { class: 'month' }, cells) };
  }

  function book(date, minute, therapistId) {
    const today = D.today(ctx.tz);
    if (date < today || (date === today && minute + CLICK_STEP <= D.nowMinutes(ctx.tz))) {
      toast(t('cal.past'));
      return;
    }
    ctx.openBooking({ date, time: D.fromMinutes(minute), therapistId });
  }

  function openingHours() {
    const business = ctx.business || {};
    const open = D.toMinutes(business.working_hours_start);
    const close = D.toMinutes(business.working_hours_end);
    return open !== null && close !== null && close > open ? [open, close] : FALLBACK_HOURS;
  }

  /** The span of the day anyone works, or null if nobody does. */
  function clinicHours(date) {
    const spans = ctx.activeTherapists().map((therapist) => workingHours(therapist, date)).filter(Boolean);
    if (!spans.length) return null;
    return [Math.min(...spans.map((s) => s[0])), Math.max(...spans.map((s) => s[1]))];
  }
}
