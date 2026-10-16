import os
import re
import time
from collections import Counter

import pandas as pd
import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request

load_dotenv()

app = Flask(__name__)

GITHUB_API = "https://api.github.com"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")  # optional, raises the rate limit
CACHE_TTL = 600  # seconds
MAX_PAGES = 5    # up to 500 repos per user
USERNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")

_cache = {}


class GitHubError(Exception):
    """An error with a message that is safe to show to the user."""


def gh_get(path, params=None, use_token=True):
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN and use_token:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    try:
        resp = requests.get(f"{GITHUB_API}{path}", headers=headers, params=params, timeout=10)
    except requests.RequestException:
        raise GitHubError("Could not reach GitHub. Please try again.")

    # A bad or expired token should not break the site: retry without it
    if resp.status_code == 401 and use_token and GITHUB_TOKEN:
        return gh_get(path, params, use_token=False)

    if resp.status_code == 404:
        raise GitHubError("GitHub User Not Found!")
    if resp.status_code in (403, 429):
        raise GitHubError("GitHub rate limit reached. Please try again in a few minutes.")
    if resp.status_code != 200:
        raise GitHubError(f"GitHub returned an error ({resp.status_code}).")
    return resp.json()


def fetch_all_repos(username):
    repos = []
    for page in range(1, MAX_PAGES + 1):
        batch = gh_get(
            f"/users/{username}/repos",
            {"per_page": 100, "page": page, "type": "owner"},
        )
        repos.extend(batch)
        if len(batch) < 100:
            break
    return repos


def get_user_data(username):
    """Fetch profile and repos, with a simple in-memory cache."""
    key = username.lower()
    now = time.time()
    cached = _cache.get(key)
    if cached and now - cached[0] < CACHE_TTL:
        return cached[1]

    profile = gh_get(f"/users/{username}")
    repos = fetch_all_repos(username)

    if len(_cache) > 200:
        _cache.clear()
    _cache[key] = (now, (profile, repos))
    return profile, repos


def build_analytics(repos):
    """Summarise a user's repos with Pandas for the charts and stat tiles."""
    if not repos:
        return None

    df = pd.DataFrame(repos)
    df["created_at"] = pd.to_datetime(df["created_at"])

    # Language breakdown (top 8)
    lang_counts = df["language"].dropna().value_counts().head(8)
    lang_total = int(df["language"].notna().sum())
    languages = {
        "labels": list(lang_counts.index),
        "values": [int(v) for v in lang_counts.values],
    }
    lang_table = [
        {"name": name, "count": int(count), "pct": round(100 * count / lang_total, 1)}
        for name, count in lang_counts.items()
    ] if lang_total else []

    # Top 5 repos by stars
    top = df.nlargest(5, "stargazers_count")
    top_stars = {
        "labels": list(top["name"]),
        "values": [int(v) for v in top["stargazers_count"]],
    }

    # Repos created per year
    per_year = df.groupby(df["created_at"].dt.year).size()
    years = {
        "labels": [str(y) for y in per_year.index],
        "values": [int(v) for v in per_year.values],
    }

    return {
        "languages": languages,
        "lang_table": lang_table,
        "top_stars": top_stars,
        "has_stars": bool(df["stargazers_count"].max() > 0),
        "years": years,
        "total_forks": int(df["forks_count"].sum()),
        "original_repos": int((~df["fork"]).sum()),
        "forked_repos": int(df["fork"].sum()),
        "avg_stars": round(float(df["stargazers_count"].mean()), 1),
    }


def build_insights(repos):
    """Profile health report based on the user's own (non-fork) repos."""
    own = [r for r in repos if not r.get("fork")]
    if not own:
        return None

    n = len(own)
    now = pd.Timestamp.now(tz="UTC")

    with_desc = sum(1 for r in own if (r.get("description") or "").strip())
    with_topics = sum(1 for r in own if r.get("topics"))
    with_license = sum(1 for r in own if r.get("license"))

    ages = [
        (now - pd.to_datetime(r["pushed_at"], utc=True)).days
        for r in own if r.get("pushed_at")
    ]
    active = sum(1 for a in ages if a <= 180)
    stale = sum(1 for a in ages if a > 365)

    # Heuristic score out of 100 (weights are my own judgement, not a GitHub metric)
    score = round(
        30 * with_desc / n
        + 20 * with_topics / n
        + 15 * with_license / n
        + 25 * active / n
        + 10 * min(n / 5, 1)
    )

    tips = []
    if n - with_desc:
        tips.append(f"Add a description to {n - with_desc} repo(s).")
    if n - with_topics:
        tips.append(f"Add topics to {n - with_topics} repo(s) so people can find them.")
    if n - with_license:
        tips.append(f"Add a license to {n - with_license} repo(s).")
    if stale:
        tips.append(f"{stale} repo(s) haven't been updated in over a year. Archive or refresh them.")
    if n < 5:
        tips.append("Pin 4 to 6 strong projects with clear READMEs.")

    return {
        "score": score,
        "original_count": n,
        "pct_description": round(100 * with_desc / n),
        "pct_topics": round(100 * with_topics / n),
        "pct_license": round(100 * with_license / n),
        "active": active,
        "stale": stale,
        "tips": tips,
    }


def build_language_trend(repos):
    """Repos created per year, split by language (top 5 plus 'Other')."""
    df = pd.DataFrame(repos).dropna(subset=["language"])
    if df.empty:
        return None
    df["year"] = pd.to_datetime(df["created_at"]).dt.year.astype(str)
    top = df["language"].value_counts().head(5).index
    df["lang"] = df["language"].where(df["language"].isin(top), "Other")
    table = df.groupby(["year", "lang"]).size().unstack(fill_value=0).sort_index()
    return {
        "labels": list(table.index),
        "datasets": [
            {"label": lang, "data": [int(v) for v in table[lang]]}
            for lang in table.columns
        ],
    }


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/search", methods=["POST"])
def search():
    username = request.form.get("username", "").strip()

    if not USERNAME_RE.match(username):
        return render_template("result.html", error="Please enter a valid GitHub username.")

    try:
        profile, repos = get_user_data(username)
    except GitHubError as e:
        return render_template("result.html", error=str(e))

    stars = sum(repo["stargazers_count"] for repo in repos)

    languages = [repo["language"] for repo in repos if repo["language"]]
    top_language = Counter(languages).most_common(1)[0][0] if languages else "N/A"

    top_repo = max(repos, key=lambda x: x["stargazers_count"]) if repos else None

    recent = sorted(repos, key=lambda x: x["created_at"], reverse=True)[:5]

    analytics = build_analytics(repos)
    if analytics:
        analytics["insights"] = build_insights(repos)
        analytics["trend"] = build_language_trend(repos)

    return render_template(
        "result.html",
        profile=profile,
        repos=repos,
        stars=stars,
        top_language=top_language,
        top_repo=top_repo,
        recent=recent,
        analytics=analytics,
        error=None,
    )


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")