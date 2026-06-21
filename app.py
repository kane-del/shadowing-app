import base64
import json
import os
import re
import sqlite3
from datetime import datetime
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request

load_dotenv()

app = Flask(__name__)

# On Railway/Render, mount a volume at /data and set DB_PATH=/data/shadowing.db
_data_dir = "/data" if os.path.isdir("/data") else os.path.dirname(__file__)
DB_PATH = os.environ.get("DB_PATH", os.path.join(_data_dir, "shadowing.db"))

SEARCH_DEFAULTS = [
    "English speaking practice intermediate",
    "TED talk English learning",
    "business English conversation",
    "English pronunciation shadowing",
    "everyday English fluency",
]


# ── Auth ──────────────────────────────────────────────────────────────────────

def require_auth(f):
    """HTTP Basic Auth guard for admin/mutating routes.
    Disabled automatically when ADMIN_PASS is not set (local dev).
    Set ADMIN_PASS in Railway environment variables before deploying."""
    @wraps(f)
    def decorated(*args, **kwargs):
        admin_pass = os.environ.get("ADMIN_PASS", "")
        if not admin_pass:
            return f(*args, **kwargs)  # auth off in local dev

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                _, pw = decoded.split(":", 1)
                if pw == admin_pass:
                    return f(*args, **kwargs)
            except Exception:
                pass

        return Response(
            "Authentication required",
            401,
            {"WWW-Authenticate": 'Basic realm="ShadowClip Admin"'},
        )
    return decorated


# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS categories (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT    NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS expressions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                expression    TEXT    NOT NULL,
                example       TEXT    DEFAULT '',
                memo          TEXT    DEFAULT '',
                category_id   INTEGER REFERENCES categories(id) ON DELETE SET NULL,
                japanese_hint TEXT    DEFAULT '',
                created_at    TEXT    DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS clips (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                youtube_id  TEXT    NOT NULL,
                title       TEXT    NOT NULL,
                channel     TEXT    DEFAULT '',
                thumbnail   TEXT    DEFAULT '',
                start_sec   REAL    DEFAULT 0,
                end_sec     REAL    DEFAULT 90,
                difficulty  TEXT    DEFAULT 'C1',
                topics      TEXT    DEFAULT '[]',
                transcript  TEXT    DEFAULT '[]',
                status      TEXT    DEFAULT 'pending',
                ai_note     TEXT    DEFAULT '',
                created_at  TEXT    DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS practice_sessions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                clip_id      INTEGER NOT NULL,
                practiced_at TEXT    DEFAULT CURRENT_TIMESTAMP,
                repetitions  INTEGER DEFAULT 1,
                FOREIGN KEY (clip_id) REFERENCES clips(id)
            );
            CREATE TABLE IF NOT EXISTS phrases (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                clip_id    INTEGER,
                text       TEXT NOT NULL,
                note       TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (clip_id) REFERENCES clips(id)
            );
        """)
        # Migrate existing phrases → expressions (after all tables exist)
        conn.execute("""
            INSERT INTO expressions (expression, memo, created_at)
            SELECT p.text, p.note, p.created_at
            FROM phrases p
            WHERE NOT EXISTS (
                SELECT 1 FROM expressions e WHERE e.expression = p.text AND e.created_at = p.created_at
            )
        """)


# ── Helpers ───────────────────────────────────────────────────────────────────

def extract_youtube_id(url_or_id: str) -> str | None:
    for pattern in [
        r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([A-Za-z0-9_-]{11})",
        r"^([A-Za-z0-9_-]{11})$",
    ]:
        m = re.search(pattern, url_or_id.strip())
        if m:
            return m.group(1)
    return None


def fetch_transcript(youtube_id: str) -> list:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        return YouTubeTranscriptApi.get_transcript(
            youtube_id, languages=["en", "en-US", "en-GB", "en-CA"]
        )
    except Exception:
        return []


def parse_iso_duration(duration_str: str) -> int:
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str)
    if not m:
        return 0
    h = int(m.group(1) or 0)
    mi = int(m.group(2) or 0)
    s = int(m.group(3) or 0)
    return h * 3600 + mi * 60 + s


def format_clip(row) -> dict:
    d = dict(row)
    d["topics"] = json.loads(d.get("topics") or "[]")
    d["transcript"] = json.loads(d.get("transcript") or "[]")
    d["thumbnail"] = d["thumbnail"] or f"https://img.youtube.com/vi/{d['youtube_id']}/mqdefault.jpg"
    d["duration"] = round(d["end_sec"] - d["start_sec"])
    return d


@app.template_filter("fmt_time")
def fmt_time(sec):
    s = int(sec or 0)
    return f"{s // 60}:{s % 60:02d}"


# ── Page routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    difficulty = request.args.get("difficulty", "")
    topic = request.args.get("topic", "")

    with get_db() as conn:
        query = "SELECT * FROM clips WHERE status = 'approved'"
        params = []
        if difficulty:
            query += " AND difficulty = ?"
            params.append(difficulty)
        query += " ORDER BY created_at DESC"
        clips = [format_clip(r) for r in conn.execute(query, params).fetchall()]

        counts = {
            r["clip_id"]: r["total"]
            for r in conn.execute(
                "SELECT clip_id, SUM(repetitions) as total FROM practice_sessions GROUP BY clip_id"
            ).fetchall()
        }

    if topic:
        clips = [c for c in clips if topic in c["topics"]]

    all_topics: set[str] = set()
    for c in clips:
        all_topics.update(c["topics"])

    for c in clips:
        c["practice_count"] = counts.get(c["id"], 0)

    today_reps = 0
    with get_db() as conn:
        row = conn.execute(
            "SELECT SUM(repetitions) as t FROM practice_sessions WHERE practiced_at >= date('now')"
        ).fetchone()
        today_reps = row["t"] or 0

    return render_template(
        "index.html",
        clips=clips,
        all_topics=sorted(all_topics),
        active_difficulty=difficulty,
        active_topic=topic,
        today_reps=today_reps,
        total_clips=len(clips),
    )


@app.route("/practice/<int:clip_id>")
def practice(clip_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    if not row:
        return "Clip not found", 404
    clip = format_clip(row)

    # Total reps for this clip
    with get_db() as conn:
        row2 = conn.execute(
            "SELECT SUM(repetitions) as t FROM practice_sessions WHERE clip_id = ?", (clip_id,)
        ).fetchone()
    clip["total_reps"] = row2["t"] or 0

    return render_template("practice.html", clip=clip, categories=get_expr_categories())


@app.route("/admin")
@require_auth
def admin():
    with get_db() as conn:
        pending = [
            format_clip(r)
            for r in conn.execute(
                "SELECT * FROM clips WHERE status = 'pending' ORDER BY created_at DESC"
            ).fetchall()
        ]
        approved = [
            format_clip(r)
            for r in conn.execute(
                "SELECT * FROM clips WHERE status = 'approved' ORDER BY created_at DESC LIMIT 30"
            ).fetchall()
        ]
        rejected = [
            format_clip(r)
            for r in conn.execute(
                "SELECT * FROM clips WHERE status = 'rejected' ORDER BY created_at DESC LIMIT 15"
            ).fetchall()
        ]

    return render_template(
        "admin.html",
        pending=pending,
        approved=approved,
        rejected=rejected,
        has_yt_api=bool(os.environ.get("YOUTUBE_API_KEY")),
        has_gemini=bool(os.environ.get("GEMINI_API_KEY")),
        search_defaults=SEARCH_DEFAULTS,
    )


# ── API: Clip CRUD ─────────────────────────────────────────────────────────────

@app.route("/api/clips", methods=["POST"])
@require_auth
def add_clip():
    data = request.json or {}
    youtube_id = extract_youtube_id(data.get("url", ""))
    if not youtube_id:
        return jsonify({"error": "Invalid YouTube URL or ID"}), 400

    transcript = fetch_transcript(youtube_id)

    with get_db() as conn:
        # Prevent duplicates
        existing = conn.execute(
            "SELECT id FROM clips WHERE youtube_id = ?", (youtube_id,)
        ).fetchone()
        if existing:
            return jsonify({"error": "Already in library", "id": existing["id"]}), 409

        cursor = conn.execute(
            """INSERT INTO clips
               (youtube_id, title, channel, thumbnail, start_sec, end_sec,
                difficulty, topics, transcript, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (
                youtube_id,
                data.get("title", "Untitled Video"),
                data.get("channel", ""),
                f"https://img.youtube.com/vi/{youtube_id}/mqdefault.jpg",
                float(data.get("start_sec", 0)),
                float(data.get("end_sec", 90)),
                data.get("difficulty", "C1"),
                json.dumps(data.get("topics", [])),
                json.dumps(transcript),
            ),
        )
        clip_id = cursor.lastrowid

    return jsonify({"id": clip_id, "youtube_id": youtube_id, "transcript_lines": len(transcript)})


