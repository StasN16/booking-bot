/** Small helpers shared by the views. */

export function groupBy(items, keyOf) {
  const groups = new Map();
  for (const item of items) {
    const key = keyOf(item);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  return groups;
}

export function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

/**
 * A phone number the way WhatsApp writes it: digits only, country code
 * first. Mirrors normalize_phone on the server, so a number typed here
 * finds the customer the bot already knows. Null when it is not a number.
 */
export function normalizePhone(value) {
  let digits = String(value || '').replace(/\D/g, '');
  if (digits.startsWith('00')) digits = digits.slice(2);
  else if (digits.startsWith('0') && (digits.length === 9 || digits.length === 10)) digits = `972${digits.slice(1)}`;
  return digits.length >= 8 && digits.length <= 15 ? digits : null;
}

/**
 * What to search customers for. Names go as typed; a number goes as it is
 * stored, so "054-338" finds 972543381998.
 */
export function searchTerm(value) {
  const text = String(value || '').trim();
  if (!/^[\d\s()+-]+$/.test(text)) return text;
  let digits = text.replace(/\D/g, '');
  if (digits.startsWith('00')) digits = digits.slice(2);
  else if (digits.startsWith('0')) digits = `972${digits.slice(1)}`;
  return digits;
}

/** A link that opens a WhatsApp chat with the number, with a drafted message if given. */
export function whatsappLink(phone, text) {
  const digits = String(phone || '').replace(/\D/g, '');
  return `https://wa.me/${digits}${text ? `?text=${encodeURIComponent(text)}` : ''}`;
}

/** Names compare the way the bot matches them: case and outer spaces aside. */
export function sameName(a, b) {
  return String(a || '').trim().toLowerCase() === String(b || '').trim().toLowerCase();
}
