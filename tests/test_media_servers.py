import asyncio
import importlib
import os

import pytest



@pytest.fixture
def clean_media_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("MEDIA_SERVER"):
            monkeypatch.delenv(k)
    yield monkeypatch


def _servers():
    import core.config as config_module

    importlib.reload(config_module)
    return config_module.CONFIG.MEDIA_SERVERS


def test_no_servers_without_url(clean_media_env):
    assert _servers() == []


def test_single_unnumbered_server_keeps_old_behaviour(clean_media_env):
    clean_media_env.setenv("MEDIA_SERVER_TYPE", "Silo")
    clean_media_env.setenv("MEDIA_SERVER_URL", "192.168.1.235:8080/")
    clean_media_env.setenv("MEDIA_SERVER_API_KEY", "sa_x")
    clean_media_env.setenv("MEDIA_SERVER_USERS", "Alice, Bob")

    [s] = _servers()
    assert (s.name, s.type, s.url, s.api_key) == ("silo", "silo", "http://192.168.1.235:8080", "sa_x")
    assert s.users == ["Alice", "Bob"]
    assert s.libraries == []


def test_numbered_servers_are_added_in_order_with_own_filters(clean_media_env):
    clean_media_env.setenv("MEDIA_SERVER_TYPE", "silo")
    clean_media_env.setenv("MEDIA_SERVER_URL", "http://silo:8080")
    clean_media_env.setenv("MEDIA_SERVER_API_KEY", "sa_x")
    clean_media_env.setenv("MEDIA_SERVER_10_TYPE", "emby")
    clean_media_env.setenv("MEDIA_SERVER_10_URL", "http://emby:8096")
    clean_media_env.setenv("MEDIA_SERVER_10_API_KEY", "e")
    clean_media_env.setenv("MEDIA_SERVER_2_TYPE", "plex")
    clean_media_env.setenv("MEDIA_SERVER_2_URL", "http://plex:32400")
    clean_media_env.setenv("MEDIA_SERVER_2_API_KEY", "p")
    clean_media_env.setenv("MEDIA_SERVER_2_LIBRARIES", "TV Shows")
    clean_media_env.setenv("MEDIA_SERVER_3_TYPE", "jellyfin")  # no URL: skipped

    servers = _servers()
    assert [s.type for s in servers] == ["silo", "plex", "emby"]
    assert servers[1].name == "plex (MEDIA_SERVER_2)"
    assert servers[1].libraries == ["TV Shows"]
    assert servers[1].users == []


def test_numbered_server_without_unnumbered_one(clean_media_env):
    clean_media_env.setenv("MEDIA_SERVER_2_TYPE", "plex")
    clean_media_env.setenv("MEDIA_SERVER_2_URL", "http://plex:32400")
    clean_media_env.setenv("MEDIA_SERVER_2_API_KEY", "p")
    [s] = _servers()
    assert s.type == "plex"


async def test_one_failing_server_does_not_stop_the_others(clean_media_env, monkeypatch):
    from core.config import MediaServer
    from services import prefetch_service

    watched = []

    async def fake_watch(server, session, sonarr):
        if server.type == "plex":
            raise RuntimeError("unreachable")
        watched.append(server.type)

    monkeypatch.setattr(prefetch_service, "_watch_server", fake_watch)
    servers = [
        MediaServer("plex", "plex", "http://plex", "p"),
        MediaServer("silo", "silo", "http://silo", "s"),
    ]
    await asyncio.gather(*(prefetch_service._watch_server_safely(s, None, None) for s in servers))
    assert watched == ["silo"]


def test_build_media_client_types():
    from core.config import MediaServer
    from prefetch.mediaserver import jellyfin_emby, plex, silo, tautulli
    from services import prefetch_service

    expected = {
        "jellyfin": jellyfin_emby.Client,
        "emby": jellyfin_emby.Client,
        "plex": plex.Client,
        "tautulli": tautulli.Client,
        "silo": silo.Client,
    }
    for kind, cls in expected.items():
        client, _ = prefetch_service._build_media_client(MediaServer(kind, kind, "http://x", "k"), None)
        assert isinstance(client, cls)

    with pytest.raises(ValueError):
        prefetch_service._build_media_client(MediaServer("nope", "nope", "http://x", "k"), None)
