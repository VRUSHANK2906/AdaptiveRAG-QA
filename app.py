import hashlib
import os
import re
import smtplib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from adaptive_rag import AdaptiveRAG
from database import (
    add_document,
    add_question_history,
    change_user_password,
    consume_password_reset_token,
    create_password_reset_token,
    create_user,
    delete_document,
    delete_question_history,
    get_dashboard_stats,
    get_db,
    get_document,
    get_user_by_email,
    get_user_by_id,
    get_user_settings,
    init_db,
    list_documents,
    list_question_history,
    now_iso,
    save_user_settings,
    search_question_history,
    update_user,
    delete_all_question_history,
)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
USER_UPLOAD_DIR = UPLOAD_DIR
MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", str(50 * 1024 * 1024)))

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", "dev-only-change-before-deploying"),
    MAX_CONTENT_LENGTH=MAX_CONTENT_LENGTH,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)
CORS(app, origins=os.getenv("CORS_ORIGINS", "http://127.0.0.1:5000").split(","))
init_db()

rag_store: dict[int, AdaptiveRAG] = {}
chat_rag_store: dict[tuple[int, str], AdaptiveRAG] = {}


def current_user() -> dict | None:
    user_id = session.get("user_id")
    if not user_id:
        return None
    return get_user_by_id(int(user_id))


def login_required(view):
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            if request.path.startswith("/api/"):
                return api_error("Authentication is required.", 401)
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def get_or_create_rag(user_id: int) -> AdaptiveRAG:
    rag = rag_store.get(user_id)
    if rag is None:
        rag = AdaptiveRAG(
            ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate"),
            ollama_model=os.getenv("OLLAMA_MODEL", "gemma3:1b"),
        )
        rag_store[user_id] = rag
    return rag


def get_or_create_chat_rag(user_id: int, chat_id: str) -> AdaptiveRAG:
    key = (user_id, chat_id)
    rag = chat_rag_store.get(key)
    if rag is None:
        rag = AdaptiveRAG(
            ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate"),
            ollama_model=os.getenv("OLLAMA_MODEL", "gemma3:1b"),
        )
        chat_rag_store[key] = rag
    return rag


def clear_user_rag(user_id: int) -> None:
    rag_store.pop(user_id, None)
    for key in list(chat_rag_store):
        if key[0] == user_id:
            chat_rag_store.pop(key, None)


def api_error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def serialize_user(user: dict) -> dict:
    return {"id": user["id"], "name": user["name"], "email": user["email"]}


def validate_email(email: str) -> bool:
    return bool(re.fullmatch(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email.strip()))


def validate_password(password: str) -> bool:
    return len(password) >= 8 and any(char.isupper() for char in password) and any(char.isdigit() for char in password)


def send_reset_email(email: str, token: str) -> bool:
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM", "DeepReasoner <noreply@localhost>")
    if not smtp_host or not smtp_user or not smtp_password:
        return False
    reset_url = urljoin(request.host_url, url_for("reset_password", token=token))
    body = f"Hello,\n\nUse this secure link to reset your password:\n{reset_url}\n\nThis link expires in 30 minutes."
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(smtp_user, smtp_password)
            smtp.sendmail(sender, email, f"To: {email}\nFrom: {sender}\nSubject: Reset your DeepReasoner password\n\n{body}")
        return True
    except Exception:
        return False


@app.context_processor
def inject_user():
    return {"current_user": current_user()}


@app.get("/")
def home():
    if current_user():
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.get("/login")
def login():
    if current_user():
        return redirect(url_for("dashboard"))
    return render_template("login.html", page="login")


@app.post("/api/login")
def api_login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not validate_email(email) or not password:
        return api_error("Enter a valid email and password.", 401)
    user = get_user_by_email(email)
    if not user or not check_password_hash(user["password_hash"], password):
        return api_error("Invalid email or password.", 401)
    session.clear()
    session["user_id"] = str(user["id"])
    session.permanent = bool(data.get("remember", False))
    return jsonify({"message": "Signed in successfully.", "user": serialize_user(user)})


@app.get("/register")
def register():
    if current_user():
        return redirect(url_for("dashboard"))
    return render_template("register.html", page="register")


@app.post("/api/register")
def api_register():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    confirm_password = data.get("confirmPassword") or ""
    if len(name) < 2 or len(name) > 80:
        return api_error("Enter a full name between 2 and 80 characters.")
    if not validate_email(email):
        return api_error("Enter a valid email address.")
    if not validate_password(password):
        return api_error("Password must contain at least 8 characters, one uppercase letter, and one number.")
    if password != confirm_password:
        return api_error("Passwords do not match.")
    if get_user_by_email(email):
        return api_error("An account with this email already exists.", 409)
    try:
        user = create_user(name, email, generate_password_hash(password))
    except Exception:
        return api_error("Unable to create the account. Please try again.", 500)
    session.clear()
    session["user_id"] = str(user["id"])
    session.permanent = True
    return jsonify({"message": "Account created successfully.", "user": serialize_user(user)})


