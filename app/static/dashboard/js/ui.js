/**
 * Building the page.
 *
 * Everything on screen is made by h() from text and properties, never by
 * parsing HTML. Customer names and notes come from outside the clinic, and
 * this way they can only ever be shown, never run. The server's
 * Content-Security-Policy is the second lock on the same door.
 */
import { t } from './i18n.js';

const SVG_NS = 'http://www.w3.org/2000/svg';

// Set as properties so they change the live state, not just the markup.
const PROPERTIES = new Set(['checked', 'disabled', 'selected', 'readOnly', 'required', 'hidden', 'multiple']);

let lastId = 0;
export const uid = (prefix = 'el') => `${prefix}-${++lastId}`;

export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  let value;
  for (const [key, val] of Object.entries(props || {})) {
    if (val === undefined || val === null || val === false) continue;
    if (key === 'class') el.className = Array.isArray(val) ? val.filter(Boolean).join(' ') : val;
    else if (key === 'style') setStyle(el, val);
    else if (key === 'dataset') Object.assign(el.dataset, val);
    else if (key === 'value') value = val; // after the children: a select needs its options first
    else if (key.startsWith('on')) el.addEventListener(key.slice(2), val);
    else if (PROPERTIES.has(key)) el[key] = val;
    else el.setAttribute(key, val === true ? '' : String(val));
  }
  append(el, children);
  if (value !== undefined) el.value = value;
  return el;
}

/** Styles go through the CSSOM, which the Content-Security-Policy allows. */
function setStyle(el, styles) {
  for (const [name, val] of Object.entries(styles)) {
    if (val === undefined || val === null) continue;
    if (name.startsWith('--')) el.style.setProperty(name, String(val));
    else el.style[name] = val;
  }
}

export function append(parent, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false || child === true) continue;
    parent.append(child instanceof Node ? child : String(child));
  }
  return parent;
}

/**
 * Replace an element's children. Unlike the DOM's own replaceChildren(),
 * a null or false child is skipped rather than shown as the word "null".
 */
export function fill(parent, ...children) {
  parent.replaceChildren();
  return append(parent, children);
}

export function svg(tag, attrs, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [key, val] of Object.entries(attrs || {})) {
    if (val !== undefined && val !== null && val !== false) el.setAttribute(key, String(val));
  }
  return append(el, children);
}

// Line icons on a 24px grid, drawn with the current text colour.
const ICONS = {
  calendar: [['rect', { x: 3, y: 4.5, width: 18, height: 16.5, rx: 2.5 }], ['path', { d: 'M3 9.5h18M8 2.5v4M16 2.5v4' }]],
  list: [['path', { d: 'M9 6h11M9 12h11M9 18h11' }], ['circle', { cx: 4.5, cy: 6, r: 1.2 }], ['circle', { cx: 4.5, cy: 12, r: 1.2 }], ['circle', { cx: 4.5, cy: 18, r: 1.2 }]],
  users: [['circle', { cx: 9, cy: 8, r: 3.5 }], ['path', { d: 'M2.5 20c.8-3.6 3.4-5.5 6.5-5.5s5.7 1.9 6.5 5.5M15.5 4.8a3.5 3.5 0 0 1 0 6.4M17.5 14.8c2 .7 3.4 2.4 4 5.2' }]],
  team: [['circle', { cx: 12, cy: 7.5, r: 3.5 }], ['path', { d: 'M5 20.5c.9-4 3.6-6 7-6s6.1 2 7 6' }]],
  leaf: [['path', { d: 'M5 19C5 11 10 5.5 20 5c-.5 10-6 15-14 15' }], ['path', { d: 'M5 19l7-7' }]],
  chart: [['path', { d: 'M3 20h18M6 20v-6M11 20V6M16 20v-9' }]],
  settings: [['path', { d: 'M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1' }], ['circle', { cx: 15, cy: 6, r: 2 }], ['circle', { cx: 9, cy: 12, r: 2 }], ['circle', { cx: 17, cy: 18, r: 2 }]],
  plus: [['path', { d: 'M12 5v14M5 12h14' }]],
  chevron: [['path', { d: 'M15 6l-6 6 6 6' }]],
  x: [['path', { d: 'M6 6l12 12M18 6L6 18' }]],
  logout: [['path', { d: 'M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 8l-4 4 4 4M6 12h10' }]],
  chat: [['path', { d: 'M4 20l1.4-4A8 8 0 1 1 8.6 19z' }]],
  clock: [['circle', { cx: 12, cy: 12, r: 9 }], ['path', { d: 'M12 7v5l3 2' }]],
  search: [['circle', { cx: 11, cy: 11, r: 6.5 }], ['path', { d: 'M20 20l-4.2-4.2' }]],
  globe: [['circle', { cx: 12, cy: 12, r: 9 }], ['path', { d: 'M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z' }]],
  edit: [['path', { d: 'M4 20h4L19 9l-4-4L4 16zM13 7l4 4' }]],
  alert: [['circle', { cx: 12, cy: 12, r: 9 }], ['path', { d: 'M12 7.5V13M12 16.5v.01' }]],
  check: [['path', { d: 'M5 12.5l4.5 4.5L19 7.5' }]],
  phone: [['path', { d: 'M5 4h3l1.5 4-2 1.5a11 11 0 0 0 7 7l1.5-2 4 1.5v3a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z' }]],
  mail: [['rect', { x: 3, y: 5, width: 18, height: 14, rx: 2 }], ['path', { d: 'M3.5 6.5L12 13l8.5-6.5' }]],
  power: [['path', { d: 'M12 3v8M6.3 6.8a8 8 0 1 0 11.4 0' }]],
  building: [['path', { d: 'M4 21V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v16M16 9h2a2 2 0 0 1 2 2v10M2 21h20M8 7h4M8 11h4M8 15h4' }]],
  key: [['circle', { cx: 8, cy: 15, r: 4 }], ['path', { d: 'M10.8 12.2L20 3M17 6l3 3M14.5 8.5l2 2' }]],
  copy: [['rect', { x: 8, y: 8, width: 12, height: 12, rx: 2 }], ['path', { d: 'M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2' }]],
};

