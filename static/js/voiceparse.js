// Voice note parser (Sprint 16). Turns a spoken sentence like
// "paid 450 to Starbucks on UPI" into one or more draft transactions. Pure
// functions, no DOM, so the same file is tested under node against
// tests/fixtures/voice_phrases.json, the very file the Python twin uses.
//
// parseVoice(text) -> { rows: [row, ...], error? } where a row is
// { amount, direction, merchant, channel, account_hint, category_guess,
//   frequency_guess, is_bill, date_word, day_of_month, confidence }.
// It never saves anything; the UI shows a confirm card first.

export const MAX_AMOUNT = 999999999999.99;

const WORD_NUM = { zero: 0, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9,
  ten: 10, eleven: 11, twelve: 12, thirteen: 13, fourteen: 14, fifteen: 15, sixteen: 16, seventeen: 17,
  eighteen: 18, nineteen: 19, twenty: 20, thirty: 30, forty: 40, fifty: 50, sixty: 60, seventy: 70,
  eighty: 80, ninety: 90, hundred: 100, thousand: 1000, lakh: 100000, lakhs: 100000, crore: 10000000, crores: 10000000 };
// Common Hindi/Hinglish number words.
const HINDI_NUM = { ek: 1, do: 2, teen: 3, char: 4, chaar: 4, paanch: 5, panch: 5, chhe: 6, cheh: 6, saat: 7,
  aath: 8, nau: 9, das: 10, bees: 20, pachaas: 50, pachas: 50, sau: 100, hazaar: 1000, hazar: 1000, lakh: 100000 };

const INCOME = /\b(received|got|credited|credit|refund(?:ed)?|salary|income|earned|deposited|cashback|reimburs\w*)\b/i;
const EXPENSE = /\b(paid|spent|bought|swiped|gave|debited|debit|purchased|billed|sent)\b/i;

const CHANNELS = [
  [/\b(g[\s-]?pay|google\s*pay)\b/i, "Google Pay"], [/\bphone\s*pe\b/i, "PhonePe"], [/\bpaytm\b/i, "Paytm"],
  [/\bbhim\b/i, "BHIM"], [/\bupi\b/i, "UPI"], [/\bnet[\s-]?banking\b/i, "Net banking"],
  [/\b(imps|neft|rtgs)\b/i, "Bank transfer"], [/\b(credit|debit)?\s*card\b|\bswiped?\b/i, "Card"],
  [/\b(emi)\b/i, "EMI"], [/\bcash\b/i, "Cash"],
];
const ACCOUNTS = [/\b(hdfc|icici|sbi|kotak|axis|yes\s*bank|idfc|amex|rbl|hsbc|citi|pnb|bob|canara)\b/i];

const CATS = [
  [/\b(chai|tea|coffee|lunch|dinner|breakfast|food|eat|snack|grocer|vegetable|veggies|restaurant|cafe|swiggy|zomato|dmart|biscuit|milk|soft drinks|beverage)\b/i, "Food"],
  [/\b(auto|cab|uber|ola|rickshaw|metro|bus|train|flights?|petrol|diesel|fuel|parking|toll|travel|taxi)\b/i, "Transport"],
  [/\b(medicine|pharmac|doctor|hospital|clinic|apollo|health|gym|fitness)\b/i, "Health"],
  [/\brent\b/i, "Rent"],
  [/\b(bill|electricity|water|gas|phone|mobile|internet|broadband|wifi|recharge|subscription|netflix|prime|spotify|hotstar|dth)\b/i, "Bills"],
  [/\b(clothes|apparel|shirt|shoes|amazon|flipkart|myntra|shopping|laundry|book)\b/i, "Shopping"],
  [/\b(movie|cinema|fun|party|game|concert|entertain)\b/i, "Entertainment"],
  [/\b(salary|payroll|wages?)\b/i, "Salary"], [/\b(freelance|invoice|client)\b/i, "Freelance"],
  [/\b(gift|present)\b/i, "Gift"], [/\b(invest|mutual fund|sip|stocks?|ipo|shares?)\b/i, "Investment"],
];
const FREQ = [[/\b(daily|every day)\b/i, "weekly"], [/\b(weekly|every week|per week)\b/i, "weekly"],
  [/\b(monthly|every month|per month|a month)\b/i, "monthly"], [/\b(quarter(ly)?)\b/i, "quarterly"],
  [/\b(yearly|annual(ly)?|every year|per year)\b/i, "yearly"]];

