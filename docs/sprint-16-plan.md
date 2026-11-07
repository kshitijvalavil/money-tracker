# Sprint 16 plan: Voice note adds a bill or expense

Status: **plan only, waiting for Boss approval on the live board.** Starts after Sprint 15 is released. Gets its own branch and its own PR.
Owner of this file: Manager.

## 1. Problem
Right after paying (say a UPI payment, a card swipe, cash for chai) the user wants to say it in one breath and have it recorded:
- "Paid 450 rupees to Starbucks on UPI"
- "Swiped HDFC card at Reliance Fresh, 1,230"
- "Chai 20 and auto 80, cash"
- "Received 5000 from Neha for the trip"
- "Electricity bill 1,840 due on the 15th, monthly"

## 2. Decision
Speech to text on the device, then a free local parser, then a one-tap confirm card. Claude is a fallback for unclear sentences, text only, only after consent. Audio is never stored by the app and never sent to our server.

- **Speech to text:** the browser's built-in `SpeechRecognition` (free, no new server). Default language `en-IN`, picker for `hi-IN` (Hinglish like "paanch sau" handled by the parser). Support is good on Chrome and Android, partial on Safari/iOS, missing on Firefox, and it needs a secure context (HTTPS or localhost) plus mic permission. Honest privacy note shown in the consent dialog: on Chrome the browser's recogniser may send the audio to the browser vendor's speech service; the app itself does not upload or keep audio.
- **When voice is unavailable** (Firefox, denied permission, plain-HTTP phone test, offline): the same box accepts typed text or the phone keyboard's own dictation mic, and goes through the same parser. Voice is an enhancement, never the only way.
- **Parser** (`voiceparser.py` + a JS twin, mirrors `bot/parsers.py` style): amount ("450", "₹450", "four fifty", "4.5k", "1.2 lakh", "paanch sau"), direction (paid, spent, bought, swiped vs received, got, salary, refund), merchant (after at/to/for/from), channel via the existing `channel_for` rule (UPI, GPay, PhonePe, Paytm, card swipe, cash, net banking), account hint ("HDFC card" matched against the user's accounts), date words (today, yesterday, "on the 15th", "last Friday"), recurring hint (monthly, every week), several items in one sentence split on "and"/commas.
- **Category:** reuse `category_rules` (`bot/review.py`) then keyword guesses; otherwise the user picks.
- **Confirm card:** shows amount, merchant, date, account, category, direction with low-confidence fields highlighted. One tap "Add" saves a transaction (`source = 'voice'`); "Add as bill" creates a recurring entry; "Send to review" puts it in the review inbox instead. Nothing is saved silently by voice.
- **Claude fallback** (`POST /api/parse-voice`, modelled on `/api/parse-bill-text`): receives only the transcript text, signed-in only, rate limited, size capped, demo mode returns a fixed sample, explicit consent.

## 3. Mobile and desktop
- Phone: floating mic button on Home and Transactions, thumb reach; full-screen listening sheet with live transcript and a big stop button; confirm card as a bottom sheet; haptic tick on success where supported; works from the installed PWA.
- Desktop: mic button in the quick-add bar, keyboard shortcut (e.g. `V`), transcript and confirm card in a side panel; push-to-talk with the space bar optional.
- Both: day and dark themes, 44px targets, screen-reader announcements for state (listening, heard, saved), visible fallback text box, reduced motion respected.
- Local phone testing needs HTTPS for the mic: `docs/local-testing.md` will give two ways (a free HTTPS tunnel, or Chrome's "treat insecure origin as secure" flag on the test phone). Plain `http://<laptop-ip>` will not get microphone access.

## 4. Work breakdown
| Bot | Work |
|---|---|
| Tabledore (db) | Check/allow `source = 'voice'` on `transactions` and `pending_imports` (migration `0021_voice.sql` only if a constraint requires it), RLS unchanged, SQL test. |
| Byte Vader (be) | `voiceparser.py` + JS twin kept identical by a shared fixture file, `/api/parse-voice`. |
| Sir Pixelot (fe) | Mic button, listening sheet, transcript editor, confirm card, language picker, consent dialog, fallbacks, `demo.js` mirror, PWA precache. |
| Sherlock Bugs (qa) | Section 5 matrix. |
| Captain Shipit (ops) | Local gate (same as `docs/sprint-15-plan.md` section 8) + new PR. |

## 5. Test matrix
**Parser fixtures** (`tests/fixtures/voice_phrases.json`, 150+ phrases, each with expected fields; the same file feeds the Python and JS parsers so they cannot drift):
- Amounts: digits, commas, Indian grouping, words ("four fifty", "twelve hundred"), k/lakh/crore, "rupees/rs/₹", decimals, "paise", ambiguous numbers (a phone number or date in the sentence is not the amount).
- Direction: paid/spent/bought/swiped/transferred out vs received/got/credited/salary/refund; "paid back"/"owe" edge cases flagged low confidence.
- Channel: UPI, GPay, PhonePe, Paytm, BHIM, card swipe, credit card, debit card, cash, net banking, EMI.
- Merchant and notes: multi-word names, Hinglish, non-Latin script, apostrophes, very long, none given.
- Dates: today, yesterday, day names, "on the 15th", "last month", future bill due dates, invalid dates.
- Multi-item sentences and misheard text (homophones like "for/four", "to/two" around numbers).
- Safety: empty transcript, silence, 1,000-word ramble, `<script>` and formula characters in text, prompt-injection text sent to the Claude fallback (must not change behaviour), amounts above the numeric limit, zero and negative.

**Speech layer (mocked)**: Playwright injects a fake `SpeechRecognition` to test: start/stop, interim vs final text, no-speech timeout, permission denied, `not-allowed`, `audio-capture`, network error, unsupported browser, double-tap start, tab backgrounded mid-listening, language switch, offline.

**Browser**: voice -> confirm -> add; voice -> add as bill; voice -> send to review; edit a wrong field; cancel; undo within the app's undo window; duplicate guard (same phrase twice in 10 s asks "add again?"); phone 390, tablet 768, desktop 1440; dark and day; keyboard-only; screen-reader labels; installed-PWA mode.

**Regression**: the full suite (all existing suites plus Sprint 15's) unchanged. Re-verify the transactions form, review inbox, bills/recurring, Snap-a-bill, budgets, alerts, Ask, offline mode.

**Security**: mic only on user tap; no always-on listening; no audio stored or logged; transcript escaped everywhere; `/api/parse-voice` verified sign-in + rate limit + size cap; consent recorded per device, revocable in Profile; CSP unchanged.

## 6. Local run + sample data for Boss
- `docs/local-testing.md` (from Sprint 15) extended with the HTTPS-for-mic steps.
- `tests/fixtures/voice_phrases.json` doubles as a **cheat sheet of phrases to say** (20 marked "try these").
- A "Type instead" box with a "Load sample sentences" button in demo mode so every case can be tried without a microphone.
- Demo data resets with one command.

## 7. Process for every sprint from now on
1. Plan on the live board, Boss approves.
2. New branch and **new PR per sprint**, draft until the gate passes. (Sprint 16 needs its own branch; the Manager will ask Boss before creating it, since this session is limited to one named branch.)
3. Captain Shipit runs the full local gate (`docs/sprint-15-plan.md` section 8) including regression counts against `main`.
4. Boss tries it locally on phone and desktop with the sample data.
5. Only then merge and deploy.

## 8. Risks and honest limits
- Browser speech recognition varies by browser, language and noise; the typed fallback and the confirm card are what make it safe.
- iOS Safari support is partial and has had quirks with continuous listening; v1 uses single-utterance (tap, speak, stop), not continuous.
- Hindi/Hinglish accuracy depends on the browser's recogniser; the parser handles common Hinglish number words but cannot fix a bad transcript.
- "Swiped a UPI" cannot move money or read the bank; it only records what the user says.