export function icon(name, className) {
  const el = svg('svg', {
    class: ['icon', className].filter(Boolean).join(' '),
    viewBox: '0 0 24 24',
    'aria-hidden': 'true',
    focusable: 'false',
  });
  for (const [tag, attrs] of ICONS[name] || []) el.append(svg(tag, attrs));
  return el;
}

/** Text that reads left to right even inside Hebrew: times, phones, prices. */
export function ltr(text) {
  return h('span', { dir: 'ltr', class: 'ltr' }, text);
}

export function badge(text, kind) {
  return h('span', { class: ['badge', kind && `badge-${kind}`] }, text);
}

export function dot(color) {
  return h('span', { class: 'dot', style: { '--c': color } });
}

export function spinner() {
  return h('div', { class: 'loading', role: 'status' },
    h('span', { class: 'spinner', 'aria-hidden': 'true' }),
    h('span', { class: 'visually-hidden' }, t('common.loading')));
}

export function empty(text, ...actions) {
  return h('div', { class: 'empty' },
    h('p', {}, text),
    actions.length ? h('div', { class: 'button-row' }, actions) : null);
}

export function notice(kind, iconName, ...content) {
  return h('div', { class: ['notice', `notice-${kind}`] },
    icon(iconName),
    h('div', { class: 'notice-text' }, content));
}

export function errorBox(error, retry) {
  return h('div', { class: 'notice notice-error', role: 'alert' },
    icon('alert'),
    h('div', { class: 'notice-text' },
      h('p', {}, (error && error.message) || t('err.generic')),
      retry ? h('button', { class: 'btn btn-small', type: 'button', onclick: retry }, t('common.retry')) : null));
}

/** A labelled form control. `hint` is text or an element to keep updating. */
export function field(label, control, { hint, className } = {}) {
  if (!control.id) control.id = uid('field');
  let hintEl = null;
  if (hint) {
    hintEl = hint instanceof Node ? hint : h('p', { class: 'hint' }, hint);
    if (!hintEl.id) hintEl.id = uid('hint');
    control.setAttribute('aria-describedby', hintEl.id);
  }
  return h('div', { class: ['field', className] }, h('label', { for: control.id }, label), control, hintEl);
}

/** An on/off switch: a real checkbox, drawn as a switch. */
export function toggle(label, { checked = false, onchange } = {}) {
  const input = h('input', { type: 'checkbox', role: 'switch', checked, onchange });
  const el = h('label', { class: 'switch' }, input, h('span', { class: 'switch-track', 'aria-hidden': 'true' }), h('span', {}, label));
  return { el, input };
}

/**
 * Run `task` with the button disabled, so a double click cannot send the
 * same request twice. The button is enabled again once it settles.
 */
