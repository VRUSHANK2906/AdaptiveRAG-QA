import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", BASE_DIR / "data" / "app.db"))
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)


def get_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db() -> None:
    with get_db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                original_name TEXT NOT NULL,
                stored_name TEXT NOT NULL UNIQUE,
                file_type TEXT NOT NULL,
                size INTEGER NOT NULL,
                uploaded_at TEXT NOT NULL,
                chunk_count INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS question_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                route TEXT NOT NULL,
                confidence REAL NOT NULL,
                retrieved_chunks INTEGER NOT NULL DEFAULT 0,
                second_pass INTEGER NOT NULL DEFAULT 0,
                sources TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY NOT NULL,
                theme TEXT NOT NULL DEFAULT 'system',
                notifications TEXT NOT NULL DEFAULT '{"login":true,"documents":true,"questions":true,"errors":true}',
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                used_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id);
            CREATE INDEX IF NOT EXISTS idx_question_history_user_id_created_at
                ON question_history(user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_question_history_created_at
                ON question_history(created_at DESC);
            """
        )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dict_row(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def create_user(name: str, email: str, password_hash: str) -> dict[str, Any]:
    timestamp = now_iso()
    with get_db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO users (name, email, password_hash, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name.strip(), email.strip().lower(), password_hash, timestamp, timestamp),
        )
        row = connection.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict_row(row)


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    with get_db() as connection:
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict_row(row) if row else None


def get_user_by_email(email: str) -> dict[str, Any] | None:
    with get_db() as connection:
        row = connection.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
    return dict_row(row) if row else None


def update_user(user_id: int, *, name: str | None = None, email: str | None = None) -> dict[str, Any] | None:
    fields: list[str] = []
    values: list[Any] = []
    if name is not None:
        fields.append("name = ?")
        values.append(name.strip())
    if email is not None:
        fields.append("email = ?")
        values.append(email.strip().lower())
    if not fields:
        return get_user_by_id(user_id)
    fields.append("updated_at = ?")
    values.append(now_iso())
    values.append(user_id)
    with get_db() as connection:
        connection.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict_row(row) if row else None


def change_user_password(user_id: int, password_hash: str) -> None:
    with get_db() as connection:
        connection.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (password_hash, now_iso(), user_id),
        )


def get_user_settings(user_id: int) -> dict[str, Any]:
    with get_db() as connection:
        row = connection.execute(
            "SELECT theme, notifications, updated_at FROM user_settings WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if row:
        return dict_row(row)
    return {"theme": "system", "notifications": {"login": True, "documents": True, "questions": True, "errors": True}, "updated_at": None}


def save_user_settings(user_id: int, *, theme: str, notifications: dict[str, bool]) -> dict[str, Any]:
    with get_db() as connection:
        connection.execute(
            """
            INSERT INTO user_settings (user_id, theme, notifications, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                theme = excluded.theme,
                notifications = excluded.notifications,
                updated_at = excluded.updated_at
            """,
            (user_id, theme, json.dumps(notifications), now_iso()),
        )
    return get_user_settings(user_id)


def list_documents(user_id: int) -> list[dict[str, Any]]:
    with get_db() as connection:
        rows = connection.execute(
            "SELECT * FROM documents WHERE user_id = ? ORDER BY uploaded_at DESC, id DESC",
            (user_id,),
        ).fetchall()
    return [dict_row(row) for row in rows]


def add_document(user_id: int, *, original_name: str, stored_name: str, file_type: str, size: int, chunk_count: int) -> dict[str, Any]:
    timestamp = now_iso()
    with get_db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO documents (user_id, original_name, stored_name, file_type, size, uploaded_at, chunk_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, original_name, stored_name, file_type, size, timestamp, chunk_count),
        )
        row = connection.execute("SELECT * FROM documents WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict_row(row)


def delete_document(user_id: int, document_id: int) -> bool:
    with get_db() as connection:
        cursor = connection.execute(
            "DELETE FROM documents WHERE id = ? AND user_id = ?",
            (document_id, user_id),
        )
    return cursor.rowcount == 1


def get_document(user_id: int, document_id: int) -> dict[str, Any] | None:
    with get_db() as connection:
        row = connection.execute(
            "SELECT * FROM documents WHERE id = ? AND user_id = ?",
            (document_id, user_id),
        ).fetchone()
    return dict_row(row) if row else None


def add_question_history(
    user_id: int,
    *,
    question: str,
    answer: str,
    route: str,
    confidence: float,
    retrieved_chunks: int,
    second_pass: bool,
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    with get_db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO question_history (
                user_id, question, answer, route, confidence, retrieved_chunks,
                second_pass, sources, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                question,
                answer,
                route,
                confidence,
                retrieved_chunks,
                int(second_pass),
                json.dumps(sources),
                now_iso(),
            ),
        )
        row = connection.execute("SELECT * FROM question_history WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict_row(row)


def list_question_history(user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    with get_db() as connection:
        rows = connection.execute(
            "SELECT * FROM question_history WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict_row(row) for row in rows]


def search_question_history(user_id: int, query: str, limit: int = 20) -> list[dict[str, Any]]:
    with get_db() as connection:
        rows = connection.execute(
            """
            SELECT * FROM question_history
            WHERE user_id = ? AND (question LIKE ? OR answer LIKE ? OR route LIKE ?)
            ORDER BY created_at DESC, id DESC LIMIT ?
            """,
            (user_id, f"%{query}%", f"%{query}%", f"%{query}%", limit),
        ).fetchall()
    return [dict_row(row) for row in rows]


def delete_question_history(user_id: int, history_id: int) -> bool:
    with get_db() as connection:
        cursor = connection.execute(
            "DELETE FROM question_history WHERE id = ? AND user_id = ?",
            (history_id, user_id),
        )
    return cursor.rowcount == 1


def delete_all_question_history(user_id: int) -> int:
    with get_db() as connection:
        cursor = connection.execute("DELETE FROM question_history WHERE user_id = ?", (user_id,))
    return cursor.rowcount


def create_password_reset_token(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with get_db() as connection:
        connection.execute(
            """
            INSERT INTO password_reset_tokens (token_hash, user_id, expires_at, used_at, created_at)
            VALUES (?, ?, ?, NULL, ?)
            """,
            (token_hash, user_id, (datetime.now(timezone.utc).timestamp() + 1800), now_iso()),
        )
    return token


def consume_password_reset_token(token: str) -> tuple[dict[str, Any] | None, bool]:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with get_db() as connection:
        row = connection.execute(
            "SELECT * FROM password_reset_tokens WHERE token_hash = ? AND used_at IS NULL AND CAST(expires_at AS REAL) > ?",
            (token_hash, datetime.now(timezone.utc).timestamp()),
        ).fetchone()
        if not row:
            return None, False
        cursor = connection.execute(
            "UPDATE password_reset_tokens SET used_at = ? WHERE token_hash = ? AND used_at IS NULL",
            (now_iso(), token_hash),
        )
        if cursor.rowcount != 1:
            return None, False
        user = get_user_by_id(row["user_id"])
    return user, True


def get_dashboard_stats(user_id: int) -> dict[str, Any]:
    with get_db() as connection:
        question_count = connection.execute(
            "SELECT COUNT(*) FROM question_history WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
        document_count = connection.execute(
            "SELECT COUNT(*) FROM documents WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
        recent = connection.execute(
            "SELECT question, answer, route, confidence, created_at FROM question_history "
            "WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT 5",
            (user_id,),
        ).fetchall()
    return {
        "questions": question_count,
        "documents": document_count,
        "recent_queries": [dict_row(row) for row in recent],
    }
