import os
import re
import time
import pandas as pd
from collections import Counter

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


def gh_get(path, params=None):
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    try:
        resp = requests.get(f"{GITHUB_API}{path}", headers=headers, params=params, timeout=10)
    except requests.RequestException:
        raise GitHubError("Could not reach GitHub. Please try again.")

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

    return render_template(
        "result.html",
        profile=profile,
        repos=repos,
        stars=stars,
        top_language=top_language,
        top_repo=top_repo,
        recent=recent,
        analytics=build_analytics(repos),
        error=None,
    )


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")