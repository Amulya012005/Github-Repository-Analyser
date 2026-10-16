import pandas as pd
import pytest

import app as app_module
from app import (app, build_analytics, build_insights, build_language_trend,
                 fetch_all_repos, get_user_data, gh_get, GitHubError)


def make_repo(name, stars, language, created, fork=False, forks=0):
    return {
        "name": name,
        "html_url": f"https://github.com/octocat/{name}",
        "stargazers_count": stars,
        "language": language,
        "created_at": created,
        "fork": fork,
        "forks_count": forks,
    }


PROFILE = {
    "login": "octocat",
    "name": "The Octocat",
    "bio": None,
    "avatar_url": "https://example.com/a.png",
    "followers": 1,
    "following": 2,
    "public_repos": 3,
}

REPOS = [
    make_repo("a", 15, "Python", "2021-03-01T10:00:00Z", forks=3),
    make_repo("b", 5, "Python", "2022-05-01T10:00:00Z", forks=1),
    make_repo("c", 0, "JavaScript", "2022-07-01T10:00:00Z", fork=True),
]


class FakeResp:
    def __init__(self, status, data=None):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app_module._cache.clear()
    return app.test_client()


def test_home_page_loads(client):
    assert client.get("/").status_code == 200


def test_invalid_username_is_rejected(client):
    r = client.post("/search", data={"username": "!!bad"})
    assert r.status_code == 200
    assert b"valid GitHub username" in r.data


def test_user_not_found_message(client, monkeypatch):
    def boom(username):
        raise GitHubError("GitHub User Not Found!")

    monkeypatch.setattr(app_module, "get_user_data", boom)
    r = client.post("/search", data={"username": "nobodyhere"})
    assert b"GitHub User Not Found!" in r.data


def test_successful_search_shows_stats(client, monkeypatch):
    monkeypatch.setattr(app_module, "get_user_data", lambda username: (PROFILE, REPOS))
    r = client.post("/search", data={"username": "octocat"})
    html = r.data.decode("utf-8")
    assert "The Octocat" in html
    assert "⭐ 20" in html            # 15 + 5 + 0 stars
    assert "<p>None</p>" not in html   # missing bio must not print "None"


def test_build_analytics():
    a = build_analytics(REPOS)
    assert a["languages"]["labels"] == ["Python", "JavaScript"]
    assert a["languages"]["values"] == [2, 1]
    assert a["years"]["labels"] == ["2021", "2022"]
    assert a["years"]["values"] == [1, 2]
    assert a["total_forks"] == 4
    assert a["original_repos"] == 2
    assert a["forked_repos"] == 1
    assert a["avg_stars"] == 6.7
    assert a["top_stars"]["labels"][0] == "a"


def test_gh_get_maps_404_to_user_not_found(monkeypatch):
    monkeypatch.setattr(app_module.requests, "get", lambda *a, **k: FakeResp(404))
    with pytest.raises(GitHubError, match="Not Found"):
        gh_get("/users/nobody")


def test_gh_get_maps_403_to_rate_limit(monkeypatch):
    monkeypatch.setattr(app_module.requests, "get", lambda *a, **k: FakeResp(403))
    with pytest.raises(GitHubError, match="rate limit"):
        gh_get("/users/someone")


def test_fetch_all_repos_follows_pagination(monkeypatch):
    def fake_gh_get(path, params=None):
        return [make_repo("x", 0, "Python", "2022-01-01T00:00:00Z")] * (100 if params["page"] == 1 else 20)

    monkeypatch.setattr(app_module, "gh_get", fake_gh_get)
    assert len(fetch_all_repos("bigorg")) == 120


def test_results_are_cached(monkeypatch):
    app_module._cache.clear()
    calls = []

    def fake_gh_get(path, params=None):
        calls.append(path)
        return PROFILE if "repos" not in path else []

    monkeypatch.setattr(app_module, "gh_get", fake_gh_get)
    get_user_data("octocat")
    get_user_data("octocat")
    assert len(calls) == 2  # profile + repos once; second call came from the cache

def test_build_insights_score_and_tips():
    now = pd.Timestamp.now(tz="UTC")
    recent = (now - pd.Timedelta(days=10)).isoformat()
    old = (now - pd.Timedelta(days=500)).isoformat()
    repos = [
        {**make_repo("a", 1, "Python", "2022-01-01T00:00:00Z"),
         "description": "x", "topics": ["flask"], "license": {"key": "mit"}, "pushed_at": recent},
        {**make_repo("b", 0, "Python", "2022-01-01T00:00:00Z"),
         "description": "", "topics": [], "license": None, "pushed_at": old},
    ]
    i = build_insights(repos)
    assert i["score"] == 49
    assert i["active"] == 1 and i["stale"] == 1
    assert any("Add a description to 1 repo" in t for t in i["tips"])


def test_build_language_trend():
    t = build_language_trend(REPOS)
    assert t["labels"] == ["2021", "2022"]
    data = {d["label"]: d["data"] for d in t["datasets"]}
    assert data["Python"] == [1, 1]
    assert data["JavaScript"] == [0, 1]