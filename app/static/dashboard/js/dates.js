/**
 * Calendar dates as 'YYYY-MM-DD' strings, in the clinic's timezone.
 *
 * The API already gives dates and times as the clinic's own, so arithmetic
 * here is on the calendar date alone, done through UTC, where no
 * daylight-saving change can move a day. Only "today" and "now" need the
 * timezone, and they ask for it by name.
 */

// The names the server stores working days under.
export const DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

export function toUTC(iso) {
  const [year, month, day] = iso.split('-').map(Number);
  return new Date(Date.UTC(year, month - 1, day));
}

function fromUTC(date) {
  return date.toISOString().slice(0, 10);
}

/** Whether a string is a real date: '2026-02-30' is not. */
export function isDate(value) {
  const match = ISO_DATE.exec(value || '');
  return Boolean(match) && fromUTC(toUTC(value)) === value;
}

export function addDays(iso, days) {
  const date = toUTC(iso);
  date.setUTCDate(date.getUTCDate() + days);
  return fromUTC(date);
}

/** The same day in another month, or that month's last day if it is shorter. */
export function addMonths(iso, months) {
  const [year, month, day] = iso.split('-').map(Number);
  const first = new Date(Date.UTC(year, month - 1 + months, 1));
  const last = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0)).getUTCDate();
  first.setUTCDate(Math.min(day, last));
  return fromUTC(first);
}

/** 0 for Sunday through 6 for Saturday. */
export function weekday(iso) {
  return toUTC(iso).getUTCDay();
}

/** Weeks start on Sunday, as they do in Israel. */
export function startOfWeek(iso) {
  return addDays(iso, -weekday(iso));
}

export function startOfMonth(iso) {
  return `${iso.slice(0, 8)}01`;
}

export function endOfMonth(iso) {
  const [year, month] = iso.split('-').map(Number);
  return fromUTC(new Date(Date.UTC(year, month, 0)));
}

export function daysBetween(from, to) {
  return Math.round((toUTC(to) - toUTC(from)) / 86400000);
}

/** Every date from `from` to `to`, both included. */
export function range(from, to) {
  const dates = [];
  for (let date = from; date <= to; date = addDays(date, 1)) dates.push(date);
  return dates;
}

/** Minutes since midnight for 'HH:MM', or null if it is not a time. */
export function toMinutes(hhmm) {
  const match = /^(\d{1,2}):(\d{2})/.exec(hhmm || '');
  return match ? Number(match[1]) * 60 + Number(match[2]) : null;
}

export function fromMinutes(total) {
  const hours = Math.floor(total / 60);
  const minutes = total % 60;
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`;
}

function clockIn(timeZone) {
  const options = {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  };
  let format;
  try {
    format = new Intl.DateTimeFormat('en-US', { ...options, timeZone });
  } catch {
    // A zone this browser does not know: its own clock is the best guess.
    format = new Intl.DateTimeFormat('en-US', options);
  }
  return Object.fromEntries(format.formatToParts(new Date()).map((part) => [part.type, part.value]));
}

/** Today's date at the clinic, wherever the dashboard is opened. */
export function today(timeZone) {
  const parts = clockIn(timeZone);
  return `${parts.year}-${parts.month}-${parts.day}`;
}

/** Minutes since midnight at the clinic, now. */
export function nowMinutes(timeZone) {
  const parts = clockIn(timeZone);
  return (Number(parts.hour) % 24) * 60 + Number(parts.minute);
}
