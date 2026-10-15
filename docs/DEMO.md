# Halloween Demo Script

End-to-end walkthrough of Phase 1 using the Halloween campaign. Run on a fresh DB.

## 1. Start
```
python -m app.cli init-db
uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Open http://127.0.0.1:8000.

## 2. Add media
1. **Media library** → **New asset** → upload 2–3 Halloween images/videos
   (or use **Sync Dropbox folder** after setting `MEDIA_SYNC_DIR`).
2. Tag each asset `halloween` and write a factual description of what is
   actually visible — Claude only sees this description, never the media.

## 3. Generate a draft
1. Filter the library by tag `Halloween` → click **Generate** on an image.
2. Choose: Instagram · `feed_post` · campaign `Halloween` ·
   seasonal context "Halloween week" → **Generate**.
3. You land on the post detail page with a **draft**.
   (`If ANTHROPIC_API_KEY is missing you get a red error message on the form.`)

## 4. Iterate on the copy
1. Click **Generate alternative** (tone variation: "more teasing") → a new
   variant appears; the original caption is unchanged.
2. Edit the caption → **Save** (draft stays draft).
3. If you prefer a variant, click **Use this version** on it.

## 5. Approve / schedule
1. Set a planned publish date (`datetime-local`, interpreted in `APP_TIMEzone`)
   → **Approve** → status becomes **Scheduled**.
2. The status banner reminds you:
   *"Approved/scheduled posts are NOT published automatically in Phase 1. Post manually."*

## 6. Second platform
Repeat steps 3–5 with a video asset, TikTok · `video`.

## 7. Queue & overview
1. **Queue** → filter by campaign `Halloween` → both posts appear with their
   status badges, thumbnail and planned date.
2. **Overview** → card counts and the platform × status table match the queue.

## 8. Archive
Archive one draft → it disappears from the default queue and is visible again
by filtering status `archived`; **Restore** returns it to Draft.
