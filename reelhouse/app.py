import os
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, send_from_directory, abort
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_POSTERS = os.path.join(BASE_DIR, "static", "uploads", "posters")
UPLOAD_VIDEOS = os.path.join(BASE_DIR, "static", "uploads", "videos")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# --- Database ---
# SQLite by default so this runs anywhere with zero setup.
# To use MySQL instead (matches your ShopSphere stack), set:
#   DATABASE_URL = "mysql+pymysql://user:password@host/dbname"
# and swap the line below.
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'reelhouse.db')}"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500MB upload cap

db = SQLAlchemy(app)

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")  # change in production

ALLOWED_POSTER_EXT = {"jpg", "jpeg", "png", "webp"}
ALLOWED_VIDEO_EXT = {"mp4", "webm", "mov"}


def allowed_file(filename, allowed_set):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed_set


# ---------------- Models ----------------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)


class Video(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(20), nullable=False)   # 'film' or 'series'
    genre = db.Column(db.String(100))
    year = db.Column(db.String(10))

    # 3 main highlight points shown on the card / detail view
    point1 = db.Column(db.String(300))
    point2 = db.Column(db.String(300))
    point3 = db.Column(db.String(300))

    description = db.Column(db.Text)
    poster_filename = db.Column(db.String(300))
    video_filename = db.Column(db.String(300))

    row_section = db.Column(db.String(100), default="Trending Now")  # which homepage row it appears in

    is_published = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def is_complete(self):
        """A video only goes live once every required field is filled."""
        required = [
            self.title, self.category, self.point1, self.point2,
            self.point3, self.poster_filename, self.video_filename
        ]
        return all(bool(f) for f in required)


class MyListItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    video_id = db.Column(db.Integer, db.ForeignKey("video.id"), nullable=False)
    added_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User")
    video = db.relationship("Video")


# ---------------- Auth helpers ----------------

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "error")
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return wrapper


def current_user():
    uid = session.get("user_id")
    return User.query.get(uid) if uid else None


# ---------------- Public site ----------------

@app.route("/")
def home():
    films = Video.query.filter_by(category="film", is_published=True).order_by(Video.created_at.desc()).all()
    series = Video.query.filter_by(category="series", is_published=True).order_by(Video.created_at.desc()).all()

    user = current_user()
    my_list_ids = set()
    if user:
        my_list_ids = {item.video_id for item in MyListItem.query.filter_by(user_id=user.id)}

    # group into rows by row_section for a Netflix-like shelf layout
    def group_rows(videos):
        rows = {}
        for v in videos:
            rows.setdefault(v.row_section or "Trending Now", []).append(v)
        return rows

    return render_template(
        "index.html",
        film_rows=group_rows(films),
        series_rows=group_rows(series),
        user=user,
        my_list_ids=my_list_ids,
    )


@app.route("/video/<int:video_id>")
def video_detail(video_id):
    video = Video.query.get_or_404(video_id)
    if not video.is_published:
        abort(404)
    user = current_user()
    in_my_list = False
    if user:
        in_my_list = MyListItem.query.filter_by(user_id=user.id, video_id=video.id).first() is not None
    return render_template("detail.html", video=video, user=user, in_my_list=in_my_list)


@app.route("/mylist")
@login_required
def my_list():
    user = current_user()
    items = MyListItem.query.filter_by(user_id=user.id).order_by(MyListItem.added_at.desc()).all()
    videos = [i.video for i in items]
    return render_template("mylist.html", videos=videos, user=user)


@app.route("/mylist/toggle/<int:video_id>", methods=["POST"])
@login_required
def toggle_my_list(video_id):
    user = current_user()
    video = Video.query.get_or_404(video_id)
    existing = MyListItem.query.filter_by(user_id=user.id, video_id=video.id).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
    else:
        db.session.add(MyListItem(user_id=user.id, video_id=video.id))
        db.session.commit()
    return redirect(request.referrer or url_for("home"))


