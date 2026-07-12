"""Forward SNS CloudWatch alarm notifications to a Discord webhook."""

import json
import os
import urllib.error
import urllib.request
from typing import Any

RED = 0xE83535
GREEN = 0x2DAF32
GRAY = 0xB3B4BC

MENTION_ROLE = "678974055365476392"
WEBHOOK_URL = "https://discord.com/api/webhooks/792425523639746611/{}"


def lambda_handler(event: Any, context: Any) -> None:
    webhook_token = os.environ["WEBHOOK_TOKEN"]
    post_data = parse_message(event)

    payload = {
        "content": post_data["content"],
        "embeds": [post_data["embed"]],
        "allowed_mentions": {"roles": [MENTION_ROLE]},
    }
    request = urllib.request.Request(
        WEBHOOK_URL.format(webhook_token),
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "femiwiki-lambda-sns-discord (+https://github.com/femiwiki/lambda)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            status_code = response.status
            body = response.read()
    except urllib.error.HTTPError as error:
        status_code = error.code
        body = error.read()

    print(
        json.dumps(
            {
                "event": event,
                "status_code": status_code,
                "response": body.decode(errors="replace"),
            },
            ensure_ascii=False,
        )
    )


def parse_message(event: Any) -> dict[str, Any]:
    message = sns_message(event)
    fields: list[dict[str, Any]] = []
    if message is None:
        notify = True
        color = RED
        summary = "알지 못하는 유형의 이벤트가 발생했습니다."
        dump = json.dumps(event, indent=2, ensure_ascii=False)
    else:
        try:
            parsed = json.loads(message)
        except ValueError:
            notify = True
            color = RED
            summary = ""
            dump = message
        else:
            alarm = parsed if isinstance(parsed, dict) else {}
            test = alarm.get("AlarmName") == "_Test"
            notify = alarm.get("NewStateValue") != "OK" and not test
            color = GRAY if test else RED if notify else GREEN
            alarm_name = alarm.get("AlarmName")
            reason = alarm.get("NewStateReason")
            summary = "[{}] {}".format(
                alarm_name
                if isinstance(alarm_name, str)
                else "(메시지에 AlarmName이 없습니다)",
                reason
                if isinstance(reason, str)
                else "(메시지에 NewStateReason이 없습니다)",
            )
            fields = message_to_fields(alarm)
            dump = ""

    mention = f"<@&{MENTION_ROLE}> " if notify else "🟢 "
    return {
        "content": mention + summary,
        "embed": {
            "color": color,
            "description": f"```json\n{dump}\n```" if dump else "",
            "fields": fields,
        },
    }


def sns_message(event: Any) -> str | None:
    try:
        message = event["Records"][0]["Sns"]["Message"]
    except (KeyError, IndexError, TypeError):
        return None
    return message if isinstance(message, str) else None


def message_to_fields(message: dict[str, Any]) -> list[dict[str, Any]]:
    fields = []
    for key, value in message.items():
        if key in ("NewStateReason", "AlarmName"):
            continue
        text, inline = value_to_string(value)
        fields.append({"name": key, "value": text, "inline": inline})
    return fields


def value_to_string(value: Any) -> tuple[str, bool]:
    if isinstance(value, str):
        text = value
    else:
        stringified = json.dumps(value, indent=2, ensure_ascii=False)
        if "\n" in stringified:
            text = f"```json\n{stringified}\n```"
        else:
            text = f"`{stringified}`"
    return text, "\n" not in text
