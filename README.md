# MAX VS 100 — Batak Reaction Challenge Leaderboard

A hosted leaderboard for a Batak reaction-time activation:

- **`/`** — the big-screen leaderboard (16:9, auto-updates every few seconds, no page flicker)
- **`/submit`** — the mobile-friendly form people scan a QR code to reach: name, score, and a photo of their Batak result
- **`/admin`** — password-protected page to delete a bad/troll entry if one slips through (auto-publish means there's no approval step, so this is your safety net)

Everything is one Flask app + a local SQLite database (`leaderboard.db`) that's created automatically on first run. Photos are compressed and saved into `static/uploads/`.

---

## 1. Run it locally first

```bash
pip install -r requirements.txt
python app.py
```

Open `http://localhost:8000` for the leaderboard and `http://localhost:8000/submit` on your phone (same wifi) to test a submission.

---

## 2. Configure it for your event

Open `app.py` — everything you'd want to change lives in the **CONFIG** block at the top:

| Setting | What it does |
|---|---|
| `SITE_TITLE` / `EVENT_SUBTITLE` | Big text on the leaderboard screen |
| `SORT_ASCENDING` | `True` if lower score wins (reaction time). Set `False` if Batak scores points where higher is better |
| `SCORE_LABEL` / `SCORE_UNIT` | Label on the submit form and unit shown next to scores (e.g. `"s"`) |
| `LEADERBOARD_LIMIT` | How many rows show on the big screen |
| `MIN_SCORE` / `MAX_SCORE` | Sanity-check range so people can't submit obviously fake numbers |
| `ADMIN_PASSWORD` | **Set this via an environment variable on your host** — don't leave the default |

### Branding

The current styling uses CSS gradients and a checkered-flag strip in Red Bull's red/navy palette rather than real logo files, since I don't have your official MAX VS 100 background/logo assets. To drop in the real branding:

1. Add your files to the `static/` folder (e.g. `static/logo.png`, `static/background.jpg`).
2. In `LEADERBOARD_HTML`, add an `<img>` tag where you want the logo, or swap the `.canvas` background gradient for `background-image: url("/static/background.jpg")`.

Happy to do this myself if you send over the actual logo/background files from the event branding pack.

---

## 3. Deploy it with a public URL

**Render.com** (free tier, easiest):

1. Push this folder to a GitHub repo.
2. In Render, "New +" → "Web Service" → connect the repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn -w 1 --threads 4 app:app` (already in the `Procfile`, Render should detect it automatically)
5. Add an environment variable `ADMIN_PASSWORD` set to something only your team knows.
6. Deploy — you'll get a `https://your-app.onrender.com` URL. Point your QR code at `https://your-app.onrender.com/submit` and the TV display at the root URL.

Railway, Fly.io, or PythonAnywhere work the same way — any host that runs a Python web app from a `Procfile`/`gunicorn` command will do.

### Important: keep it to a single worker

The app uses SQLite plus a small in-memory cooldown to stop accidental double-taps. Both assume **one process**. Don't scale this to multiple workers/instances — `-w 1` in the `Procfile` is intentional. For a single-day activation with normal foot traffic, one worker handles this fine.

### A note on data persistence

Most free hosting tiers use an ephemeral filesystem — the database and uploaded photos live only as long as the app instance is running, and can be wiped on a redeploy or restart. That's fine for a one-day event; just avoid redeploying mid-event, and export/back up `leaderboard.db` and the `static/uploads/` folder afterward if you want to keep the results. If you need it to survive restarts, Render's paid tier offers a persistent disk you can mount at `static/uploads` and point `DB_PATH` to.

---

## 4. On the day

- Print/display a QR code pointing to `https://your-app.onrender.com/submit` near the Batak machine.
- Leave the leaderboard URL open on the venue TV/screen — it polls automatically, no refresh needed.
- Keep `/admin` handy on a phone or laptop in case you need to remove a joke entry.
