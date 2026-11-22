"""Wires up the prefetch feature: builds a backend for every configured media
server, watches each one's playback sessions, and drives a Sonarr prefetch
Actor per server. One server failing does not stop the others."""

import asyncio
import logging

import aiohttp

from core.config import CONFIG, MediaServer
from prefetch.actor import Actor
from prefetch.mediaserver import jellyfin_emby, plex, silo, tautulli
from prefetch.mediaserver.base import HasPendingFlag, libraries_filter, users_filter
from sonarr.client import Client as SonarrClient

log = logging.getLogger("prefetch_service")


def _build_media_client(server: MediaServer, session: aiohttp.ClientSession):
    """Return (client, queue) for one configured media server; queue is None
    when the backend can't append to a player's queue or it's turned off."""
    server_type = server.type
    if server_type in (jellyfin_emby.JELLYFIN, jellyfin_emby.EMBY):
        client = jellyfin_emby.Client(server.url, server.api_key, server_type, session)
        queue = client if CONFIG.PREFETCH_APPEND_TO_QUEUE else None
        return client, queue
    if server_type == "plex":
        client = plex.Client(server.url, server.api_key, session)
        queue = client if CONFIG.PREFETCH_APPEND_TO_QUEUE else None
        return client, queue
    if server_type in ("tautulli", "silo"):
        client = (tautulli if server_type == "tautulli" else silo).Client(server.url, server.api_key, session)
        if CONFIG.PREFETCH_APPEND_TO_QUEUE:
            log.warning(
                "append_to_queue is not supported with %s. "
                "The feature will be a no-op for this backend.",
                server.name,
            )
        return client, None
    raise ValueError(f"unknown media server type for {server.name}: {server_type!r}")


async def _watch_server(server: MediaServer, session: aiohttp.ClientSession, sonarr: SonarrClient) -> None:
    """Probe one media server and feed its sessions to its own Actor. Runs
    until cancelled; a failure here is logged and does not stop the other
    servers."""
    media_client, queue = _build_media_client(server, session)
    await media_client.probe_with_retry(CONFIG.PREFETCH_CONNECTION_RETRIES)
    log.info("Connected to %s at %s", server.name, server.url)
    log.info("Start watching %s sessions", server.name)

    has_pending = HasPendingFlag()
    actor = Actor(
        sonarr=sonarr,
        prefetch_num=CONFIG.PREFETCH_NUM,
        request_seasons=CONFIG.PREFETCH_REQUEST_SEASONS,
        exclude_tag_label=CONFIG.PREFETCH_EXCLUDE_TAG,
        check_aired=CONFIG.PREFETCH_CHECK_AIRED,
        queue=queue,
        has_pending=has_pending,
        pending_ttl=CONFIG.PREFETCH_INTERVAL * 2 + 60,
    )
    user_filter = users_filter(server.users)
    library_filter = libraries_filter(server.libraries)

    async def watch_sessions():
        async for np in media_client.now_playing_updates(CONFIG.PREFETCH_INTERVAL, has_pending):
            if not user_filter(np):
                continue
            if not library_filter(np):
                continue
            try:
                await actor.prefetch(np)
            except Exception as e:
                log.error("[%s] Failed to process %s: %s", server.name, np, e)

    await asyncio.gather(watch_sessions(), media_client.run())


async def _watch_server_safely(server: MediaServer, session, sonarr) -> None:
    try:
        await _watch_server(server, session, sonarr)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.error("Stopped watching %s at %s: %s", server.name, server.url, e)


async def run(session: aiohttp.ClientSession) -> None:
    if not CONFIG.ENABLE_PREFETCH:
        return

    if not (CONFIG.SONARR_URL and CONFIG.SONARR_API_KEY):
        log.error("ENABLE_PREFETCH is set but SONARR_URL/SONARR_API_KEY are missing")
        return

    servers = [s for s in CONFIG.MEDIA_SERVERS if s.api_key]
    for s in CONFIG.MEDIA_SERVERS:
        if not s.api_key:
            log.error("Skipping %s at %s: no API key set", s.name, s.url)
    if not servers:
        log.error("ENABLE_PREFETCH is set but no media server has both a URL and an API key")
        return

    sonarr = SonarrClient(CONFIG.SONARR_URL, CONFIG.SONARR_API_KEY, session, CONFIG.SONARR_API_VERSION)
    from core.retry import retry

    await retry(CONFIG.PREFETCH_CONNECTION_RETRIES, sonarr.probe)
    log.info("Connected to Sonarr at %s", CONFIG.SONARR_URL)
    log.info("Watching %d media server(s): %s", len(servers), ", ".join(s.name for s in servers))

    await asyncio.gather(*(_watch_server_safely(s, session, sonarr) for s in servers))
