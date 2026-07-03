from flask import Flask, render_template, request
import requests
from collections import Counter

app = Flask(__name__)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/search", methods=["POST"])
def search():

    username = request.form["username"].strip()

    profile_url = f"https://api.github.com/users/{username}"
    repos_url = f"https://api.github.com/users/{username}/repos"

    profile = requests.get(profile_url)

    if profile.status_code != 200:
        return render_template("result.html", error="GitHub User Not Found!")

    profile = profile.json()
    repos = requests.get(repos_url).json()

    stars = sum(repo["stargazers_count"] for repo in repos)

    languages = [repo["language"] for repo in repos if repo["language"]]

    if languages:
        top_language = Counter(languages).most_common(1)[0][0]
    else:
        top_language = "N/A"

    top_repo = None

    if repos:
        top_repo = max(repos, key=lambda x: x["stargazers_count"])

    recent = sorted(
        repos,
        key=lambda x: x["created_at"],
        reverse=True
    )[:5]

    return render_template(
        "result.html",
        profile=profile,
        repos=repos,
        stars=stars,
        top_language=top_language,
        top_repo=top_repo,
        recent=recent,
        error=None
    )


if __name__ == "__main__":
    app.run(debug=True)