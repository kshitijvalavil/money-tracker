# Incident response: if something goes wrong with people's data

Money Tracker holds private money information. If you think data was seen, copied,
changed or lost by someone who should not have it, follow this in order. Do not
wait until you are sure.

## 1. Stop the bleeding (first 30 minutes)

- [ ] Turn the app off on purpose: Render > money-tracker > Environment > `MAINTENANCE_MODE=1` (people see the friendly updating page; nothing else is reachable).
- [ ] If an account is involved: Supabase > Authentication > Users > the user > **Ban user** (or Delete user if they asked).
- [ ] If a key may have leaked, rotate it now (list below). Rotating is cheap, leaking twice is not.
- [ ] Write down the time you found out and how. This starts the clock.

## 2. Find out what happened

- [ ] Supabase > Logs (Auth, API, Postgres): who signed in, from where, what was read.
- [ ] GitHub > Security tab and Actions logs: any new secret alerts, unexpected workflow runs, unexpected pushes to `main`.
- [ ] Render > Logs and Events: unexpected deploys, env var changes.
- [ ] Each person's "Recent security activity" on their Profile shows their own sign-ins and changes.
- [ ] Decide: which people, which data (login email? transactions? imported emails?), which time window.

## 3. Rotate secrets (as needed)

| Secret | Where it lives | How to rotate |
|---|---|---|
| Supabase anon/publishable key | Render env `SUPABASE_ANON_KEY`, public by design | Supabase > Settings > API keys; update Render |
| Supabase service_role / secret key | Not used by the app. Must never be in code | Rotate in Supabase if it ever appears anywhere |
| Database password | Supabase > Settings > Database | Reset; update anything that uses it |
| Anthropic key | Render env `ANTHROPIC_API_KEY` | Create a new key, update Render, delete the old one |
| Render deploy hook | GitHub secret `RENDER_DEPLOY_HOOK` | Regenerate in Render, update the GitHub secret |
| A person's password / sessions | Supabase Auth | Ban or reset the user; Profile > "Sign out of all devices" |

## 4. Tell people, without delay

India's data protection law (DPDP Act 2023 and its 2025 Rules, duties start about May 2027) requires telling the Data Protection Board and each affected person when personal data is breached, "without delay", with a fuller report soon after (summaries mention 72 hours). Treat that as the standard now.

Message to affected people (edit the brackets, send by email):

> Subject: Important: a problem with your Money Tracker data
>
> On [date/time] we found that [what happened, in one or two plain sentences].
> The information involved was: [list: for example your email address and your transactions].
> It was not involved: [for example your password, which we never see].
> What we have done: [stopped it, fixed it, changed keys].
> What you should do: [change your password; sign out of all devices from Profile; watch for strange messages; nothing else needed].
> If you have questions or want your account deleted, reply to this email.
> We are sorry. [name]

- [ ] Send to every affected person.
- [ ] Report to the Data Protection Board of India once its reporting process is live (check the current official process).
- [ ] If the problem is in Supabase, Render or GitHub themselves, follow their status pages and tell people what they say.

## 5. Fix and learn

- [ ] Fix the cause on a branch, with a test that would have caught it; PR, CI green, merge.
- [ ] Turn `MAINTENANCE_MODE` off only when you are sure it is fixed. Open the live site and check.
- [ ] Write a short, blame-free note: what happened, when we knew, what we changed, what we will do so it cannot repeat. Keep it in `security/`.
