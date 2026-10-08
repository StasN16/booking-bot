/**
 * The treatments the clinic offers, with their length and price. The bot
 * offers customers exactly the active ones listed here.
 */
import { api } from '../api.js';
import { fmt, t } from '../i18n.js';
import { badge, busy, confirmDialog, empty, field, fill, h, icon, modal, toast, uid } from '../ui.js';
import { sameName } from '../util.js';

export default function treatmentsView(container, params, ctx) {
  let showInactive = params.inactive === '1';
  const list = h('div', { class: 'cards' });
  const inactiveToggle = h('button', { type: 'button', class: 'chip', onclick: () => {
    showInactive = !showInactive;
    ctx.replaceParams('treatments', showInactive ? { inactive: '1' } : {});
    draw();
  } });

  fill(container, h('section', { class: 'page' },
    h('div', { class: 'page-head' },
      h('h1', { class: 'page-title' }, t('treat.title')),
      h('div', { class: 'button-row' },
        inactiveToggle,
        h('button', { class: 'btn btn-primary', type: 'button', onclick: () => edit(null) }, icon('plus'), h('span', {}, t('treat.add'))))),
    h('p', { class: 'hint' }, t('treat.intro')),
    list));
  draw();

  return {
    refresh: async () => {
      await ctx.loadReference();
      draw();
    },
  };

  function draw() {
    const inactive = ctx.treatments.filter((tr) => !tr.is_active).length;
    inactiveToggle.hidden = !inactive;
    inactiveToggle.textContent = t('common.showInactive', { n: inactive });
    inactiveToggle.setAttribute('aria-pressed', String(showInactive));

    const shown = ctx.treatments.filter((tr) => tr.is_active || showInactive);
    if (!shown.length) {
      fill(list, empty(t('treat.empty'),
        h('button', { class: 'btn btn-primary', type: 'button', onclick: () => edit(null) }, t('treat.add'))));
      return;
    }
    fill(list, ...shown.map(card));
  }

  function card(treatment) {
    return h('article', { class: ['card', !treatment.is_active && 'is-inactive'] },
      h('div', { class: 'card-head' },
        h('h2', { class: 'card-title' }, treatment.name),
        treatment.is_active ? null : badge(t('treat.inactive'), 'muted')),
      h('div', { class: 'card-figures' },
        h('span', { class: 'figure' }, fmt.money(treatment.price)),
        h('span', { class: 'meta-row muted' }, icon('clock'), t('common.minutes', { n: treatment.duration_minutes }))),
      treatment.description ? h('p', { class: 'card-text' }, treatment.description) : null,
      h('div', { class: 'card-actions' },
        h('button', { class: 'btn btn-small', type: 'button', onclick: () => edit(treatment) }, icon('edit'), h('span', {}, t('common.edit'))),
        treatment.is_active
          ? h('button', { class: 'btn btn-small btn-danger-ghost', type: 'button', onclick: () => deactivate(treatment) }, t('treat.deactivate'))
          : h('button', { class: 'btn btn-small', type: 'button', onclick: () => reactivate(treatment) }, t('common.reactivate'))));
  }

  function edit(treatment) {
    const m = modal({ title: t(treatment ? 'treat.edit' : 'treat.add'), dismissable: false });
    const name = h('input', { type: 'text', required: true, maxlength: 255, autocomplete: 'off', value: treatment ? treatment.name : '', autofocus: true });
    const duration = h('input', { type: 'number', inputmode: 'numeric', min: 5, max: 600, step: 5, required: true, value: treatment ? treatment.duration_minutes : 60 });
    const price = h('input', { type: 'number', inputmode: 'numeric', min: 0, step: 1, required: true, value: treatment ? treatment.price : '' });
    const description = h('textarea', { rows: 3, maxlength: 1000, value: treatment && treatment.description ? treatment.description : '' });
    const error = h('p', { class: 'form-error', role: 'alert' });
    const formId = uid('form');
    const submit = h('button', { class: 'btn btn-primary', type: 'submit', form: formId }, t('common.save'));

    m.body.append(h('form', { id: formId, class: 'form-grid', novalidate: true, onsubmit: save },
      field(t('treat.name'), name, { className: 'span-2', hint: t('treat.nameHint') }),
      field(t('treat.duration'), duration),
      field(t('treat.price'), price),
      field(t('treat.description'), description, { className: 'span-2', hint: t('common.optional') }),
      h('div', { class: 'span-2' }, error)));
    m.foot.append(h('span', { class: 'spacer' }),
      h('button', { class: 'btn', type: 'button', onclick: () => m.close() }, t('common.cancel')),
      submit);

    async function save(event) {
      event.preventDefault();
      error.textContent = '';
      const minutes = Number(duration.value);
      const cost = Number(price.value);
      const problem = !name.value.trim() ? t('treat.needName')
        : ctx.treatments.some((other) => (!treatment || other.id !== treatment.id) && sameName(other.name, name.value)) ? t('treat.nameTaken')
          : !Number.isInteger(minutes) || minutes < 5 || minutes > 600 ? t('treat.badDuration')
            : price.value === '' || !Number.isInteger(cost) || cost < 0 ? t('treat.badPrice')
              : '';
      if (problem) {
        error.textContent = problem;
        return;
      }
      const body = {
        name: name.value.trim(),
        duration_minutes: minutes,
        price: cost,
        description: description.value.trim() || null,
      };
      await busy(submit, async () => {
        try {
          await api(treatment ? `/treatments/${treatment.id}` : '/treatments', { method: treatment ? 'PUT' : 'POST', body });
          await ctx.loadReference();
          m.close();
          toast(t(treatment ? 'common.saved' : 'treat.added', { name: body.name }), 'success');
          draw();
        } catch (err) {
          error.textContent = err.message;
        }
      });
    }
  }

  async function deactivate(treatment) {
    const yes = await confirmDialog({
      title: t('treat.deactivateTitle', { name: treatment.name }),
      message: t('treat.deactivateText'),
      confirm: t('treat.deactivate'),
      danger: true,
    });
    if (!yes) return;
    try {
      await api(`/treatments/${treatment.id}`, { method: 'DELETE' });
      await ctx.loadReference();
      draw();
      toast(t('treat.deactivated', { name: treatment.name }), 'success');
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  async function reactivate(treatment) {
    try {
      await api(`/treatments/${treatment.id}`, { method: 'PUT', body: { is_active: true } });
      await ctx.loadReference();
      draw();
      toast(t('treat.reactivated', { name: treatment.name }), 'success');
    } catch (error) {
      toast(error.message, 'error');
    }
  }
}
