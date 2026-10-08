/**
 * The dashboard: signing in, the frame around every page, and moving
 * between pages. Pages live in views/; each draws itself into the main area
 * and may return { refresh } to be redrawn with fresh data.
 */
import { api, clearToken, isSignedIn, tokenExpiry } from './api.js';
import { applyLang, getLang, setLang, t } from './i18n.js';
import { errorBox, h, icon, spinner } from './ui.js';
import { openAppointment } from './views/appointment.js';
import appointments from './views/appointments.js';
import { openBooking } from './views/booking.js';
import calendar from './views/calendar.js';
import customers, { openCustomer } from './views/customers.js';
import { renderLogin } from './views/login.js';
import settings from './views/settings.js';
import stats from './views/stats.js';
import team from './views/team.js';
import treatments from './views/treatments.js';

const ROUTES = [
  { path: 'calendar', icon: 'calendar', label: 'nav.calendar', short: 'nav.calendarShort', view: calendar },
  { path: 'appointments', icon: 'list', label: 'nav.appointments', short: 'nav.appointmentsShort', view: appointments },
  { path: 'customers', icon: 'users', label: 'nav.customers', short: 'nav.customersShort', view: customers },
  { path: 'team', icon: 'team', label: 'nav.team', short: 'nav.teamShort', view: team },
  { path: 'treatments', icon: 'leaf', label: 'nav.treatments', short: 'nav.treatmentsShort', view: treatments },
  { path: 'stats', icon: 'chart', label: 'nav.stats', short: 'nav.statsShort', view: stats },
  { path: 'settings', icon: 'settings', label: 'nav.settings', short: 'nav.settingsShort', view: settings },
];
const HOME = 'calendar';
// Bookings made through WhatsApp appear without anyone reloading the page.
const REFRESH_MS = 60000;
// Distinct, and readable as text and as fills in light and dark themes.
const PALETTE = ['#0d9488', '#7c3aed', '#d97706', '#e11d48', '#0284c7', '#65a30d', '#c026d3', '#ea580c'];

const root = document.getElementById('app');
let main = null; // the area pages draw into, while signed in
let page = null; // what the current page returned
let expiryTimer = null;

const ctx = {
  business: null,
  therapists: [],
  treatments: [],

  get tz() {
    return (this.business && this.business.timezone) || 'Asia/Jerusalem';
  },

  /** Load what every page uses: the clinic, its team and its treatments. */
  async loadReference() {
    const [business, therapists, treatmentList] = await Promise.all([
      api('/business'),
      api('/therapists', { query: { include_inactive: true } }),
      api('/treatments', { query: { include_inactive: true } }),
    ]);
    this.therapists = therapists;
    this.treatments = treatmentList;
    this.setBusiness(business);
  },

  setBusiness(business) {
    this.business = business;
    for (const el of root.querySelectorAll('.brand-name')) el.textContent = business.name;
    setTitle();
  },

  activeTherapists() {
    return this.therapists.filter((therapist) => therapist.is_active);
  },

  activeTreatments() {
    return this.treatments.filter((treatment) => treatment.is_active);
  },

  /**
   * Each therapist keeps one colour everywhere: calendar, lists and charts.
   * Colours go in the order people joined, so adding someone never changes
   * the colour everyone else already has.
   */
  therapistColor(id) {
    const index = this.therapists
      .slice()
      .sort((a, b) => (a.created_at || '').localeCompare(b.created_at || '') || a.id.localeCompare(b.id))
      .findIndex((therapist) => therapist.id === id);
    return PALETTE[Math.max(index, 0) % PALETTE.length];
  },

  navigate(path, params) {
    window.location.hash = hashFor(path, params);
  },

  /** Record a page's state in the address without redrawing it. */
  replaceParams(path, params) {
    window.history.replaceState(null, '', hashFor(path, params));
  },

  /** Something was booked, moved or edited: redraw the page behind the dialog. */
  changed() {
    if (page && page.refresh) page.refresh();
  },

  openAppointment: (appointment) => openAppointment(ctx, appointment),
  openBooking: (prefill) => openBooking(ctx, prefill),
  openCustomer: (customer) => openCustomer(ctx, customer),

  setLanguage(lang) {
    setLang(lang);
    if (main) {
      frame();
      route();
    } else {
      showLogin();
    }
  },

  signOut() {
    clearToken();
    showLogin();
  },
};

function hashFor(path, params = {}) {
  const query = new URLSearchParams(Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== null && value !== '')).toString();
  return `#/${path}${query ? `?${query}` : ''}`;
}

