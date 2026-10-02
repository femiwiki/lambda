"""Keep one GitHub issue per Grafana alert rule that only operators need to act on.

Grafana's webhook contact point posts its default payload to this function's
URL. The first firing opens an issue titled with the rule's name; later
notifications for the same rule, repeats and resolves alike, become comments.
A new issue mentions GITHUB_MENTION, a team, so its members get a push.
The pull request that fixes the cause closes the issue.
"""

import base64
import hmac
import json
import os
from typing import Any

from github import Auth, GithubIntegration
from github.Repository import Repository


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, int]:
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if not authorized(headers.get("authorization", ""), os.environ["WEBHOOK_TOKEN"]):
        return {"statusCode": 401}

    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode()
    payload = json.loads(body)

    owner, name = os.environ["GITHUB_REPOSITORY"].split("/")
    app = GithubIntegration(
        auth=Auth.AppAuth(
            os.environ["GITHUB_APP_CLIENT_ID"], os.environ["GITHUB_APP_PRIVATE_KEY"]
        )
    )
    bot = f"{app.get_app().slug}[bot]"
    installation = app.get_repo_installation(owner, name)
    github = app.get_github_for_installation(installation.id, {"issues": "write"})
    record(
        github.get_repo(f"{owner}/{name}"),
        bot,
        payload,
        os.environ.get("GITHUB_MENTION", ""),
    )
    return {"statusCode": 204}


def authorized(header: str, token: str) -> bool:
    return hmac.compare_digest(header.encode(), f"Bearer {token}".encode())


def record(
    repo: Repository, bot: str, payload: dict[str, Any], mention: str = ""
) -> None:
    title = (
        payload.get("groupLabels", {}).get("alertname")
        or payload["commonLabels"]["alertname"]
    )
    text = render(payload)
    for issue in repo.get_issues(state="open"):
        if issue.title == title and issue.user.login == bot and not issue.pull_request:
            issue.create_comment(text)
            return
    if payload["status"] == "firing":
        repo.create_issue(title=title, body=f"{text}\n\n{mention}".strip())


def render(payload: dict[str, Any]) -> str:
    sections = []
    for alert in payload["alerts"]:
        annotations = alert.get("annotations", {})
        lines = []
        if alert["status"] == "resolved":
            lines.append(
                f"Resolved at {stamp(alert['endsAt'])}. The numbers below were measured then.\n"
            )
        lines.append(annotations.get("summary", ""))
        lines.append("")
        lines.append(f"- Started: {stamp(alert['startsAt'])}")
        if alert.get("generatorURL"):
            lines.append(f"- Rule: {alert['generatorURL']}")
        if annotations.get("logs"):
            lines.append(f"- Logs: {annotations['logs']}")
        lines.append(f"- id {alert.get('fingerprint', '')[:8]}")
        sections.append("\n".join(lines))
    return "\n\n---\n\n".join(sections)


def stamp(value: str) -> str:
    """2026-10-01T12:00:00.123Z as 2026-10-01 12:00 UTC."""
    return f"{value[:10]} {value[11:16]} UTC"
