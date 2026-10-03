"""Post CloudWatch alarm and AWS Budgets notifications from SNS to Discord in Korean."""

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
import hgtk

RED = 0xE83535
GREEN = 0x2DAF32
GRAY = 0xB3B4BC

MENTION_ROLE = "678974055365476392"

STAT_NAMES = {
    "SAMPLECOUNT": "SampleCount",
    "AVERAGE": "Average",
    "SUM": "Sum",
    "MINIMUM": "Minimum",
    "MAXIMUM": "Maximum",
}
STAT_KOREAN = {
    "SAMPLECOUNT": "표본 수",
    "AVERAGE": "평균",
    "SUM": "합계",
    "MINIMUM": "최솟값",
    "MAXIMUM": "최댓값",
}
COMPARISON_KOREAN = {
    "GreaterThanThreshold": "초과",
    "GreaterThanOrEqualToThreshold": "이상",
    "LessThanThreshold": "미만",
    "LessThanOrEqualToThreshold": "이하",
}
KST = timezone(timedelta(hours=9), "KST")
# A datapoint in NewStateReason, e.g. 3184.0 (03/10/26 01:37:00), in UTC
DATAPOINT = re.compile(r"(-?[\d.]+(?:E-?\d+)?) \((\d\d/\d\d/\d\d \d\d:\d\d:\d\d)\)")
CHART_WINDOW_MULTIPLIER = 12
CHART_MIN_MINUTES = 30
CHART_MAX_MINUTES = 360
MULTIPART_BOUNDARY = "----snsdiscordboundary"

cloudwatch = boto3.client("cloudwatch")