@app.get("/forgot-password")
def forgot_password():
    if current_user():
        return redirect(url_for("dashboard"))
    return render_template("forgot.html", page="forgot")


@app.post("/api/forgot-password")
def api_forgot_password():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not validate_email(email):
        return api_error("Enter a valid email address.")
    user = get_user_by_email(email)
    if user:
        token = create_password_reset_token(user["id"])
        sent = send_reset_email(email, token)
        if not sent:
            app.logger.warning("Password reset email delivery is not configured.")
    return jsonify({"message": "If an account exists for that email, a reset link has been sent."})


@app.get("/reset-password/<token>")
def reset_password(token: str):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with get_db() as connection:
        row = connection.execute(
            "SELECT user_id FROM password_reset_tokens WHERE token_hash = ? AND used_at IS NULL AND CAST(expires_at AS REAL) > ?",
            (token_hash, datetime.now(timezone.utc).timestamp()),
        ).fetchone()
    user = get_user_by_id(row["user_id"]) if row else None
    if not user:
        return render_template("reset.html", page="reset", valid=False, token=token), 400
    return render_template("reset.html", page="reset", valid=True, token=token, user=user)


@app.post("/api/reset-password")
def api_reset_password():
    data = request.get_json(silent=True) or {}
    token = data.get("token") or ""
    password = data.get("password") or ""
    confirm_password = data.get("confirmPassword") or ""
    if not token or not validate_password(password) or password != confirm_password:
        return api_error("The password reset request is invalid or incomplete.")
    user, valid = consume_password_reset_token(token)
    if not valid or not user:
        return api_error("The reset link is invalid or expired.", 400)
    change_user_password(user["id"], generate_password_hash(password))
    return jsonify({"message": "Password updated successfully."})


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"message": "Signed out successfully."})


@app.get("/dashboard")
@login_required
def dashboard():
    user = current_user()
    stats = get_dashboard_stats(user["id"])
    recent = stats["recent_queries"][:3]
    return render_template("dashboard.html", page="dashboard", current_user=user, stats=stats, recent=recent)


@app.get("/qa")
@login_required
def qa():
    return render_template("qa.html", page="qa")


@app.get("/documents")
@login_required
def documents():
    return render_template("documents.html", page="documents")


@app.get("/history")
@login_required
def history():
    return render_template("history.html", page="history")


@app.get("/profile")
@login_required
def profile():
    return render_template("profile.html", page="profile")


@app.get("/settings")
@login_required
def settings():
    return render_template("settings.html", page="settings")


@app.get("/api/user")
@login_required
def api_user():
    return jsonify({"user": serialize_user(current_user())})


@app.get("/api/settings")
@login_required
def api_settings():
    return jsonify({"settings": get_user_settings(current_user()["id"])})


@app.put("/api/settings")
@login_required
def update_settings():
    data = request.get_json(silent=True) or {}
    theme = data.get("theme")
    notifications = data.get("notifications") or {}
    if theme not in {"light", "dark", "system"}:
        return api_error("Theme must be light, dark, or system.")
    if not isinstance(notifications, dict):
        return api_error("Notification settings must be an object.")
    normalized = {key: bool(value) for key, value in notifications.items() if key in {"login", "documents", "questions", "errors"}}
    save_user_settings(current_user()["id"], theme=theme, notifications=normalized)
    return jsonify({"message": "Settings saved.", "settings": get_user_settings(current_user()["id"])})


@app.post("/api/profile")
@login_required
def update_profile():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    if len(name) < 2 or len(name) > 80:
        return api_error("Enter a name between 2 and 80 characters.")
    if not validate_email(email):
        return api_error("Enter a valid email address.")
    if email != current_user()["email"] and get_user_by_email(email):
        return api_error("That email is already registered.", 409)
    updated = update_user(current_user()["id"], name=name, email=email)
    return jsonify({"message": "Profile updated.", "user": serialize_user(updated)})


@app.post("/api/password")
@login_required
def update_password():
    data = request.get_json(silent=True) or {}
    current_password = data.get("currentPassword") or ""
    new_password = data.get("newPassword") or ""
    confirm_password = data.get("confirmPassword") or ""
    user = current_user()
    if not check_password_hash(user["password_hash"], current_password):
        return api_error("The current password is incorrect.", 401)
    if not validate_password(new_password):
        return api_error("The new password must contain at least 8 characters, one uppercase letter, and one number.")
    if new_password != confirm_password:
        return api_error("Passwords do not match.")
    change_user_password(user["id"], generate_password_hash(new_password))
    return jsonify({"message": "Password changed successfully."})


