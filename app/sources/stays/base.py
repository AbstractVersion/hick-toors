from typing import Protocol

from app.models import Stay, StayQuery


class ProviderUnavailable(Exception):
    """A stay source is blocked or returned something unparsable; the message is shown to the user."""


class StayProvider(Protocol):
    name: str

    async def search(self, q: StayQuery) -> list[Stay]: ...
