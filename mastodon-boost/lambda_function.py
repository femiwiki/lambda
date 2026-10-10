"""Boost mentions of the wiki's Mastodon status account from trusted accounts.

Runs on a schedule. Mentions from accounts outside ALLOWED_ACCTS are skipped,
and so are private and direct ones, which Mastodon does not let anyone boost.
"""

import json
import os
import urllib.parse
from typing import Any

import boto3
from mastodon import Mastodon, MastodonNotFoundError

USER_AGENT = "femiwiki-lambda-mastodon-boost (+https://github.com/femiwiki/lambda)"
PAGE_LIMIT = 40
BOOSTABLE = {"public", "unlisted"}

ssm = boto3.client("ssm")


def lambda_handler(event: Any, context: Any) -> None:
    instance = os.environ["MASTODON_INSTANCE"]
    allowed = parse_allowed(os.environ["ALLOWED_ACCTS"], instance)
    parameter = os.environ["CURSOR_PARAMETER"]
    mastodon = Mastodon(
        access_token=os.environ["MASTODON_TOKEN"],
        api_base_url=instance,
        user_agent=USER_AGENT,
        request_timeout=10,
    )

    cursor = read_cursor(parameter)
    if cursor is None:
        # First run: start from now instead of boosting the whole history.
        latest = mastodon.notifications(types=["mention"], limit=1)
        if latest:
            write_cursor(parameter, str(latest[0].id))
        return

    for notification in fetch_mentions(mastodon, cursor):
        status = notification.get("status")
        if status and normalize_acct(notification.account.acct, instance) in allowed:
            boost(status, mastodon)
        # Moved one mention at a time, so a failure retries only what is left.
        write_cursor(parameter, str(notification.id))


def parse_allowed(value: str, instance: str) -> set[str]:
    return {normalize_acct(acct, instance) for acct in value.split(",") if acct.strip()}


def normalize_acct(acct: str, instance: str) -> str:
    acct = acct.strip().lstrip("@").lower()
    if "@" not in acct:
        acct = f"{acct}@{urllib.parse.urlsplit(instance).hostname}"
    return acct


def fetch_mentions(mastodon: Mastodon, cursor: str) -> list[Any]:
    """Every mention newer than cursor, oldest first."""
    mentions: list[Any] = []
    while page := mastodon.notifications(
        types=["mention"], min_id=cursor, limit=PAGE_LIMIT
    ):
        page = sorted(page, key=lambda n: int(n.id))
        mentions.extend(page)
        cursor = str(page[-1].id)
    return mentions


def boost(status: Any, mastodon: Mastodon) -> None:
    if status.visibility not in BOOSTABLE:
        log("skipped", status, f"visibility is {status.visibility}")
        return
    if status.reblogged:
        log("skipped", status, "already boosted")
        return
    try:
        mastodon.status_reblog(status.id)
    except MastodonNotFoundError:
        log("skipped", status, "deleted")
        return
    log("boosted", status)


def log(action: str, status: Any, reason: str | None = None) -> None:
    entry = {action: str(status.id), "url": status.url}
    if reason:
        entry["reason"] = reason
    print(json.dumps(entry))


def read_cursor(parameter: str) -> str | None:
    try:
        return ssm.get_parameter(Name=parameter)["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        return None


def write_cursor(parameter: str, value: str) -> None:
    ssm.put_parameter(Name=parameter, Value=value, Type="String", Overwrite=True)
