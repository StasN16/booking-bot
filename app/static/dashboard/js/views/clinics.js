/**
 * The owner's page: every clinic on the service. Each has its own
 * dashboard, logins and WhatsApp number, and sees only its own data.
 * Only the owner of the service gets this page.
 */
import { api } from '../api.js';
import { fmt, t } from '../i18n.js';
import {
  badge, busy, confirmDialog, errorBox, field, fill, h, icon, ltr, modal, notice, spinner, timeSelect, toast, toggle, uid,
} from '../ui.js';

const PHONE_NUMBER_ID = /^\d{5,30}$/;

export default function clinicsView(container, params, ctx) {
  const list = h('div', { class: 'cards clinic-cards' }, spinner());
  fill(container, h('section', { class: 'page' },
    h('div', { class: 'page-head' },
      h('h1', { class: 'page-title' }, t('clinics.title')),
      h('button', { class: 'btn btn-primary', type: 'button', onclick: () => editClinic(null) },
        icon('plus'), h('span', {}, t('clinics.add')))),
    h('p', { class: 'hint' }, t('clinics.intro')),
    list));
  load();

  return { refresh: load };

  async function load() {
    try {
      await ctx.loadClinics();
      draw();
    } catch (error) {
      if (error.status !== 401) fill(list, errorBox(error, load));
    }
  }

  function draw() {
    fill(list, ctx.clinics.map(card));
  }

  function whatsappLine(clinic) {
    if (clinic.whatsapp_phone_id) return [t('clinics.numberShort'), ' ', ltr(clinic.whatsapp_phone_id)];
    if (clinic.whatsapp_from_env) return t('clinics.envNumber');
    return h('span', { class: 'warning-text' }, t('clinics.noNumber'));
  }

  function card(clinic) {
    const showing = Boolean(ctx.business && ctx.business.id === clinic.id);
    return h('article', { class: ['card', !clinic.is_active && 'is-inactive', showing && 'is-current'] },
      h('div', { class: 'card-head' },
        h('h2', { class: 'card-title' }, clinic.name),
        clinic.is_home ? badge(t('clinics.home'), 'muted') : null,
        clinic.is_active ? null : badge(t('clinics.off'), 'warning')),
      h('div', { class: 'card-meta' },
        h('span', { class: 'meta-row' }, icon('chat'), h('span', {}, whatsappLine(clinic))),
        h('span', { class: 'meta-row' }, icon('key'), t('clinics.loginsCount', { n: clinic.logins })),
        h('span', { class: 'meta-row' }, icon('calendar'), t('clinics.upcoming', { n: clinic.upcoming_appointments }))),
      h('div', { class: 'card-actions' },
        showing
          ? badge(t('clinics.showing'), 'success')
          : h('button', { class: 'btn btn-small btn-primary', type: 'button', onclick: () => ctx.switchClinic(clinic.id) },
            t('clinics.open')),
        h('button', { class: 'btn btn-small', type: 'button', onclick: () => openLogins(clinic) },
          icon('key'), h('span', {}, t('clinics.logins'))),
        h('button', { class: 'btn btn-small', type: 'button', onclick: () => editClinic(clinic) },
          icon('edit'), h('span', {}, t('common.edit')))));
  }

  function editClinic(clinic) {
    const editing = Boolean(clinic);
    const value = (key, fallback = '') => (editing && clinic[key]) || fallback;
    const m = modal({ title: t(editing ? 'clinics.edit' : 'clinics.add'), dismissable: false, wide: true });

    const name = h('input', { type: 'text', required: true, maxlength: 255, value: value('name'), autofocus: true });
    const phone = h('input', { type: 'tel', dir: 'ltr', required: true, maxlength: 20, value: value('phone') });
    const email = h('input', { type: 'email', dir: 'ltr', maxlength: 255, value: value('email') });
    const address = h('input', { type: 'text', maxlength: 500, value: value('address') });
    const open = timeSelect(value('working_hours_start', '09:00'), { optional: true });
    const close = timeSelect(value('working_hours_end', '19:00'), { optional: true });
    const number = h('input', { type: 'text', dir: 'ltr', inputmode: 'numeric', maxlength: 30, autocomplete: 'off', value: value('whatsapp_phone_id') });
    const hasToken = editing && clinic.has_own_token;
    const token = h('input', { type: 'password', dir: 'ltr', maxlength: 500, autocomplete: 'off', placeholder: hasToken ? '••••••••' : '' });
    const tokenHint = h('p', { class: 'hint' }, t(hasToken ? 'clinics.tokenSaved' : 'clinics.tokenHint'));
    let removeToken = false;
    const removeButton = hasToken ? h('button', {
      class: 'btn btn-small btn-ghost',
      type: 'button',
      onclick: () => {
        removeToken = true;
        token.value = '';
        token.placeholder = '';
        tokenHint.textContent = t('clinics.tokenWillGo');
        removeButton.hidden = true;
      },
    }, t('clinics.tokenRemove')) : null;
    const active = editing ? toggle(t('clinics.active'), { checked: clinic.is_active }) : null;
    const error = h('p', { class: 'form-error', role: 'alert' });
    const formId = uid('form');
    const submit = h('button', { class: 'btn btn-primary', type: 'submit', form: formId }, t('common.save'));

    const tokenField = field(t('clinics.token'), token, { className: 'span-2', hint: tokenHint });
    if (removeButton) tokenField.append(removeButton);

    fill(m.body, h('form', { id: formId, class: 'form-grid', novalidate: true, onsubmit: save },
      field(t('set.name'), name, { className: 'span-2' }),
      field(t('set.phone'), phone),
      field(t('set.email'), email, { hint: t('common.optional') }),
      field(t('set.address'), address, { className: 'span-2', hint: t('common.optional') }),
      field(t('set.open'), open),
      field(t('set.close'), close),
      h('h3', { class: 'section-title span-2' }, t('clinics.whatsapp')),
      field(t('clinics.number'), number, { className: 'span-2', hint: t('clinics.numberHint') }),
      tokenField,
      active ? h('div', { class: 'stack-tight span-2' }, active.el, h('p', { class: 'hint' }, t('clinics.activeHint'))) : null,
      h('div', { class: 'span-2' }, error)));
    m.foot.append(h('span', { class: 'spacer' }),
      h('button', { class: 'btn', type: 'button', onclick: () => m.close() }, t('common.cancel')),
      submit);

    async function save(event) {
      event.preventDefault();
      error.textContent = '';
      const problem = !name.value.trim() ? t('set.needName')
        : phone.value.trim().length < 5 ? t('set.needPhone')
          : email.value.trim() && !email.checkValidity() ? t('team.badEmail')
            : open.value && close.value && open.value >= close.value ? t('team.badHours')
              : number.value.trim() && !PHONE_NUMBER_ID.test(number.value.trim()) ? t('clinics.badNumber')
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
        working_hours_start: open.value ? open.value.slice(0, 5) : null,
        working_hours_end: close.value ? close.value.slice(0, 5) : null,
        whatsapp_phone_id: number.value.trim() || null,
      };
      if (token.value.trim()) body.whatsapp_token = token.value.trim();
      else if (removeToken) body.whatsapp_token = '';
      if (active) body.is_active = active.input.checked;

      await busy(submit, async () => {
        try {
          const saved = await api(editing ? `/platform/clinics/${clinic.id}` : '/platform/clinics', {
            method: editing ? 'PUT' : 'POST',
            body,
          });
          m.close();
          toast(editing ? t('common.saved') : t('clinics.added', { name: saved.name }), 'success');
          await ctx.clinicsChanged(saved.id);
          // A new clinic cannot sign in until it has a login: make one now.
          if (!editing) openLogins(saved);
        } catch (err) {
          error.textContent = err.message;
        }
      });
    }
  }

  async function openLogins(clinic) {
    const m = modal({ title: t('logins.title', { name: clinic.name }), wide: true });
    const listBox = h('div', { class: 'stack' }, spinner());
    const email = h('input', { type: 'email', dir: 'ltr', required: true, maxlength: 255, autocomplete: 'off', placeholder: 'name@example.co.il' });
    const name = h('input', { type: 'text', maxlength: 255, autocomplete: 'off' });
    const error = h('p', { class: 'form-error', role: 'alert' });
    const create = h('button', { class: 'btn btn-primary', type: 'submit' }, icon('plus'), h('span', {}, t('logins.create')));

    fill(m.body,
      h('p', { class: 'muted' }, t('logins.intro', { name: clinic.name })),
      listBox,
      h('section', { class: 'panel' },
        h('h3', { class: 'panel-title' }, t('logins.add')),
        h('form', { class: 'form-grid', novalidate: true, onsubmit: add },
          field(t('logins.email'), email, { hint: t('logins.emailHint') }),
          field(t('logins.name'), name, { hint: t('common.optional') }),
          h('div', { class: 'span-2' }, error),
          h('div', { class: 'span-2 button-row end' }, create))));
    loadLogins();

    async function loadLogins() {
      try {
        const logins = await api(`/platform/clinics/${clinic.id}/logins`);
        fill(listBox, logins.length
          ? h('div', { class: 'history' }, logins.map(row))
          : h('p', { class: 'muted' }, t('logins.empty')));
      } catch (err) {
        if (err.status !== 401) fill(listBox, errorBox(err, loadLogins));
      }
    }

    function row(login) {
      const access = toggle(t('logins.canSignIn'), {
        checked: login.is_active,
        onchange: async (event) => {
          const input = event.target;
          const wanted = input.checked;
          input.disabled = true;
          try {
            await api(`/platform/logins/${login.id}`, { method: 'PUT', body: { is_active: wanted } });
            toast(t(wanted ? 'logins.turnedOn' : 'logins.turnedOff'), 'success');
          } catch (err) {
            input.checked = !wanted;
            toast(err.message, 'error');
          } finally {
            input.disabled = false;
          }
        },
      });
      const seen = login.last_login_at ? t('logins.lastSeen', { when: fmt.moment(login.last_login_at, ctx.tz) }) : t('logins.never');
      return h('div', { class: 'login-row' },
        h('div', { class: 'stack-tight' },
          h('span', { class: 'strong ellipsis' }, ltr(login.email)),
          h('span', { class: 'muted small' }, login.name ? `${login.name} · ${seen}` : seen),
          login.must_change_password ? h('span', {}, badge(t('logins.waiting'), 'warning')) : null),
        h('div', { class: 'button-row' },
          access.el,
          h('button', { class: 'btn btn-small', type: 'button', onclick: () => reset(login) },
            icon('key'), h('span', {}, t('logins.reset')))));
    }

    async function add(event) {
      event.preventDefault();
      error.textContent = '';
      if (!email.value.trim() || !email.checkValidity()) {
        error.textContent = t('team.badEmail');
        email.focus();
        return;
      }
      await busy(create, async () => {
        try {
          const result = await api(`/platform/clinics/${clinic.id}/logins`, {
            method: 'POST',
            body: { email: email.value.trim(), name: name.value.trim() || undefined },
          });
          email.value = '';
          name.value = '';
          await loadLogins();
          showPassword(result);
          ctx.clinicsChanged();
        } catch (err) {
          error.textContent = err.message;
        }
      });
    }

    async function reset(login) {
      const yes = await confirmDialog({
        title: t('logins.resetTitle', { email: login.email }),
        message: t('logins.resetText'),
        confirm: t('logins.reset'),
      });
      if (!yes) return;
      try {
        showPassword(await api(`/platform/logins/${login.id}/password`, { method: 'POST' }));
        loadLogins();
      } catch (err) {
        toast(err.message, 'error');
      }
    }
  }
}

/** A password to pass on to the clinic, which replaces it at first sign-in. */
function showPassword({ login, password }) {
  const m = modal({ title: t('password.title'), dismissable: false });
  const secret = h('code', { class: 'secret', dir: 'ltr' }, password);
  const copy = h('button', {
    class: 'btn btn-small',
    type: 'button',
    onclick: async () => {
      try {
        await navigator.clipboard.writeText(password);
        toast(t('password.copied'), 'success');
      } catch {
        // No clipboard here: select it, ready for Cmd+C.
        const range = document.createRange();
        range.selectNodeContents(secret);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
      }
    },
  }, icon('copy'), h('span', {}, t('password.copy')));

  fill(m.body,
    h('dl', { class: 'details' },
      h('dt', {}, t('logins.email')), h('dd', {}, ltr(login.email)),
      h('dt', {}, t('login.password')), h('dd', {}, h('div', { class: 'button-row' }, secret, copy))),
    notice('warning', 'alert', h('p', {}, t('password.once'))));
  m.foot.append(h('span', { class: 'spacer' }),
    h('button', { class: 'btn btn-primary', type: 'button', onclick: () => m.close() }, t('password.done')));
}
