import os
import re
from dataclasses import dataclass, field


def env_bool(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).lower() in ("1", "true", "yes", "on")


def env_int(name: str, default: str = "0") -> int:
    try:
        return int(os.environ.get(name, default))
    except Exception:
        return int(default)


def env_url(name: str) -> str:
    """Read a base URL, trimming whitespace and trailing slashes and adding
    `http://` when no scheme was given (a common slip in Unraid templates)."""
    raw = os.environ.get(name, "").strip().rstrip("/")
    if raw and "://" not in raw:
        raw = f"http://{raw}"
    return raw


def env_list(name: str, default: str = "") -> list:
    raw = os.environ.get(name, default)
    return [v.strip() for v in raw.split(",") if v.strip()]


@dataclass(frozen=True)
class MediaServer:
    """One media server prefetch watches. `name` is how logs refer to it."""

    name: str
    type: str
    url: str
    api_key: str
    users: list = field(default_factory=list)
    libraries: list = field(default_factory=list)


_NUMBERED_SLOT = re.compile(r"^MEDIA_SERVER_(\d+)_(?:TYPE|URL|API_KEY)$")


def media_servers() -> list:
    """Every configured media server. The unnumbered MEDIA_SERVER_* settings
    are server 1; MEDIA_SERVER_2_*, MEDIA_SERVER_3_*, ... add more. Each
    server has its own TYPE, URL, API_KEY, USERS and LIBRARIES. A server
    without a URL is skipped."""
    prefixes = ["MEDIA_SERVER"]
    numbers = sorted(
        {int(m.group(1)) for k in os.environ if (m := _NUMBERED_SLOT.match(k)) and int(m.group(1)) >= 2}
    )
    prefixes += [f"MEDIA_SERVER_{n}" for n in numbers]

    servers = []
    for i, prefix in enumerate(prefixes):
        url = env_url(f"{prefix}_URL")
        if not url:
            continue
        kind = os.environ.get(f"{prefix}_TYPE", "jellyfin").strip().lower()
        servers.append(
            MediaServer(
                name=kind if i == 0 else f"{kind} ({prefix})",
                type=kind,
                url=url,
                api_key=os.environ.get(f"{prefix}_API_KEY", "").strip(),
                users=env_list(f"{prefix}_USERS"),
                libraries=env_list(f"{prefix}_LIBRARIES"),
            )
        )
    return servers


class Config:
    # ---- General ----
    TZ = os.environ.get("TZ", "UTC")
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
    DRY_RUN = env_bool("DRY_RUN", "1")
    WEBHOOK_PORT = env_int("WEBHOOK_PORT", "5099")

    # ---- unmonitor: time-delayed monitoring ----
    SLEEP_MINUTES = env_int("SLEEP_MINUTES", "30")
    DELAY_MINUTES = env_int("DELAY_MINUTES", "120")
    SKIP_IF_FILE = env_bool("SKIP_IF_FILE", "1")
    AUTO_TAG_NAME = os.environ.get("AUTO_TAG_NAME", "auto-unmonitored")
    IGNORE_TAG_NAME = os.environ.get("IGNORE_TAG_NAME", "ignore")

    ENABLE_RADARR = env_bool("ENABLE_RADARR", "1")
    RADARR_URL = env_url("RADARR_URL")
    RADARR_API_KEY = os.environ.get("RADARR_API_KEY", "")
    PREFERRED_RELEASE = os.environ.get("PREFERRED_RELEASE", "either").lower()
    IGNORE_INCINEMAS = env_bool("IGNORE_INCINEMAS", "0")
    RADARR_REMONITOR_WINDOW_DAYS = env_int("RADARR_REMONITOR_WINDOW_DAYS", "30")

    ENABLE_SONARR = env_bool("ENABLE_SONARR", "1")
    SONARR_URL = env_url("SONARR_URL")
    SONARR_API_KEY = os.environ.get("SONARR_API_KEY", "")
    # Sonarr v5 adds a parallel /api/v5 surface alongside the existing
    # /api/v3 one. "auto" (the default) detects which one to use from the
    # connected instance's own reported version (see sonarr.client); set
    # this explicitly to "v3" or "v5" to skip detection entirely.
    SONARR_API_VERSION = os.environ.get("SONARR_API_VERSION", "auto")
    SEASON_PACK_MODE = env_bool("SEASON_PACK_MODE", "0")
    SEASON_PACK_MODE_TAG = os.environ.get("SEASON_PACK_MODE_TAG", "season-pack")
    SONARR_REMONITOR_WINDOW_DAYS = env_int("SONARR_REMONITOR_WINDOW_DAYS", "14")

    # ---- prefetch: predictive episode fetching ----
    # Sonarr connection is shared with the unmonitor feature (SONARR_URL / SONARR_API_KEY
    # above); prefetch only needs its own toggle and behavioural knobs.
    ENABLE_PREFETCH = env_bool("ENABLE_PREFETCH", "0")

    MEDIA_SERVER_TYPE = os.environ.get("MEDIA_SERVER_TYPE", "jellyfin").lower()
    MEDIA_SERVER_URL = env_url("MEDIA_SERVER_URL")
    MEDIA_SERVER_API_KEY = os.environ.get("MEDIA_SERVER_API_KEY", "")
    MEDIA_SERVER_USERS = env_list("MEDIA_SERVER_USERS")
    MEDIA_SERVER_LIBRARIES = env_list("MEDIA_SERVER_LIBRARIES")
    MEDIA_SERVERS = media_servers()

    PREFETCH_INTERVAL = env_int("PREFETCH_INTERVAL", "900")
    PREFETCH_NUM = env_int("PREFETCH_NUM", "2")
    PREFETCH_REQUEST_SEASONS = env_bool("PREFETCH_REQUEST_SEASONS", "1")
    PREFETCH_APPEND_TO_QUEUE = env_bool("PREFETCH_APPEND_TO_QUEUE", "0")
    PREFETCH_CHECK_AIRED = env_bool("PREFETCH_CHECK_AIRED", "0")
    PREFETCH_CONNECTION_RETRIES = env_int("PREFETCH_CONNECTION_RETRIES", "6")
    PREFETCH_EXCLUDE_TAG = os.environ.get("PREFETCH_EXCLUDE_TAG", "") or None

    def __getitem__(self, key):
        return getattr(self, key)

    def __contains__(self, key):
        return hasattr(self, key)


CONFIG = Config()
