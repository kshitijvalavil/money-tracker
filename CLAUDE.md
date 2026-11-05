# Context for Claude sessions

## Live board (source of truth for project status)

Before planning or changing anything, read the Dev Team's live board:
**Money Tracker Live View** - https://claude.ai/artifact/TPwyarAeB9hScVxJrnMNpN

- Read the page with the Artifact tool (`action: "read"`), not WebFetch.
- Its shared database (read with `ArtifactData`) holds the current sprint, task
  states, Boss approvals and the activity log. Treat that data as the current
  status; treat this repo as the code.
- Related: Dev Team Office (visual only) - https://claude.ai/artifact/2MvCDFj2gphzLoQrLWg572
- Follow `docs/release-checklist.md` and post sprint entries to the board's log.

## Feedback

Owner feedback lives in `feedback/`. Read open items in `feedback/FEEDBACK.md`
before starting work, and mark an item addressed (with the commit) once done.
