"""Fetcher protocol: every source turns its raw data into Items and never raises."""

from datetime import datetime
from typing import Protocol

import httpx

from digest.config import Config
from digest.models import Item


class FetchFn(Protocol):
    async def __call__(
        self, cfg: Config, client: httpx.AsyncClient, now: datetime
    ) -> list[Item]: ...
