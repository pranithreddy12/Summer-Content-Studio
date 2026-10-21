# Live generation proof (Phase 1)

Run on 2026-10-16 through the real app (`POST /api/v1/content/generate`) against a fresh scratch database,
provider **Mistral**, model `ministral-8b-latest`, prompt version `v1`, campaign `halloween`, profile Summer.
Output is stored as a **draft**; nothing is published. Captions are AI drafts that need human review.

## instagram / reel
- status: `draft`  source: `ai`
- hook: Trick or treat? 👀
- caption: This Halloween, I’m going full *spooky* but *sweet*—because even ghosts need a little sugar. 🍬 Who’s team candy or team mischief? Drop a 🎃 in the comments if you’re with me. 😏
- hashtags: #halloweenvibes, #spookysweet, #candylover, #witchyenergy, #halloween2024
- call to action: Follow for more playful Halloween energy!
- content notes: Consider adding a playful text overlay like 'Spooky but not scary' or 'Candy or chaos?' for visual engagement. Ensure AI-generated content is labeled as such in the description or notes. Hook could also be a question like 'Ready to turn up the Halloween?' for variety.

## tiktok / video
- status: `draft`  source: `ai`
- hook: trick or treat? 👀
- caption: okay, who’s ready to turn up the spooky *and* the heat this halloween? 🎃✨ i’m not just about the candy—i’m about the *vibes*. who’s joining me for a night of fun (and maybe a little mischief)?
- hashtags: #halloweenseason, #spookybutsweet, #vibesonly, #witchyenergy, #playfulhalloween
- call to action: turn on notifications—i’ve got plans!
- content notes: Hook leans into playful teasing with a wink (👀) to keep it flirty but Halloween-appropriate. Ensure on-screen text matches the hook for visibility. Label this AI-generated content as per platform guidelines. Vibe check: playful, not scary.

## Generation log rows
| provider | model | purpose | success | input tokens | output tokens | prompt |
|---|---|---|---|---|---|---|
| mistral | ministral-8b-latest | generate | True | 805 | 204 | v1 |
| mistral | ministral-8b-latest | generate | True | 827 | 209 | v1 |

## Review notes
- Tone matches the persona (playful, flirty, non-explicit); no political content; no adult-platform names; no invented personal events.
- Known weakness: the small model added a stale year hashtag (`#halloween2024`). Review hashtags before posting.
- Not yet verified: Claude (Anthropic) live generation - no funded key available during testing.
- Not yet verified: generation with a real image/video description from the client's media.
