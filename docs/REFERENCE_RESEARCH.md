# Reference Account Research (human task — NOT automated)

Six public reference accounts were suggested by the client for style
calibration of Summer's persona.

**What was actually done:** nothing automated. Instagram profiles were **not
scraped**: Instagram is behind a login wall and its ToS forbid automated
collection, and Phase 1 explicitly excludes any scraping. TikTok was not
scraped either. No fake "summaries" were invented.

**What happens next:** a human looks at the accounts manually (in a regular
browser session) and fills the table below with observations. Then copy the
summary into Summer's profile `style_notes` field via the dashboard Profile
page (this field is already wired into the system prompt via
`<style_notes_from_reference_accounts>`).

| # | Account (URL) | Hook styles seen | Typical caption length | Emoji usage | CTA patterns | Posting cadence | Formats used (feed/reel/story/photo-mode) |
|---|---|---|---|---|---|---|---|
| 1 | _fill in_ | | | | | | |
| 2 | _fill in_ | | | | | | |
| 3 | _fill in_ | | | | | | |
| 4 | _fill in_ | | | | | | |
| 5 | _fill in_ | | | | | | |
| 6 | _fill in_ | | | | | | |

## Instructions for the human filling this in

- Describe patterns, never copy exact wording from any real account.
- Note what works on **feed posts vs reels vs photo-mode** separately.
- When done, paste a distilled summary into the Summer profile's
  **Style notes** box at `/profiles/1` and save. The next generation call
  picks it up.