# ---------------- User auth ----------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        if User.query.filter_by(email=email).first():
            flash("An account with that email already exists.", "error")
            return redirect(url_for("register"))

        user = User(name=name, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        session["user_id"] = user.id
        flash("Account created.", "success")
        return redirect(url_for("home"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            session["user_id"] = user.id
            flash(f"Welcome back, {user.name}.", "success")
            return redirect(request.args.get("next") or url_for("home"))

        flash("Invalid email or password.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("user_id", None)
    return redirect(url_for("home"))


# ---------------- Developer / Admin content panel ----------------

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["is_admin"] = True
            return redirect(url_for("admin_dashboard"))
        flash("Wrong admin password.", "error")
    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("admin_login"))


@app.route("/admin")
@admin_required
def admin_dashboard():
    videos = Video.query.order_by(Video.created_at.desc()).all()
    return render_template("admin_dashboard.html", videos=videos)


@app.route("/admin/new", methods=["GET", "POST"])
@admin_required
def admin_new_video():
    if request.method == "POST":
        video = Video(
            title=request.form.get("title", "").strip(),
            category=request.form.get("category", "film"),
            genre=request.form.get("genre", "").strip(),
            year=request.form.get("year", "").strip(),
            point1=request.form.get("point1", "").strip(),
            point2=request.form.get("point2", "").strip(),
            point3=request.form.get("point3", "").strip(),
            description=request.form.get("description", "").strip(),
            row_section=request.form.get("row_section", "Trending Now").strip() or "Trending Now",
        )

        poster = request.files.get("poster")
        if poster and poster.filename and allowed_file(poster.filename, ALLOWED_POSTER_EXT):
            fname = secure_filename(f"{datetime.utcnow().timestamp()}_{poster.filename}")
            poster.save(os.path.join(UPLOAD_POSTERS, fname))
            video.poster_filename = fname

        videofile = request.files.get("video")
        if videofile and videofile.filename and allowed_file(videofile.filename, ALLOWED_VIDEO_EXT):
            fname = secure_filename(f"{datetime.utcnow().timestamp()}_{videofile.filename}")
            videofile.save(os.path.join(UPLOAD_VIDEOS, fname))
            video.video_filename = fname

        # Only goes live on the site once every required field + file is present
        video.is_published = video.is_complete()

        db.session.add(video)
        db.session.commit()

        if video.is_published:
            flash(f'"{video.title}" is complete and now live on the site.', "success")
        else:
            flash(f'"{video.title}" saved as draft — missing fields, so it will not show on the site yet.', "error")

        return redirect(url_for("admin_dashboard"))

    return render_template("admin_form.html", video=None)


@app.route("/admin/edit/<int:video_id>", methods=["GET", "POST"])
@admin_required
def admin_edit_video(video_id):
    video = Video.query.get_or_404(video_id)

    if request.method == "POST":
        video.title = request.form.get("title", "").strip()
        video.category = request.form.get("category", "film")
        video.genre = request.form.get("genre", "").strip()
        video.year = request.form.get("year", "").strip()
        video.point1 = request.form.get("point1", "").strip()
        video.point2 = request.form.get("point2", "").strip()
        video.point3 = request.form.get("point3", "").strip()
        video.description = request.form.get("description", "").strip()
        video.row_section = request.form.get("row_section", "Trending Now").strip() or "Trending Now"

        poster = request.files.get("poster")
        if poster and poster.filename and allowed_file(poster.filename, ALLOWED_POSTER_EXT):
            fname = secure_filename(f"{datetime.utcnow().timestamp()}_{poster.filename}")
            poster.save(os.path.join(UPLOAD_POSTERS, fname))
            video.poster_filename = fname

        videofile = request.files.get("video")
        if videofile and videofile.filename and allowed_file(videofile.filename, ALLOWED_VIDEO_EXT):
            fname = secure_filename(f"{datetime.utcnow().timestamp()}_{videofile.filename}")
            videofile.save(os.path.join(UPLOAD_VIDEOS, fname))
            video.video_filename = fname

        video.is_published = video.is_complete()
        db.session.commit()

        flash("Video updated." if video.is_published else "Saved as draft — still missing required fields.",
              "success" if video.is_published else "error")
        return redirect(url_for("admin_dashboard"))

    return render_template("admin_form.html", video=video)


@app.route("/admin/delete/<int:video_id>", methods=["POST"])
@admin_required
def admin_delete_video(video_id):
    video = Video.query.get_or_404(video_id)
    db.session.delete(video)
    db.session.commit()
    flash("Video deleted.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/unpublish/<int:video_id>", methods=["POST"])
@admin_required
def admin_unpublish(video_id):
    video = Video.query.get_or_404(video_id)
    video.is_published = False
    db.session.commit()
    return redirect(url_for("admin_dashboard"))


# ---------------- File serving ----------------

@app.route("/media/posters/<path:filename>")
def poster_file(filename):
    return send_from_directory(UPLOAD_POSTERS, filename)


@app.route("/media/videos/<path:filename>")
def video_file(filename):
    return send_from_directory(UPLOAD_VIDEOS, filename)


with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(debug=True, port=5000)