export async function busy(button, task) {
  if (button.dataset.busy) return undefined;
  button.dataset.busy = '1';
  button.disabled = true;
  button.classList.add('is-busy');
  try {
    return await task();
  } finally {
    delete button.dataset.busy;
    button.disabled = false;
    button.classList.remove('is-busy');
  }
}

/**
 * A modal dialog. The caller fills `body` and `foot`. Escape closes it, as
 * does a click on the backdrop when `dismissable`; forms turn that off so a
 * stray click cannot throw away what was typed.
 */
export function modal({ title = '', wide = false, dismissable = true, onClose } = {}) {
  const heading = h('h2', { class: 'modal-title', id: uid('title') }, title);
  const body = h('div', { class: 'modal-body' });
  const foot = h('div', { class: 'modal-foot' });
  const dialog = h('dialog', { class: ['modal', wide && 'modal-wide'], 'aria-labelledby': heading.id },
    h('div', { class: 'modal-head' },
      heading,
      h('button', { class: 'btn-icon', type: 'button', 'aria-label': t('common.close'), onclick: () => close() }, icon('x'))),
    body,
    foot);

  function close() {
    if (dialog.open) {
      dialog.close();
    } else if (dialog.isConnected) {
      // Closed before it had opened: there will be no close event.
      dialog.remove();
      if (onClose) onClose();
    }
  }
  dialog.addEventListener('close', () => {
    dialog.remove();
    if (onClose) onClose();
  });

  if (dismissable) {
    // A press on the backdrop lands on the dialog itself, outside its box.
    // Both ends must be outside, or selecting text in a field and letting
    // go past the edge would close the dialog.
    const outside = (event) => {
      const box = dialog.getBoundingClientRect();
      return event.target === dialog && (event.clientX < box.left || event.clientX > box.right
        || event.clientY < box.top || event.clientY > box.bottom);
    };
    let pressedOutside = false;
    dialog.addEventListener('pointerdown', (event) => { pressedOutside = outside(event); });
    dialog.addEventListener('click', (event) => {
      if (pressedOutside && outside(event)) close();
      pressedOutside = false;
    });
  }

  document.body.append(dialog);
  // Opened once the caller has filled it, so a field marked autofocus is
  // there to receive the focus.
  queueMicrotask(() => {
    if (dialog.isConnected && !dialog.open) dialog.showModal();
  });
  return { dialog, body, foot, close, setTitle: (text) => { heading.textContent = text; } };
}

/** Ask before doing something that cannot be undone. Resolves true to go ahead. */
export function confirmDialog({ title, message, confirm, cancel, danger = false }) {
  return new Promise((resolve) => {
    let answer = false;
    const m = modal({ title, onClose: () => resolve(answer) });
    m.body.append(...(Array.isArray(message) ? message : [h('p', {}, message)]));
    m.foot.append(
      h('span', { class: 'spacer' }),
      h('button', { class: 'btn', type: 'button', onclick: () => m.close() }, cancel || t('common.cancel')),
      h('button', {
        class: ['btn', danger ? 'btn-danger' : 'btn-primary'],
        type: 'button',
        autofocus: true,
        onclick: () => { answer = true; m.close(); },
      }, confirm || t('common.ok')));
  });
}

// Toasts sit in a popover where the browser has one: the top layer, above
// any open dialog. Elsewhere they show under the dialog's backdrop instead.
const canPopover = typeof HTMLElement !== 'undefined' && 'showPopover' in HTMLElement.prototype;

function toastHost() {
  let host = document.getElementById('toasts');
  if (!host) {
    host = h('div', { id: 'toasts', class: 'toasts', popover: canPopover ? 'manual' : undefined });
    document.body.append(host);
  }
  if (canPopover) {
    try {
      // Showing it again lifts it above a dialog opened since.
      if (host.matches(':popover-open')) host.hidePopover();
      host.showPopover();
    } catch {
      // Still on the page, only perhaps beneath a dialog.
    }
  }
  return host;
}

export function toast(message, kind = 'info') {
  const host = toastHost();
  const item = h('div', { class: ['toast', `toast-${kind}`], role: kind === 'error' ? 'alert' : 'status' },
    icon(kind === 'error' ? 'alert' : kind === 'success' ? 'check' : 'clock'),
    h('span', {}, message));
  host.append(item);
  setTimeout(() => {
    item.classList.add('is-leaving');
    setTimeout(() => {
      item.remove();
      if (canPopover && !host.children.length) {
        try { host.hidePopover(); } catch { /* already hidden */ }
      }
    }, 220);
  }, kind === 'error' ? 7000 : 3500);
}
