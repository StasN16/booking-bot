/**
 * Booking on a customer's behalf. Only free times are offered, worked out
 * by the same rules the bot follows, and the booking itself goes through
 * the bot's own booking code on the server.
 */
import { api } from '../api.js';
import * as D from '../dates.js';
import { fmt, t } from '../i18n.js';
import { busy, empty, errorBox, field, fill, h, ltr, modal, notice, spinner, toast, uid } from '../ui.js';
import { debounce, groupBy, normalizePhone } from '../util.js';

/**
 * prefill: { date, time, therapistId, phone, name } - all optional. A time
 * clicked on the calendar is chosen for the user when it is free.
 */
export function openBooking(ctx, prefill = {}) {
  const m = modal({ title: t('book.title'), dismissable: false, wide: true });
  const treatments = ctx.activeTreatments();
  const therapists = ctx.activeTherapists();

  if (!treatments.length || !therapists.length) {
    const missingTreatments = !treatments.length;
    m.body.append(empty(t(missingTreatments ? 'book.noTreatments' : 'book.noTherapists'),
      h('a', {
        class: 'btn btn-primary',
        href: missingTreatments ? '#/treatments' : '#/team',
        onclick: () => m.close(),
      }, t(missingTreatments ? 'book.goTreatments' : 'book.goTeam'))));
    return;
  }

  const today = D.today(ctx.tz);
  let wanted = prefill.time ? { time: prefill.time, therapistId: prefill.therapistId } : null;
  let chosen = null; // the slot picked: { time, therapist_id, therapist_name }
  let known = null; // the existing customer with the typed number, if any
  let sequence = 0;
  let lookups = 0;

  const formId = uid('form');
  const phone = h('input', {
    type: 'tel', inputmode: 'tel', dir: 'ltr', autocomplete: 'off', required: true, placeholder: '054-338-1998',
    value: prefill.phone ? fmt.phone(prefill.phone) : '',
    autofocus: !prefill.phone,
  });
  const phoneHint = h('p', { class: 'hint' }, t('book.phoneHint'));
  const name = h('input', { type: 'text', autocomplete: 'off', maxlength: 255, value: prefill.name || '' });
  const treatment = h('select', {}, treatments.map((tr) => h('option', { value: tr.id },
    `${tr.name} · ${t('common.minutes', { n: tr.duration_minutes })} · ${fmt.money(tr.price)}`)));
  const date = h('input', { type: 'date', min: today, required: true, value: prefill.date && prefill.date >= today ? prefill.date : today });
  const therapist = h('select', {},
    h('option', { value: '' }, t('book.anyone')),
    therapists.map((th) => h('option', { value: th.id }, th.name)));
  if (prefill.therapistId && therapists.some((th) => th.id === prefill.therapistId)) therapist.value = prefill.therapistId;
  const slotsBox = h('div', { class: 'slots-box', 'aria-live': 'polite' });
  const notes = h('textarea', { rows: 2, maxlength: 1000, placeholder: t('appt.notesPlaceholder') });
  const summary = h('p', { class: 'summary' });
  const error = h('p', { class: 'form-error', role: 'alert' });
  const submit = h('button', { class: 'btn btn-primary', type: 'submit', form: formId, disabled: true }, t('book.submit'));

  const form = h('form', { id: formId, class: 'form-grid', novalidate: true, onsubmit: book },
    field(t('book.phone'), phone, { hint: phoneHint }),
    field(t('book.name'), name, { hint: t('common.optional') }),
    field(t('book.treatment'), treatment),
    field(t('book.date'), date),
    field(t('book.therapist'), therapist, { className: 'span-2' }),
    h('div', { class: 'field span-2' }, h('span', { class: 'label' }, t('book.freeTimes')), slotsBox),
    field(t('book.notes'), notes, { className: 'span-2', hint: t('appt.notesHint') }));
  m.body.append(form);
  m.foot.append(h('div', { class: 'foot-text' }, summary, error), h('span', { class: 'spacer' }),
    h('button', { class: 'btn', type: 'button', onclick: () => m.close() }, t('common.cancel')),
    submit);

  const lookUp = debounce(findCustomer, 350);
  phone.addEventListener('input', () => {
    error.textContent = '';
    known = null;
    lookUp();
    update();
  });
  phone.addEventListener('blur', () => {
    if (phone.value.trim() && !normalizePhone(phone.value)) {
      phoneHint.textContent = t('book.phoneInvalid');
      phoneHint.classList.add('is-error');
    }
  });
  treatment.addEventListener('change', loadSlots);
  date.addEventListener('change', loadSlots);
  therapist.addEventListener('change', loadSlots);

  loadSlots();
  if (prefill.phone) findCustomer();

  function update() {
    const ready = Boolean(chosen && normalizePhone(phone.value));
    submit.disabled = !ready;
    if (!chosen) {
      summary.textContent = '';
      return;
    }
    const tr = treatments.find((x) => x.id === treatment.value);
    summary.textContent = t('summary.appt', {
      treatment: tr ? tr.name : '',
      therapist: chosen.therapist_name,
      date: fmt.dateLong(date.value),
      time: chosen.time,
    });
  }

  async function loadSlots() {
    const mine = ++sequence;
    chosen = null;
    update();
    if (!D.isDate(date.value) || date.value < today) {
      fill(slotsBox, h('p', { class: 'muted' }, t('book.pickDate')));
      return;
    }
    fill(slotsBox, spinner());
    try {
      const result = await api('/availability', {
        query: { treatment_id: treatment.value, date: date.value, therapist_id: therapist.value },
      });
      if (mine !== sequence) return;
      showSlots(result.slots);
    } catch (err) {
      if (mine === sequence) fill(slotsBox, errorBox(err, loadSlots));
    }
  }

  function showSlots(slots) {
    const missed = wanted && !slots.some((slot) => slot.time === wanted.time
      && (!wanted.therapistId || slot.therapist_id === wanted.therapistId));
    const note = missed ? notice('warning', 'clock', h('p', {}, t('book.wantedTaken', { time: wanted.time }))) : null;

    if (!slots.length) {
      fill(slotsBox, note, h('div', { class: 'button-row' },
        h('p', { class: 'muted' }, t('book.noSlots')),
        h('button', {
          class: 'btn btn-small',
          type: 'button',
          onclick: () => {
            date.value = D.addDays(date.value, 1);
            loadSlots();
          },
        }, t('book.nextDay'))));
      wanted = null;
      return;
    }

    const groups = [...groupBy(slots, (slot) => slot.therapist_id)].map(([id, list]) => h('div', { class: 'slot-group' },
      h('div', { class: 'slot-group-title', style: { '--c': ctx.therapistColor(id) } }, h('span', { class: 'dot' }), list[0].therapist_name),
      h('div', { class: 'slots' }, list.map((slot) => {
        const button = h('button', {
          type: 'button',
          class: 'slot',
          'aria-pressed': 'false',
          dataset: { time: slot.time, therapist: slot.therapist_id },
          onclick: () => pick(slot, button),
        }, ltr(slot.time));
        return button;
      }))));
    fill(slotsBox, note, ...groups);

    if (wanted && !missed) {
      const match = slots.find((slot) => slot.time === wanted.time && (!wanted.therapistId || slot.therapist_id === wanted.therapistId));
      const button = [...slotsBox.querySelectorAll('.slot')]
        .find((el) => el.dataset.time === match.time && el.dataset.therapist === match.therapist_id);
      pick(match, button);
    }
    wanted = null;
  }

  function pick(slot, button) {
    for (const other of slotsBox.querySelectorAll('.slot')) other.setAttribute('aria-pressed', 'false');
    if (button) button.setAttribute('aria-pressed', 'true');
    chosen = slot;
    error.textContent = '';
    update();
  }

  async function findCustomer() {
    const mine = ++lookups;
    const digits = normalizePhone(phone.value);
    phoneHint.classList.remove('is-error');
    if (!digits) {
      phoneHint.textContent = t('book.phoneHint');
      return;
    }
    try {
      const found = await api('/customers', { query: { search: digits, limit: 5 } });
      if (mine !== lookups) return;
      known = found.find((customer) => customer.phone === digits) || null;
    } catch {
      // Only a convenience: booking works the same without it.
      return;
    }
    if (!known) {
      phoneHint.textContent = t('book.newCustomer');
      return;
    }
    phoneHint.textContent = t('book.known', {
      name: known.name || fmt.phone(known.phone),
      count: t('cal.count', { n: known.appointments }),
    });
    if (!name.value.trim() && known.name) name.value = known.name;
  }

  async function book(event) {
    event.preventDefault();
    error.textContent = '';
    const digits = normalizePhone(phone.value);
    if (!digits) {
      error.textContent = t('book.phoneInvalid');
      phone.focus();
      return;
    }
    if (!chosen) {
      error.textContent = t('book.pickTime');
      return;
    }
    const tr = treatments.find((x) => x.id === treatment.value);
    await busy(submit, async () => {
      try {
        const created = await api('/appointments', {
          method: 'POST',
          body: {
            customer_phone: digits,
            customer_name: name.value.trim() || undefined,
            treatment_name: tr.name,
            therapist_name: chosen.therapist_name,
            date: date.value,
            time: chosen.time,
            notes: notes.value.trim() || undefined,
          },
        });
        m.close();
        toast(t('book.done', {
          who: created.customer_name || fmt.phone(created.customer_phone),
          date: fmt.dateShort(created.date),
          time: created.time,
        }), 'success');
        ctx.changed();
      } catch (err) {
        error.textContent = err.message;
        // Someone else took the time meanwhile: show what is free now.
        if (err.status === 409) loadSlots();
      }
    });
    update();
  }
}