@app.route("/api/clips/<int:clip_id>", methods=["PUT"])
@require_auth
def update_clip(clip_id):
    data = request.json or {}
    allowed = {"start_sec", "end_sec", "difficulty", "topics", "title", "status", "ai_note"}
    fields = {k: v for k, v in data.items() if k in allowed}
    if "topics" in fields:
        fields["topics"] = json.dumps(fields["topics"])
    if "start_sec" in fields:
        fields["start_sec"] = float(fields["start_sec"])
    if "end_sec" in fields:
        fields["end_sec"] = float(fields["end_sec"])

    if not fields:
        return jsonify({"error": "No valid fields"}), 400

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with get_db() as conn:
        conn.execute(f"UPDATE clips SET {set_clause} WHERE id = ?", [*fields.values(), clip_id])

    return jsonify({"success": True})


@app.route("/api/clips/<int:clip_id>", methods=["DELETE"])
@require_auth
def delete_clip(clip_id):
    with get_db() as conn:
        conn.execute("DELETE FROM practice_sessions WHERE clip_id = ?", (clip_id,))
        conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
    return jsonify({"success": True})


@app.route("/api/clips/<int:clip_id>/approve", methods=["POST"])
@require_auth
def approve_clip(clip_id):
    with get_db() as conn:
        conn.execute("UPDATE clips SET status = 'approved' WHERE id = ?", (clip_id,))
    return jsonify({"success": True})


@app.route("/api/clips/<int:clip_id>/reject", methods=["POST"])
@require_auth
def reject_clip(clip_id):
    with get_db() as conn:
        conn.execute("UPDATE clips SET status = 'rejected' WHERE id = ?", (clip_id,))
    return jsonify({"success": True})


# ── API: Phrases ──────────────────────────────────────────────────────────────

@app.route("/phrases")
def phrases():
    with get_db() as conn:
        rows = conn.execute("""
            SELECT p.*, c.title as clip_title, c.youtube_id
            FROM phrases p
            LEFT JOIN clips c ON p.clip_id = c.id
            ORDER BY p.created_at DESC
        """).fetchall()
    return render_template("phrases.html", phrases=[dict(r) for r in rows])

@app.route("/api/phrases", methods=["POST"])
def save_phrase():
    data = request.json or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO phrases (clip_id, text, note) VALUES (?, ?, ?)",
            (data.get("clip_id"), text, (data.get("note") or "").strip()),
        )
    return jsonify({"success": True, "id": cursor.lastrowid})

@app.route("/api/phrases/<int:phrase_id>", methods=["DELETE"])
def delete_phrase(phrase_id):
    with get_db() as conn:
        conn.execute("DELETE FROM phrases WHERE id = ?", (phrase_id,))
    return jsonify({"success": True})


# ── API: Practice logging ──────────────────────────────────────────────────────

@app.route("/api/practice/<int:clip_id>", methods=["POST"])
def log_practice(clip_id):
    data = request.json or {}
    reps = max(1, int(data.get("repetitions", 1)))
    with get_db() as conn:
        conn.execute(
            "INSERT INTO practice_sessions (clip_id, repetitions) VALUES (?, ?)",
            (clip_id, reps),
        )
    return jsonify({"success": True})


@app.route("/api/stats")
def get_stats():
    with get_db() as conn:
        total_reps = (
            conn.execute("SELECT SUM(repetitions) FROM practice_sessions").fetchone()[0] or 0
        )
        today_reps = (
            conn.execute(
                "SELECT SUM(repetitions) FROM practice_sessions WHERE practiced_at >= date('now')"
            ).fetchone()[0]
            or 0
        )
        recent = conn.execute(
            """SELECT DATE(practiced_at) as date, SUM(repetitions) as reps
               FROM practice_sessions
               WHERE practiced_at >= date('now', '-14 days')
               GROUP BY DATE(practiced_at) ORDER BY date""",
        ).fetchall()

    return jsonify(
        {
            "total_reps": total_reps,
            "today_reps": today_reps,
            "recent": [{"date": r["date"], "reps": r["reps"]} for r in recent],
        }
    )


