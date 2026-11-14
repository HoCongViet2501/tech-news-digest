"""Which database the pipeline may write to.

The URL comes from DIGEST_DB_URL in the process environment only (never from .env,
which holds the production SUPABASE_DB_URL). Outside GitHub Actions a non-local host is
refused unless the caller passes --allow-remote. Neither the URL nor its parts are ever
put into a message: errors only say what is wrong.
"""

from collections.abc import Mapping

from psycopg.conninfo import conninfo_to_dict

DB_URL_ENV = "DIGEST_DB_URL"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
LOCAL_ADDRS = frozenset({"127.0.0.1", "::1"})


class DbUrlError(Exception):
    """The configured DB URL is unusable or not allowed here. Never contains the URL."""


def is_local(url: str) -> bool:
    """True only when every host is loopback or a Unix socket. A missing host counts as
    remote: libpq would then fall back to PGHOST / service files, which could be anywhere."""
    try:
        info = conninfo_to_dict(url)
    except Exception:
        raise DbUrlError(f"{DB_URL_ENV} is not a valid connection string") from None
    if info.get("service"):
        return False
    hosts = [h.strip() for h in str(info.get("host") or "").split(",")]
    if not all(h in LOCAL_HOSTS or h.startswith("/") for h in hosts):
        return False
    addrs = [a.strip() for a in str(info.get("hostaddr") or "").split(",") if a.strip()]
    return all(a in LOCAL_ADDRS for a in addrs)


def resolve_db_url(env: Mapping[str, str], *, allow_remote: bool) -> str | None:
    """The DB URL to use, or None when none is configured.

    Raises DbUrlError for a malformed URL, or a remote one outside GitHub Actions
    without `allow_remote`.
    """
    url = env.get(DB_URL_ENV, "").strip()
    if not url:
        return None
    in_actions = env.get("GITHUB_ACTIONS") == "true"
    if not is_local(url) and not (in_actions or allow_remote):
        raise DbUrlError(
            f"{DB_URL_ENV} points at a non-local database. Outside GitHub Actions this "
            "needs --allow-remote."
        )
    return url
