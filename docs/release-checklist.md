# Sherlock Bugs release checklist

Run for every sprint. Tick each box in the PR and post the ticked list to the
live board's log ("Before deploy" and "After deploy" entries for that sprint).

## Before deploy (on the PR, before merge)

- [ ] Parser, scanner, recurring, bill reader and review-engine unit tests pass
- [ ] Database tests pass (schema, RLS between two users, money math, new migration)
- [ ] All browser suites pass in demo mode (smoke, bills, goals, PWA, capture, investments, email/review, phone layout, this sprint's suite)
- [ ] Design check: 390, 768 and 1440 wide, dark and day: no sideways scroll, no clipped text, 44px tap targets on phone
- [ ] Security re-run: no keys in code or git history, keys only in Render/GitHub secrets, RLS on every new table, forms escaped (no XSS), rate limits intact, security headers + CSP, debug off, `pip-audit` clean, unused files removed
- [ ] New database migration applied to Supabase (and checked with the advisors)
- [ ] CI green on the PR

## After deploy (on the live site)

- [ ] Render finished the deploy of the merged commit (auto-deploy hook ran)
- [ ] Live site loads; the version served matches main (CSS/JS of the new commit)
- [ ] Security headers present on the live response
- [ ] Every page opens at 390 and 1440 with no sideways scroll, dark and day
- [ ] This sprint's new screens are live and look like the approved design
- [ ] Sign-in pages redirect correctly when signed out
- [ ] Live link posted on the board under Captain Shipit, sprint marked Released

## Planned maintenance (database changes, risky releases)

Normal deploys need nothing: if the server restarts, people who have the app
installed see the "Captain Shipit is landing a fresh build" page and are taken
back in automatically when it is up. (First visit ever during a restart is
answered by Render, not by us.)

For planned work where the app should be closed on purpose:

- [ ] Render > money-tracker > Environment: set `MAINTENANCE_MODE=1` and save. Every page now shows the friendly updating screen with a 503; `/healthz` stays up so Render keeps the service
- [ ] Do the work (migration, checks)
- [ ] Set `MAINTENANCE_MODE` back to empty (or delete it) and save. Waiting users land back in the app by themselves within seconds
- [ ] Open the live site and confirm it is back
