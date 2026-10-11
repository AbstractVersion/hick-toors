"""Voting-round routes: open / patch / close / cancel a round and cast or clear votes."""

from fastapi import APIRouter, Depends, HTTPException, Request

from app.accounts import User
from app.auth_routes import current_user, require_user
from app.models import RoundIn, RoundPatch, RoundSummary, RoundView, VoteIn, VoteResult
from app.votes_store import NotInRound, NotOpener, RoundAlreadyOpen, RoundClosed, RoundNotFound, RoundStore


def rounds_router(rounds: RoundStore) -> APIRouter:
    router = APIRouter(prefix="/api/rounds")

    def call(fn, *args):
        """Run a store call, mapping its exceptions to HTTP errors."""
        try:
            return fn(*args)
        except RoundNotFound:
            raise HTTPException(status_code=404, detail="Round not found")
        except NotInRound:
            raise HTTPException(status_code=404, detail="Not in this round")
        except (RoundAlreadyOpen, RoundClosed) as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except NotOpener as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    # Sync handlers: sqlite runs in FastAPI's threadpool.
    @router.get("/current", response_model=RoundView | None)
    def current(request: Request):
        return rounds.current(current_user(request))

    @router.get("", response_model=list[RoundSummary])
    def history():
        return rounds.history()

    @router.get("/{round_id}", response_model=RoundView)
    def get_round(round_id: str, request: Request):
        view = rounds.get(round_id, current_user(request))
        if view is None:
            raise HTTPException(status_code=404, detail="Round not found")
        return view

    @router.post("", response_model=RoundView, status_code=201)
    def open_round(body: RoundIn, user: User = Depends(require_user)):
        return call(rounds.open, user, body)

    @router.patch("/{round_id}", response_model=RoundView)
    def patch_round(round_id: str, body: RoundPatch, user: User = Depends(require_user)):
        return call(rounds.patch, round_id, user, body)

    @router.post("/{round_id}/join", response_model=RoundView)
    def join_round(round_id: str, user: User = Depends(require_user)):
        return call(rounds.join, round_id, user)

    @router.post("/{round_id}/close", response_model=RoundView)
    def close_round(round_id: str, user: User = Depends(require_user)):
        return call(rounds.close, round_id, user)

    @router.post("/{round_id}/cancel", response_model=RoundView)
    def cancel_round(round_id: str, user: User = Depends(require_user)):
        return call(rounds.cancel, round_id, user)

    @router.put("/{round_id}/trips/{trip_id}/vote", response_model=VoteResult)
    def vote_trip(round_id: str, trip_id: str, body: VoteIn, user: User = Depends(require_user)):
        return call(rounds.vote_trip, round_id, trip_id, user, body)

    @router.delete("/{round_id}/trips/{trip_id}/vote", response_model=VoteResult)
    def clear_trip_vote(round_id: str, trip_id: str, user: User = Depends(require_user)):
        return call(rounds.clear_trip_vote, round_id, trip_id, user)

    @router.put("/{round_id}/trips/{trip_id}/stays/{stay_id}/vote", response_model=VoteResult)
    def vote_stay(round_id: str, trip_id: str, stay_id: str, body: VoteIn, user: User = Depends(require_user)):
        return call(rounds.vote_stay, round_id, trip_id, stay_id, user, body)

    @router.delete("/{round_id}/trips/{trip_id}/stays/{stay_id}/vote", response_model=VoteResult)
    def clear_stay_vote(round_id: str, trip_id: str, stay_id: str, user: User = Depends(require_user)):
        return call(rounds.clear_stay_vote, round_id, trip_id, stay_id, user)

    return router
