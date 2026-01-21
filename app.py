from flask import Flask, render_template, request, redirect, session, send_from_directory
import os, sqlite3, datetime

app = Flask(__name__)

# ---------------- CONFIG ----------------

app.secret_key = os.environ.get("SECRET_KEY", "fallback-secret")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_ROOT = os.path.join(BASE_DIR, "uploads")
DB_PATH = os.path.join(BASE_DIR, "data.db")

os.makedirs(UPLOAD_ROOT, exist_ok=True)

# ---------------- DATABASE ----------------

def get_db():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    db = get_db()
    cur = db.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        username TEXT PRIMARY KEY,
        password TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS files(
        username TEXT,
        filename TEXT,
        locked INTEGER DEFAULT 0,
        expiry INTEGER DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS logs(
        username TEXT,
        action TEXT,
        filename TEXT,
        time TEXT
    )
    """)

    db.commit()
    db.close()

def log_action(user, action, filename="-"):
    db = get_db()
    db.execute(
        "INSERT INTO logs VALUES (?,?,?,?)",
        (user, action, filename,
         datetime.datetime.now().strftime("%d %b %Y %H:%M"))
    )
    db.commit()
    db.close()

# ---------------- HELPERS ----------------

def user_folder(user):
    path = os.path.join(UPLOAD_ROOT, user)
    os.makedirs(path, exist_ok=True)
    return path

# ---------------- AUTH ----------------

@app.route("/", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        u = request.form["username"]
        p = request.form["password"]

        db = get_db()
        cur = db.cursor()
        cur.execute("SELECT * FROM users WHERE username=? AND password=?", (u, p))
        user = cur.fetchone()
        db.close()
        if user:
            session["user"] = u
            session["theme"] = "light"
            return redirect("/dashboard")
        else:
            error = "Invalid username or password"

    return render_template("login.html", error=error)

@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        u = request.form["username"]
        p = request.form["password"]

        if len(p) < 5:
            error = "Password must be at least 5 characters"
        else:
            try:
                db = get_db()
                db.execute("INSERT INTO users VALUES (?,?)", (u, p))
                db.commit()
                db.close()
                return redirect("/")
            except:
                error = "Username already exists"

    return render_template("register.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# ---------------- FORGOT PASSWORD ----------------

@app.route("/forgetpassword", methods=["GET", "POST"])
def forgetpassword():
    error = None
    success = None

    if request.method == "POST":
        u = request.form["username"]
        new = request.form["new"]

        db = get_db()
        cur = db.cursor()
        cur.execute("SELECT * FROM users WHERE username=?", (u,))
        if cur.fetchone():
            cur.execute("UPDATE users SET password=? WHERE username=?", (new, u))
            db.commit()
            success = "Password reset successful"
        else:
            error = "Username not found"
        db.close()

    return render_template("forgetpassword.html", error=error, success=success)

# ---------------- DASHBOARD ----------------

@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect("/")

    user = session["user"]
    db = get_db()
    cur = db.cursor()

    cur.execute("SELECT filename, locked FROM files WHERE username=?", (user,))
    files = cur.fetchall()

    count = len(files)
    locked = sum(1 for f in files if f[1] == 1)

    size = 0
    for f in os.listdir(user_folder(user)):
        size += os.path.getsize(os.path.join(user_folder(user), f))
    size = round(size / 1024 / 1024, 2)

    cur.execute("SELECT * FROM logs WHERE username=? ORDER BY time DESC LIMIT 5", (user,))
    logs = cur.fetchall()
    db.close()

    return render_template(
        "dashboard.html",
        user=user,
        count=count,
        size=size,
        quota=100,
        locked=locked,
        logs=logs,
        theme=session.get("theme", "light")
    )

@app.route("/toggle_theme")
def toggle_theme():
    session["theme"] = "dark" if session.get("theme") == "light" else "light"
    return redirect("/dashboard")

# ---------------- UPLOAD ----------------

@app.route("/upload", methods=["POST"])
def upload():
    if "user" not in session:
        return redirect("/")

    file = request.files["file"]
    lock = request.form.get("lock")
    expiry = int(request.form.get("expiry", 0))

    if file:
        file.save(os.path.join(user_folder(session["user"]), file.filename))
        db = get_db()
        db.execute(
            "INSERT INTO files VALUES (?,?,?,?)",
            (session["user"], file.filename, 1 if lock else 0, expiry)
        )
        db.commit()
        db.close()
        log_action(session["user"], "Uploaded", file.filename)

    return redirect("/dashboard")

# ---------------- FILES ----------------

@app.route("/myuploads")
def myuploads():
    if "user" not in session:
        return redirect("/")

    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT filename, locked FROM files WHERE username=?", (session["user"],))
    files = cur.fetchall()
    db.close()

    return render_template("myuploads.html", files=files, theme=session.get("theme"))

@app.route("/download/<name>")
def download(name):
    if "user" not in session:
        return redirect("/")
    log_action(session["user"], "Downloaded", name)
    return send_from_directory(user_folder(session["user"]), name, as_attachment=True)

@app.route("/delete/<name>")
def delete(name):
    if "user" not in session:
        return redirect("/")

    path = os.path.join(user_folder(session["user"]), name)
    if os.path.exists(path):
        os.remove(path)

    db = get_db()
    db.execute("DELETE FROM files WHERE username=? AND filename=?", (session["user"], name))
    db.commit()
    db.close()

    log_action(session["user"], "Deleted", name)
    return redirect("/myuploads")

# ---------------- CHANGE PASSWORD ----------------

@app.route("/change_password", methods=["GET", "POST"])
def change_password():
    error = None
    if request.method == "POST":
        old = request.form["old"]
        new = request.form["new"]

        db = get_db()
        cur = db.cursor()
        cur.execute(
            "SELECT * FROM users WHERE username=? AND password=?",
            (session["user"], old)
        )
        if cur.fetchone():
            cur.execute(
                "UPDATE users SET password=? WHERE username=?",
                (new, session["user"])
            )
            db.commit()
            db.close()
            return redirect("/dashboard")
        else:
            error = "Old password incorrect"
            db.close()

    return render_template("change_password.html", error=error)

# ---------------- INIT ----------------

init_db()
# For Render / Gunicorn
application = app
