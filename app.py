from flask import Flask, request, jsonify, render_template_string, redirect, url_for, send_from_directory, session
from werkzeug.utils import secure_filename
from PIL import Image, ImageOps
import sqlite3
import os
import uuid
import time
import io

# ==========================================================
# CONFIG — tweak these for your event
# ==========================================================
SITE_TITLE = "MAX VS 100"
EVENT_SUBTITLE = "BATAK REACTION CHALLENGE"
LEADERBOARD_LIMIT = 15          # how many rows to show on the big screen
POLL_SECONDS = 6                # how often the TV display refreshes
SORT_ASCENDING = True           # True = lowest score wins (reaction time in seconds). Set False if higher score wins.
SCORE_LABEL = "TIME (s)"        # label shown next to the score column
SCORE_UNIT = "s"                # appended after each score, e.g. "12.34s"
MAX_NAME_LEN = 24
MIN_SCORE = 0.0
MAX_SCORE = 999.0
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "heic", "heif"}
MAX_UPLOAD_MB = 15
SUBMIT_COOLDOWN_SECONDS = 8     # basic per-IP throttle to stop accidental double-taps

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")  # set this as an env var on your host!

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
DB_PATH = os.path.join(BASE_DIR, "leaderboard.db")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

_last_submit_by_ip = {}

