"""Data models shared by every pipeline step."""

import hashlib
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


def item_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


class Item(BaseModel):
    id: str  # sha1 of the canonical URL
    source: str  # "hn" | "github" | "lobsters" | "reddit" | "rss:<name>"
    title: str
    url: str  # canonical URL
    discussion_url: str | None = None  # HN / Reddit / Lobsters discussion link
    score: int  # source-native score (points, stars today, ...)
    comments: int = 0
    published_at: datetime
    tags: list[str] = Field(default_factory=list)
    also_on: list[str] = Field(default_factory=list)  # other sources that had the same URL


class ScoredItem(Item):
    relevance: float  # 0-10; phase 1 = heuristic, phase 2 = AI
    reason: str | None = None
    summary: str | None = None
    why_it_matters: str | None = None


Ecosystem = Literal["npm", "PyPI", "Go"]  # OSV ecosystem names


class Dependency(BaseModel):
    ecosystem: Ecosystem
    name: str
    version: str | None  # pinned or minimum version from the manifest; None if unknown
    manifests: list[str] = Field(default_factory=list)  # "owner/repo:path" that declare it

    @property
    def key(self) -> str:
        return f"{self.ecosystem}:{self.name}"


class Digest(BaseModel):
    date: date
    items: list[ScoredItem]
    stats: dict[str, int | str] = Field(default_factory=dict)
