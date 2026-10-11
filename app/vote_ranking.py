"""Pure ranking rules for voting rounds: score, order with tie-breaks, winner."""

from typing import Callable, Protocol, TypeVar

T = TypeVar("T")

LOVE, OK, NOGO = 2, 1, -3


class _Counts(Protocol):
    love: int
    ok: int
    nogo: int


def score(counts: _Counts) -> int:
    return LOVE * counts.love + OK * counts.ok + NOGO * counts.nogo


def rank(items: list[T], counts_of: Callable[[T], _Counts], position_of: Callable[[T], int]) -> list[T]:
    """New list: score desc, then fewer nogos, then more loves, then earlier position."""

    def key(item: T):
        c = counts_of(item)
        return (-score(c), c.nogo, -c.love, position_of(item))

    return sorted(items, key=key)


def winner(ranked: list[T], counts_of: Callable[[T], _Counts]) -> T | None:
    """The first item (of an already ranked list) with at least one vote, else None."""
    for item in ranked:
        c = counts_of(item)
        if c.love + c.ok + c.nogo > 0:
            return item
    return None