# ==========================================================
# DB HELPERS
# ==========================================================
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS submissions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            score REAL NOT NULL,
            photo_filename TEXT,
            created_at REAL NOT NULL
        )
    """)
    conn.commit()
    conn.close()

init_db()

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def save_photo(file_storage):
    if not file_storage or file_storage.filename == "":
        return None
    if not allowed_file(file_storage.filename):
        raise ValueError("Unsupported photo format. Please upload a JPG, PNG, WEBP, or HEIC image.")

    ext = file_storage.filename.rsplit(".", 1)[1].lower()
    fname = f"{uuid.uuid4().hex}.jpg"  # normalize everything to jpg after processing
    dest = os.path.join(UPLOAD_FOLDER, fname)

    try:
        img = Image.open(file_storage.stream)
        img = ImageOps.exif_transpose(img)  # fix phone-camera rotation
        img = img.convert("RGB")
        img.thumbnail((1200, 1200))
        img.save(dest, "JPEG", quality=85, optimize=True)
    except Exception:
        raise ValueError("Could not read that photo — please try a different image.")

    return fname

def fetch_leaderboard(limit=None):
    order = "ASC" if SORT_ASCENDING else "DESC"
    conn = get_db()
    q = f"SELECT id, name, score, photo_filename, created_at FROM submissions ORDER BY score {order}, created_at ASC"
    if limit:
        q += f" LIMIT {int(limit)}"
    rows = conn.execute(q).fetchall()
    conn.close()
    return rows

# ==========================================================
# TEMPLATES
# ==========================================================
LEADERBOARD_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>{{ site_title }} — Leaderboard</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: #050a1a;
      font-family: 'Arial Black', 'Segoe UI', system-ui, sans-serif;
      overflow: hidden;
    }
    .canvas {
      position: relative;
      width: 100vw;
      height: 100vh;
      background:
        radial-gradient(circle at 15% 10%, rgba(220,7,20,0.35), transparent 40%),
        radial-gradient(circle at 85% 90%, rgba(0,52,120,0.5), transparent 45%),
        linear-gradient(160deg, #040816 0%, #0b1636 55%, #150316 100%);
      color: white;
      overflow: hidden;
    }
    .checker-strip {
      position: absolute;
      top: 0; left: 0; right: 0;
      height: 14px;
      background-image: repeating-conic-gradient(#ffffff 0% 25%, #0b1636 0% 50%);
      background-size: 28px 28px;
      opacity: 0.9;
    }
    .header {
      text-align: center;
      padding-top: 4.5vh;
    }
    .header .brand {
      font-size: clamp(28px, 3.2vw, 48px);
      letter-spacing: 0.35em;
      color: #ff2436;
      text-shadow: 0 0 18px rgba(255,36,54,0.55);
    }
    .header .title {
      margin-top: 0.6vh;
      font-size: clamp(38px, 6vw, 88px);
      letter-spacing: 0.08em;
      text-transform: uppercase;
      text-shadow: 0 8px 24px rgba(0,0,0,0.6);
    }
    .subtitle {
      margin-top: 0.5vh;
      font-size: clamp(16px, 1.6vw, 26px);
      color: #9db3ff;
      letter-spacing: 0.15em;
      text-transform: uppercase;
    }
    .board {
      margin: 3vh auto 0 auto;
      width: min(80vw, 1400px);
      display: flex;
      flex-direction: column;
      gap: 1vh;
    }
    .row {
      display: flex;
      align-items: center;
      gap: 2vw;
      padding: 1.1vh 2vw;
      border-radius: 999px;
      background: rgba(255,255,255,0.06);
      border: 1px solid rgba(255,255,255,0.08);
      transition: transform 0.3s ease, background 0.3s ease;
    }
    .row.rank-1 { background: linear-gradient(90deg, rgba(255,215,80,0.25), rgba(255,255,255,0.05)); border-color: rgba(255,215,80,0.5); }
    .row.rank-2 { background: linear-gradient(90deg, rgba(210,210,220,0.2), rgba(255,255,255,0.05)); border-color: rgba(210,210,220,0.4); }
    .row.rank-3 { background: linear-gradient(90deg, rgba(205,140,80,0.22), rgba(255,255,255,0.05)); border-color: rgba(205,140,80,0.45); }
    .rank {
      width: 3.2vw;
      min-width: 44px;
      font-size: clamp(18px, 2.2vw, 32px);
      color: #ff2436;
      text-align: center;
    }
    .row.rank-1 .rank { color: #ffd750; }
    .row.rank-2 .rank { color: #d2d2dc; }
    .row.rank-3 .rank { color: #cd8c50; }
    .pname {
      flex: 1;
      font-size: clamp(18px, 2.3vw, 34px);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .pscore {
      font-variant-numeric: tabular-nums;
      font-size: clamp(18px, 2.3vw, 34px);
      color: #ffffff;
      min-width: 8vw;
      text-align: right;
    }
    .empty-state {
      text-align: center;
      margin-top: 6vh;
      font-size: clamp(18px, 2vw, 28px);
      color: #8fa0d0;
      letter-spacing: 0.1em;
    }
    .qr-hint {
      position: absolute;
      bottom: 3vh;
      right: 3vw;
      text-align: right;
      font-size: clamp(14px, 1.3vw, 20px);
      color: #9db3ff;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .qr-hint span { display: block; color: white; font-size: 1.3em; margin-top: 0.3vh; }
  </style>
</head>
<body>
  <div class="canvas">
    <div class="checker-strip"></div>
    <div class="header">
      <div class="brand">RED BULL</div>
      <div class="title">{{ site_title }}</div>
      <div class="subtitle">{{ event_subtitle }}</div>
    </div>
    <div class="board" id="board"></div>
    <div class="qr-hint">Scan to submit your score<span>{{ submit_url }}</span></div>
  </div>

  <script>
    const scoreUnit = {{ score_unit|tojson }};

    async function refresh() {
      try {
        const res = await fetch('/api/leaderboard');
        const data = await res.json();
        const board = document.getElementById('board');

        if (data.length === 0) {
          board.innerHTML = '<div class="empty-state">Be the first to set a time — scan the QR code to submit!</div>';
          return;
        }

        board.innerHTML = data.map((r, i) => `
          <div class="row rank-${i+1}">
            <div class="rank">${i+1}</div>
            <div class="pname">${r.name}</div>
            <div class="pscore">${r.score.toFixed(2)}${scoreUnit}</div>
          </div>
        `).join('');
      } catch (e) {
        console.error('leaderboard refresh failed', e);
      }
    }

    refresh();
    setInterval(refresh, {{ poll_seconds }} * 1000);
  </script>
</body>
</html>
"""

