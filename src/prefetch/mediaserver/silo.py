"""Silo backend (https://github.com/Silo-Server/silo-server).

Silo serves a Jellyfin-compatible API, but its `/Sessions` only lists the
calling token's own plays and replaces file paths with stand-ins, so the
Jellyfin backend cannot see other viewers or match libraries. This backend
uses Silo's native `/api/v2` admin API instead, which needs an API key
(`sa_...`) owned by an administrator account:

- `GET /api/v2/admin/sessions` -- live playback across every account.
- `GET /api/v2/admin/catalog/items?content_ids=` -- the episode's series and
  file versions, and the series' TVDB id.
- `GET /api/v2/libraries` -- library names, for MEDIA_SERVER_LIBRARIES.

Silo has no play-queue API for third parties, so `append_to_queue` is not
supported.
"""

import logging
import time
from typing import Optional

import aiohttp

from prefetch.mediaserver.base import Client as BaseClient
from prefetch.mediaserver.base import NowPlaying, Series, User

log = logging.getLogger("media_server.silo")

API = "api/v2"
MAX_SESSION_PAGES = 20
MAX_CATALOG_PAGES = 100
SERIES_INDEX_TTL = 60 * 60  # seconds


def _series_title(session: dict):
    if session.get("series_title"):
        return session["series_title"]
    if session.get("media_type") == "series":
        return session.get("media_title")
    return None


class Client(BaseClient):
    def __init__(self, base_url: str, api_key: str, session: aiohttp.ClientSession):
        self.base_url = base_url.rstrip("/")
        self.session = session
        self._headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        self._series_cache: dict = {}
        self._library_names: dict = {}
        self._series_index: dict = {}
        self._series_index_at: Optional[float] = None

    async def _get(self, path: str, params=None):
        url = f"{self.base_url}/{API}/{path}"
        async with self.session.get(url, headers=self._headers, params=params) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def probe(self) -> None:
        await self._get("admin/sessions/capabilities")

    async def sessions(self) -> list:
        valid = []
        cursor: Optional[str] = None
        for _ in range(MAX_SESSION_PAGES):
            params = {"limit": "200"}
            if cursor:
                params["cursor"] = cursor
            data = await self._get("admin/sessions", params)
            for s in data.get("items") or []:
                # An episode Silo has no episode title for is reported with
                # its series' fields: media_type "series", the series title
                # in media_title and the series' content id. Season and
                # episode numbers are still set, so accept both shapes.
                if (
                    s.get("media_type") in ("episode", "series")
                    and isinstance(s.get("season_number"), int)
                    and isinstance(s.get("episode_number"), int)
                    and isinstance(_series_title(s), str)
                    and isinstance(s.get("profile_id"), str)
                ):
                    valid.append(s)
                else:
                    log.debug(
                        "ignoring session %s: media_type=%r media_item_id=%r season=%r episode=%r title=%r",
                        s.get("session_id"), s.get("media_type"), s.get("media_item_id"),
                        s.get("season_number"), s.get("episode_number"), _series_title(s),
                    )
            page = data.get("page") or {}
            cursor = page.get("next_cursor")
            if not (page.get("has_more") and cursor):
                break
        log.debug("silo reports %d playing episode(s)", len(valid))
        return valid

    async def _catalog_item(self, content_id: str) -> Optional[dict]:
        data = await self._get("admin/catalog/items", {"content_ids": content_id})
        for item in data.get("items") or []:
            if item.get("content_id") == content_id:
                return item
        return None

    async def _series(self, series_id: str, title: str, item: Optional[dict] = None) -> Series:
        cached = self._series_cache.get(series_id)
        if cached is not None:
            return cached
        if item is None:
            item = await self._catalog_item(series_id)
        tvdb = (item or {}).get("tvdb_id")
        try:
            series = Series.tvdb(int(tvdb)) if tvdb else Series.title(title)
        except ValueError:
            series = Series.title(title)
        self._series_cache[series_id] = series
        return series

    async def _series_by_title(self, title: str) -> Optional[dict]:
        """Find a series item by title, for sessions Silo reports without a
        media_item_id. The index of every series is cached for an hour."""
        now = time.monotonic()
        stale = self._series_index_at is None or now - self._series_index_at > SERIES_INDEX_TTL
        key = title.casefold()
        if stale or key not in self._series_index:
            if not stale and self._series_index_at is not None and now - self._series_index_at < 60:
                return None  # rebuilt under a minute ago; the title is not there
            index: dict = {}
            cursor: Optional[str] = None
            for _ in range(MAX_CATALOG_PAGES):
                params = {"type": "series", "limit": "200"}
                if cursor:
                    params["cursor"] = cursor
                data = await self._get("admin/catalog/items", params)
                for item in data.get("items") or []:
                    if item.get("title"):
                        index.setdefault(item["title"].casefold(), item)
                page = data.get("page") or {}
                cursor = page.get("next_cursor")
                if not (page.get("has_more") and cursor):
                    break
            self._series_index = index
            self._series_index_at = now
        return self._series_index.get(key)

    async def _library_name(self, library_id) -> Optional[str]:
        if library_id is None:
            return None
        key = str(library_id)
        if key not in self._library_names:
            data = await self._get("libraries")
            self._library_names = {
                str(lib.get("id")): lib.get("name") for lib in data.get("items") or []
            }
        return self._library_names.get(key)

    async def extract(self, session: dict) -> NowPlaying:
        title = _series_title(session)
        if session.get("media_item_id"):
            episode_item = await self._catalog_item(session["media_item_id"])
        else:
            # Silo can report a play without a content id; find the series
            # by title instead.
            episode_item = await self._series_by_title(title)
            if episode_item is None:
                log.info("Silo series %r not found in the catalog; matching Sonarr by title", title)

        series = Series.title(title)
        library_id = None
        if episode_item is not None:
            if episode_item.get("type") == "series":
                # The session named the series itself; it carries the TVDB id.
                series = await self._series(episode_item["content_id"], title, episode_item)
            elif episode_item.get("series_id"):
                series = await self._series(episode_item["series_id"], title)
            file_id = str(session.get("media_file_id"))
            versions = episode_item.get("versions") or []
            match = next((v for v in versions if str(v.get("file_id")) == file_id), None)
            if match is not None:
                library_id = match.get("library_id")
            elif episode_item.get("library_ids"):
                library_id = episode_item["library_ids"][0]

        library = await self._library_name(library_id)
        name = session.get("profile_name") or session.get("username") or ""

        return NowPlaying(
            series=series,
            episode=session["episode_number"],
            season=session["season_number"],
            user=User(id=session["profile_id"], name=name),
            library=library,
            session_id=None,
        )
