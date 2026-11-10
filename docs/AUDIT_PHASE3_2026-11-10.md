# Phase 3 (Fanvue) audit - 2026-11-10

Read-only review of `PLAN_PHASE3.md` against `app/platforms/fanvue.py`, `app/services/fan_inbox.py`, `fanvue_report.py`, publishing/posts rules and tests. No live Fanvue call was made.

## Checked and fine
- **No `write:creator`**: scope validator refuses it; test asserts it (`test_phase3_rules.py`).
- **H7 Fanvue-only media**: refused at draft/approve (`posts.py`), at publish (`publishing.py`) and in random drafts.
- **Human in the loop**: `send_reply` runs only from a button; the AI never sets a price; PPV price is range-checked server-side; escalation runs before any AI call and again on the AI output.
- **Fan text**: no `|safe` or custom autoescape in templates; no fan text in log calls; pushes carry handle + fixed reason only; the thread is wrapped as untrusted data (tested).
- **Rotating refresh tokens**: only the worker refreshes (`accounts.is_worker`); web path raises `worker_not_running` once expired.
- **Retention**: `purge_old` deletes by age and keeps open escalations; tested with a fake clock.
- **Tests**: ~109 Phase 3 tests (plan target 90), all MockTransport; `conftest` blanks the Fanvue keys.
- A new fan message re-opening an *ignored* chat is deliberate (`test_new_message_reopens_an_ignored_chat`).

## Defects found and fixed
| # | Severity | Problem | Fix |
|---|---|---|---|
| 1 | Medium | A worker sync during a send (a PPV video upload can take minutes) flipped the chat from `sending` back to `needs_reply`, so a second Send press could message the fan twice. | `_sync_chat` leaves a `sending` chat's status alone. Test `test_sync_during_a_send_does_not_reopen_the_chat`. |
| 2 | Low | If sync stored our just-sent message (same Fanvue message uuid) before `send_reply` committed, the unique key raised after the message had already gone out: the user saw an error and might resend. | `send_reply` reuses the stored row. Test `test_send_survives_sync_having_stored_the_sent_message`. |

Both tests failed before the fix and pass after it.

## Still open (unchanged, needs Lee / live)
Every Fanvue call shape is doc-derived and unproven until L7-L10; H3-H9 client answers; auto-send stays unbuilt by design.
