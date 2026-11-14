"""Enrichment of new items: og:image, description, reading time, language (PLAN §13 step 4).

Respects robots.txt and never persists the full article text."""

from digest.enrich.core import enrich, enrich_many
from digest.enrich.model import Enrichment
from digest.enrich.robots import RobotsCache

__all__ = ["Enrichment", "RobotsCache", "enrich", "enrich_many"]
