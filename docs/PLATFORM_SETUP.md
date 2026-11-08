# Connecting the accounts — step by step

For Lee (and whoever helps him). **Nobody ever needs your passwords.** The app only receives:
an *App ID / App secret* (made by you, per platform), a *bot token* (Telegram) and an *API key* (Anthropic).
Everything else happens in your own browser by pressing **Allow** on the platform's own page.

> Menu names on Meta, TikTok and Dropbox change from time to time. Where a step says
> "look for…", the **official page linked in that section is the authority**; if a button has moved,
> send a screenshot and we will adjust. Items marked **(unconfirmed)** have not been tried yet — we
> find out on the first real attempt (see `docs/PLATFORM_API_NOTES.md`).

## 0. The short version

| # | What | Who | Time | Needed for |
|---|---|---|---|---|
| 1 | Server + domain | Lee rents, developer sets up | 30 min | everything below |
| 2 | Anthropic API key + credit | Lee | 10 min | Claude writing the captions |
| 3 | Telegram bot (or ntfy) | Lee | 10 min | push notifications on your phone |
| 4 | Instagram: Meta developer app | Lee (or developer, see 4b) | 30–60 min | posting to Instagram, comments |
| 5 | TikTok developer app | Lee | 30 min + TikTok's review time **(unconfirmed)** | sending videos to TikTok drafts |
| 6 | Dropbox → server copy | Lee approves once | 15 min | media library fills itself |
| 7 | Fanvue developer app (Phase 3) | Lee | 15 min | Fanvue posts, fan inbox, bio links, earnings report |

Do **1** first; the platforms need the server's web address. Do not wait for everything: the **reminder mode**
(push notification at each slot, you post by hand and press *Mark as posted*) works with only items 1–3 — or
with no accounts at all.

