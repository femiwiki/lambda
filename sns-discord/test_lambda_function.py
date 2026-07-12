import json
import unittest
from io import BytesIO
from unittest.mock import patch

from lambda_function import (
    GRAY,
    GREEN,
    RED,
    build_chart_widget,
    encode_multipart,
    fetch_chart_image,
    message_to_fields,
    parse_message,
    value_to_string,
)

FULL_TRIGGER = {
    "ComparisonOperator": "LessThanThreshold",
    "DatapointsToAlarm": 1,
    "Dimensions": [{"name": "InstanceId", "value": "i-0d6c06981a9aa5112"}],
    "EvaluationPeriods": 1,
    "MetricName": "CPUCreditBalance",
    "Namespace": "AWS/EC2",
    "Period": 300,
    "Statistic": "MINIMUM",
    "Threshold": 72.0,
}


class ParseMessageTest(unittest.TestCase):
    def test_arbitrary_message(self):
        post_data = parse_message("arbitrary")
        self.assertEqual(
            post_data["content"],
            "<@&678974055365476392> 알지 못하는 유형의 이벤트가 발생했습니다.",
            "An arbitrary message should be parsed as an alarm.",
        )
        self.assertEqual(post_data["embed"]["color"], RED)

    def test_alarm(self):
        post_data = parse_message(
            {
                "Records": [
                    {
                        "Sns": {
                            "Message": json.dumps(
                                {
                                    "NewStateValue": "ALARM",
                                    "AlarmName": "Femiwiki CPU credit balance",
                                    "NewStateReason": "Threshold Crossed: 1 out of the last 1 datapoints was less than the threshold.",
                                }
                            )
                        }
                    }
                ]
            }
        )
        self.assertEqual(
            post_data["content"],
            "<@&678974055365476392> [Femiwiki CPU credit balance] Threshold Crossed: 1 out of the last 1 datapoints was less than the threshold.",
            "An alarm should be parsed as an alarm.",
        )
        self.assertEqual(post_data["embed"]["color"], RED)

    def test_ok(self):
        post_data = parse_message(
            {
                "Records": [
                    {
                        "Sns": {
                            "Message": json.dumps(
                                {
                                    "AlarmName": "Test",
                                    "NewStateValue": "OK",
                                }
                            )
                        }
                    }
                ]
            }
        )
        self.assertEqual(
            post_data["content"],
            "🟢 [Test] (메시지에 NewStateReason이 없습니다)",
            "An OK should be parsed as an OK.",
        )
        self.assertEqual(post_data["embed"]["color"], GREEN)

    def test_full_alarm(self):
        post_data = parse_message(
            {
                "Records": [
                    {
                        "Sns": {
                            "Message": json.dumps(
                                {
                                    "AWSAccountId": "302617221463",
                                    "AlarmActions": [
                                        "arn:aws:sns:ap-northeast-1:302617221463:CloudWatch_Alarms_Topic"
                                    ],
                                    "AlarmArn": "arn:aws:cloudwatch:ap-northeast-1:302617221463:alarm:Femiwiki CPU credit balance",
                                    "AlarmConfigurationUpdatedTimestamp": "2021-05-17T02:32:05.144+0000",
                                    "AlarmDescription": None,
                                    "AlarmName": "Femiwiki CPU credit balance",
                                    "InsufficientDataActions": [],
                                    "NewStateReason": "Threshold Crossed: 1 out of the last 1 datapoints [71.58514626666667 (09/04/22 21:01:00)] was less than the threshold (72.0) (minimum 1 datapoint for OK -> ALARM transition).",
                                    "NewStateValue": "ALARM",
                                    "OKActions": [],
                                    "OldStateValue": "OK",
                                    "Region": "Asia Pacific (Tokyo)",
                                    "StateChangeTime": "2022-04-09T21:06:48.198+0000",
                                    "Trigger": {
                                        "ComparisonOperator": "LessThanThreshold",
                                        "DatapointsToAlarm": 1,
                                        "Dimensions": [
                                            {
                                                "name": "InstanceId",
                                                "value": "i-0d6c06981a9aa5112",
                                            }
                                        ],
                                        "EvaluateLowSampleCountPercentile": "",
                                        "EvaluationPeriods": 1,
                                        "MetricName": "CPUCreditBalance",
                                        "Namespace": "AWS/EC2",
                                        "Period": 300,
                                        "Statistic": "MINIMUM",
                                        "StatisticType": "Statistic",
                                        "Threshold": 72.0,
                                        "TreatMissingData": "missing",
                                        "Unit": None,
                                    },
                                }
                            )
                        }
                    }
                ]
            }
        )
        self.assertEqual(
            post_data["content"],
            "<@&678974055365476392> [Femiwiki CPU credit balance] Threshold Crossed: 1 out of the last 1 datapoints [71.58514626666667 (09/04/22 21:01:00)] was less than the threshold (72.0) (minimum 1 datapoint for OK -> ALARM transition).",
        )
        self.assertEqual(post_data["embed"]["color"], RED)
        self.assertEqual(post_data["trigger"]["MetricName"], "CPUCreditBalance")

    def test_test_alarm(self):
        post_data = parse_message(
            {
                "Records": [
                    {
                        "Sns": {
                            "Message": json.dumps(
                                {
                                    "AlarmName": "_Test",
                                    "NewStateValue": "OK",
                                    "NewStateReason": "테스트",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        )
        self.assertEqual(
            post_data["content"],
            "🟢 [_Test] 테스트",
            "A test alarm should be parsed as a test alarm.",
        )
        self.assertEqual(post_data["embed"]["color"], GRAY)
        self.assertIsNone(post_data["trigger"])


class MessageToFieldsTest(unittest.TestCase):
    def test_message_to_fields(self):
        fields = message_to_fields(
            {
                "NewStateValue": "ALARM",
                "OldStateValue": "OK",
                "NewStateReason": "Threshold Crossed: 1 out of the last 1 datapoints [71.58514626666667 (09/04/22 21:01:00)] was less than the threshold (72.0) (minimum 1 datapoint for OK -> ALARM transition).",
            }
        )
        self.assertEqual(len(fields), 2, "NewStateReason should be removed")
        self.assertEqual(fields[1]["value"], "OK")

        fields = message_to_fields(
            {
                "InsufficientDataActions": [],
                "OKActions": [],
            }
        )
        self.assertEqual(len(fields), 2)
        self.assertEqual(fields[1]["value"], "`[]`")


class ValueToStringTest(unittest.TestCase):
    def test_value_to_string(self):
        self.assertEqual(value_to_string(None), ("`null`", True))
        self.assertEqual(value_to_string("Foo"), ("Foo", True))
        self.assertEqual(
            value_to_string(["Foo", "Bar"]),
            ('```json\n[\n  "Foo",\n  "Bar"\n]\n```', False),
        )
        self.assertEqual(
            value_to_string({"Foo": "bar"}),
            ('```json\n{\n  "Foo": "bar"\n}\n```', False),
        )


class BuildChartWidgetTest(unittest.TestCase):
    def test_full_trigger(self):
        widget = build_chart_widget(FULL_TRIGGER)
        assert widget is not None
        self.assertEqual(
            widget["metrics"],
            [
                [
                    "AWS/EC2",
                    "CPUCreditBalance",
                    "InstanceId",
                    "i-0d6c06981a9aa5112",
                    {"stat": "Minimum", "period": 300},
                ]
            ],
        )
        self.assertEqual(
            widget["annotations"]["horizontal"], [{"value": 72.0, "label": "Threshold"}]
        )
        self.assertEqual(widget["end"], "PT0H")

    def test_extended_statistic(self):
        trigger = {**FULL_TRIGGER, "Statistic": None, "ExtendedStatistic": "p90"}
        widget = build_chart_widget(trigger)
        assert widget is not None
        self.assertEqual(widget["metrics"][0][-1]["stat"], "p90")

    def test_missing_metric_name(self):
        trigger = {**FULL_TRIGGER, "MetricName": None}
        self.assertIsNone(build_chart_widget(trigger))

    def test_missing_statistic(self):
        trigger = {**FULL_TRIGGER, "Statistic": None}
        self.assertIsNone(build_chart_widget(trigger))

    def test_window_clamped_to_minimum(self):
        trigger = {**FULL_TRIGGER, "Period": 10, "EvaluationPeriods": 1}
        widget = build_chart_widget(trigger)
        assert widget is not None
        self.assertEqual(widget["start"], "-PT30M")

    def test_window_clamped_to_maximum(self):
        trigger = {**FULL_TRIGGER, "Period": 3600, "EvaluationPeriods": 100}
        widget = build_chart_widget(trigger)
        assert widget is not None
        self.assertEqual(widget["start"], "-PT360M")


class EncodeMultipartTest(unittest.TestCase):
    def test_contains_payload_and_image(self):
        body = encode_multipart({"content": "hi"}, b"\x89PNG...")
        self.assertIn(b'name="payload_json"', body)
        self.assertIn(b'{"content": "hi"}', body)
        self.assertIn(b'name="files[0]"; filename="chart.png"', body)
        self.assertIn(b"\x89PNG...", body)


class FetchChartImageTest(unittest.TestCase):
    def test_no_widget_skips_api_call(self):
        with patch("lambda_function.cloudwatch") as mock_cloudwatch:
            result = fetch_chart_image({})
        self.assertIsNone(result)
        mock_cloudwatch.get_metric_widget_image.assert_not_called()

    def test_api_error_returns_none(self):
        with patch("lambda_function.cloudwatch") as mock_cloudwatch:
            mock_cloudwatch.get_metric_widget_image.side_effect = Exception("boom")
            result = fetch_chart_image(FULL_TRIGGER)
        self.assertIsNone(result)

    def test_success_returns_image_bytes(self):
        with patch("lambda_function.cloudwatch") as mock_cloudwatch:
            mock_cloudwatch.get_metric_widget_image.return_value = {
                "MetricWidgetImage": BytesIO(b"\x89PNG...")
            }
            result = fetch_chart_image(FULL_TRIGGER)
        self.assertEqual(result, b"\x89PNG...")


if __name__ == "__main__":
    unittest.main()
