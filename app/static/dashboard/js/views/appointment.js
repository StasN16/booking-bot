/**
 * One appointment: its details and private notes, and moving or
 * cancelling it. Moving offers only times the bot itself would offer.
 */
import { api } from '../api.js';
import * as D from '../dates.js';
import { fmt, t } from '../i18n.js';
import { badge, busy, confirmDialog, errorBox, field, fill, h, icon, ltr, modal, notice, spinner, toast } from '../ui.js';
import { whatsappLink } from '../util.js';

export async function openAppointment(ctx, given) {
  const m = modal({ title: t('appt.title') });
  let appointment = typeof given === 'object' ? given : null;
  let moving = false;
  let done = null; // 'moved' or 'cancelled', to offer telling the customer

  if (!appointment) {
    fill(m.body, spinner());
    try {
      appointment = await api(`/appointments/${encodeURIComponent(given)}`);
    } catch (error) {
      fill(m.body, errorBox(error));
      return;
    }
  }
  // Made once and kept across redraws, so typed but unsaved notes survive them.
  const notes = notesEditor();
  render();

  function upcoming() {
    const a = appointment;
    const today = D.today(ctx.tz);
    return a.status === 'confirmed'
      && (a.date > today || (a.date === today && D.toMinutes(a.time) > D.nowMinutes(ctx.tz)));
  }

  function render() {
    const a = appointment;
    const phone = a.customer_phone;
    m.setTitle(a.treatment || t('appt.title'));

    let status;
    if (a.status === 'cancelled') status = badge(t('status.cancelled'), 'danger');
    else if (upcoming()) status = badge(t('status.confirmed'), 'success');
    else status = badge(t('status.past'), 'muted');

    const details = h('dl', { class: 'details' },
      row(t('appt.status'), status),
      row(t('appt.when'), [fmt.dateLong(a.date), ' · ', ltr(`${a.time}–${a.end_time}`)]),
      row(t('appt.therapist'), a.therapist || '—'),
      row(t('appt.customer'), [a.customer_name || t('appt.noName'), phone ? [' · ', ltr(fmt.phone(phone))] : null]),
      row(t('appt.price'), a.price === null || a.price === undefined ? '—' : fmt.money(a.price)),
      a.status === 'confirmed' ? row(t('appt.reminders'), reminders(a)) : null);

    const contact = h('div', { class: 'button-row' },
      phone ? h('a', { class: 'btn btn-small', href: whatsappLink(phone), target: '_blank', rel: 'noopener noreferrer' },
        icon('chat'), h('span', {}, t('appt.whatsapp'))) : null,
      a.customer_id ? h('button', {
        class: 'btn btn-small',
        type: 'button',
        onclick: () => {
          m.close();
          ctx.openCustomer(a.customer_id);
        },
      }, icon('users'), h('span', {}, t('appt.customerCard'))) : null);

    fill(m.body, done ? tellCustomer() : null, details, contact, notes, moving ? movePanel() : null);

    const actions = upcoming() && !moving;
    fill(m.foot, 
      actions ? h('button', { class: 'btn btn-danger-ghost', type: 'button', onclick: cancel }, t('appt.cancel')) : null,
      h('span', { class: 'spacer' }),
      actions ? h('button', {
        class: 'btn btn-primary',
        type: 'button',
        onclick: () => {
          moving = true;
          render();
        },
      }, icon('clock'), h('span', {}, t('appt.move'))) : null);
  }

  function notesEditor() {
    const area = h('textarea', { rows: 3, maxlength: 1000, placeholder: t('appt.notesPlaceholder'), value: appointment.notes || '' });
    const save = h('button', { class: 'btn btn-small', type: 'button', disabled: true }, t('common.save'));
    const unchanged = () => area.value.trim() === (appointment.notes || '').trim();
    area.addEventListener('input', () => { save.disabled = unchanged(); });
    save.addEventListener('click', async () => {
      await busy(save, async () => {
        try {
          appointment = await api(`/appointments/${appointment.id}`, { method: 'PATCH', body: { notes: area.value } });
          toast(t('appt.notesSaved'), 'success');
          ctx.changed();
        } catch (error) {
          toast(error.message, 'error');
        }
      });
      save.disabled = unchanged();
    });
    return h('div', { class: 'stack' },
      field(t('appt.notes'), area, { hint: t('appt.notesHint') }),
      h('div', { class: 'button-row end' }, save));
  }

  function movePanel() {
    const a = appointment;
    const today = D.today(ctx.tz);
    const date = h('input', { type: 'date', min: today, value: a.date >= today ? a.date : today, required: true });
    const slots = h('div', { class: 'slots-box', 'aria-live': 'polite' });
    const summary = h('p', { class: 'summary' });
    const confirm = h('button', { class: 'btn btn-primary', type: 'button', disabled: true }, t('appt.moveConfirm'));
    let chosen = null;
    let sequence = 0;

    async function load() {
      const mine = ++sequence;
      chosen = null;
      confirm.disabled = true;
      summary.textContent = '';
      if (!D.isDate(date.value) || date.value < today) {
        fill(slots, h('p', { class: 'muted' }, t('book.pickDate')));
        return;
      }
      fill(slots, spinner());
      try {
        const result = await api('/availability', {
          query: { treatment_id: a.treatment_id, date: date.value, therapist_id: a.therapist_id, exclude_appointment_id: a.id },
        });
        if (mine !== sequence) return;
        // Its own time is free too, with itself left out; offering it would move nothing.
        const times = result.slots.filter((slot) => !(date.value === a.date && slot.time === a.time));
        if (!times.length) {
          fill(slots, h('p', { class: 'muted' }, t('appt.moveNone', { therapist: a.therapist })));
          return;
        }
        fill(slots, h('div', { class: 'slots' }, times.map((slot) => {
          const button = h('button', {
            type: 'button',
            class: 'slot',
            'aria-pressed': 'false',
            onclick: () => {
              for (const other of slots.querySelectorAll('.slot')) other.setAttribute('aria-pressed', 'false');
              button.setAttribute('aria-pressed', 'true');
              chosen = slot.time;
              confirm.disabled = false;
              summary.textContent = t('appt.moveTo', { date: fmt.dateLong(date.value), time: slot.time });
            },
          }, ltr(slot.time));
          return button;
        })));
      } catch (error) {
        if (mine === sequence) fill(slots, errorBox(error, load));
      }
    }

    confirm.addEventListener('click', () => busy(confirm, async () => {
      try {
        appointment = await api(`/appointments/${a.id}`, { method: 'PUT', body: { date: date.value, time: chosen } });
        moving = false;
        done = 'moved';
        toast(t('appt.moved'), 'success');
        ctx.changed();
        render();
      } catch (error) {
        toast(error.message, 'error');
        if (error.status === 409) load();
      }
    }));
    date.addEventListener('change', load);
    load();

    return h('section', { class: 'panel' },
      h('h3', { class: 'panel-title' }, t('appt.moveTitle', { therapist: a.therapist })),
      field(t('book.date'), date),
      slots,
      summary,
      h('div', { class: 'button-row end' },
        h('button', {
          class: 'btn',
          type: 'button',
          onclick: () => {
            moving = false;
            render();
          },
        }, t('common.cancel')),
        confirm));
  }

  async function cancel() {
    const a = appointment;
    const yes = await confirmDialog({
      title: t('appt.cancelTitle'),
      message: [h('p', {}, summaryOf(a)), h('p', { class: 'muted' }, t('appt.cancelNotice'))],
      confirm: t('appt.cancelConfirm'),
      cancel: t('appt.keep'),
      danger: true,
    });
    if (!yes) return;
    try {
      await api(`/appointments/${a.id}`, { method: 'DELETE' });
      appointment = { ...a, status: 'cancelled' };
      done = 'cancelled';
      toast(t('appt.cancelled'), 'success');
      ctx.changed();
      render();
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  /** The customer is not told by the system, so offer a drafted WhatsApp message. */
  function tellCustomer() {
    const a = appointment;
    if (!a.customer_phone) return null;
    const draft = t(done === 'cancelled' ? 'wa.cancelled' : 'wa.moved', {
      name: a.customer_name ? ` ${a.customer_name}` : '',
      treatment: a.treatment,
      date: fmt.dateLong(a.date),
      time: a.time,
    });
    return notice('info', 'chat',
      h('p', {}, t(done === 'cancelled' ? 'appt.tellCancelled' : 'appt.tellMoved')),
      h('a', { class: 'btn btn-small btn-primary', href: whatsappLink(a.customer_phone, draft), target: '_blank', rel: 'noopener noreferrer' },
        t('appt.tellButton')));
  }

  function summaryOf(a) {
    return t('summary.appt', { treatment: a.treatment, therapist: a.therapist, date: fmt.dateLong(a.date), time: a.time });
  }
}

function row(label, value) {
  return [h('dt', {}, label), h('dd', {}, value)];
}

function reminders(a) {
  const state = (sent) => (sent ? t('appt.sent') : t('appt.notYet'));
  return h('span', { class: 'reminders' },
    h('span', { class: a.reminder_24h_sent ? 'is-sent' : '' }, `${t('appt.reminderDay')}: ${state(a.reminder_24h_sent)}`),
    h('span', { class: a.reminder_1h_sent ? 'is-sent' : '' }, `${t('appt.reminderHour')}: ${state(a.reminder_1h_sent)}`));
}
