# Phase 3 demo scripts (Fanvue)

Fanvue has **no sandbox**: the live demo uses Summer's real account and every send is real. Get Lee's OK before step B3.

## A. Offline demo (no Fanvue account, `PUBLISHING_MODE=dry_run`)

1. `python -m pytest` → all green (the Fanvue tests run against a fake Fanvue, never the real one).
2. **Media → any item → tick "Fanvue only" → Save.** Try to attach it to an Instagram post: refused with
   "This media is marked Fanvue-only".
3. **Generate → platform `fanvue`, type `fanvue_post`** → the draft has no hashtags. Open it: *Who can see it* and
   *Price to unlock* appear. Enter `1.00` → refused (below the 3.00 minimum); `9.99` → saved, status back to draft.
4. Approve it with a planned time inside 08:00–21:00 and run `python -m app.cli worker --once`: the attempt reads
   "dry run: would publish fanvue_post to fanvue" and nothing leaves the machine.
5. **Fans** and **Fanvue** pages explain that they work in live mode; nothing is read or sent.

## B. Live demo (after Lee connects Fanvue, `PUBLISHING_MODE=live`, worker running)

1. **Accounts:** the Fanvue card shows `@its_me_summer`. After ~1 hour the worker log shows a token refresh.
2. **Fanvue:** 7-day and 30-day net earnings and subscriber changes match the Fanvue app. Press *Create bio links*;
   two links appear (`summer-instagram-bio`, `summer-tiktok-bio`) with copy boxes.
3. **One real post (with Lee's OK):** approve a subscribers-only Fanvue post; the worker publishes it inside the window;
   check it in the Fanvue app; the post page shows *published*.
4. **Fans:** within 2 minutes waiting chats appear; ordinary ones get an AI draft, one sent from a test fan account
   saying "are you real?" shows **needs you** and a push arrives with no message text. Lee edits a draft and presses
   Send; the message appears in the Fanvue app. Optional: one paid media reply to his own test fan account.
5. Monday after 09:00 (UK time): the weekly report push arrives.

Record dates and results in `docs/LIVE_PROOF.md`; list anything that behaved differently from `docs/PLATFORM_API_NOTES.md`.
