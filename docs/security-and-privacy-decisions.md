# Security and privacy: what is done, what was decided, what needs the owner

Written during Sprint 14 (November 2026). It answers "what does a bank-grade setup have that this app does not" honestly, item by item.

## In the app and the repository (done)

- Sign-in checked on the server for the bill endpoints; rate limits per person.
- Row-level security on every table; tested with two users on every table in CI.
- Two-step sign-in (authenticator app), idle sign-out after 30 minutes, sign out of all devices, recent security activity.
- Download all my data; delete my account (one step, removes everything).
- Privacy notice and a consent screen before first use; consent recorded.
- Security log (append-only, 12 months, trims itself).
- Content-Security-Policy with per-request nonces: no outside script hosts, no inline scripts without the nonce. The bill-reading OCR is served from our own files.
- Lint in CI that fails if user-typed text reaches the page without escaping.
- Card numbers (Luhn-valid, 13 to 19 digits) are masked to the last four in imported emails.
- CI: dependency audit, secret scan of code and history, CodeQL, Dependabot, live-site check every 30 minutes.
- `security.txt` and private vulnerability reporting link; incident runbook (`docs/incident-response.md`).
- Friendly "we are updating" page and a maintenance switch.

## Decisions (not building now, and why)

- **Column-level encryption of money data.** Supabase encrypts the database disks and backups at rest and all traffic uses TLS. Encrypting individual columns in the app would break server-side totals, search, budgets and forecasts (they run in the database) and move the key problem into the browser or server. Revisit only if the threat is "someone with database access", which today is the owner. The privacy notice says plainly that the owner has administrator access.
- **Direct bank connections.** Not done and not planned. In India the lawful route is the RBI Account Aggregator framework through a licensed Account Aggregator and Financial Information User registration. Never ask for or store bank logins or scrape bank sites.
- **Card data (PCI DSS).** The app never takes or stores card numbers; imported text is masked. That keeps it outside PCI scope. Keep it that way.
- **Third-party AI for bills.** The live "Snap a bill" path reads the photo on the device (OCR) and sends only text to our server, which does not store it. The paid vision path (sends the image to Anthropic) exists in code but is not used by the live app. If it is ever switched on for users it needs its own consent switch and a mention in the privacy notice first.
- **Email import.** Parsed fields only (amount, date, note, masked account tail); the email body is not stored. Only the owner's own mailbox is connected. Before connecting anyone else's, add a per-person consent step, a disconnect button and token revocation.
- **Retention.** Data stays until the person deletes it or their account. The security log keeps 12 months (automatic). There is no scheduler, so nothing else is purged automatically; rejected import rows stay until the person deletes them.
- **Pinning GitHub Actions to commit SHAs.** Not done. Dependabot keeps the action versions current; SHA pinning would need the SHAs looked up outside this repository. Do it when convenient.

## Needs the owner (cannot be done from code)

These are settings in other people's dashboards. Each is a card on the live board.

| What | Why it matters | Where |
|---|---|---|
| Upgrade Supabase to Pro | Leaked-password check (HaveIBeenPwned), daily backups with restore, session time-boxing | Supabase > Billing |
| Privacy contact email on Render | Put your email in `PRIVACY_CONTACT_EMAIL` so the privacy notice and security.txt show who to write to (no email is published today) | Render > Environment |
| Turn on GitHub private vulnerability reporting | The security.txt link points to it | GitHub > Settings > Code security |
| Review the privacy notice wording | It makes promises in your name | `templates/privacy.html` |
| Test a restore once you have backups | A backup you have not restored is a hope | Supabase > Database > Backups |
| WAF / bot protection (for example Cloudflare in front) and a separate staging Supabase project | Real tools for a public launch; not needed while invite-only | Later |
| External penetration test | Before opening sign-up to the public | Later |

## Every sprint

- [ ] Run the Supabase security and performance advisors (Dashboard > Advisors, or the MCP tool) and post the result on the board.
- [ ] Re-read this file's "Needs the owner" table; tick what has been done.
