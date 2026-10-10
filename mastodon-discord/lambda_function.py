"""Boost and relay mentions of the wiki's Mastodon status account to Discord.

Runs on a schedule. Mentions from accounts outside ALLOWED_ACCTS are skipped.
The status account boosts each public or unlisted mention, then relays it.
A reply to an alert post lands in Discord as a reply to the alert message
that carries the same id, which the Grafana templates in femiwiki/infra write
into both.
"""

import json
import os
import re
import urllib.parse
from datetime import datetime, timedelta
from typing import Any

import boto3
import html2text
import requests
from mastodon import Mastodon, MastodonNotFoundError

USER_AGENT = "femiwiki-lambda-mastodon-discord (+https://github.com/femiwiki/lambda)"
DISCORD_API = "https://discord.com/api/v10"
DISCORD_EPOCH_MS = 1420070400000
DISCORD_MAX_LENGTH = 2000
ALERT_ID = re.compile(r"\bid ([0-9a-f]{8})\b")
# Grafana posts to Mastodon and Discord from the same evaluation, but either
# can land a little after the other.
MATCH_SLACK = timedelta(minutes=2)
PAGE_LIMIT = 40
BOOSTABLE = {"public", "unlisted"}

ssm = boto3.client("ssm")


def lambda_handler(event: Any, context: Any) -> None:
    instance = os.environ["MASTODON_INSTANCE"]
    allowed = parse_allowed(os.environ["ALLOWED_ACCTS"], instance)
    parameter = os.environ["CURSOR_PARAMETER"]
    channel_id = os.environ["DISCORD_CHANNEL_ID"]
    mastodon = Mastodon(
        access_token=os.environ["MASTODON_TOKEN"],
        api_base_url=instance,
        user_agent=USER_AGENT,
        request_timeout=10,
    )
    discord = requests.Session()
    discord.headers.update(
        {
            "Authorization": f"Bot {os.environ['DISCORD_BOT_TOKEN']}",
            "User-Agent": USER_AGENT,
        }
    )

    cursor = read_cursor(parameter)
    if cursor is None:
        # First run: start from now instead of relaying the whole history.
        latest = mastodon.notifications(types=["mention"], limit=1)
        if latest:
            write_cursor(parameter, str(latest[0].id))
        return

    for notification in fetch_mentions(mastodon, cursor):
        status = notification.get("status")
        if status and normalize_acct(notification.account.acct, instance) in allowed:
            # Boosting again is harmless, posting to Discord again is not.
            boost(status, mastodon)
            relay(status, instance, mastodon, discord, channel_id)
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


def relay(
    status: Any,
    instance: str,
    mastodon: Mastodon,
    discord: requests.Session,
    channel_id: str,
) -> None:
    body: dict[str, Any] = {
        "content": format_content(status, instance),
        "allowed_mentions": {"parse": []},
    }
    if status.in_reply_to_id:
        parent = mastodon.status(status.in_reply_to_id)
        reference = find_alert_message(parent, discord, channel_id)
        if reference:
            body["message_reference"] = {
                "message_id": reference,
                "fail_if_not_exists": False,
            }
    response = discord.post(
        f"{DISCORD_API}/channels/{channel_id}/messages", json=body, timeout=10
    )
    response.raise_for_status()


def find_alert_message(
    parent: Any, discord: requests.Session, channel_id: str
) -> str | None:
    """The Discord alert message that carries the same id as parent."""
    match = ALERT_ID.search(html_to_text(parent.content))
    if match is None:
        return None
    alert_id = match.group(1)
    posted = parent.created_at

    response = discord.get(
        f"{DISCORD_API}/channels/{channel_id}/messages",
        params={"limit": 100, "around": snowflake(posted)},
        timeout=10,
    )
    response.raise_for_status()
    best = None
    for message in response.json():
        sent = datetime.fromisoformat(message["timestamp"])
        if sent > posted + MATCH_SLACK or alert_id not in message_ids(message):
            continue
        # Discord repeats an alert more often than Mastodon does; the latest
        # message up to the post is the one sent together with it.
        if best is None or sent > best[0]:
            best = (sent, message["id"])
    return best[1] if best else None


def message_ids(message: dict[str, Any]) -> set[str]:
    texts = [message.get("content") or ""]
    for embed in message.get("embeds") or []:
        texts.append(embed.get("title") or "")
        texts.append(embed.get("description") or "")
    return {m for text in texts for m in ALERT_ID.findall(text)}


def format_content(status: Any, instance: str) -> str:
    account = status.account
    name = account.display_name or account.username
    header = f"**{name}** (@{normalize_acct(account.acct, instance)}) 마스토돈 답글"
    link = f"<{status.url}>"
    text = strip_leading_mentions(html_to_text(status.content))
    room = DISCORD_MAX_LENGTH - len(header) - len(link) - 2
    if len(text) > room:
        text = text[: room - 1] + "…"
    return f"{header}\n{text}\n{link}"


def strip_leading_mentions(text: str) -> str:
    return re.sub(r"^(?:\[@[^\]]+\]\([^)]*\)\s*)+", "", text)


def html_to_text(content: str) -> str:
    converter = html2text.HTML2Text()
    converter.body_width = 0
    return converter.handle(content).strip()


def snowflake(moment: datetime) -> int:
    return (int(moment.timestamp() * 1000) - DISCORD_EPOCH_MS) << 22


def read_cursor(parameter: str) -> str | None:
    try:
        return ssm.get_parameter(Name=parameter)["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        return None


def write_cursor(parameter: str, value: str) -> None:
    ssm.put_parameter(Name=parameter, Value=value, Type="String", Overwrite=True)
