"""Hand SES bounce notifications from SNS to MediaWiki's BounceHandler API.

SES receives the bounce itself, so this rebuilds the minimal delivery status
report BounceHandler parses: the VERP address the wiki sent from as To, and the
recipient's status code. See femiwiki/docker-mediawiki#215.
"""

import json
import os
import re
import urllib.parse
import urllib.request
from typing import Any

import boto3

USER_AGENT = "femiwiki-lambda-bounce-handler (+https://github.com/femiwiki/lambda)"
# prefix-wikiId-base36(user id)-base36(time)-base64(12-byte HMAC)@domain
VERP = re.compile(r"^wiki-[^-@\s]+-[0-9a-z]+-[0-9a-z]+-[A-Za-z0-9+/]{16}@[^@\s]+$")
# What a permanent bounce without a recipient status, such as a suppressed one, means
DEFAULT_STATUS = "5.0.0"

ssm = boto3.client("ssm")
_token: str | None = None


class RefusedError(Exception):
    """The API answered with an error, such as invalid-ip when the token is wrong."""


def lambda_handler(event: Any, context: Any) -> None:
    for record in event.get("Records", []):
        notification = json.loads(record["Sns"]["Message"])
        email = build_email(notification)
        if email is None:
            print(json.dumps({"skipped": summary(notification)}))
            continue
        result = submit(email)
        print(json.dumps({"submitted": summary(notification), "result": result}))
        if "error" in result:
            # Raising makes Lambda retry the asynchronous invocation
            raise RefusedError(result["error"])


def build_email(notification: dict[str, Any]) -> str | None:
    """A delivery status report for a permanent bounce of a VERP address, else None."""
    bounce = notification.get("bounce") or {}
    source = (notification.get("mail") or {}).get("source") or ""
    if (
        notification.get("notificationType") != "Bounce"
        or bounce.get("bounceType") != "Permanent"
        or not VERP.match(source)
    ):
        return None
    recipient = (bounce.get("bouncedRecipients") or [{}])[0]
    reason = recipient.get("diagnosticCode") or bounce.get("bounceSubType") or ""
    subject = " ".join(
        f"{bounce['bounceType']}/{bounce.get('bounceSubType')}: {reason}".split()
    )
    return "\n".join(
        [
            f"To: {source}",
            f"Subject: {subject}",
            f"Date: {bounce.get('timestamp', '')}",
            'Content-Type: multipart/report; report-type=delivery-status; boundary="ses"',
            "",
            "--ses",
            "Content-Type: message/delivery-status",
            "",
            f"Status: {recipient.get('status') or DEFAULT_STATUS}",
            "--ses--",
            "",
        ]
    )


def submit(email: str) -> dict[str, Any]:
    body = urllib.parse.urlencode(
        {
            "action": "bouncehandler",
            "format": "json",
            "email": email,
            "bouncehandlertoken": token(),
        }
    ).encode()
    request = urllib.request.Request(
        os.environ["API_URL"],
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read())


def token() -> str:
    global _token
    if _token is None:
        _token = ssm.get_parameter(
            Name=os.environ["TOKEN_PARAMETER"], WithDecryption=True
        )["Parameter"]["Value"]
    return _token


def summary(notification: dict[str, Any]) -> dict[str, Any]:
    bounce = notification.get("bounce") or {}
    return {
        "type": notification.get("notificationType"),
        "bounceType": bounce.get("bounceType"),
        "bounceSubType": bounce.get("bounceSubType"),
        "source": (notification.get("mail") or {}).get("source"),
        "feedbackId": bounce.get("feedbackId"),
    }
