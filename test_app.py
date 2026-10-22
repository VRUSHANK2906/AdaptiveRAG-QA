import io
import tempfile
from pathlib import Path

import app as app_module
import database as database_module


def setup_module():
    app_module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    temporary_directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    app_module.DATABASE_PATH = Path(temporary_directory.name) / "test-app.db"
    database_module.DATABASE_PATH = app_module.DATABASE_PATH
    database_module.init_db()
    app_module._test_temporary_directory = temporary_directory


def teardown_module():
    app_module._test_temporary_directory.cleanup()


def client():
    return app_module.app.test_client()


def test_authentication_and_isolation():
    with client() as c:
        assert c.get("/dashboard").status_code == 302
        assert c.get("/register").status_code == 200
        assert c.post("/api/register", json={"name":"Ada Lovelace", "email":"ada@example.com", "password":"Password1", "confirmPassword":"Password1"}).status_code == 200
        assert c.post("/api/register", json={"name":"Ada Lovelace", "email":"ada@example.com", "password":"Password1", "confirmPassword":"Password1"}).status_code == 409
        assert c.get("/api/user").get_json()["user"]["email"] == "ada@example.com"
        assert c.post("/api/logout").status_code == 200
        assert c.get("/dashboard").status_code == 302
        assert c.post("/api/login", json={"email":"ada@example.com", "password":"Password1"}).status_code == 200
        assert c.get("/dashboard").status_code == 200
        assert c.post("/api/login", json={"email":"ada@example.com", "password":"wrong"}).status_code == 401
        assert c.get("/api/settings").get_json()["settings"]["theme"] == "system"
        assert c.put("/api/settings", json={"theme":"dark", "notifications":{"login":False,"documents":True,"questions":True,"errors":True}}).status_code == 200
        assert c.get("/api/settings").get_json()["settings"]["theme"] == "dark"
        assert c.post("/api/profile", json={"name":"Ada L.", "email":"ada@example.com"}).status_code == 200
        assert c.post("/api/password", json={"currentPassword":"Password1", "newPassword":"NewPassword2", "confirmPassword":"NewPassword2"}).status_code == 200
        assert c.post("/api/logout").status_code == 200


def test_password_reset_token_is_single_use():
    with client() as c:
        c.post("/api/register", json={"name":"Grace Hopper", "email":"grace@example.com", "password":"Password1", "confirmPassword":"Password1"})
        with app_module.app.test_request_context("/forgot-password"):
            token = database_module.create_password_reset_token(database_module.get_user_by_email("grace@example.com")["id"])
        stored = database_module.get_db().execute(
            "SELECT token_hash, used_at, expires_at FROM password_reset_tokens WHERE token_hash = ?",
            (database_module.hashlib.sha256(token.encode()).hexdigest(),),
        ).fetchone()
        assert stored is not None and stored["used_at"] is None and float(stored["expires_at"]) > database_module.datetime.now(database_module.timezone.utc).timestamp()
        response = c.get(f"/reset-password/{token}")
        assert response.status_code == 200
        assert c.post("/api/reset-password", json={"token": token, "password":"NewPassword3", "confirmPassword":"NewPassword3"}).status_code == 200
        assert c.post("/api/reset-password", json={"token": token, "password":"AnotherPassword4", "confirmPassword":"AnotherPassword4"}).status_code == 400


def test_user_isolated_documents_and_history(monkeypatch):
    class IngestingRag:
        @staticmethod
        def ingest_pdfs(paths):
            assert len(paths) == 1
            return 3

    monkeypatch.setattr(app_module, "get_or_create_rag", lambda user_id: IngestingRag())
    with client() as c:
        c.post("/api/register", json={"name":"Alice", "email":"alice@example.com", "password":"Password1", "confirmPassword":"Password1"})
        c.post("/api/register", json={"name":"Bob", "email":"bob@example.com", "password":"Password1", "confirmPassword":"Password1"})
        assert c.post("/api/login", json={"email":"alice@example.com", "password":"Password1"}).status_code == 200
        response = c.post("/upload", data={"files": (io.BytesIO(b"not a pdf"), "sample.txt")})
        assert response.status_code == 400
        assert response.get_json()["error"] == "sample.txt is not a PDF."
        response = c.post("/upload", data={"files": (io.BytesIO(b"valid pdf bytes"), "sample.pdf")})
        assert response.status_code == 200
        assert response.get_json()["chunks"] == 3
        documents = c.get("/api/documents").get_json()["documents"]
        assert len(documents) == 1 and documents[0]["original_name"] == "sample.pdf"
        assert c.post("/api/profile", json={"name":"Alice", "email":"alice@example.com"}).status_code == 200
        assert c.post("/api/logout").status_code == 200
        assert c.post("/api/login", json={"email":"bob@example.com", "password":"Password1"}).status_code == 200
        assert c.get("/api/documents").get_json()["documents"] == []
        assert c.post("/api/logout").status_code == 200


def test_rag_rejects_unindexed_question_without_ollama():
    with client() as c:
        c.post("/api/register", json={"name":"Rag User", "email":"rag@example.com", "password":"Password1", "confirmPassword":"Password1"})
        response = c.post("/ask", json={"question":"What is in the document?", "chat_id":"missing"})
        assert response.status_code == 400
        assert "upload and index" in response.get_json()["error"]


def test_each_chat_uses_only_its_uploaded_pdf(monkeypatch):
    class FakeRag:
        def __init__(self, document):
            self.document = document
            self.index = object()
            self.chunks = [{"id": 0, "source": document, "page": 1, "text": document, "score": 1.0}]

        def ingest_pdfs(self, paths):
            self.chunks = [{"id": 0, "source": Path(paths[0]).name, "page": 1, "text": Path(paths[0]).name, "score": 1.0}]
            return 1

        def answer(self, question):
            return {
                "answer": self.chunks[0]["text"],
                "route": "simple/factual",
                "confidence": 1.0,
                "retrieved_chunks": 1,
                "sources": [{"file": self.chunks[0]["source"], "page": 1, "score": 1.0}],
            }

    chat_rags = {}

    def create_chat_rag(user_id, chat_id):
        if chat_id not in chat_rags:
            chat_rags[chat_id] = FakeRag(chat_id)
        return chat_rags[chat_id]

    monkeypatch.setattr(app_module, "get_or_create_chat_rag", create_chat_rag)
    with client() as c:
        c.post("/api/register", json={"name":"Chat User", "email":"chat@example.com", "password":"Password1", "confirmPassword":"Password1"})
        first = c.post("/upload", data={"chat_id":"chat-one", "files": (io.BytesIO(b"apple"), "apple.pdf")})
        second = c.post("/upload", data={"chat_id":"chat-two", "files": (io.BytesIO(b"netflix"), "netflix.pdf")})
        assert first.status_code == 200 and second.status_code == 200
        first_answer = c.post("/ask", json={"chat_id":"chat-one", "question":"What is this?"})
        second_answer = c.post("/ask", json={"chat_id":"chat-two", "question":"What is this?"})
        assert first_answer.get_json()["answer"].endswith("_apple.pdf")
        assert second_answer.get_json()["answer"].endswith("_netflix.pdf")