# ── API: AI-powered search ────────────────────────────────────────────────────

@app.route("/api/search", methods=["POST"])
@require_auth
def ai_search():
    data = request.json or {}
    query = data.get("query", SEARCH_DEFAULTS[0]).strip()
    max_results = min(int(data.get("max_results", 3)), 3)

    yt_key = os.environ.get("YOUTUBE_API_KEY")
    if not yt_key:
        return jsonify({"error": "YOUTUBE_API_KEY not configured in .env"}), 400

    try:
        from googleapiclient.discovery import build

        youtube = build("youtube", "v3", developerKey=yt_key)

        search_resp = (
            youtube.search()
            .list(
                part="snippet",
                q=query,
                type="video",
                videoDuration="medium",
                videoCaption="closedCaption",
                relevanceLanguage="en",
                maxResults=max_results * 3,
            )
            .execute()
        )

        candidates = []
        for item in search_resp.get("items", []):
            vid_id = item["id"]["videoId"]
            snippet = item["snippet"]

            # Fetch duration
            vid_resp = (
                youtube.videos().list(part="contentDetails", id=vid_id).execute()
            )
            if not vid_resp["items"]:
                continue

            duration_sec = parse_iso_duration(
                vid_resp["items"][0]["contentDetails"]["duration"]
            )
            if not (120 <= duration_sec <= 1200):  # 2–20 minutes
                continue

            transcript = fetch_transcript(vid_id)

            candidates.append(
                {
                    "youtube_id": vid_id,
                    "title": snippet["title"],
                    "channel": snippet["channelTitle"],
                    "thumbnail": snippet["thumbnails"].get("medium", {}).get("url", ""),
                    "duration_sec": duration_sec,
                    "transcript": transcript,
                }
            )
            if len(candidates) >= max_results:
                break

        # Evaluate with Gemini
        gemini_key = os.environ.get("GEMINI_API_KEY")
        results = []
        if gemini_key and candidates:
            import time
            import google.generativeai as genai

            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel("gemini-2.0-flash-lite")
            for c in candidates:
                time.sleep(3)
                t_text = " ".join(
                    f"[{int(t['start'])}s] {t['text']}"
                    for t in c["transcript"]
                    if t["start"] <= 240
                )[:3500]

                try:
                    resp = model.generate_content(
                        f"""Evaluate this YouTube video for English shadowing practice.
Target learner: intermediate to advanced English learner who wants to improve speaking fluency.

Video: "{c['title']}" by {c['channel']} ({c['duration_sec']}s)
Transcript excerpt:
{t_text}

Reply ONLY with valid JSON (no markdown fences):
{{
  "suitable": true or false,
  "difficulty": "B1" or "B2" or "C1",
  "start_sec": <integer, best clip start>,
  "end_sec": <integer, 60-120 seconds after start_sec>,
  "topics": ["tag1", "tag2"],
  "assessment": "<1 concise sentence on suitability>"
}}
Topics must be from: Daily Life, Travel, Business, Education, Culture, Technology, Health, Entertainment, News, Sports"""
                    )
                    ev = json.loads(resp.text)
                    c.update(
                        {
                            "ai_suitable": bool(ev.get("suitable", True)),
                            "difficulty": ev.get("difficulty", "C1"),
                            "start_sec": max(0, int(ev.get("start_sec", 0))),
                            "end_sec": int(ev.get("end_sec", 90)),
                            "topics": ev.get("topics", []),
                            "ai_note": ev.get("assessment", ""),
                        }
                    )
                except Exception as ex:
                    c.update(
                        {
                            "ai_suitable": True,
                            "difficulty": "C1",
                            "start_sec": 0,
                            "end_sec": 90,
                            "topics": [],
                            "ai_note": f"AI evaluation skipped: {ex}",
                        }
                    )
                results.append(c)
        else:
            results = [
                {
                    **c,
                    "ai_suitable": True,
                    "difficulty": "C1",
                    "start_sec": 0,
                    "end_sec": 90,
                    "topics": [],
                    "ai_note": "Add GEMINI_API_KEY to .env for AI evaluation.",
                }
                for c in candidates
            ]

        return jsonify({"results": results})

    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/save-search-result", methods=["POST"])
