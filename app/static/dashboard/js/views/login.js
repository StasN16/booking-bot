/**
 * Signing in: a clinic with its email and password, or the owner of the
 * service with the password alone. The server first says whether sign-in
 * is set up at all, so a server without a password explains how to fix
 * that instead of answering "wrong password" forever.
 *
 * A clinic user signing in with the password the owner gave them chooses
 * their own next, before anything else opens.
 */
import { api, saveToken, setClinic } from '../api.js';
import { t } from '../i18n.js';
import { busy, field, fill, h, icon, ltr, notice, toast } from '../ui.js';

export function renderLogin(root, { expired = false, message: reason = '', onSignedIn, onLanguage }) {
  const email = h('input', { type: 'email', name: 'email', dir: 'ltr', autocomplete: 'username', inputmode: 'email' });
  const password = h('input', { type: 'password', name: 'password', autocomplete: 'current-password', required: true });
  const message = h('div', { class: 'login-message' });
  const submit = h('button', { class: 'btn btn-primary btn-block', type: 'submit' }, t('login.submit'));

  fill(root, h('main', { class: 'login-page' },
    h('div', { class: 'login-card' },
      h('div', { class: 'login-top' },
        h('span', { class: 'brand-mark' }, icon('calendar')),
        h('button', { class: 'btn btn-ghost btn-small', type: 'button', onclick: onLanguage }, icon('globe'), h('span', {}, t('lang.other')))),
      h('h1', { class: 'login-title' }, t('login.title')),
      h('p', { class: 'muted' }, t('login.subtitle')),
      message,
      h('form', { class: 'login-form', onsubmit: signIn },
        field(t('login.email'), email, { hint: t('login.emailHint') }),
        field(t('login.password'), password),
        submit),
      h('p', { class: 'hint' }, t('login.forgot')))));

  if (expired) show(notice('info', 'clock', h('p', {}, reason || t('login.expired'))));
  email.focus();
  checkConfigured();

  async function checkConfigured() {
    try {
      const status = await api('/auth/status', { auth: false });
      if (status.configured) return;
      show(notice('warning', 'alert',
        h('p', {}, t('login.notConfigured')),
        h('code', { dir: 'ltr' }, './scripts/setup_mac.sh')));
      email.disabled = true;
      password.disabled = true;
      submit.disabled = true;
    } catch (error) {
      show(notice('error', 'alert', h('p', {}, error.message)));
    }
  }

  async function signIn(event) {
    event.preventDefault();
    if (!password.value) {
      password.focus();
      return;
    }
    const withEmail = Boolean(email.value.trim());
    submit.disabled = true;
    show(null);
    try {
      saveToken(await api('/auth/login', {
        method: 'POST',
        body: { email: withEmail ? email.value.trim() : undefined, password: password.value },
        auth: false,
      }));
      // A fresh start: whatever clinic was open before belongs to that sign-in.
      setClinic(null);
      // Passed on in case it is the owner's password, so choosing a new
      // one does not ask for it a second time.
      onSignedIn({ password: password.value });
    } catch (error) {
      let text = error.message;
      if (error.status === 401) text = t(withEmail ? 'login.wrongEmail' : 'login.wrong');
      else if (error.status === 429) text = t('login.tooMany', { minutes: Math.max(1, Math.ceil(error.retryAfter / 60)) });
      show(notice('error', 'alert', h('p', {}, text)));
      submit.disabled = false;
      password.select();
    }
  }

  function show(node) {
    fill(message, node);
  }
}

/**
 * Signed in with a password the owner gave: choose one's own to go on.
 * `current` is the password just typed to sign in, when there is one;
 * otherwise (the page was reloaded) it is asked for here.
 */
export function renderChoosePassword(root, { email = '', current = '', onDone, onSignOut, onLanguage }) {
  const currentInput = current ? null : h('input', { type: 'password', autocomplete: 'current-password', required: true });
  const fresh = h('input', { type: 'password', autocomplete: 'new-password', required: true, minlength: 8 });
  const again = h('input', { type: 'password', autocomplete: 'new-password', required: true });
  const error = h('p', { class: 'form-error', role: 'alert' });
  const submit = h('button', { class: 'btn btn-primary btn-block', type: 'submit' }, t('choose.submit'));

  fill(root, h('main', { class: 'login-page' },
    h('div', { class: 'login-card' },
      h('div', { class: 'login-top' },
        h('span', { class: 'brand-mark' }, icon('key')),
        h('button', { class: 'btn btn-ghost btn-small', type: 'button', onclick: onLanguage }, icon('globe'), h('span', {}, t('lang.other')))),
      h('h1', { class: 'login-title' }, t('choose.title')),
      h('p', { class: 'muted' }, t('choose.text')),
      email ? h('p', { class: 'muted small' }, t('set.signedInAs'), ' ', ltr(email)) : null,
      h('form', { class: 'login-form', novalidate: true, onsubmit: save },
        currentInput ? field(t('set.current'), currentInput) : null,
        field(t('set.new'), fresh, { hint: t('set.newHint') }),
        field(t('set.again'), again),
        error,
        submit),
      h('button', { class: 'btn btn-ghost btn-small', type: 'button', onclick: onSignOut }, icon('logout'), h('span', {}, t('nav.signOut'))))));
  (currentInput || fresh).focus();

  async function save(event) {
    event.preventDefault();
    error.textContent = '';
    const currentValue = current || currentInput.value;
    const problem = !currentValue ? t('set.needCurrent')
      : fresh.value.length < 8 ? t('set.tooShort')
        : fresh.value !== again.value ? t('set.mismatch')
          : '';
    if (problem) {
      error.textContent = problem;
      return;
    }
    await busy(submit, async () => {
      try {
        await api('/auth/password', { method: 'POST', body: { current_password: currentValue, new_password: fresh.value } });
      } catch (err) {
        if (err.status !== 401) error.textContent = err.message;
        return;
      }
      toast(t('set.passwordChanged'), 'success');
      onDone();
    });
  }
}