const clean = (s) => String(s || "").replace(/\s+/g, " ").trim();

// ---- amount ----------------------------------------------------------------
function digitsAmount(text) {
  // "2340 rupees 50 paise" -> 2340.50 before the main scan.
  text = text.replace(/(\d[\d,]*)\s*(?:rupees?|rs\.?|₹)\s*(\d{1,2})\s*paise?/gi, (_, r, p) => `${r.replace(/,/g, "")}.${p.padStart(2, "0")}`);
  // Numbers with optional k / lakh / crore and rupee words. Skip 10-digit phone numbers.
  const re = /(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d{1,2})?)\s*(k|lakhs?|lacs?|crores?|cr)?/gi;
  let m, best = null;
  while ((m = re.exec(text))) {
    const digits = m[1].replace(/,/g, "");
    if (/^\d{10}$/.test(digits) && !m[2]) continue;                 // phone number
    let v = parseFloat(digits);
    const unit = (m[2] || "").toLowerCase();
    if (unit === "k") v *= 1000;
    else if (unit.startsWith("lakh") || unit.startsWith("lac")) v *= 100000;
    else if (unit.startsWith("cr")) v *= 10000000;
    const at = m.index + (m[0].length - m[0].trimStart().length);
    if (v > 0 && (best === null || at < best.at)) best = { value: v, at, end: re.lastIndex, raw: m[0].trim() };
  }
  return best;
}

function wordsAmount(text) {
  const tokens = text.toLowerCase().replace(/[^a-z\s-]/g, " ").split(/[\s-]+/).filter(Boolean);
  let total = 0, cur = 0, used = [], any = false, startedAt = -1;
  let prevUnit = 0;
  tokens.forEach((tok, i) => {
    const n = WORD_NUM[tok] ?? HINDI_NUM[tok];
    if (n == null) { prevUnit = 0; return; }
    if (startedAt < 0) startedAt = i;
    any = true; used.push(i);
    if (n >= 100) { cur = (cur || 1) * n; if (n >= 1000) { total += cur; cur = 0; } }
    else if (prevUnit >= 1 && prevUnit <= 9 && n >= 20 && n % 10 === 0 && cur === prevUnit) {
      // Spoken price idiom: "four fifty" = 450, "two twenty" = 220.
      cur = prevUnit * 100 + n;
    } else cur += n;
    prevUnit = n < 10 ? n : 0;
  });
  const v = total + cur;
  // Only trust a run of number-words that are adjacent (a sentence may contain "to").
  const contiguous = used.length && used[used.length - 1] - used[0] <= used.length + 1;
  return any && v > 0 && contiguous ? { value: v, words: new Set(used), startTok: startedAt } : null;
}

// ---- one item ---------------------------------------------------------------
function parseItem(text) {
  text = clean(text);
  if (!text) return { error: "empty" };
  const amt = digitsAmount(text);
  let amount = null, cutStart = -1, cutEnd = -1;
  if (amt) { amount = amt.value; cutStart = amt.at; cutEnd = amt.end; }
  else {
    const w = wordsAmount(text);
    if (w) amount = w.value;
  }
  if (amount == null) return { error: "no_amount" };
  if (amount > MAX_AMOUNT) return { error: "too_big" };
  amount = Math.round(amount * 100) / 100;
  if (amount <= 0) return { error: "no_amount" };

  const low = text.toLowerCase();
  const direction = INCOME.test(low) && !EXPENSE.test(low) ? "income"
    : EXPENSE.test(low) ? "expense"
    : INCOME.test(low) ? "income" : "expense";

  let channel = null, account_hint = null;
  for (const [re, name] of CHANNELS) if (re.test(text)) { channel = name; break; }
  for (const re of ACCOUNTS) { const m = text.match(re); if (m) { account_hint = m[1].toUpperCase().replace(/\s+/g, " "); break; } }

  let category_guess = null;
  for (const [re, name] of CATS) if (re.test(low)) { category_guess = name; break; }
  let frequency_guess = null;
  for (const [re, name] of FREQ) if (re.test(low)) { frequency_guess = name; break; }
  const is_bill = /\bbill\b/.test(low) || !!frequency_guess;

  let date_word = null, day_of_month = null;
  if (/\btoday\b/.test(low)) date_word = "today";
  else if (/\byesterday\b/.test(low)) date_word = "yesterday";
  else { const dm = low.match(/\b(?:on\s+)?the\s+(\d{1,2})(?:st|nd|rd|th)\b/); if (dm) day_of_month = +dm[1]; }

  const merchant = extractMerchant(text, { cutStart, cutEnd, channel, account_hint, category_guess });

  // Confidence: lower if we had to guess the direction or the amount was in words.
  let confidence = 0.9;
  if (!INCOME.test(low) && !EXPENSE.test(low)) confidence -= 0.2;
  if (!amt) confidence -= 0.15;
  if (!merchant) confidence -= 0.1;
  return { amount, direction, merchant: merchant || null, channel, account_hint, category_guess,
    frequency_guess, is_bill, date_word, day_of_month, confidence: Math.max(0.3, Math.round(confidence * 100) / 100) };
}