SUBMIT_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Submit your score — {{ site_title }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: linear-gradient(160deg, #040816 0%, #0b1636 60%, #150316 100%);
      font-family: -apple-system, 'Segoe UI', system-ui, sans-serif;
      color: white;
      display: flex;
      justify-content: center;
    }
    .wrap { width: 100%; max-width: 480px; padding: 32px 20px 60px 20px; }
    .brand { text-align: center; color: #ff2436; letter-spacing: 0.3em; font-weight: 700; font-size: 14px; }
    h1 { text-align: center; margin: 6px 0 2px 0; font-size: 28px; letter-spacing: 0.03em; }
    .sub { text-align: center; color: #9db3ff; margin-bottom: 28px; font-size: 14px; letter-spacing: 0.08em; text-transform: uppercase; }
    label { display: block; margin: 18px 0 6px 0; font-size: 14px; color: #cbd5f5; letter-spacing: 0.04em; }
    input[type=text], input[type=number] {
      width: 100%; padding: 14px 16px; border-radius: 12px; border: 1px solid rgba(255,255,255,0.15);
      background: rgba(255,255,255,0.06); color: white; font-size: 18px;
    }
    input[type=file] {
      width: 100%; padding: 14px 0; color: #cbd5f5; font-size: 15px;
    }
    .hint { font-size: 12px; color: #7f8fc2; margin-top: 6px; }
    button {
      width: 100%; margin-top: 28px; padding: 16px; border: none; border-radius: 999px;
      background: linear-gradient(90deg, #ff2436, #d40018);
      color: white; font-size: 18px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
    }
    .error { background: rgba(255,36,54,0.15); border: 1px solid rgba(255,36,54,0.4); padding: 12px 16px; border-radius: 10px; margin-top: 20px; font-size: 14px; }
    .success { background: rgba(60,200,120,0.15); border: 1px solid rgba(60,200,120,0.4); padding: 16px; border-radius: 10px; margin-top: 20px; font-size: 15px; text-align: center; }
    a.back { display: block; text-align: center; margin-top: 18px; color: #9db3ff; font-size: 14px; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="brand">RED BULL</div>
    <h1>{{ site_title }}</h1>
    <div class="sub">{{ event_subtitle }}</div>

    {% if success %}
      <div class="success">You're in! <strong>{{ success_name }}</strong> — {{ success_score }}{{ score_unit }}.<br>Check the leaderboard screen to see where you land.</div>
      <a class="back" href="{{ url_for('submit') }}">Submit another score</a>
    {% else %}
      {% if error %}<div class="error">{{ error }}</div>{% endif %}
      <form method="POST" enctype="multipart/form-data">
        <label>Your name</label>
        <input type="text" name="name" maxlength="{{ max_name_len }}" required placeholder="e.g. Alex" value="{{ old_name or '' }}">

        <label>Your {{ score_label }}</label>
        <input type="number" name="score" step="0.01" min="{{ min_score }}" max="{{ max_score }}" required placeholder="e.g. 12.34" value="{{ old_score or '' }}">
        <div class="hint">Enter it exactly as shown on the Batak screen.</div>

        <label>Photo of your score (recommended)</label>
        <input type="file" name="photo" accept="image/*" capture="environment">
        <div class="hint">Snap the Batak screen so we can verify it if needed.</div>

        <button type="submit">Submit score</button>
      </form>
    {% endif %}
  </div>
</body>
</html>
"""

ADMIN_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Admin — {{ site_title }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body { font-family: system-ui, sans-serif; background: #0b1020; color: white; margin: 0; padding: 24px; }
    h1 { font-size: 20px; }
    table { width: 100%; border-collapse: collapse; margin-top: 16px; }
    th, td { text-align: left; padding: 10px 8px; border-bottom: 1px solid rgba(255,255,255,0.1); font-size: 14px; vertical-align: middle; }
    img.thumb { height: 60px; border-radius: 6px; }
    form.delete { display: inline; }
    button.del { background: #d40018; border: none; color: white; padding: 6px 12px; border-radius: 6px; cursor: pointer; }
    a.photo-link { color: #9db3ff; }
  </style>
</head>
<body>
  <h1>{{ site_title }} — Admin ({{ rows|length }} entries)</h1>
  <table>
    <tr><th>Rank-order</th><th>Name</th><th>Score</th><th>Photo</th><th>Submitted</th><th></th></tr>
    {% for r in rows %}
    <tr>
      <td>{{ loop.index }}</td>
      <td>{{ r.name }}</td>
      <td>{{ '%.2f'|format(r.score) }}</td>
      <td>
        {% if r.photo_filename %}
          <a class="photo-link" href="{{ url_for('static', filename='uploads/' + r.photo_filename) }}" target="_blank">
            <img class="thumb" src="{{ url_for('static', filename='uploads/' + r.photo_filename) }}">
          </a>
        {% else %}—{% endif %}
      </td>
      <td>{{ r.created_at_str }}</td>
      <td>
        <form class="delete" method="POST" action="{{ url_for('admin_delete', submission_id=r.id) }}" onsubmit="return confirm('Delete this entry?');">
          <button class="del" type="submit">Delete</button>
        </form>
      </td>
    </tr>
    {% endfor %}
  </table>
</body>
</html>
"""

ADMIN_LOGIN_HTML = """
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Admin login</title>
<style>
body { font-family: system-ui, sans-serif; background: #0b1020; color: white; display:flex; justify-content:center; align-items:center; height:100vh; margin:0; }
form { background: rgba(255,255,255,0.06); padding: 32px; border-radius: 14px; width: 280px; }
input { width: 100%; padding: 12px; margin-top: 10px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.2); background: rgba(255,255,255,0.05); color:white; }
button { width: 100%; margin-top: 16px; padding: 12px; border: none; border-radius: 8px; background: #d40018; color: white; font-weight: 700; }
.error { color: #ff8a94; font-size: 13px; margin-top: 10px; }
</style>
</head>
<body>
<form method="POST">
  <h3>Admin login</h3>
  {% if error %}<div class="error">{{ error }}</div>{% endif %}
  <input type="password" name="password" placeholder="Password" required autofocus>
  <button type="submit">Enter</button>
</form>
</body>
</html>
"""

# ==========================================================
# ROUTES
# ==========================================================
@app.route("/")
def leaderboard():
    submit_url = request.url_root.rstrip("/") + url_for("submit")
    return render_template_string(
        LEADERBOARD_HTML,
        site_title=SITE_TITLE,
        event_subtitle=EVENT_SUBTITLE,
        poll_seconds=POLL_SECONDS,
        score_unit=SCORE_UNIT,
        submit_url=submit_url,
    )

@app.route("/api/leaderboard")
def api_leaderboard():
    rows = fetch_leaderboard(limit=LEADERBOARD_LIMIT)
    return jsonify([{"name": r["name"], "score": r["score"]} for r in rows])

@app.route("/submit", methods=["GET", "POST"])
def submit():
    if request.method == "GET":
        return render_template_string(
            SUBMIT_HTML,
            site_title=SITE_TITLE,
            event_subtitle=EVENT_SUBTITLE,
            score_label=SCORE_LABEL,
            score_unit=SCORE_UNIT,
            max_name_len=MAX_NAME_LEN,
            min_score=MIN_SCORE,
            max_score=MAX_SCORE,
            success=False,
        )

    # POST — handle submission
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
    now = time.time()
    last = _last_submit_by_ip.get(ip, 0)
    if now - last < SUBMIT_COOLDOWN_SECONDS:
        return render_template_string(
            SUBMIT_HTML, site_title=SITE_TITLE, event_subtitle=EVENT_SUBTITLE,
            score_label=SCORE_LABEL, score_unit=SCORE_UNIT, max_name_len=MAX_NAME_LEN,
            min_score=MIN_SCORE, max_score=MAX_SCORE, success=False,
            error="Whoa, one at a time! Please wait a few seconds and try again.",
        ), 429

    name = (request.form.get("name") or "").strip()
    score_raw = (request.form.get("score") or "").strip()

    error = None
    score_val = None

    if not name:
        error = "Please enter your name."
    elif len(name) > MAX_NAME_LEN:
        error = f"Name must be {MAX_NAME_LEN} characters or fewer."
    else:
        try:
            score_val = float(score_raw)
        except (TypeError, ValueError):
            error = "Please enter a valid score."

    if not error and score_val is not None:
        if score_val < MIN_SCORE or score_val > MAX_SCORE:
            error = f"Score must be between {MIN_SCORE} and {MAX_SCORE}."

    photo_filename = None
    if not error:
        try:
            photo_filename = save_photo(request.files.get("photo"))
        except ValueError as e:
            error = str(e)

    if error:
        return render_template_string(
            SUBMIT_HTML, site_title=SITE_TITLE, event_subtitle=EVENT_SUBTITLE,
            score_label=SCORE_LABEL, score_unit=SCORE_UNIT, max_name_len=MAX_NAME_LEN,
            min_score=MIN_SCORE, max_score=MAX_SCORE, success=False,
            error=error, old_name=name, old_score=score_raw,
        )

    conn = get_db()
    conn.execute(
        "INSERT INTO submissions (id, name, score, photo_filename, created_at) VALUES (?, ?, ?, ?, ?)",
        (uuid.uuid4().hex, name, score_val, photo_filename, now),
    )
    conn.commit()
    conn.close()

    _last_submit_by_ip[ip] = now

    return render_template_string(
        SUBMIT_HTML, site_title=SITE_TITLE, event_subtitle=EVENT_SUBTITLE,
        score_label=SCORE_LABEL, score_unit=SCORE_UNIT, max_name_len=MAX_NAME_LEN,
        min_score=MIN_SCORE, max_score=MAX_SCORE,
        success=True, success_name=name, success_score=f"{score_val:.2f}",
    )

# ---------- Admin (basic password-protected cleanup panel) ----------
@app.route("/admin", methods=["GET", "POST"])
def admin():
    if not session.get("is_admin"):
        error = None
        if request.method == "POST":
            if request.form.get("password") == ADMIN_PASSWORD:
                session["is_admin"] = True
                return redirect(url_for("admin"))
            error = "Incorrect password."
        return render_template_string(ADMIN_LOGIN_HTML, error=error)

    rows = fetch_leaderboard(limit=None)
    enriched = []
    for r in rows:
        d = dict(r)
        d["created_at_str"] = time.strftime("%H:%M:%S", time.localtime(r["created_at"]))
        enriched.append(d)

    return render_template_string(ADMIN_HTML, site_title=SITE_TITLE, rows=enriched)

@app.route("/admin/delete/<submission_id>", methods=["POST"])
def admin_delete(submission_id):
    if not session.get("is_admin"):
        return redirect(url_for("admin"))
    conn = get_db()
    row = conn.execute("SELECT photo_filename FROM submissions WHERE id=?", (submission_id,)).fetchone()
    conn.execute("DELETE FROM submissions WHERE id=?", (submission_id,))
    conn.commit()
    conn.close()
    if row and row["photo_filename"]:
        try:
            os.remove(os.path.join(UPLOAD_FOLDER, row["photo_filename"]))
        except OSError:
            pass
    return redirect(url_for("admin"))

@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("admin"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=True)