def lambda_handler(event: Any, context: Any) -> None:
    webhook_url = os.environ["WEBHOOK_URL"]
    post_data = parse_message(event)

    trigger = post_data["trigger"]
    chart = fetch_chart_image(trigger, post_data["region"]) if trigger else None
    payload = build_payload(post_data, chart)

    if chart is None:
        body = json.dumps(payload).encode()
        content_type = "application/json"
    else:
        body = encode_multipart(payload, chart)
        content_type = f"multipart/form-data; boundary={MULTIPART_BOUNDARY}"

    request = urllib.request.Request(
        webhook_url,
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
        embed = {**embed, "image": {"url": "attachment://chart.png"}}
    return {
        "content": post_data["content"],
        "embeds": [embed],
        "allowed_mentions": {"roles": [MENTION_ROLE]},
    }


def parse_message(event: Any) -> dict[str, Any]:
    message = sns_message(event)
    trigger = None
    region = None
    if message is None:
        notify = True
        color = RED
        summary = "알지 못하는 유형의 이벤트가 발생했습니다."
        description = code_block(json.dumps(event, indent=2, ensure_ascii=False))
    else:
        try:
            parsed = json.loads(message)
        except ValueError:
            notify = True
            color = RED
            budget = budget_summary(message)
            summary = budget or ""
            description = "" if budget else code_block(message)
        else:
            alarm = parsed if isinstance(parsed, dict) else {}
            test = alarm.get("AlarmName") == "_Test"
            notify = alarm.get("NewStateValue") != "OK" and not test
            color = GRAY if test else RED if notify else GREEN
            summary = alarm_summary(alarm)
            # Written by whoever made the alarm, so already in their words
            alarm_description = alarm.get("AlarmDescription")
            description = (
                alarm_description if isinstance(alarm_description, str) else ""
            )
            if isinstance(alarm.get("Trigger"), dict):
                trigger = alarm["Trigger"]
            region = alarm_region(alarm)

    mention = f"<@&{MENTION_ROLE}> " if notify else "🟢 "
    return {
        "content": mention + summary,
        "embed": {"color": color, "description": description},
        "trigger": trigger,
        "region": region,
    }


def budget_summary(message: str) -> str | None:
    # AWS Budgets sends plain text with lines such as "Budget Name: lambda"
    lines = dict(line.split(": ", 1) for line in message.splitlines() if ": " in line)
    name = lines.get("Budget Name")
    kind = lines.get("Alert Type")
    threshold = lines.get("Alert Threshold")
    if name is None or kind is None or threshold is None:
        return None
    noun = {"ACTUAL": "실제", "FORECASTED": "예상"}.get(kind, kind)
    summary = f"[예산 {name}] {noun} 비용이 알림 기준({threshold})을 넘었습니다."
    figures = [
        f"{label} {amount}"
        for label, amount in (
            (noun, lines.get(f"{kind} Amount")),
            ("예산", lines.get("Budgeted Amount")),
        )
        if amount is not None
    ]
    return f"{summary} {', '.join(figures)}." if figures else summary


def alarm_region(alarm: dict[str, Any]) -> str | None:
    # arn:aws:cloudwatch:<region>:<account>:alarm:<name>
    arn = alarm.get("AlarmArn")
    parts = arn.split(":") if isinstance(arn, str) else []
    return parts[3] if len(parts) > 3 and parts[3] else None


def alarm_summary(alarm: dict[str, Any]) -> str:
    name = alarm.get("AlarmName")
    state = {"ALARM": "경보", "OK": "해제", "INSUFFICIENT_DATA": "데이터 부족"}.get(
        alarm.get("NewStateValue"), "상태 알 수 없음"
    )
    when = parse_time(alarm.get("StateChangeTime"), "%Y-%m-%dT%H:%M:%S.%f%z")
    head = "[{}] {}".format(
        name if isinstance(name, str) else "(메시지에 AlarmName이 없습니다)",
        state if when is None else f"{state}, {when:%-m월 %-d일 %H:%M} KST",
    )
    trigger = alarm.get("Trigger")
    condition = (
        alarm_condition(trigger, alarm.get("NewStateValue"))
        if isinstance(trigger, dict)
        else None
    )
    reason = alarm.get("NewStateReason")
    datapoints = recent_datapoints(reason) if isinstance(reason, str) else None
    return " ".join(
        part
        for part in (head + ".", condition, datapoints and f"최근 값 {datapoints}.")
        if part
    )


def alarm_condition(trigger: dict[str, Any], state: Any) -> str | None:
    period = trigger.get("Period")
    threshold = trigger.get("Threshold")
    comparison = COMPARISON_KOREAN.get(trigger.get("ComparisonOperator"))
    raw_stat = trigger.get("ExtendedStatistic") or trigger.get("Statistic")
    stat = (
        STAT_KOREAN.get(raw_stat.upper(), f"{raw_stat} 값")
        if isinstance(raw_stat, str)
        else None
    )
    if (
        not isinstance(period, int)
        or not isinstance(threshold, (int, float))
        or comparison is None
        or stat is None
    ):
        return None

    rule = f"기준({format_number(threshold)} {comparison})"
    subject = f"{format_period(period)} {hgtk.josa.attach(stat, hgtk.josa.I_GA)}"
    if state == "ALARM":
        evaluated = trigger.get("EvaluationPeriods")
        breached = trigger.get("DatapointsToAlarm") or evaluated
        if isinstance(evaluated, int) and isinstance(breached, int):
            return f"{subject} 최근 {evaluated}번 중 {breached}번 {rule}에 걸렸습니다."
        return f"{subject} {rule}에 걸렸습니다."
    if state == "OK":
        return f"{subject} {rule}에 걸리지 않습니다."
    return f"{rule}을 판단할 데이터가 모자랍니다."


def recent_datapoints(reason: str) -> str | None:
    points = []
    for value, stamp in DATAPOINT.findall(reason):
        when = parse_time(stamp + "+0000", "%d/%m/%y %H:%M:%S%z")
        try:
            number = float(value)
        except ValueError:
            continue
        points.append(
            format_number(number)
            if when is None
            else f"{format_number(number)}({when:%H:%M})"
        )
    return ", ".join(points) or None


def parse_time(value: Any, pattern: str) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, pattern).astimezone(KST)
    except ValueError:
        return None


def format_period(seconds: int) -> str:
    if seconds % 86400 == 0:
        return f"{seconds // 86400}일"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}시간"
    if seconds % 60 == 0:
        return f"{seconds // 60}분"
    return f"{seconds}초"


def format_number(value: float) -> str:
    if value == int(value):
        return f"{int(value):,}"
    if abs(value) >= 1:
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"{value:.3g}"


def sns_message(event: Any) -> str | None:
    try:
        message = event["Records"][0]["Sns"]["Message"]
    except (KeyError, IndexError, TypeError):
        return None
    return message if isinstance(message, str) else None


def code_block(text: str) -> str:
    return f"```json\n{text}\n```"


def build_chart_widget(
    trigger: dict[str, Any], region: str | None = None
) -> dict[str, Any] | None:
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
    # The alarm's metrics may live in another region than this function.
    if region:
        widget["region"] = region
    threshold = trigger.get("Threshold")
    if isinstance(threshold, (int, float)):
        widget["annotations"] = {"horizontal": [{"value": threshold, "label": "기준"}]}
    return widget


def fetch_chart_image(
    trigger: dict[str, Any], region: str | None = None
) -> bytes | None:
    widget = build_chart_widget(trigger, region)
    if widget is None:
        return None
    try:
        response = cloudwatch.get_metric_widget_image(
            MetricWidget=json.dumps(widget), OutputFormat="png"
        )
        return response["MetricWidgetImage"]
    except Exception:  # noqa: BLE001
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