@require_auth
def save_search_result():
    data = request.json or {}
    youtube_id = data.get("youtube_id")
    if not youtube_id:
        return jsonify({"error": "Missing youtube_id"}), 400

    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM clips WHERE youtube_id = ?", (youtube_id,)
        ).fetchone()
        if existing:
            return jsonify({"error": "Already in library", "id": existing["id"]}), 409

        cursor = conn.execute(
            """INSERT INTO clips
               (youtube_id, title, channel, thumbnail, start_sec, end_sec,
                difficulty, topics, transcript, status, ai_note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (
                youtube_id,
                data.get("title", "Untitled"),
                data.get("channel", ""),
                data.get(
                    "thumbnail",
                    f"https://img.youtube.com/vi/{youtube_id}/mqdefault.jpg",
                ),
                float(data.get("start_sec", 0)),
                float(data.get("end_sec", 90)),
                data.get("difficulty", "C1"),
                json.dumps(data.get("topics", [])),
                json.dumps(data.get("transcript", [])),
                data.get("ai_note", ""),
            ),
        )
        clip_id = cursor.lastrowid

    return jsonify({"success": True, "id": clip_id})


@app.route("/api/transcript/<youtube_id>")
def get_transcript(youtube_id):
    t = fetch_transcript(youtube_id)
    return jsonify({"transcript": t, "count": len(t)})


# ── Expressions (Speaking表現集) ───────────────────────────────────────────────

def get_expr_categories():
    with get_db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM categories ORDER BY name").fetchall()]


def resolve_expr_category(form, conn) -> int | None:
    category_id = form.get("category_id") or None
    new_category = (form.get("new_category") or "").strip()
    if new_category and not category_id:
        try:
            cursor = conn.execute("INSERT INTO categories (name) VALUES (?)", (new_category,))
            category_id = cursor.lastrowid
        except sqlite3.IntegrityError:
            row = conn.execute("SELECT id FROM categories WHERE name = ?", (new_category,)).fetchone()
            category_id = row["id"] if row else None
    return category_id


@app.route("/expressions")
def expressions():
    category_id = request.args.get("category", type=int)
    with get_db() as conn:
        if category_id:
            rows = conn.execute("""
                SELECT e.*, c.name as category_name
                FROM expressions e LEFT JOIN categories c ON e.category_id = c.id
                WHERE e.category_id = ? ORDER BY e.created_at DESC
            """, (category_id,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT e.*, c.name as category_name
                FROM expressions e LEFT JOIN categories c ON e.category_id = c.id
                ORDER BY e.created_at DESC
            """).fetchall()
    return render_template(
        "expressions.html",
        expressions=[dict(r) for r in rows],
        categories=get_expr_categories(),
        active_category=category_id,
    )


@app.route("/expressions/add", methods=["GET", "POST"])
def expressions_add():
    if request.method == "POST":
        expression = request.form.get("expression", "").strip()
        if not expression:
            return render_template("expressions_add.html", categories=get_expr_categories(), error="表現を入力してください")
        try:
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO expressions (expression, example, memo, category_id, japanese_hint) VALUES (?, ?, ?, ?, ?)",
                    (
                        expression,
                        request.form.get("example", "").strip(),
                        request.form.get("memo", "").strip(),
                        resolve_expr_category(request.form, conn),
                        request.form.get("japanese_hint", "").strip(),
                    ),
                )
            return redirect(url_for("expressions"))
        except Exception as e:
            import traceback
            traceback.print_exc()
            return f"ADD ERROR: {e}", 500
    return render_template("expressions_add.html", categories=get_expr_categories())


@app.route("/expressions/edit/<int:expr_id>", methods=["GET", "POST"])
def expressions_edit(expr_id):
    if request.method == "POST":
        with get_db() as conn:
            conn.execute(
                "UPDATE expressions SET expression=?, example=?, memo=?, category_id=?, japanese_hint=? WHERE id=?",
                (
                    request.form.get("expression", "").strip(),
                    request.form.get("example", "").strip(),
                    request.form.get("memo", "").strip(),
                    resolve_expr_category(request.form, conn),
                    request.form.get("japanese_hint", "").strip(),
                    expr_id,
                ),
            )
        return redirect(url_for("expressions"))
    with get_db() as conn:
        expr = conn.execute("SELECT * FROM expressions WHERE id = ?", (expr_id,)).fetchone()
    if not expr:
        return redirect(url_for("expressions"))
    return render_template("expressions_edit.html", expr=dict(expr), categories=get_expr_categories())