Send secrets (bot token, app secret, API key) **by a private route** (password manager share, or type them
yourself into the server's `.env` while the developer is on a call). Never paste them into a group chat.

---

## 1. Server and domain

Recommended: **DigitalOcean** (London, "Basic" 2 GB RAM droplet, tick *Backups*) or **Hetzner Cloud**
(similar size, usually cheaper). Ubuntu 24.04 LTS. Disk: the size of your Dropbox media **plus 20 GB**.
Domain: any registrar (Cloudflare, Namecheap…); about the price of a coffee per month for the whole stack
(see `docs/DEPLOY.md` for approximate costs — check current prices when you buy).

You own the accounts; add the developer as a team member / give them your SSH public key — never your
login. Then in the domain's DNS add an **A record**: `studio.<yourdomain>` → the server's IP address.
The rest is in `docs/DEPLOY.md`.

## 2. Anthropic API key (Claude)

A **Claude.ai subscription does not include API credit** — the API is billed separately.

1. Go to <https://console.anthropic.com> and sign in (create an account if needed).
2. **Settings → Billing**: add a card and buy a small amount of credit (e.g. $10 lasts a long time: one caption
   costs a few cents). Turn on a **monthly spend limit**.
3. **Settings → API keys → Create key**. Name it `summer-content`. Copy the key **once** (it starts with `sk-ant-`).
4. Give it to the developer privately; it goes into `.env` as `ANTHROPIC_API_KEY`.

To stop everything instantly: delete the key in the console.

## 3. Push notifications

### 3a. Telegram (recommended — free, private)
1. In Telegram, search **@BotFather** → press **Start** → send `/newbot`.
2. Choose a name (e.g. "Summer helper") and a username ending in `bot`. BotFather replies with a **token**
   (`123456789:ABC…`). That token is the bot's password — keep it private.
3. Open a chat with your new bot and send it any message (e.g. "hi").
4. Find your **chat id**: open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser (replace `<TOKEN>`),
   and look for `"chat":{"id":123456789,…`. That number is the chat id.
5. `.env`: `NOTIFY_PROVIDER=telegram`, `TELEGRAM_BOT_TOKEN=…`, `TELEGRAM_CHAT_ID=…`.
6. In the dashboard open **Accounts → Send test notification**. It should arrive on your phone within seconds.

### 3b. ntfy (works today, even before Telegram)
1. Install the **ntfy** app (iOS/Android). Subscribe to a topic that is a long random string
   (the topic is effectively the password), e.g. `summer-7f3k9q2x8w4m1z6b`.
2. `.env`: `NOTIFY_PROVIDER=ntfy`, `NTFY_TOPIC=<that string>`. Same test button.

### What you will get
`post due` (at each slot, with the full caption to copy) · `video is in your TikTok drafts` · `post failed` ·
`account needs reconnecting` · `new comments` (hourly) · `planner has empty slots` · `server disk almost full`.

## 4. Instagram

Instagram must be a **professional** account. Yours is a **Creator** account (confirmed from your screenshot).
Meta's account object reports `Business` or `Media_Creator`, so Creator accounts are expected to work with the
API we use; whether publishing works for Creator accounts **(unconfirmed)** is proven on the first real post.

Official pages: <https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/get-started> ·
<https://developers.facebook.com/docs/instagram-platform/content-publishing>

### 4a. You create the Meta app (best: you own it)
1. Go to <https://developers.facebook.com> and sign in with a Facebook account → **Get started / Create account**
   (identity verification may be asked).
2. **My Apps → Create app.** Choose the use case for **Instagram / "Manage messaging & content on Instagram"**
   and the **Business** app type (Meta's page says to select *Business* when creating the app).
3. In the app dashboard's left menu look for **Instagram → API setup with Instagram business login**.
4. **Add your Instagram account** to the app (Meta shows an *Add account* button there, or add it under
   *App roles → Roles* as an **Instagram Tester** and accept the invite in the Instagram app).
   Because it is your own account and the app stays in *development mode*, **App Review is not needed**
   (Meta's docs: access to accounts you own and have added to the app).
5. Set the **redirect URL** (the "OAuth redirect URIs" box) to exactly:
   `https://studio.<yourdomain>/oauth/instagram/callback`
   Meta also asks for a **Privacy Policy URL**, a **Terms of Service URL** and **user data deletion** instructions before an app can leave
   development mode (field names may differ): use the pages on your main domain, `https://<your-domain>/privacy`, `https://<your-domain>/terms` and
   `https://<your-domain>/privacy#data-deletion` (for example `https://itsmesummer.app/privacy`). The developer sets these pages up (`docs/DEPLOY.md`, section 12); you only check the text.
6. **Unified Login (new Meta apps):** in the app dashboard create a **Facebook Login for Business configuration** with the permissions `instagram_basic`, `instagram_content_publish`, `instagram_manage_comments`, and add the redirect URL from step 5. Copy the **Configuration ID**. Also copy the **Meta app ID and app secret** (App settings → Basic; *not* an "Instagram app ID"). The Instagram account must belong to a business portfolio and have a role on the app.
   `.env`: `INSTAGRAM_APP_ID=<Meta app id>`, `INSTAGRAM_APP_SECRET=<Meta app secret>`, `INSTAGRAM_CONFIG_ID=<configuration id>`.
7. In our dashboard: **Accounts → Connect Instagram** → Instagram's own page asks you to log in and press **Allow**.
   The row turns **connected** and shows `@summerh_03`.

### 4b. If Meta will not let you create the developer account (your "ID verification" problem)
Send the **exact error message / screenshot**. Often a verification step fixes it. If it cannot be fixed quickly:
the **developer creates the Meta app under their own developer account** (in development mode) and invites
`@summerh_03` as an **Instagram Tester**; you accept the invite in the Instagram app and then press **Allow** in
step 7. Trade-off, plainly: the *app* then belongs to the developer, while the *Instagram account and its content*
stay yours. You can revoke access at any time in Instagram (*Settings → Apps and websites*) and we can move the
app to your own developer account later.

### What the app can and cannot do on Instagram
Posts Reels, photo posts (**JPEG only** — PNG/WebP are refused with a clear message) and Stories (Stories have
no caption); reads like/comment counts and comments; sends a reply **only when you press Send**. No DMs,
no automatic replies. **Instagram has no API switch for the "AI label" — add it in the Instagram app after posting.**
Instagram allows 100 API posts per 24 h; your pattern (10 per week) is far below that.

## 5. TikTok

Use a TikTok **creator** account (you are switching it). Official pages:
<https://developers.tiktok.com/doc/getting-started-create-an-app> (could not be opened when we checked —
the steps below come from the app's own requirements and are **(unconfirmed)**) ·
<https://developers.tiktok.com/doc/content-posting-api-get-started>

1. Go to <https://developers.tiktok.com>, log in with the TikTok account, register as a developer.
2. **Manage apps → Connect an app.** Name it `Summer Content`.
3. Add the products **Login Kit** and **Content Posting API**. Scopes: `user.info.basic` and `video.upload`.
4. **Redirect URI** (Login Kit): `https://studio.<yourdomain>/oauth/tiktok/callback`.
   TikTok's app form also asks for a **Privacy Policy** and **Terms of Service** URL (**unconfirmed** which fields are mandatory): the same two apex-domain pages.
5. Copy the **Client key** and **Client secret**. `.env`: `TIKTOK_CLIENT_KEY=…`, `TIKTOK_CLIENT_SECRET=…`.
6. In the dashboard: **Accounts → Connect TikTok** → press **Allow** on TikTok's page.

**How TikTok posting works here ("inbox mode"):** the app uploads the finished **video** to your **TikTok drafts**.
You get a push, open TikTok, **add the AI label**, press **Publish**, then press **Mark as posted** in our
dashboard. We cannot publish straight to the public feed: TikTok only allows that for apps that passed its own
audit, and until then it forces such posts to be private. Photo posts are not supported in this mode.
TikTok may need to **review the app** before it works outside your own test account **(unconfirmed)** — allow
a week or two and tell us the day you submit.

## 6. Dropbox → the server

The app cannot open a Dropbox *share link*; files must be on the server. We copy them with **rclone**
(read from Dropbox, never delete anything) every 15 minutes, then the app registers new files and tags them by
folder name (a folder called `Halloween` tags its files `halloween`).

1. The developer installs rclone on the server (`docs/DEPLOY.md`).
2. **Access:** easiest is rclone's built-in Dropbox app (choose it by leaving *client id/secret* empty in
   `rclone config`); you approve once in your browser and can revoke any time under
   <https://www.dropbox.com/account/connected_apps>. For tighter control create **your own Dropbox app**
   at <https://www.dropbox.com/developers/apps> (type *Scoped access → Full Dropbox*, or *App folder* if you
   put the Summer files in one folder), add `http://localhost:53682/` as a redirect URI and give rclone the key and secret.
   rclone's page lists file **write** scopes as the minimum for its own use; for download-only copying read scopes
   (`files.metadata.read`, `files.content.read`) should be enough **(unconfirmed — we try the read-only set first
   and only widen it if rclone refuses)**.
3. A server has no browser, so rclone's authorisation is done on **your computer**:
   run `rclone authorize "dropbox"` there, press Allow, copy the long token it prints and give it to the developer privately.
4. Tell us the **folder name** and roughly **how many files and how many GB** — the server disk is sized from that.
   The app refuses to sync when less than 2 GB is free and sends a push.

## 7. Fanvue (Phase 3)

Checked against Fanvue's developer docs on 2026-11-06 (`https://api.fanvue.com/docs`). Fanvue has **no test mode**:
everything the app does happens on Summer's real account, so the first real post and message are done together, with your OK.

**What the app does on Fanvue:** posts approved content (subscribers only by default, optional unlock price), lists chats
where a fan is waiting, drafts a reply with AI that **you** edit and send, lets you attach paid media to a reply (you set the
price), makes tracking links for Summer's Instagram/TikTok bios, and shows earnings and new subscribers.
**What it never does:** send a message by itself, set or change prices/promotions/subscription price, or touch payments
or payouts (those stay entirely inside Fanvue). Messages that need a human (are you real, age, meeting, refunds, safety)
get **no** AI draft; you get a push instead.

**Steps (signed in to Fanvue as Summer's creator account):**
1. Open `https://www.fanvue.com/dev` → **Create developer profile** (first visit only) → **Create app**, name it
   `Summer Content`, press **Create**.
2. App type: **API only** ("Basic OAuth for using our API"). This type needs no App Store listing and no review.
3. Copy the **Client ID** and the **Client secret**. The secret is shown **once**: if you lose it, press
   *Reset secret* on the Authentication tab and send the new one.
4. **Authentication** tab: add the redirect URI exactly `https://studio.itsmesummer.app/oauth/fanvue/callback`, and tick
   these scopes: `read:self`, `read:creator`, `read:post`, `write:post`, `read:media`, `write:media`, `read:chat`,
   `write:chat`, `read:fan`, `read:insights`, `read:tracking_links`, `write:tracking_links`. Do **not** tick
   `write:creator` (that would allow price changes; the app refuses to start with it).
5. Send the Client ID + secret securely (not by plain e-mail). The developer puts them in `.env` as
   `FANVUE_CLIENT_ID` / `FANVUE_CLIENT_SECRET`.
6. On the dashboard: **Accounts → Connect Fanvue**, sign in, press **Allow**. The card then shows `@its_me_summer`.
7. **Fanvue → Bio links:** press *Create bio links*, copy each link into Summer's Instagram and TikTok bio yourself.
   Check each platform's rules on links to subscription sites first; the app never edits those profiles.

**Answers we still need from you (defaults are already set and can be changed later):**
- Fanvue's rules: if you know of any rule about AI creators or automated chat, send it. Default: you send every reply.
- Things Summer must **never say** in chats, topics that must always come to you, and ~5 example fan messages with how
  she would answer. Default: the safety list above. You can type the lists yourself on **Profiles → Summer**, in the
  three "Fanvue chat" boxes (never say, topics that need you, notes for the AI), then press *Save Profile*.
- Price range for paid media in chats. Default: 3.00 – 50.00 USD.
- Which media is **Fanvue only** (never on Instagram/TikTok). Tick *Fanvue only* on each item's page.
- Check that Summer's Fanvue **bio says clearly that she is AI-generated** (Fanvue's guidelines require a clear disclosure;
  a bio statement is one of the accepted ways).

## 8. Checklist to send back

- [ ] Server rented, developer added · domain A record `studio.…` created
- [ ] Anthropic key (+ credit, + spend limit)
- [ ] Telegram bot token + chat id (or ntfy topic)
- [ ] Meta app: App ID + secret, redirect URL added — or the exact error message
- [ ] TikTok app: client key + secret, redirect URI added · date submitted for review (if asked)
- [ ] Dropbox: folder name, file count, total GB, rclone token
- [ ] Fanvue app: Client ID + secret, redirect URI added, scopes ticked (section 7) · AI disclosure in the Fanvue bio
- [ ] Which time zone you mean by "GMT" (UK clock time, i.e. summer-time aware, or fixed GMT?) and whether the
      10 weekly posts are all videos (TikTok drafts accept videos only)
