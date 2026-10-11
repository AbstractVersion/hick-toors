"""Sign-up / log-in routes and the `current_user` / `require_user` dependencies."""

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.accounts import (COOKIE_NAME, SESSION_DAYS, AccountStore, BadCredentials, LoginLocked, SignupRateLimited,
                          TooManyAccounts, User, UsernameTaken)

LOG_IN_FIRST = "Log in first"


class Credentials(BaseModel):
    username: str = Field(max_length=20)
    password: str = Field(max_length=200)  # oversized passwords never reach scrypt


def current_user(request: Request) -> User | None:
    accounts: AccountStore = request.app.state.accounts
    return accounts.session_user(request.cookies.get(COOKIE_NAME))


def require_user(request: Request) -> User:
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail=LOG_IN_FIRST)
    return user


def auth_router(accounts: AccountStore) -> APIRouter:
    router = APIRouter(prefix="/api/auth")

    def start_session(request: Request, response: Response, username: str) -> dict:
        token = accounts.create_session(username)
        response.set_cookie(COOKIE_NAME, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
                            secure=request.url.scheme == "https", path="/")
        return {"username": username}

    def client_ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    # Sync handlers: sqlite and scrypt run in FastAPI's threadpool.
    @router.post("/signup", status_code=201)
    def signup(body: Credentials, request: Request, response: Response):
        try:
            username = accounts.signup(body.username, body.password, client_ip(request))
        except UsernameTaken:
            raise HTTPException(status_code=409, detail="That username is taken")
        except TooManyAccounts:
            raise HTTPException(status_code=409, detail="No more accounts can be created")
        except SignupRateLimited:
            raise HTTPException(status_code=429, detail="Too many sign-ups – try again later")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return start_session(request, response, username)

    @router.post("/login")
    def login(body: Credentials, request: Request, response: Response):
        try:
            username = accounts.login(body.username, body.password)
        except LoginLocked:
            raise HTTPException(status_code=429, detail="Too many attempts – wait a minute")
        except BadCredentials:
            raise HTTPException(status_code=401, detail="Wrong username or password")
        return start_session(request, response, username)

    @router.post("/logout", status_code=204)
    def logout(request: Request):
        accounts.delete_session(request.cookies.get(COOKIE_NAME))
        response = Response(status_code=204)
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @router.get("/me")
    def me(request: Request):
        user = current_user(request)
        return {"username": user.username if user else None}

    return router