function parseHash() {
  const [path, query = ''] = window.location.hash.replace(/^#\/?/, '').split('?');
  return { path: path || HOME, params: Object.fromEntries(new URLSearchParams(query)) };
}

function setTitle() {
  const current = main && ROUTES.find((r) => r.path === parseHash().path);
  const clinic = (ctx.business && ctx.business.name) || t('app.name');
  document.title = current ? `${t(current.label)} · ${clinic}` : clinic;
}

function closeDialogs() {
  for (const dialog of document.querySelectorAll('dialog')) {
    if (dialog.open) dialog.close();
    else dialog.remove();
  }
}

function toggleLanguage() {
  ctx.setLanguage(getLang() === 'he' ? 'en' : 'he');
}

function showLogin({ expired = false } = {}) {
  if (page && page.unmount) page.unmount();
  page = null;
  main = null;
  clearTimeout(expiryTimer);
  closeDialogs();
  document.title = t('login.title');
  renderLogin(root, { expired, onSignedIn: start, onLanguage: toggleLanguage });
}

async function start() {
  frame();
  scheduleSignOut();
  main.replaceChildren(spinner());
  try {
    await ctx.loadReference();
  } catch (error) {
    // A rejected token has already brought the sign-in page back.
    if (error.status !== 401 && main) main.replaceChildren(errorBox(error, start));
    return;
  }
  route();
}

/** The frame around every page: navigation, the clinic's name, language and sign-out. */
function frame() {
  const brand = () => h('div', { class: 'brand' },
    h('span', { class: 'brand-mark' }, icon('calendar')),
    h('span', { class: 'brand-name' }, (ctx.business && ctx.business.name) || t('app.name')));
  const languageButton = () => h('button', { class: 'btn btn-ghost', type: 'button', onclick: toggleLanguage, lang: getLang() === 'he' ? 'en' : 'he' },
    icon('globe'), h('span', {}, t('lang.other')));

  const nav = h('nav', { class: 'nav', 'aria-label': t('nav.label') }, ROUTES.map((r) => h('a', {
    class: 'nav-item', href: `#/${r.path}`, dataset: { route: r.path },
  },
  icon(r.icon),
  h('span', { class: 'nav-label' }, t(r.label)),
  h('span', { class: 'nav-short', 'aria-hidden': 'true' }, t(r.short)))));

  main = h('main', { class: 'main', id: 'main' });
  root.replaceChildren(h('div', { class: 'app' },
    h('aside', { class: 'sidebar' },
      brand(),
      nav,
      h('div', { class: 'sidebar-foot' },
        languageButton(),
        h('button', { class: 'btn btn-ghost', type: 'button', onclick: () => ctx.signOut() }, icon('logout'), h('span', {}, t('nav.signOut'))))),
    h('div', { class: 'content' },
      h('header', { class: 'topbar' }, brand(), languageButton()),
      main)));
}

function route() {
  if (!main) return;
  const { path, params } = parseHash();
  const entry = ROUTES.find((r) => r.path === path);
  if (!entry) {
    window.history.replaceState(null, '', hashFor(HOME));
    route();
    return;
  }
  for (const link of root.querySelectorAll('.nav-item')) {
    if (link.dataset.route === path) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
  if (page && page.unmount) page.unmount();
  main.replaceChildren();
  main.scrollTop = 0;
  window.scrollTo(0, 0);
  page = entry.view(main, params, ctx) || null;
  setTitle();
}

function scheduleSignOut() {
  clearTimeout(expiryTimer);
  const expiry = tokenExpiry();
  if (expiry === null) return;
  const wait = expiry - Date.now();
  // Timers past about 24 days fire at once; the server refuses the token then anyway.
  if (wait < 2 ** 31 - 1) {
    expiryTimer = setTimeout(() => {
      clearToken();
      showLogin({ expired: true });
    }, Math.max(0, wait));
  }
}

/** Fresh data every minute, but never under an open dialog or in a hidden tab. */
function tick() {
  if (!main || !page || !page.refresh) return;
  if (document.visibilityState !== 'visible' || document.querySelector('dialog[open]')) return;
  page.refresh();
}

applyLang();
window.addEventListener('hashchange', route);
window.addEventListener('bb:signed-out', (event) => {
  if (main) showLogin({ expired: Boolean(event.detail && event.detail.expired) });
});
document.addEventListener('visibilitychange', tick);
setInterval(tick, REFRESH_MS);

if (isSignedIn()) start();
else showLogin();
