"""Forward SNS CloudWatch alarm notifications to a Discord webhook."""

import json
import os
import urllib.error
import urllib.request
from typing import Any

import boto3

RED = 0xE83535
GREEN = 0x2DAF32
GRAY = 0xB3B4BC

MENTION_ROLE = "678974055365476392"
WEBHOOK_URL = "https://discord.com/api/webhooks/792425523639746611/{}"

STAT_NAMES = {
    "SAMPLECOUNT": "SampleCount",
    "AVERAGE": "Average",
    "SUM": "Sum",
    "MINIMUM": "Minimum",
    "MAXIMUM": "Maximum",
}
CHART_WINDOW_MULTIPLIER = 12
CHART_MIN_MINUTES = 30
CHART_MAX_MINUTES = 360
MULTIPART_BOUNDARY = "----snsdiscordboundary"

cloudwatch = boto3.client("cloudwatch")


def lambda_handler(event: Any, context: Any) -> None:
    webhook_token = os.environ["WEBHOOK_TOKEN"]
    post_data = parse_message(event)

    trigger = post_data["trigger"]
    chart = fetch_chart_image(trigger) if trigger else None
    payload = build_payload(post_data, chart)

    if chart is None:
        body = json.dumps(payload).encode()
        content_type = "application/json"
    else:
        body = encode_multipart(payload, chart)
        content_type = f"multipart/form-data; boundary={MULTIPART_BOUNDARY}"

    request = urllib.request.Request(
        WEBHOOK_URL.format(webhook_token),
        data=body,
        headers={
            "Content-Type": content_type,
            "User-Agent": "femiwiki-lambda-sns-discord (+https://github.com/femiwiki/lambda)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            status_code = response.status
            response_body = response.read()
    except urllib.error.HTTPError as error:
        status_code = error.code
        response_body = error.read()

    print(
        json.dumps(
            {
                "event": event,
                "status_code": status_code,
                "response": response_body.decode(errors="replace"),
            },
            ensure_ascii=False,
        )
    )


def build_payload(post_data: dict[str, Any], chart: bytes | None) -> dict[str, Any]:
    embed = post_data["embed"]
    if chart is not None:
        embed = {
            **embed,
            "description": "",
            "fields": [],
            "image": {"url": "attachment://chart.png"},
        }
    return {
        "content": post_data["content"],
        "embeds": [embed],
        "allowed_mentions": {"roles": [MENTION_ROLE]},
    }


def parse_message(event: Any) -> dict[str, Any]:
    message = sns_message(event)
    fields: list[dict[str, Any]] = []
    trigger = None
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
            if isinstance(alarm.get("Trigger"), dict):
                trigger = alarm["Trigger"]

    mention = f"<@&{MENTION_ROLE}> " if notify else "🟢 "
    return {
        "content": mention + summary,
        "embed": {
            "color": color,
            "description": f"```json\n{dump}\n```" if dump else "",
            "fields": fields,
        },
        "trigger": trigger,
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


def build_chart_widget(trigger: dict[str, Any]) -> dict[str, Any] | None:
    namespace = trigger.get("Namespace")
    metric_name = trigger.get("MetricName")
    period = trigger.get("Period")
    if (
        not isinstance(namespace, str)
        or not isinstance(metric_name, str)
        or not isinstance(period, int)
    ):
        return None

    stat = trigger.get("ExtendedStatistic")
    if not isinstance(stat, str):
        raw_stat = trigger.get("Statistic")
        stat = (
            STAT_NAMES.get(raw_stat.upper(), raw_stat)
            if isinstance(raw_stat, str)
            else None
        )
    if stat is None:
        return None

    dimensions = []
    for dimension in trigger.get("Dimensions") or []:
        name = dimension.get("name") if isinstance(dimension, dict) else None
        value = dimension.get("value") if isinstance(dimension, dict) else None
        if isinstance(name, str) and isinstance(value, str):
            dimensions.extend([name, value])

    evaluation_periods = trigger.get("EvaluationPeriods")
    periods = evaluation_periods if isinstance(evaluation_periods, int) else 1
    window_minutes = max(
        CHART_MIN_MINUTES,
        min(CHART_MAX_MINUTES, period * periods * CHART_WINDOW_MULTIPLIER // 60),
    )

    widget: dict[str, Any] = {
        "metrics": [
            [namespace, metric_name, *dimensions, {"stat": stat, "period": period}]
        ],
        "view": "timeSeries",
        "width": 600,
        "height": 200,
        "start": f"-PT{window_minutes}M",
        "end": "PT0H",
    }
    threshold = trigger.get("Threshold")
    if isinstance(threshold, (int, float)):
        widget["annotations"] = {
            "horizontal": [{"value": threshold, "label": "Threshold"}]
        }
    return widget


def fetch_chart_image(trigger: dict[str, Any]) -> bytes | None:
    widget = build_chart_widget(trigger)
    if widget is None:
        return None
    try:
        response = cloudwatch.get_metric_widget_image(
            MetricWidget=json.dumps(widget), OutputFormat="png"
        )
        return response["MetricWidgetImage"]
    except Exception:
        # A chart is a nice-to-have; never let it block the alarm itself.
        return None


def encode_multipart(payload: dict[str, Any], image: bytes) -> bytes:
    return (
        (
            f"--{MULTIPART_BOUNDARY}\r\n"
            'Content-Disposition: form-data; name="payload_json"\r\n'
            "Content-Type: application/json\r\n\r\n"
        ).encode()
        + json.dumps(payload).encode()
        + (
            f"\r\n--{MULTIPART_BOUNDARY}\r\n"
            'Content-Disposition: form-data; name="files[0]"; filename="chart.png"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode()
        + image
        + f"\r\n--{MULTIPART_BOUNDARY}--\r\n".encode()
    )
