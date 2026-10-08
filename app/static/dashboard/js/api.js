/**
 * Talking to the server.
 *
 * Every request goes through api(), which adds the sign-in token, gives up
 * after a while instead of hanging, and turns any failure into an ApiError
 * whose message is fit to show as it is.
 */
import { serverText, t } from './i18n.js';

const BASE = '/api/v1';
const TIMEOUT_MS = 20000;
const TOKEN_KEY = 'bb.token';
const EXPIRES_KEY = 'bb.expires';
// The clinic the owner of the service is looking at. A clinic login never
// sets it: the server holds a clinic login to its own clinic anyway.
const CLINIC_KEY = 'bb.clinic';

// Storage can be refused (private browsing, blocked site data). The token
// then lasts as long as the page does.
const memory = {};
const store = {
  get(key) {
    try { return localStorage.getItem(key); } catch { return memory[key] ?? null; }
  },
  set(key, value) {
    try { localStorage.setItem(key, value); } catch { memory[key] = value; }
  },
  remove(key) {
    try { localStorage.removeItem(key); } catch { /* nothing was stored */ }
    delete memory[key];
  },
};

export class ApiError extends Error {
  constructor(status, message, retryAfter = 0) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

export function saveToken({ access_token: token, expires_at: expiresAt }) {
  store.set(TOKEN_KEY, token);
  store.set(EXPIRES_KEY, expiresAt || '');
}

export function clearToken() {
  store.remove(TOKEN_KEY);
  store.remove(EXPIRES_KEY);
}

export function getClinic() {
  return store.get(CLINIC_KEY);
}

export function setClinic(id) {
  if (id) store.set(CLINIC_KEY, id);
  else store.remove(CLINIC_KEY);
}

/** When the token runs out, in milliseconds since the epoch, or null if unknown. */
export function tokenExpiry() {
  const raw = store.get(EXPIRES_KEY);
  if (!raw) return null;
  // Python writes microseconds; not every browser reads past milliseconds.
  const time = Date.parse(raw.replace(/(\.\d{3})\d+/, '$1'));
  return Number.isNaN(time) ? null : time;
}

export function isSignedIn() {
  if (!store.get(TOKEN_KEY)) return false;
  const expiry = tokenExpiry();
  return expiry === null || expiry > Date.now() + 30000;
}

export async function api(path, { method = 'GET', body, query, auth = true } = {}) {
  const url = new URL(BASE + path, window.location.origin);
  for (const [key, value] of Object.entries(query || {})) {
    if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, value);
  }

  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const token = auth ? store.get(TOKEN_KEY) : null;
  if (token) headers.Authorization = `Bearer ${token}`;
  const clinic = auth ? store.get(CLINIC_KEY) : null;
  if (clinic) headers['X-Business-Id'] = clinic;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  let response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      cache: 'no-store',
    });
  } catch {
    throw new ApiError(0, t(controller.signal.aborted ? 'err.timeout' : 'err.network'));
  } finally {
    clearTimeout(timer);
  }

  const data = await response.json().catch(() => null);
  if (response.ok) return data;

  const message = describe(response.status, data);
  if (response.status === 401 && auth) {
    // The token ran out, the server's secret changed, or the login was
    // switched off: sign in again, saying why.
    clearToken();
    window.dispatchEvent(new CustomEvent('bb:signed-out', { detail: { expired: true, message } }));
  }
  throw new ApiError(response.status, message, Number(response.headers.get('Retry-After')) || 0);
}

function describe(status, data) {
  const detail = data && data.detail;
  if (typeof detail === 'string') return serverText(detail);
  if (Array.isArray(detail) && detail.length) {
    // Validation: one message per field the server refused.
    const messages = detail.map((item) => serverText(String(item.msg || ''))).filter(Boolean);
    return [...new Set(messages)].join(' · ') || t('err.invalid');
  }
  if (status === 404) return t('err.notFound');
  if (status >= 500) return t('err.server');
  return t('err.generic');
}
