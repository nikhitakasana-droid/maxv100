from flask import Flask, request, jsonify, render_template_string, redirect, url_for, send_from_directory, session, Response
from werkzeug.utils import secure_filename
from PIL import Image, ImageOps
import sqlite3
import os
import uuid
import time
import io
import csv
import base64
import qrcode
from qrcode.constants import ERROR_CORRECT_H

# ==========================================================
# CONFIG — tweak these for your event
# ==========================================================
SITE_TITLE = "BATAK CHALLENGE"
EVENT_SUBTITLE = "WHERE DO YOU RANK AMONG THE 100?"
LEADERBOARD_LIMIT = 15          # how many rows to show on the big screen
POLL_SECONDS = 6                # how often the TV display refreshes
SORT_ASCENDING = False          # True = lowest score wins (e.g. reaction time). Set False if higher score wins.
SCORE_LABEL = "HITS"            # label shown next to the score column
SCORE_UNIT = ""                 # appended after each score (blank for a plain hit count)
MAX_NAME_LEN = 24
MIN_SCORE = 0
MAX_SCORE = 150
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

def make_qr_data_uri(url):
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_H, box_size=10, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"

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
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Jost:wght@600&display=swap" rel="stylesheet">
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: #050a1a;
      font-family: 'Jost', system-ui, sans-serif; font-weight: 600;
      overflow: hidden;
    }
    .canvas {
      position: relative;
      width: 100vw;
      height: 100vh;
      background-image: url("{{ url_for('static', filename='bg_full.png') }}");
      background-size: cover;
      background-position: center;
      background-repeat: no-repeat;
      background-color: #050a1a;
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
      z-index: 6;
    }
    @keyframes textShimmer {
      0%   { background-position: -120% 0; }
      100% { background-position: 220% 0; }
    }
    .header {
      position: relative;
      z-index: 6;
      width: min(90vw, 1800px);
      margin: 0 auto;
      text-align: center;
      padding-top: 2.5vh;
    }
    .header .title {
      font-family: 'Jost', sans-serif; font-weight: 600;
      margin-top: 0.2vh;
      font-size: clamp(38px, 6vw, 88px);
      letter-spacing: 0.08em;
      text-transform: uppercase;
      text-shadow: 0 0 24px rgba(255,36,54,0.5), 0 8px 24px rgba(0,0,0,0.6);

      background: linear-gradient(100deg, #ff2436 35%, #ffc2c8 50%, #ff2436 65%);
      background-size: 300% 100%;
      -webkit-background-clip: text;
      background-clip: text;
      -webkit-text-fill-color: transparent;
      color: transparent;
      animation: textShimmer 8s linear infinite;
    }
    .subtitle {
      font-family: 'Jost', sans-serif; font-weight: 600;
      margin-top: 0.2vh;
      font-size: clamp(16px, 1.6vw, 26px);
      letter-spacing: 0.15em;
      text-transform: uppercase;

      background: linear-gradient(100deg, #9db3ff 35%, #e8edff 50%, #9db3ff 65%);
      background-size: 300% 100%;
      -webkit-background-clip: text;
      background-clip: text;
      -webkit-text-fill-color: transparent;
      color: transparent;
      animation: textShimmer 8s linear infinite;
    }
    .columns {
      position: relative;
      z-index: 6;
      display: flex;
      justify-content: space-between;
      margin: 2vh 4vw 0 4vw;
    }
    .col-left {
      width: 36vw;
      display: flex;
      flex-direction: column;
      gap: 1.6vh;
    }
    #board-top3 {
      display: flex;
      flex-direction: column;
      gap: 1.6vh;
    }
    .col-right {
      width: 34vw;
      display: flex;
      flex-direction: column;
      gap: 0.3vh;
      padding-bottom: 6vh;
      box-sizing: border-box;
    }
    @keyframes rowEmphasize {
      0%   { transform: scale(1); filter: brightness(1); box-shadow: none; }
      4%   { transform: scale(1.035); filter: brightness(1.3); box-shadow: 0 0 34px rgba(255,255,255,0.4); }
      11%  { transform: scale(1); filter: brightness(1); box-shadow: none; }
      100% { transform: scale(1); filter: brightness(1); box-shadow: none; }
    }
    .row {
      display: flex;
      align-items: center;
      gap: 1.6vw;
      padding: 0.4vh 1.6vw;
      border-radius: 999px;
      background: rgba(4, 8, 20, 0.72);
      border: 1px solid rgba(255,255,255,0.1);
      backdrop-filter: blur(2px);
      transition: transform 0.3s ease, background 0.3s ease;
      transform-origin: center;
      animation: rowEmphasize 7s ease-in-out infinite;
    }
    .row.row-empty { animation: none; opacity: 0.45; }
    .row.rank-1, .row.rank-2, .row.rank-3 {
      padding: 1.8vh 2vw;
    }
    .row.rank-1 { background: linear-gradient(90deg, rgba(255,215,80,0.32), rgba(4,8,20,0.72)); border-color: rgba(255,215,80,0.55); }
    .row.rank-2 { background: linear-gradient(90deg, rgba(210,210,220,0.28), rgba(4,8,20,0.72)); border-color: rgba(210,210,220,0.45); }
    .row.rank-3 { background: linear-gradient(90deg, rgba(205,140,80,0.3), rgba(4,8,20,0.72)); border-color: rgba(205,140,80,0.5); }
    .rank {
      width: 2.8vw;
      min-width: 32px;
      font-family: 'Jost', sans-serif; font-weight: 600;
      font-size: clamp(13px, 1.3vw, 20px);
      color: #ff2436;
      text-align: center;
    }
    .row.rank-1 .rank, .row.rank-2 .rank, .row.rank-3 .rank {
      font-size: clamp(26px, 3.4vw, 52px);
    }
    .row.rank-1 .rank { color: #ffd750; }
    .row.rank-2 .rank { color: #d2d2dc; }
    .row.rank-3 .rank { color: #cd8c50; }
    .pname {
      flex: 1;
      font-weight: 600;
      font-size: clamp(13px, 1.3vw, 20px);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .pscore {
      font-variant-numeric: tabular-nums;
      font-weight: 600;
      font-size: clamp(13px, 1.3vw, 20px);
      color: #ffffff;
      min-width: 4vw;
      text-align: right;
    }
    .row.rank-1 .pname, .row.rank-2 .pname, .row.rank-3 .pname,
    .row.rank-1 .pscore, .row.rank-2 .pscore, .row.rank-3 .pscore {
      font-size: clamp(26px, 3.4vw, 52px);
    }
    .empty-state {
      text-align: center;
      margin-top: 6vh;
      font-size: clamp(18px, 2vw, 28px);
      color: #8fa0d0;
      letter-spacing: 0.1em;
    }
    .qr-hint {
      z-index: 6;
      display: flex;
      flex-direction: row;
      align-items: center;
      text-align: left;
      gap: 1.2vw;
      margin-top: 3vh;
    }
    .qr-hint .qr-label {
      font-size: clamp(13px, 1.1vw, 18px);
      color: white;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      line-height: 1.3;
      max-width: 12vw;
    }
    .qr-hint img {
      width: clamp(80px, 9vw, 130px);
      height: clamp(80px, 9vw, 130px);
      background: white;
      padding: 0.6vw;
      border-radius: 12px;
      box-shadow: 0 8px 24px rgba(0,0,0,0.5);
    }
  </style>
</head>
<body>
  <div class="canvas">
    <div class="checker-strip"></div>
    <div class="header">
      <div class="title">{{ site_title }}</div>
      <div class="subtitle">{{ event_subtitle }}</div>
    </div>
    <div class="columns">
      <div class="col-left">
        <div id="board-top3"></div>
        <div class="qr-hint">
          <img src="{{ qr_data_uri }}" alt="QR code to submit your score">
          <div class="qr-label">Scan to submit your score</div>
        </div>
      </div>
      <div class="col-right" id="board-rest"></div>
    </div>
  </div>

  <script>
    const scoreUnit = {{ score_unit|tojson }};
    const totalSlots = {{ total_slots }};

    async function refresh() {
      try {
        const res = await fetch('/api/leaderboard');
        const data = await res.json();
        const top3Box = document.getElementById('board-top3');
        const restBox = document.getElementById('board-rest');

        const slots = Array.from({ length: totalSlots }, (_, i) => data[i] || null);

        function renderRow(r, i) {
          if (!r) {
            return `
              <div class="row row-empty rank-${i+1}">
                <div class="rank">${i+1}</div>
                <div class="pname">—</div>
                <div class="pscore">—</div>
              </div>
            `;
          }
          return `
            <div class="row rank-${i+1}" style="animation-delay: ${(i * 0.28).toFixed(2)}s">
              <div class="rank">${i+1}</div>
              <div class="pname">${r.name}</div>
              <div class="pscore">${r.score}${scoreUnit}</div>
            </div>
          `;
        }

        top3Box.innerHTML = slots.slice(0, 3).map(renderRow).join('');
        restBox.innerHTML = slots.slice(3).map((r, i) => renderRow(r, i + 3)).join('');
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
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Jost:wght@600&display=swap" rel="stylesheet">
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: linear-gradient(160deg, #040816 0%, #0b1636 60%, #150316 100%);
      font-family: 'Jost', system-ui, sans-serif; font-weight: 600;
      color: white;
      display: flex;
      justify-content: center;
    }
    .wrap { width: 100%; max-width: 480px; padding: 32px 20px 60px 20px; }
    .brand { font-family: 'Jost', sans-serif; text-align: center; color: #ff2436; letter-spacing: 0.3em; font-weight: 600; font-size: 14px; }
    h1 { font-family: 'Jost', sans-serif; font-weight: 600; text-align: center; margin: 6px 0 2px 0; font-size: 32px; letter-spacing: 0.03em; }
    .sub { text-align: center; color: #9db3ff; margin-bottom: 28px; font-size: 14px; letter-spacing: 0.08em; text-transform: uppercase; font-weight: 600; }
    label { display: block; margin: 18px 0 6px 0; font-size: 14px; color: #cbd5f5; letter-spacing: 0.04em; font-weight: 600; }
    input[type=text], input[type=number] {
      width: 100%; padding: 14px 16px; border-radius: 12px; border: 1px solid rgba(255,255,255,0.15);
      background: rgba(255,255,255,0.06); color: white; font-size: 18px; font-family: 'Jost', system-ui, sans-serif; font-weight: 600;
    }
    input[type=file] {
      display: none;
    }
    .photo-btn {
      display: block;
      width: 100%;
      padding: 14px 16px;
      border-radius: 12px;
      border: 1px solid rgba(255,255,255,0.15);
      background: rgba(255,255,255,0.06);
      color: white;
      font-family: 'Jost', sans-serif;
      font-size: 16px;
      font-weight: 600;
      text-align: center;
      letter-spacing: 0.04em;
      cursor: pointer;
    }
    #photo-filename { color: #cbd5f5; }
    .hint { font-size: 12px; color: #7f8fc2; margin-top: 6px; }
    button {
      width: 100%; margin-top: 28px; padding: 16px; border: none; border-radius: 999px;
      background: linear-gradient(90deg, #ff2436, #d40018);
      color: white; font-family: 'Jost', sans-serif; font-size: 18px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase;
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
        <input type="number" name="score" step="1" min="{{ min_score }}" max="{{ max_score }}" required placeholder="e.g. 42" value="{{ old_score or '' }}">
        <div class="hint">Enter the number of hits shown on the Batak screen.</div>

        <label>Photo of your score</label>
        <label for="photo-input" class="photo-btn">📷 Take Photo</label>
        <input type="file" id="photo-input" name="photo" accept="image/*" capture="environment" onchange="document.getElementById('photo-filename').textContent = this.files.length ? this.files[0].name : '';">
        <div class="hint" id="photo-filename"></div>
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
    a.export-link {
      display: inline-block;
      margin-top: 12px;
      padding: 10px 18px;
      background: #1a2440;
      color: white;
      text-decoration: none;
      border-radius: 8px;
      font-size: 14px;
      border: 1px solid rgba(255,255,255,0.15);
    }
  </style>
</head>
<body>
  <h1>{{ site_title }} — Admin ({{ rows|length }} entries)</h1>
  <a class="export-link" href="{{ url_for('admin_export') }}">⬇ Export as CSV</a>
  <table>
    <tr><th>Rank-order</th><th>Name</th><th>Score</th><th>Photo</th><th>Submitted</th><th></th></tr>
    {% for r in rows %}
    <tr>
      <td>{{ loop.index }}</td>
      <td>{{ r.name }}</td>
      <td>{{ r.score | int }}</td>
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
button { width: 100%; margin-top: 16px; padding: 12px; border: none; border-radius: 8px; background: #d40018; color: white; font-family: 'Jost', sans-serif; font-weight: 600; }
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
        qr_data_uri=make_qr_data_uri(submit_url),
        total_slots=LEADERBOARD_LIMIT,
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
            score_val = int(score_raw)
        except (TypeError, ValueError):
            error = "Please enter a whole number of hits."

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
        success=True, success_name=name, success_score=str(score_val),
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

@app.route("/admin/export.csv")
def admin_export():
    if not session.get("is_admin"):
        return redirect(url_for("admin"))

    rows = fetch_leaderboard(limit=None)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["rank", "name", "score", "photo_filename", "submitted_at"])
    for i, r in enumerate(rows):
        submitted_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["created_at"]))
        writer.writerow([i + 1, r["name"], r["score"], r["photo_filename"] or "", submitted_at])

    csv_data = buf.getvalue()
    filename = f"{SITE_TITLE.lower().replace(' ', '-')}-leaderboard.csv"
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

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
