/**
 * Customers: everyone who has written to the bot or been booked, their
 * history, and whether they get reminders.
 */
import { api } from '../api.js';
import * as D from '../dates.js';
import { fmt, t } from '../i18n.js';
import { badge, busy, empty, errorBox, fill, h, icon, ltr, modal, spinner, toast, toggle, uid } from '../ui.js';
import { debounce, searchTerm, whatsappLink } from '../util.js';

const LIMIT = 200;

export default function customersView(container, params, ctx) {
  let sequence = 0;
  const search = h('input', { type: 'search', placeholder: t('cust.search'), value: params.q || '', autocomplete: 'off', 'aria-label': t('cust.search') });
  const results = h('div', { class: 'stack' }, spinner());

  search.addEventListener('input', debounce(() => {
    ctx.replaceParams('customers', { q: search.value.trim() });
    load();
  }, 250));

  fill(container, h('section', { class: 'page' },
    h('div', { class: 'page-head' }, h('h1', { class: 'page-title' }, t('cust.title'))),
    h('div', { class: 'search-box' }, icon('search'), search),
    results));
  load();

  return { refresh: load };

  async function load() {
    const mine = ++sequence;
    results.classList.add('is-loading');
    try {
      const list = await api('/customers', { query: { search: searchTerm(search.value), limit: LIMIT } });
      if (mine !== sequence) return;
      draw(list);
    } catch (error) {
      if (mine !== sequence || error.status === 401) return;
      fill(results, errorBox(error, load));
    } finally {
      if (mine === sequence) results.classList.remove('is-loading');
    }
  }

  function draw(list) {
    if (!list.length) {
      fill(results, empty(t(search.value.trim() ? 'cust.noMatch' : 'cust.empty')));
      return;
    }
    fill(results,
      list.length >= LIMIT ? h('p', { class: 'hint' }, t('cust.limit')) : null,
      h('div', { class: 'table' },
        h('div', { class: 'table-head', 'aria-hidden': 'true' },
          h('span', {}, t('cust.name')),
          h('span', {}, t('cust.language')),
          h('span', {}, t('cust.appointments')),
          h('span', {}, t('cust.reminders'))),
        list.map((customer) => h('button', {
          type: 'button',
          class: 'table-row',
          onclick: () => ctx.openCustomer(customer),
        },
        h('span', { class: 'cell-main' },
          h('span', { class: 'strong ellipsis' }, customer.name || t('appt.noName')),
          h('span', { class: 'muted small' }, ltr(fmt.phone(customer.phone)))),
        h('span', { class: 'cell-language' }, fmt.language(customer.language)),
        h('span', { class: 'cell-count' }, t('cal.count', { n: customer.appointments })),
        h('span', { class: 'cell-flag' }, customer.is_blocked ? badge(t('cust.remindersOff'), 'warning') : badge(t('cust.remindersOn'), 'success'))))));
  }
}

/** A customer's card: name, reminders, and every appointment they have had. */
export async function openCustomer(ctx, given) {
  const m = modal({ title: t('cust.card'), wide: true });
  let customer = typeof given === 'object' ? given : null;

  if (!customer) {
    fill(m.body, spinner());
    try {
      customer = await api(`/customers/${encodeURIComponent(given)}`);
    } catch (error) {
      fill(m.body, errorBox(error));
      return;
    }
  }
  m.setTitle(customer.name || fmt.phone(customer.phone));

  const name = h('input', { id: uid('name'), type: 'text', maxlength: 255, value: customer.name || '', autocomplete: 'off' });
  const saveName = h('button', { class: 'btn', type: 'button', disabled: true }, t('common.save'));
  const unchanged = () => name.value.trim() === (customer.name || '').trim();
  name.addEventListener('input', () => { saveName.disabled = unchanged(); });
  saveName.addEventListener('click', async () => {
    await busy(saveName, async () => {
      try {
        customer = await api(`/customers/${customer.id}`, { method: 'PUT', body: { name: name.value.trim() || null } });
        m.setTitle(customer.name || fmt.phone(customer.phone));
        toast(t('cust.nameSaved'), 'success');
        ctx.changed();
      } catch (error) {
        toast(error.message, 'error');
      }
    });
    saveName.disabled = unchanged();
  });

  const reminders = toggle(t('cust.sendReminders'), {
    checked: !customer.is_blocked,
    onchange: async (event) => {
      const input = event.target;
      const wanted = input.checked;
      input.disabled = true;
      try {
        customer = await api(`/customers/${customer.id}`, { method: 'PUT', body: { is_blocked: !wanted } });
        toast(t(wanted ? 'cust.remindersOnSaved' : 'cust.remindersOffSaved'), 'success');
        ctx.changed();
      } catch (error) {
        input.checked = !wanted;
        toast(error.message, 'error');
      } finally {
        input.disabled = false;
      }
    },
  });

  const history = h('div', { class: 'stack' }, spinner());

  m.body.append(
    h('div', { class: 'form-grid' },
      h('div', { class: 'field' },
        h('label', { for: name.id }, t('cust.name')),
        h('div', { class: 'input-row' }, name, saveName)),
      h('div', { class: 'field' },
        h('span', { class: 'label' }, t('cust.phone')),
        h('div', { class: 'input-row' },
          h('span', { class: 'value' }, ltr(fmt.phone(customer.phone))),
          h('a', { class: 'btn btn-small', href: whatsappLink(customer.phone), target: '_blank', rel: 'noopener noreferrer' },
            icon('chat'), h('span', {}, t('appt.whatsapp')))))),
    h('dl', { class: 'details' },
      h('dt', {}, t('cust.language')), h('dd', {}, fmt.language(customer.language))),
    h('div', { class: 'stack-tight' }, reminders.el, h('p', { class: 'hint' }, t('cust.remindersHint'))),
    h('section', { class: 'stack' },
      h('div', { class: 'section-head' },
        h('h3', { class: 'section-title' }, t('cust.history')),
        h('button', {
          class: 'btn btn-primary btn-small',
          type: 'button',
          onclick: () => {
            m.close();
            ctx.openBooking({ phone: customer.phone, name: customer.name || '' });
          },
        }, icon('plus'), h('span', {}, t('cust.book')))),
      history));

  try {
    const list = await api('/appointments', { query: { customer_phone: customer.phone, limit: 1000 } });
    const today = D.today(ctx.tz);
    const now = D.nowMinutes(ctx.tz);
    const ahead = (a) => a.date > today || (a.date === today && D.toMinutes(a.time) > now);
    if (!list.length) {
      fill(history, h('p', { class: 'muted' }, t('cust.noHistory')));
      return;
    }
    // Newest first: what is coming up, then what has been.
    list.reverse();
    fill(history, h('div', { class: 'history' }, list.map((a) => h('button', {
      type: 'button',
      class: ['history-row', a.status === 'cancelled' && 'is-cancelled'],
      onclick: () => {
        m.close();
        ctx.openAppointment(a);
      },
    },
    h('span', { class: 'strong' }, fmt.dateShort(a.date), ' · ', ltr(a.time)),
    h('span', { class: 'history-status' },
      a.status === 'cancelled' ? badge(t('status.cancelled'), 'danger')
        : ahead(a) ? badge(t('status.upcoming'), 'success') : null),
    h('span', { class: 'muted' }, `${a.treatment} · ${a.therapist}`),
    h('span', { class: 'muted' }, a.price === null || a.price === undefined ? '' : fmt.money(a.price))))));
  } catch (error) {
    fill(history, errorBox(error));
  }
}
