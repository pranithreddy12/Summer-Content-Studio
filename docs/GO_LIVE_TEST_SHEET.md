# Go-live test sheet

Tick each box and write the date and what you saw. Do A first, then B, then C. The server is in **Live** mode, so anything in B really posts. Do B with Lee watching.

Where things are: Settings = sidebar → Setup → Settings. Drafts = Queue. Accounts = Setup → Accounts.

## A. Safe tests (nothing public)

- [ ] **A1 Pause.** Press Pause posting (sidebar foot). Pass: the button turns into Resume and Settings shows mode Off. Press Resume. Pass: mode is back to what it was.
- [ ] **A2 Settings save.** Change "Drafts per day" from 1 to 2, Save. Pass: "Recent changes" lists it with old and new value. Reset it. Pass: the list shows the reset.
- [ ] **A3 Telegram draft.** Generate one draft. Pass: Telegram message arrives with Approve and Open buttons; Open lands on that post.
- [ ] **A4 Approve by tap.** Tap Approve in Telegram. Pass: the post is Scheduled with a time inside the window (8am–9pm London) and shows in Planner/Calendar.
- [ ] **A5 Quiet day and daily cap.** Tick a weekday under "No posting on" and set "Most posts per day" to 1. Pass: nothing is planned on the quiet day; a second due post waits until tomorrow. Untick and set back.
- [ ] **A6 Connections.** Accounts shows Instagram, TikTok and Fanvue as Connected; `/health` shows `worker_ok: true`.

## B. First real posts (one per platform, with Lee's OK)

- [ ] **B1 TikTok.** Approve one TikTok draft with a video. Pass: it appears in Summer's TikTok app drafts and the "TikTok ready" push arrives. Post it in the app, then press Mark as posted.
- [ ] **B2 Instagram.** First confirm with Lee that `@pand.aa460` is the intended account. Approve one post. Pass: it appears on the account, the "Posted to Instagram" push has the link, the studio shows Published.
- [ ] **B3 Fanvue post.** Approve one subscribers-only post. Pass: it shows in the Fanvue app and the studio shows Published.
- [ ] **B4 Fanvue fan reply.** Reply to one real fan message and press Send yourself. Pass: the fan receives it.
- [ ] **B5 Escalation.** Send a test message "are you real?" from a test fan. Pass: no AI reply is drafted and a Telegram alert arrives.
- [ ] **B6 Token refresh.** Next day, Accounts still shows Connected without reconnecting. In the worker log (`journalctl -u summer-worker`) the TikTok and Fanvue refresh lines show no errors.

## C. Unattended run (2 to 3 days)

Settings: Publishing mode Live, "Plan and draft every day" on, "Fully automatic" and "Approve after hours" **off**. Approve from Telegram.

- [ ] **C1 Day 1.** Drafts arrive in the morning with Approve buttons; approved posts get slots inside the window.
- [ ] **C2 Day 2.** Approved posts went out by themselves at their slots; no post went out twice; no post is stuck in Publishing or Failed.
- [ ] **C3 Day 3.** Numbers match the rules (posts per day, no posts on quiet days, daily cap respected). Check `/health` and the worker log once per day.
- [ ] **C4 Draft quality.** Lee has read at least 10 drafts and is happy with the voice. Only then consider turning on "Approve after hours".

## D. Reviews and open items

- [ ] **D1 TikTok app review.** After B1 works: record the demo (PC clip + phone clip, mp4/mov, max 50 MB each) and Submit for review. Takes about 1 to 2 weeks. Until then TikTok stays in inbox mode.
- [ ] **D2 Meta.** Check in the Meta app dashboard whether posting to Lee's own Instagram account needs app review. If it does, submit it.
- [ ] **D3 Legal pages.** Lee supplies brand name, a non-personal contact email, data deletion days and governing law; Lee reviews the Terms wording.
- [ ] **D4 Login.** Lee signs in on his PC with the login page.
- [ ] **D5 Fully automatic.** Only after C is clean for a few days and D4/C4 are done: switch on per platform, starting with one.

## Known low-risk limits (audit 2026-11-27)

- Telegram Approve approves the caption as it is at tap time (including edits made after the message was sent).
- If `TELEGRAM_CHAT_ID` is a group, any member can tap Approve. Keep it a private chat.
- Two saves of the calendar at the same moment as a worker run could in theory overlap; a second save fixes it.
- Instagram image size and shape are not checked before sending; a wrong shape fails on Instagram's side and shows as Failed with the reason.
- While posting is paused, drafts can still be auto-approved and scheduled (if those options are on); nothing is posted until you Resume.
- The daily cap counts manual "Publish now" posts as used, but never blocks them.
