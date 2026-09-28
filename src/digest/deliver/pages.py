"""Rebuild the static site from every saved digest."""

from pathlib import Path

from digest.config import Config
from digest.render import build_site
from digest.state import load_digests


def build_pages(cfg: Config, digests_dir: Path, site_dir: Path) -> None:
    build_site(
        load_digests(digests_dir),
        site_dir,
        base_url=cfg.delivery.pages.base_url,
        language=cfg.ai.output_language,
    )
