"""Private team access through one shared administrator account."""
from __future__ import annotations

from contextlib import contextmanager
import getpass
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from . import store

COOKIE = "pe_session"
SESSION_SECONDS = 8 * 60 * 60
IDLE_SECONDS = 30 * 60
router = APIRouter(prefix="/api/auth")


@contextmanager
def connection():
    db = sqlite3.connect(store.data_root() / "evaluations.sqlite3", timeout=15)
    db.row_factory = sqlite3.Row
    db.executescript('''
        CREATE TABLE IF NOT EXISTS auth_users (
            id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
            must_change INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS auth_sessions (
            token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
            csrf TEXT NOT NULL, expires INTEGER NOT NULL, last_seen INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS auth_attempts (
            scope TEXT NOT NULL, attempted_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS auth_attempt_scope ON auth_attempts(scope,attempted_at);
        CREATE TABLE IF NOT EXISTS auth_events (
            id INTEGER PRIMARY KEY, occurred_at INTEGER NOT NULL,
            actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL
        );
    ''')
    try:
        with db:
            yield db
    finally:
        db.close()


def audit(db, actor, action, target=""):
    db.execute("INSERT INTO auth_events(occurred_at,actor,action,target) VALUES(?,?,?,?)",
               (int(time.time()), actor, action, target))


def password_hash(password):
    if len(password) < 12 or len(password) > 128:
        raise HTTPException(400, "密码须为 12–128 个字符")
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=3, maxmem=64*1024*1024).hex()
    return f"scrypt${salt}${digest}"


def verify_password(password, encoded):
    try:
        algorithm, salt, expected = encoded.split("$")
        if algorithm != "scrypt" or len(password) > 128:
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=3, maxmem=64*1024*1024).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


# Equal-cost work for unknown usernames; contains no usable account credential.
DUMMY_PASSWORD = password_hash(secrets.token_urlsafe(32))


def create_user(username, password, actor="bootstrap"):
    username = username.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", username):
        raise HTTPException(400, "账号须为 3–40 位字母、数字、点、横线或下划线")
    encoded = password_hash(password)
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT COUNT(*) FROM auth_users").fetchone()[0]:
            raise HTTPException(400, "团队共用账号已初始化，无需创建其他账号")
        try:
            db.execute("INSERT INTO auth_users(id,username,password_hash,created_at) VALUES(?,?,?,?)",
                       (secrets.token_hex(16), username, encoded, int(time.time())))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(400, "账号已存在") from exc
        audit(db, actor, "create_user", username)


def authorize(request: Request):
    token = request.cookies.get(COOKIE, "")
    if not token or len(token) > 200:
        raise HTTPException(401, "请先登录")
    now = int(time.time())
    with connection() as db:
        row = db.execute('''SELECT u.*,s.csrf,s.token_hash,s.expires,s.last_seen
            FROM auth_sessions s JOIN auth_users u ON u.id=s.user_id
            WHERE s.token_hash=? AND u.id=(SELECT id FROM auth_users ORDER BY created_at,id LIMIT 1)''', (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not row or not row["active"] or row["expires"] <= now or row["last_seen"] <= now-IDLE_SECONDS:
            raise HTTPException(401, "登录已失效，请重新登录")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if not hmac.compare_digest(request.headers.get("X-CSRF-Token", "").encode(), row["csrf"].encode()):
                raise HTTPException(403, "请求验证失败，请刷新页面重试")
        if row["must_change"] and request.url.path not in {"/api/auth/me", "/api/auth/password", "/api/auth/logout"}:
            raise HTTPException(403, "请先修改初始密码")
        db.execute("UPDATE auth_sessions SET last_seen=? WHERE token_hash=?", (now, row["token_hash"]))
        return dict(row)


async def access_control(request: Request, call_next):
    path = request.url.path
    protected = path.startswith("/api/") or path in {"/docs", "/redoc", "/openapi.json"}
    public = path in {"/api/health", "/api/auth/login"}
    try:
        if protected and not public:
            request.state.user = authorize(request)
        # Same-origin login prevents login CSRF; do not trust arbitrary forwarded headers.
        if path == "/api/auth/login" and request.method == "POST":
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                raise HTTPException(403, "请从本站登录页面操作")
        response = await call_next(request)
        if protected:
            response.headers["Cache-Control"] = "no-store"
        if hasattr(request.state, "user") and request.method not in {"GET", "HEAD", "OPTIONS"}:
            with connection() as db:
                audit(db, request.state.user["username"], f"{request.method} {response.status_code}", path)
        return response
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers={"Cache-Control": "no-store"})


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=128)