@app.route("/expressions/delete/<int:expr_id>", methods=["POST"])
def expressions_delete(expr_id):
    try:
        with get_db() as conn:
            conn.execute("DELETE FROM expressions WHERE id = ?", (expr_id,))
        return redirect(url_for("expressions"))
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"DELETE ERROR: {e}", 500


@app.route("/expressions/quiz")
def expressions_quiz():
    with get_db() as conn:
        expr = conn.execute("""
            SELECT e.*, c.name as category_name
            FROM expressions e LEFT JOIN categories c ON e.category_id = c.id
            ORDER BY RANDOM() LIMIT 1
        """).fetchone()
    return render_template("expressions_quiz.html", expr=dict(expr) if expr else None)


@app.route("/api/expressions/quiz/next")
def expressions_quiz_next():
    exclude_id = request.args.get("exclude", type=int, default=0)
    with get_db() as conn:
        expr = conn.execute("""
            SELECT e.*, c.name as category_name
            FROM expressions e LEFT JOIN categories c ON e.category_id = c.id
            WHERE e.id != ? ORDER BY RANDOM() LIMIT 1
        """, (exclude_id,)).fetchone()
        if not expr:
            expr = conn.execute("""
                SELECT e.*, c.name as category_name
                FROM expressions e LEFT JOIN categories c ON e.category_id = c.id
                ORDER BY RANDOM() LIMIT 1
            """).fetchone()
    if not expr:
        return jsonify({"error": "No expressions"}), 404
    return jsonify(dict(expr))


@app.route("/api/expressions/generate-hint", methods=["POST"])
def expressions_generate_hint():
    data = request.json or {}
    expression = (data.get("expression") or "").strip()
    example = (data.get("example") or "").strip()
    if not expression:
        return jsonify({"error": "expression is required"}), 400
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        return jsonify({"error": "GEMINI_API_KEY not configured"}), 400
    try:
        import google.generativeai as genai
        genai.configure(api_key=gemini_key)
        model = genai.GenerativeModel("gemini-2.0-flash-lite")
        example_line = f"\n参考例文: {example}" if example else ""
        prompt = f"""英語表現「{expression}」を使う日本語→英語の翻訳問題を作ってください。{example_line}

条件:
- 日本語の文章を1つだけ出力する
- その日本語文を英語にすると「{expression}」が自然に使われる内容にする
- 日本語の文章のみ出力し、説明や英語訳は含めない
- 自然で日常的な日本語にする"""
        resp = model.generate_content(prompt)
        return jsonify({"japanese_hint": resp.text.strip()})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/expressions", methods=["POST"])
def api_expressions_save():
    data = request.json or {}
    expression = (data.get("expression") or "").strip()
    if not expression:
        return jsonify({"error": "expression is required"}), 400
    with get_db() as conn:
        category_id = data.get("category_id") or None
        new_category = (data.get("new_category") or "").strip()
        if new_category and not category_id:
            try:
                cur = conn.execute("INSERT INTO categories (name) VALUES (?)", (new_category,))
                category_id = cur.lastrowid
            except sqlite3.IntegrityError:
                row = conn.execute("SELECT id FROM categories WHERE name = ?", (new_category,)).fetchone()
                category_id = row["id"] if row else None
        cursor = conn.execute(
            "INSERT INTO expressions (expression, example, memo, category_id, japanese_hint) VALUES (?, ?, ?, ?, ?)",
            (
                expression,
                (data.get("example") or "").strip(),
                (data.get("memo") or "").strip(),
                category_id,
                (data.get("japanese_hint") or "").strip(),
            ),
        )
    return jsonify({"success": True, "id": cursor.lastrowid})


# ─────────────────────────────────────────────────────────────────────────────

init_db()  # always run so gunicorn also initializes DB

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    debug = os.environ.get("FLASK_ENV") != "production"
    print("\n" + "=" * 50)
    print("  ShadowClip 起動中...")
    print(f"  ブラウザで開く → http://localhost:{port}")
    print("=" * 50 + "\n")
    app.run(debug=debug, host="0.0.0.0", port=port)