function extractMerchant(text, ctx) {
  // "at <place>" names the merchant best ("...for groceries at DMart" -> DMart).
  // Otherwise take the words after to / from / for, stopping at a later for/to
  // clause ("from Neha for the trip" -> "Neha").
  let cand;
  const at = text.match(/\bat\s+(.+)$/i);
  if (at) cand = at[1];
  else {
    const m = text.match(/\b(?:to|from|for)\s+(.+)$/i);
    cand = m ? m[1].replace(/\s+\b(?:for|to)\b\s+.*$/i, "") : text;
  }
  // Drop the amount span and leading verbs.
  cand = cand.replace(/\b(paid|spent|bought|swiped|gave|received|got|credited|sent|purchased)\b/gi, " ");
  cand = cand.replace(/(?:₹|rs\.?|inr)?\s*\d[\d,]*(?:\.\d{1,2})?\s*(?:k|lakhs?|lacs?|crores?|cr|rupees?|rupee|paise)?/gi, " ");
  // Remove channel / account / date / frequency words.
  for (const [re] of CHANNELS) cand = cand.replace(re, " ");
  for (const re of ACCOUNTS) cand = cand.replace(re, " ");
  cand = cand.replace(/\b(card|on|via|using|my|the bill|bill|due|monthly|weekly|yearly|quarterly|daily|today|yesterday|net\s*banking|subscription|ride|trip|order|payment|txn|transaction|recharge)\b/gi, " ");
  cand = cand.replace(/\b(?:on\s+)?the\s+\d{1,2}(?:st|nd|rd|th)?\b/gi, " ");
  cand = cand.replace(/\band\b.*$/i, " ");
  cand = cand.replace(/[^\p{L}\p{N}\s'&.-]/gu, " ");
  // Number words at the start (word-amounts) shouldn't appear as the name.
  cand = cand.split(/\s+/).filter((w) => !(WORD_NUM[w.toLowerCase()] != null || HINDI_NUM[w.toLowerCase()] != null) || w.length > 6).join(" ");
  cand = clean(cand).replace(/^(for|to|at|from|of)\s+/i, "").replace(/\s+(for|to|at)$/i, "");
  return cand ? cand.slice(0, 80) : null;
}

// ---- several items in one sentence ------------------------------------------
export function parseVoice(text) {
  text = clean(text);
  if (!text) return { rows: [], error: "empty" };
  // Split on " and " / commas only when each part has its own number.
  const parts = text.split(/\s*,\s*|\s+and\s+/i).map(clean).filter(Boolean);
  const haveNums = parts.filter((p) => digitsAmount(p) || wordsAmount(p));
  const items = haveNums.length >= 2 ? parts : [text];
  const rows = []; let firstError = null;
  for (const part of items) {
    const r = parseItem(part);
    if (r.error) { if (!firstError) firstError = r.error; continue; }
    rows.push(r);
  }
  if (!rows.length) return { rows: [], error: firstError || "no_amount" };
  // A channel or account said once for the whole sentence ("…and auto 80 cash")
  // applies to every item that didn't name its own.
  if (rows.length > 1) {
    let fallbackCh = null, fallbackAcc = null;
    for (const [re, name] of CHANNELS) if (re.test(text)) { fallbackCh = name; break; }
    for (const re of ACCOUNTS) { const mm = text.match(re); if (mm) { fallbackAcc = mm[1].toUpperCase(); break; } }
    for (const r of rows) { if (!r.channel && fallbackCh) r.channel = fallbackCh; if (!r.account_hint && fallbackAcc) r.account_hint = fallbackAcc; }
  }
  return { rows };
}