@router.post("/login")
def login(body: Credentials, request: Request):
    now = int(time.time())
    ip = request.client.host if request.client else "unknown"
    scopes = ["ip:"+ip, "user:"+body.username.strip()]
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM auth_attempts WHERE attempted_at<?", (now-900,))
        for scope, limit in zip(scopes, (30, 10)):
            count = db.execute("SELECT COUNT(*) FROM auth_attempts WHERE scope=?", (scope,)).fetchone()[0]
            if count >= limit:
                raise HTTPException(429, "登录尝试过多，请 15 分钟后重试")
        for scope in scopes:
            db.execute("INSERT INTO auth_attempts VALUES(?,?)", (scope, now))
        row = db.execute("SELECT * FROM auth_users WHERE username=? AND id=(SELECT id FROM auth_users ORDER BY created_at,id LIMIT 1)", (body.username.strip(),)).fetchone()
        valid = verify_password(body.password, row["password_hash"] if row else DUMMY_PASSWORD)
        if not row or not valid or not row["active"]:
            audit(db, body.username.strip(), "login_failed")
            # Commit failed attempts before returning an error.
            db.commit()
            raise HTTPException(401, "账号或密码错误")
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        db.execute("DELETE FROM auth_attempts WHERE scope=?", (scopes[1],))
        db.execute("DELETE FROM auth_sessions WHERE expires<=? OR last_seen<=?", (now, now-IDLE_SECONDS))
        db.execute("INSERT INTO auth_sessions VALUES(?,?,?,?,?)",
                   (hashlib.sha256(token.encode()).hexdigest(), row["id"], csrf, now+SESSION_SECONDS, now))
        audit(db, row["username"], "login")
    response = JSONResponse({"username": row["username"], "must_change": bool(row["must_change"]), "csrf": csrf})
    secure = os.environ.get("PRODUCT_EVAL_COOKIE_SECURE", "true").lower() != "false"
    response.set_cookie(COOKIE, token, httponly=True, secure=secure, samesite="strict", max_age=SESSION_SECONDS, path="/")
    return response


@router.get("/me")
def me(request: Request):
    user = request.state.user
    return {"username": user["username"], "must_change": bool(user["must_change"]), "csrf": user["csrf"]}


@router.post("/logout")
def logout(request: Request):
    with connection() as db:
        db.execute("DELETE FROM auth_sessions WHERE token_hash=?", (request.state.user["token_hash"],))
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE, path="/")
    return response


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: str = Field(max_length=128)


@router.post("/password")
def change_password(body: PasswordChange, request: Request):
    user = request.state.user
    if not verify_password(body.current_password, user["password_hash"]):
        raise HTTPException(400, "原密码不正确")
    if body.current_password == body.new_password:
        raise HTTPException(400, "新密码不能与原密码相同")
    encoded = password_hash(body.new_password)
    with connection() as db:
        db.execute("UPDATE auth_users SET password_hash=?,must_change=0 WHERE id=?", (encoded, user["id"]))
        db.execute("DELETE FROM auth_sessions WHERE user_id=?", (user["id"],))
    return {"ok": True, "message": "密码已修改，请重新登录"}


def reset_password(username, password):
    """Server-local recovery only; no public bootstrap or reset endpoint."""
    encoded = password_hash(password)
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        user = db.execute("SELECT id,username FROM auth_users ORDER BY created_at,id LIMIT 1").fetchone()
        if not user or user["username"] != username:
            raise HTTPException(400, "共享账号不存在，请确认原账号名称")
        db.execute("UPDATE auth_users SET password_hash=?,must_change=1,active=1 WHERE id=?", (encoded, user["id"]))
        db.execute("DELETE FROM auth_sessions WHERE user_id=?", (user["id"],))
        db.execute("DELETE FROM auth_attempts WHERE scope=?", ("user:"+username,))
        audit(db, "server-console", "reset_shared_password", username)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="在服务器本机初始化团队共享账号；不开放网页注册")
    parser.add_argument("username")
    parser.add_argument("--reset-password", action="store_true", help="忘记密码时重置已有共享账号")
    args = parser.parse_args()
    password = getpass.getpass("初始密码（至少12字符）: ")
    if password != getpass.getpass("再次输入: "):
        parser.error("两次输入不一致")
    try:
        if args.reset_password:
            reset_password(args.username, password)
        else:
            create_user(args.username, password)
    except HTTPException as exc:
        parser.error(exc.detail)
    print("共享账号已就绪，首次登录需修改初始密码。")
