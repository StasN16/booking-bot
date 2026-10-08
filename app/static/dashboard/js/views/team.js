/**
 * The team: who works which days and hours. Availability for the bot and
 * the dashboard alike is worked out from these hours.
 */
import { api } from '../api.js';
import * as D from '../dates.js';
import { fmt, t } from '../i18n.js';
import { badge, busy, confirmDialog, empty, field, fill, h, icon, ltr, modal, toast } from '../ui.js';
import { sameName } from '../util.js';

export default function teamView(container, params, ctx) {
  let showInactive = params.inactive === '1';
  const list = h('div', { class: 'cards' });
  const inactiveToggle = h('button', { type: 'button', class: 'chip', onclick: () => {
    showInactive = !showInactive;
    ctx.replaceParams('team', showInactive ? { inactive: '1' } : {});
    draw();
  } });

  fill(container, h('section', { class: 'page' },
    h('div', { class: 'page-head' },
      h('h1', { class: 'page-title' }, t('team.title')),
      h('div', { class: 'button-row' },
        inactiveToggle,
        h('button', { class: 'btn btn-primary', type: 'button', onclick: () => edit(null) }, icon('plus'), h('span', {}, t('team.add'))))),
    h('p', { class: 'hint' }, t('team.intro')),
    list));
  draw();

  return {
    refresh: async () => {
      await ctx.loadReference();
      draw();
    },
  };

  function draw() {
    const inactive = ctx.therapists.filter((th) => !th.is_active).length;
    inactiveToggle.hidden = !inactive;
    inactiveToggle.textContent = t('common.showInactive', { n: inactive });
    inactiveToggle.setAttribute('aria-pressed', String(showInactive));

    const shown = ctx.therapists.filter((th) => th.is_active || showInactive);
    if (!shown.length) {
      fill(list, empty(t('team.empty'),
        h('button', { class: 'btn btn-primary', type: 'button', onclick: () => edit(null) }, t('team.add'))));
      return;
    }
    fill(list, ...shown.map(card));
  }

  function card(therapist) {
    const days = (therapist.working_days || '').split(',').map((day) => day.trim());
    return h('article', { class: ['card', !therapist.is_active && 'is-inactive'], style: { '--c': ctx.therapistColor(therapist.id) } },
      h('div', { class: 'card-head' },
        h('span', { class: 'dot dot-lg' }),
        h('h2', { class: 'card-title' }, therapist.name),
        therapist.is_active ? null : badge(t('team.inactive'), 'muted')),
      h('div', { class: 'days', 'aria-label': t('team.days') },
        D.DAY_NAMES.map((day) => h('span', {
          class: ['day-chip', days.includes(day) && 'is-on'],
          title: fmt.dayName(day, 'long'),
        }, fmt.dayChip(day)))),
      h('div', { class: 'card-meta' },
        h('span', { class: 'meta-row' }, icon('clock'),
          ltr(`${therapist.working_hours_start || '—'}–${therapist.working_hours_end || '—'}`)),
        therapist.phone ? h('span', { class: 'meta-row' }, icon('phone'), ltr(therapist.phone)) : null,
        therapist.email ? h('span', { class: 'meta-row' }, icon('mail'), ltr(therapist.email)) : null),
      h('div', { class: 'card-actions' },
        h('button', { class: 'btn btn-small', type: 'button', onclick: () => edit(therapist) }, icon('edit'), h('span', {}, t('common.edit'))),
        therapist.is_active
          ? h('button', { class: 'btn btn-small btn-danger-ghost', type: 'button', onclick: () => deactivate(therapist) }, t('team.deactivate'))
          : h('button', { class: 'btn btn-small', type: 'button', onclick: () => reactivate(therapist) }, t('common.reactivate'))));
  }

  function edit(therapist) {
    const m = modal({ title: t(therapist ? 'team.edit' : 'team.add'), dismissable: false });
    const name = h('input', { type: 'text', required: true, maxlength: 255, autocomplete: 'off', value: therapist ? therapist.name : '', autofocus: true });
    const phone = h('input', { type: 'tel', dir: 'ltr', maxlength: 20, autocomplete: 'off', value: therapist && therapist.phone ? therapist.phone : '' });
    const email = h('input', { type: 'email', dir: 'ltr', maxlength: 255, autocomplete: 'off', value: therapist && therapist.email ? therapist.email : '' });
    const working = new Set(therapist ? (therapist.working_days || '').split(',').map((day) => day.trim()) : D.DAY_NAMES.slice(0, 5));
    const dayBoxes = D.DAY_NAMES.map((day) => {
      const input = h('input', { type: 'checkbox', value: day, checked: working.has(day) });
      return { day, input, el: h('label', { class: 'day-toggle' }, input, h('span', {}, fmt.dayName(day, 'short'))) };
    });
    const start = h('input', { type: 'time', step: 300, required: true, value: therapist ? therapist.working_hours_start || '09:00' : '09:00' });
    const end = h('input', { type: 'time', step: 300, required: true, value: therapist ? therapist.working_hours_end || '18:00' : '18:00' });
    const error = h('p', { class: 'form-error', role: 'alert' });
    const formId = `team-form-${Date.now()}`;
    const submit = h('button', { class: 'btn btn-primary', type: 'submit', form: formId }, t('common.save'));

    m.body.append(h('form', { id: formId, class: 'form-grid', novalidate: true, onsubmit: save },
      field(t('team.name'), name, { className: 'span-2', hint: t('team.nameHint') }),
      field(t('team.phone'), phone, { hint: t('common.optional') }),
      field(t('team.email'), email, { hint: t('common.optional') }),
      h('fieldset', { class: 'field span-2' },
        h('legend', { class: 'label' }, t('team.days')),
        h('div', { class: 'day-picker' }, dayBoxes.map((box) => box.el))),
      field(t('team.start'), start),
      field(t('team.end'), end),
      h('div', { class: 'span-2' }, error)));
    m.foot.append(h('span', { class: 'spacer' }),
      h('button', { class: 'btn', type: 'button', onclick: () => m.close() }, t('common.cancel')),
      submit);

    async function save(event) {
      event.preventDefault();
      error.textContent = '';
      const days = dayBoxes.filter((box) => box.input.checked).map((box) => box.day);
      const problem = !name.value.trim() ? t('team.needName')
        : ctx.therapists.some((other) => (!therapist || other.id !== therapist.id) && sameName(other.name, name.value)) ? t('team.nameTaken')
          : !days.length ? t('team.needDay')
            : !start.value || !end.value || start.value >= end.value ? t('team.badHours')
              : email.value.trim() && !email.checkValidity() ? t('team.badEmail')
                : '';
      if (problem) {
        error.textContent = problem;
        return;
      }
      const body = {
        name: name.value.trim(),
        phone: phone.value.trim() || null,
        email: email.value.trim() || null,
        working_days: days.join(','),
        working_hours_start: start.value.slice(0, 5),
        working_hours_end: end.value.slice(0, 5),
      };
      await busy(submit, async () => {
        try {
          await api(therapist ? `/therapists/${therapist.id}` : '/therapists', { method: therapist ? 'PUT' : 'POST', body });
          await ctx.loadReference();
          m.close();
          toast(t(therapist ? 'common.saved' : 'team.added', { name: body.name }), 'success');
          draw();
        } catch (err) {
          error.textContent = err.message;
        }
      });
    }
  }

  async function deactivate(therapist) {
    const yes = await confirmDialog({
      title: t('team.deactivateTitle', { name: therapist.name }),
      message: t('team.deactivateText', { name: therapist.name }),
      confirm: t('team.deactivate'),
      danger: true,
    });
    if (!yes) return;
    try {
      const result = await api(`/therapists/${therapist.id}`, { method: 'DELETE' });
      await ctx.loadReference();
      draw();
      if (result.upcoming_appointments > 0) {
        const m = modal({ title: t('team.stillBookedTitle') });
        m.body.append(h('p', {}, t('team.stillBooked', {
          name: therapist.name,
          count: t('cal.count', { n: result.upcoming_appointments }),
        })));
        m.foot.append(h('span', { class: 'spacer' }),
          h('button', { class: 'btn', type: 'button', onclick: () => m.close() }, t('common.ok')),
          h('button', {
            class: 'btn btn-primary',
            type: 'button',
            onclick: () => {
              m.close();
              ctx.navigate('appointments', { therapist: therapist.id, status: 'confirmed', from: D.today(ctx.tz), to: D.addDays(D.today(ctx.tz), 365) });
            },
          }, t('team.showThem')));
      } else {
        toast(t('team.deactivated', { name: therapist.name }), 'success');
      }
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  async function reactivate(therapist) {
    try {
      await api(`/therapists/${therapist.id}`, { method: 'PUT', body: { is_active: true } });
      await ctx.loadReference();
      draw();
      toast(t('team.reactivated', { name: therapist.name }), 'success');
    } catch (error) {
      toast(error.message, 'error');
    }
  }
}
