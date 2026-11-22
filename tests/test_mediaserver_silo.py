import re

import aiohttp
from aioresponses import aioresponses

from prefetch.mediaserver import silo
from prefetch.mediaserver.base import Series

BASE = "http://silo"
SESSIONS = re.compile(r"^http://silo/api/v2/admin/sessions\?.*$")


def _catalog(content_id: str) -> re.Pattern:
    return re.compile(rf"^http://silo/api/v2/admin/catalog/items\?content_ids={re.escape(content_id)}$")


def _session(**overrides):
    s = {
        "session_id": "s1",
        "user_id": "3",
        "username": "alice",
        "profile_id": "p-alice",
        "profile_name": "Alice",
        "media_file_id": "120",
        "media_item_id": "ep-1",
        "media_type": "episode",
        "series_title": "Test Show",
        "season_number": 2,
        "episode_number": 5,
    }
    s.update(overrides)
    return s


async def test_extract_episode_with_tvdb_and_library():
    with aioresponses() as m:
        m.get(SESSIONS, payload={"items": [_session()], "page": {"has_more": False}})
        m.get(
            _catalog("ep-1"),
            payload={
                "items": [
                    {
                        "content_id": "ep-1",
                        "type": "episode",
                        "series_id": "series-1",
                        "library_ids": ["9", "4"],
                        "versions": [{"file_id": "99", "library_id": "9"}, {"file_id": "120", "library_id": "4"}],
                    }
                ]
            },
        )
        m.get(_catalog("series-1"), payload={"items": [{"content_id": "series-1", "tvdb_id": "1234"}]})
        m.get(f"{BASE}/api/v2/libraries", payload={"items": [{"id": "4", "name": "TV Shows"}, {"id": "9", "name": "Anime"}]})

        async with aiohttp.ClientSession() as session:
            client = silo.Client(BASE, "sa_secret", session)
            sessions = await client.sessions()
            assert len(sessions) == 1
            np = await client.extract(sessions[0])

            req = next(iter(m.requests.values()))[0]
            assert req.kwargs["headers"]["Authorization"] == "Bearer sa_secret"

    assert np.series == Series.tvdb(1234)
    assert np.season == 2
    assert np.episode == 5
    assert np.library == "TV Shows"
    assert np.user.id == "p-alice"
    assert np.user.name == "Alice"
    assert np.session_id is None


async def test_falls_back_to_title_without_tvdb():
    with aioresponses() as m:
        m.get(_catalog("ep-1"), payload={"items": [{"content_id": "ep-1", "series_id": "series-1", "library_ids": [], "versions": []}]})
        m.get(_catalog("series-1"), payload={"items": [{"content_id": "series-1"}]})

        async with aiohttp.ClientSession() as session:
            client = silo.Client(BASE, "sa_secret", session)
            np = await client.extract(_session(profile_name=None))

    assert np.series == Series.title("Test Show")
    assert np.library is None
    assert np.user.name == "alice"


async def test_series_lookup_is_cached():
    with aioresponses() as m:
        ep = {"items": [{"content_id": "ep-1", "series_id": "series-1", "library_ids": [], "versions": []}]}
        m.get(_catalog("ep-1"), payload=ep, repeat=True)
        m.get(_catalog("series-1"), payload={"items": [{"content_id": "series-1", "tvdb_id": "77"}]})

        async with aiohttp.ClientSession() as session:
            client = silo.Client(BASE, "sa_secret", session)
            first = await client.extract(_session())
            second = await client.extract(_session())

    assert first.series == second.series == Series.tvdb(77)


async def test_non_episodes_and_malformed_sessions_are_skipped():
    with aioresponses() as m:
        m.get(
            SESSIONS,
            payload={
                "items": [
                    _session(media_type="movie", season_number=None, episode_number=None),
                    {"session_id": "broken"},
                    _session(session_id="ok"),
                ],
                "page": {"has_more": False},
            },
        )
        async with aiohttp.ClientSession() as session:
            client = silo.Client(BASE, "sa_secret", session)
            sessions = await client.sessions()

    assert [s["session_id"] for s in sessions] == ["ok"]


async def test_sessions_follow_pagination():
    with aioresponses() as m:
        m.get(SESSIONS, payload={"items": [_session(session_id="a")], "page": {"has_more": True, "next_cursor": "c2"}})
        m.get(SESSIONS, payload={"items": [_session(session_id="b")], "page": {"has_more": False}})
        async with aiohttp.ClientSession() as session:
            client = silo.Client(BASE, "sa_secret", session)
            sessions = await client.sessions()

    assert [s["session_id"] for s in sessions] == ["a", "b"]


async def test_probe_hits_capabilities():
    with aioresponses() as m:
        m.get(f"{BASE}/api/v2/admin/sessions/capabilities", payload={"available": True})
        async with aiohttp.ClientSession() as session:
            await silo.Client(BASE, "sa_secret", session).probe()


async def test_episode_reported_as_series_is_accepted():
    """Silo reports an episode without its own title with the series'
    fields: media_type "series", the series title in media_title, no
    series_title, and the series' content id as media_item_id."""
    raw = _session(media_type="series", media_title="Dexter", media_item_id="series-9", season_number=2, episode_number=4)
    del raw["series_title"]
    with aioresponses() as m:
        m.get(SESSIONS, payload={"items": [raw], "page": {"has_more": False}})
        m.get(
            _catalog("series-9"),
            payload={"items": [{"content_id": "series-9", "type": "series", "tvdb_id": "79349", "library_ids": ["4"], "versions": []}]},
        )
        m.get(f"{BASE}/api/v2/libraries", payload={"items": [{"id": "4", "name": "TV Shows"}]})

        async with aiohttp.ClientSession() as session:
            client = silo.Client(BASE, "sa_secret", session)
            sessions = await client.sessions()
            assert len(sessions) == 1
            np = await client.extract(sessions[0])

    assert np.series == Series.tvdb(79349)
    assert (np.season, np.episode) == (2, 4)
    assert np.library == "TV Shows"


async def test_movie_is_still_skipped():
    with aioresponses() as m:
        m.get(SESSIONS, payload={"items": [_session(media_type="movie")], "page": {"has_more": False}})
        async with aiohttp.ClientSession() as session:
            assert await silo.Client(BASE, "sa_secret", session).sessions() == []
