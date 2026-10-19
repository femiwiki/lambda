"""Keep one GitHub issue per Grafana alert rule that only operators need to act on.

Grafana's webhook contact point posts its default payload to this function's
URL. The first firing opens an issue titled with the rule's name and the time it
started in KST, so issues for the same rule can be told apart; later
notifications for the same rule, repeats and resolves alike, become comments
while that issue is open.
A new issue carries the label LABEL and mentions GITHUB_MENTION, a team, so its
members get a push.
The pull request that fixes the cause closes the issue.

An EventBridge rule also invokes it for each new IAM Access Analyzer finding,
and each active finding opens an issue of its own, closed once the finding is
resolved or archived. The issue says only that one was found and the finding's
id, since the repository is public; the console shows which.

Another EventBridge rule invokes it for each successful call, in Seoul or Tokyo,
that adds an inbound rule to a security group or changes any of its rules. A call
that names 0.0.0.0/0 or ::/0 opens an issue that names only the region and the
CloudTrail event id, for the same reason. An operator closes it once the rule is
checked.
"""

import base64
import hmac
import json
import os
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from github import Auth, GithubIntegration
from github.Repository import Repository

KST = timezone(timedelta(hours=9), "KST")
LABEL = "alert"
WORLD = ("0.0.0.0/0", "::/0")


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, int]:
    # EventBridge invokes the function directly, so only IAM can reach this branch.
    if event.get("source") == "aws.access-analyzer":
        repo, _ = connect()
        record_finding(repo, event, os.environ.get("GITHUB_MENTION", ""))
        return {"statusCode": 204}
    if event.get("source") == "aws.ec2":
        if opens_to_everyone(event["detail"].get("requestParameters")):
            repo, _ = connect()
            record_ingress(repo, event, os.environ.get("GITHUB_MENTION", ""))
        return {"statusCode": 204}

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if not authorized(headers.get("authorization", ""), os.environ["WEBHOOK_TOKEN"]):
        return {"statusCode": 401}

    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode()
    payload = json.loads(body)

    repo, bot = connect()
    record(repo, bot, payload, os.environ.get("GITHUB_MENTION", ""))
    return {"statusCode": 204}


def connect() -> tuple[Repository, str]:
    owner, name = os.environ["GITHUB_REPOSITORY"].split("/")
    app = GithubIntegration(
        auth=Auth.AppAuth(
            os.environ["GITHUB_APP_CLIENT_ID"], os.environ["GITHUB_APP_PRIVATE_KEY"]
        )
    )
    bot = f"{app.get_app().slug}[bot]"
    installation = app.get_repo_installation(owner, name)
    github = app.get_github_for_installation(installation.id, {"issues": "write"})
    return github.get_repo(f"{owner}/{name}"), bot


def record_finding(repo: Repository, event: dict[str, Any], mention: str = "") -> None:
    """Open an issue for an active finding, and close it once the finding is not."""
    detail = event["detail"]
    marker = f"Finding {detail['id']}"
    existing = next(
        (
            issue
            for issue in repo.get_issues(state="open", labels=[LABEL])
            if marker in (issue.body or "")
        ),
        None,
    )
    active = detail.get("status") == "ACTIVE" and not detail.get("isDeleted")
    if existing is not None:
        if not active:
            existing.create_comment(
                f"The finding is {detail.get('status', 'gone').lower()} now."
            )
            existing.edit(state="closed", state_reason="completed")
        return
    if not active:
        return
    region = event.get("region") or "ap-northeast-2"
    start = (
        datetime.fromisoformat(event["time"][:16]).replace(tzinfo=UTC).astimezone(KST)
    )
    console = f"https://{region}.console.aws.amazon.com/access-analyzer/home?region={region}#/findings"
    text = (
        "IAM Access Analyzer found 1 new external access finding. "
        f"Check it in the console: {console}\n\n{marker}"
    )
    repo.create_issue(
        title=f"External access finding ({start.month}/{start.day} {start:%H:%M} KST)",
        body=f"{text}\n\n{mention}".strip(),
        labels=[LABEL],
    )


def opens_to_everyone(value: Any) -> bool:
    """Whether a request names 0.0.0.0/0 or ::/0 anywhere, whatever its shape."""
    if isinstance(value, dict):
        return any(opens_to_everyone(v) for v in value.values())
    if isinstance(value, list):
        return any(opens_to_everyone(v) for v in value)
    return value in WORLD


def record_ingress(repo: Repository, event: dict[str, Any], mention: str = "") -> None:
    """Open an issue for a call that opened an inbound rule to everyone."""
    detail = event["detail"]
    marker = f"Event {detail['eventID']}"
    # EventBridge delivers at least once.
    if any(
        marker in (issue.body or "")
        for issue in repo.get_issues(state="open", labels=[LABEL])
    ):
        return
    region = detail.get("awsRegion") or event.get("region") or "ap-northeast-2"
    start = (
        datetime.fromisoformat(detail["eventTime"][:16])
        .replace(tzinfo=UTC)
        .astimezone(KST)
    )
    console = f"https://{region}.console.aws.amazon.com/cloudtrailv2/home?region={region}#/events/{detail['eventID']}"
    text = (
        f"A security group in {region} got an inbound rule open to everyone. "
        f"Check it in CloudTrail: {console}\n\n{marker}"
    )
    repo.create_issue(
        title=f"World-open inbound rule ({start.month}/{start.day} {start:%H:%M} KST)",
        body=f"{text}\n\n{mention}".strip(),
        labels=[LABEL],
    )


def authorized(header: str, token: str) -> bool:
    return hmac.compare_digest(header.encode(), f"Bearer {token}".encode())


def record(
    repo: Repository, bot: str, payload: dict[str, Any], mention: str = ""
) -> None:
    rule = (
        payload.get("groupLabels", {}).get("alertname")
        or payload["commonLabels"]["alertname"]
    )
    text = render(payload)
    for issue in repo.get_issues(state="open"):
        if (
            is_for(issue.title, rule)
            and issue.user.login == bot
            and not issue.pull_request
        ):
            issue.create_comment(text)
            return
    if payload["status"] == "firing":
        repo.create_issue(
            title=title(rule, payload),
            body=f"{text}\n\n{mention}".strip(),
            labels=[LABEL],
        )


def is_for(title: str, rule: str) -> bool:
    """Whether an issue title is the rule's, with or without the start time."""
    return title == rule or title.startswith(f"{rule} (")


def title(rule: str, payload: dict[str, Any]) -> str:
    """The rule's name and when its earliest firing alert started, in KST."""
    starts = min(
        alert["startsAt"] for alert in payload["alerts"] if alert["status"] == "firing"
    )
    start = datetime.fromisoformat(starts[:16]).replace(tzinfo=UTC).astimezone(KST)
    return f"{rule} ({start.month}/{start.day} {start:%H:%M} KST)"


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
