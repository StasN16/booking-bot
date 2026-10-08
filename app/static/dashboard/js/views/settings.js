/**
 * The clinic's own details, the dashboard's language, and signing out.
 */
import { api } from '../api.js';
import { getLang, t } from '../i18n.js';
import { busy, field, fill, h, icon, ltr, toast, uid } from '../ui.js';

export default function settingsView(container, params, ctx) {
  const b = ctx.business || {};
  const name = h('input', { type: 'text', required: true, maxlength: 255, value: b.name || '' });
  const phone = h('input', { type: 'tel', dir: 'ltr', required: true, maxlength: 20, value: b.phone || '' });
  const email = h('input', { type: 'email', dir: 'ltr', maxlength: 255, value: b.email || '' });
  const address = h('input', { type: 'text', maxlength: 500, value: b.address || '' });
  const open = h('input', { type: 'time', step: 300, value: b.working_hours_start || '' });
  const close = h('input', { type: 'time', step: 300, value: b.working_hours_end || '' });
  const error = h('p', { class: 'form-error', role: 'alert' });
  const formId = uid('form');
  const submit = h('button', { class: 'btn btn-primary', type: 'submit', form: formId }, t('common.save'));

  async function save(event) {
    event.preventDefault();
    error.textContent = '';
    const problem = !name.value.trim() ? t('set.needName')
      : phone.value.trim().length < 5 ? t('set.needPhone')
        : email.value.trim() && !email.checkValidity() ? t('team.badEmail')
          : (open.value || close.value) && !(open.value && close.value && open.value < close.value) ? t('team.badHours')
            : '';
    if (problem) {
      error.textContent = problem;
      return;
    }
    const body = {
      name: name.value.trim(),
      phone: phone.value.trim(),
      email: email.value.trim() || null,
      address: address.value.trim() || null,
    };
    if (open.value && close.value) {
      body.working_hours_start = open.value.slice(0, 5);
      body.working_hours_end = close.value.slice(0, 5);
    }
    await busy(submit, async () => {
      try {
        ctx.setBusiness(await api('/business', { method: 'PUT', body }));
        toast(t('set.saved'), 'success');
      } catch (err) {
        error.textContent = err.message;
      }
    });
  }

  const languages = [['he', 'עברית'], ['en', 'English']].map(([code, label]) => h('button', {
    type: 'button',
    class: 'seg-btn',
    lang: code,
    'aria-pressed': String(getLang() === code),
    onclick: () => ctx.setLanguage(code),
  }, label));

  fill(container, h('section', { class: 'page settings' },
    h('div', { class: 'page-head' }, h('h1', { class: 'page-title' }, t('set.title'))),
    h('section', { class: 'card' },
      h('h2', { class: 'card-title' }, t('set.business')),
      h('form', { id: formId, class: 'form-grid', novalidate: true, onsubmit: save },
        field(t('set.name'), name, { className: 'span-2' }),
        field(t('set.phone'), phone),
        field(t('set.email'), email, { hint: t('common.optional') }),
        field(t('set.address'), address, { className: 'span-2', hint: t('common.optional') }),
        field(t('set.open'), open),
        field(t('set.close'), close),
        h('p', { class: 'hint span-2' }, t('set.hoursHint')),
        h('div', { class: 'span-2' }, error)),
      h('div', { class: 'button-row end' }, submit)),
    h('section', { class: 'card' },
      h('h2', { class: 'card-title' }, t('set.language')),
      h('div', { class: 'seg', role: 'group', 'aria-label': t('set.language') }, languages)),
    h('section', { class: 'card' },
      h('h2', { class: 'card-title' }, t('set.system')),
      h('dl', { class: 'details' },
        h('dt', {}, t('set.timezone')), h('dd', {}, ltr(ctx.tz))),
      h('p', { class: 'hint' }, t('set.timezoneHint')),
      h('div', { class: 'button-row' },
        h('a', { class: 'btn', href: '/docs', target: '_blank', rel: 'noopener noreferrer' }, t('set.apiDocs')),
        h('button', { class: 'btn btn-danger-ghost', type: 'button', onclick: () => ctx.signOut() }, icon('logout'), h('span', {}, t('nav.signOut')))))));
}
