# Review record — multi-user web app

Why this is in the repo: the branch handles other people's Google credentials,
and most of its design was settled by review rounds rather than by the plan.
These are the reports behind those decisions. The conclusions are summarised in
[the handoff notes](../../specs/2026-10-14-multiuser-webapp-handoff.md); this
directory is the long form, kept so a later change can see what was already
checked and what it would be breaking.

Spec: [`../../specs/2026-10-14-multiuser-webapp-design.md`](../../specs/2026-10-14-multiuser-webapp-design.md) ·
Plan: [`../../plans/2026-10-14-multiuser-webapp.md`](../../plans/2026-10-14-multiuser-webapp.md)

| File | What it is |
|---|---|
| `ledger.md` | The execution record: every task, every fix round, and all 64 numbered rulings (A–VV) with their cost-if-wrong |
| `review-9.md` | Task 9 (FastAPI assembly). Found the path-traversal hole in `aoi_path` and the 64 KiB stdout line limit |
| `review-9-fix2.md`, `review-9-fix3.md` | Re-reviews of the Task 9 fixes, incl. 12 probed traversal-bypass variants |
| `review-10.md` | Task 10 (deployment). `deploy/.env` was not git-ignored |
| `final-review-brief.md` | What the whole-branch reviewer was asked to check |
| `review-final.md` | Whole-branch review. Found the critical unguarded inventory handler |
| `review-final-fixes.md` | Re-review of fix rounds A–C |
| `review-round-d.md` | Re-review of the submit-timeout race fix, with the real-clock stall sweep |
| `task-*-report.md` | Each implementer's own account of its task |

Not kept: the `git diff` files each review ran against — regenerate any of them
with the commit range named at the top of the report.

## The two findings worth remembering

**A render must never run in the web process.** `ee._state._state` is one
process-wide global, so an `ee.Initialize` on the server would attribute a
signed-in user's render to whatever credentials the host happens to hold. One
unguarded Gradio handler (`gui.py`, the scene-inventory button) did exactly
that and survived all ten task reviews.

**A credential must never leave memory or a worker's stdin.** Not `argv`, not
the environment, not a log record, not a user-facing error. `review-final.md`
traces the whole path; the handoff notes list the two invariants a future change
could break without any test going red.