@app.get("/api/documents")
@login_required
def api_documents():
    return jsonify({"documents": list_documents(current_user()["id"])})


@app.post("/upload")
@login_required
def upload():
    files = request.files.getlist("files")
    if not files or all(not file.filename for file in files):
        return api_error("Select at least one PDF.")
    user = current_user()
    chat_id = (request.form.get("chat_id") or "").strip()
    if chat_id and len(chat_id) > 128:
        return api_error("The chat identifier is invalid.")
    user_dir = USER_UPLOAD_DIR / str(user["id"])
    user_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    stored_names: list[str] = []
    for file in files:
        filename = secure_filename(file.filename)
        if not filename.lower().endswith(".pdf"):
            return api_error(f"{file.filename} is not a PDF.")
        stored_name = f"{uuid.uuid4().hex}_{filename}"
        destination = user_dir / stored_name
        file.save(destination)
        if destination.stat().st_size == 0:
            destination.unlink(missing_ok=True)
            return api_error(f"{file.filename} is empty.")
        paths.append(str(destination))
        stored_names.append(stored_name)
    try:
        rag = get_or_create_chat_rag(user["id"], chat_id) if chat_id else get_or_create_rag(user["id"])
        chunks = rag.ingest_pdfs(paths)
        for original_name, stored_name, path in zip([secure_filename(file.filename) for file in files], stored_names, paths):
            add_document(
                user["id"],
                original_name=original_name,
                stored_name=stored_name,
                file_type="PDF",
                size=Path(path).stat().st_size,
                chunk_count=chunks,
            )
        return jsonify({"message": "Documents indexed successfully.", "files": len(paths), "chunks": chunks})
    except Exception as exc:
        for path in paths:
            Path(path).unlink(missing_ok=True)
        return api_error(str(exc), 500)


@app.delete("/api/documents/<int:document_id>")
@login_required
def delete_user_document(document_id: int):
    user = current_user()
    document = get_document(user["id"], document_id)
    if not document:
        return api_error("Document not found.", 404)
    stored_path = USER_UPLOAD_DIR / str(user["id"]) / document["stored_name"]
    stored_path.unlink(missing_ok=True)
    if not delete_document(user["id"], document_id):
        return api_error("Document not found.", 404)
    clear_user_rag(user["id"])
    return jsonify({"message": "Document deleted."})


@app.post("/ask")
@login_required
def ask():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    chat_id = (data.get("chat_id") or "").strip()
    if not question or len(question) > 2000:
        return api_error("Enter a question between 1 and 2,000 characters.")
    if not chat_id:
        return api_error("A chat identifier is required.")
    user = current_user()
    rag = get_or_create_chat_rag(user["id"], chat_id)
    if rag.index is None or not rag.chunks:
        return api_error("Please upload and index at least one PDF before asking a question.")
    try:
        result = rag.answer(question)
        add_question_history(
            user["id"],
            question=question,
            answer=result["answer"],
            route=result.get("route", "unknown"),
            confidence=result.get("confidence", 0.0),
            retrieved_chunks=result.get("retrieved_chunks", 0),
            second_pass=bool(result.get("second_pass", False)),
            sources=result.get("sources", []),
        )
        return jsonify(result)
    except Exception as exc:
        app.logger.exception("RAG request failed")
        return api_error("The question could not be processed. Please try again.", 503)


@app.get("/api/history")
@login_required
def api_history():
    data = request.args.to_dict()
    query = (data.get("search") or "").strip()
    try:
        limit = max(1, min(int(data.get("limit", 20)), 100))
    except ValueError:
        return api_error("History limit must be a number.")
    history = search_question_history(current_user()["id"], query, limit) if query else list_question_history(current_user()["id"], limit)
    return jsonify({"history": history})


@app.delete("/api/history/<int:history_id>")
@login_required
def delete_history(history_id: int):
    if not delete_question_history(current_user()["id"], history_id):
        return api_error("History item not found.", 404)
    return jsonify({"message": "History item deleted."})


@app.delete("/api/history")
@login_required
def clear_history():
    deleted = delete_all_question_history(current_user()["id"])
    return jsonify({"message": f"Deleted {deleted} history items."})


@app.post("/clear")
@login_required
def clear_docs():
    clear_user_rag(current_user()["id"])
    return jsonify({"message": "Session data cleared."})


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "ollama": os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")})


@app.errorhandler(413)
def request_too_large(_):
    return api_error("The file is too large. Maximum size is 50 MB.", 413)


@app.errorhandler(404)
def not_found(_):
    if request.path.startswith("/api/"):
        return api_error("Endpoint not found.", 404)
    return render_template("404.html", page="404"), 404


if __name__ == "__main__":
    app.run(debug=os.getenv("APP_ENV") != "production", host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "5000")))
