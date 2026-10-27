import json
import os
import unittest
from unittest.mock import MagicMock, patch

import lambda_function
from lambda_function import (
    authorized,
    lambda_handler,
    opens_to_everyone,
    record,
    record_finding,
    record_ingress,
    render,
    title,
)

BOT = "femiwiki-alerts[bot]"


def alert(
    status: str = "firing", starts: str = "2026-10-01T12:00:00.123Z", **annotations: str
) -> dict:
    return {
        "status": status,
        "labels": {"alertname": "Disk almost full"},
        "annotations": {"summary": "/ is 92% full", **annotations},
        "startsAt": starts,
        "endsAt": "2026-10-01T13:30:00Z"
        if status == "resolved"
        else "0001-01-01T00:00:00Z",
        "generatorURL": "https://femiwiki.grafana.net/alerting/grafana/abc/view",
        "fingerprint": "a1b2c3d4e5f60718",
    }


def payload(status: str = "firing") -> dict:
    return {
        "status": status,
        "alerts": [alert(status)],
        "groupLabels": {"alertname": "Disk almost full"},
        "commonLabels": {"alertname": "Disk almost full", "impact": "operators"},
    }


def issue(title: str, pull_request: object = None, author: str = BOT) -> MagicMock:
    i = MagicMock()
    i.title = title
    i.user.login = author
    i.pull_request = pull_request
    return i


def repo(open_issues: list[MagicMock]) -> MagicMock:
    r = MagicMock()
    r.get_issues.return_value = open_issues
    return r


FINDING: dict = {
    "source": "aws.access-analyzer",
    "detail-type": "Access Analyzer Finding",
    "time": "2026-10-15T11:00:00Z",
    "region": "ap-northeast-2",
    "detail": {
        "id": "0f1e2d3c-aaaa-bbbb-cccc-000000000001",
        "status": "ACTIVE",
        "resourceType": "AWS::S3::Bucket",
        "resource": "arn:aws:s3:::secret-bucket",
        "principal": {"AWS": "*"},
    },
}


def finding(**detail: object) -> dict:
    return {**FINDING, "detail": {**FINDING["detail"], **detail}}


class TestRecordFinding(unittest.TestCase):
    def test_opens_an_issue_without_details(self):
        r = repo([])
        record_finding(r, FINDING, "@femiwiki/pager")
        kwargs = r.create_issue.call_args.kwargs
        self.assertEqual(kwargs["title"], "External access finding (10/15 20:00 KST)")
        self.assertIn("1 new external access finding", kwargs["body"])
        self.assertTrue(kwargs["body"].endswith("\n\n@femiwiki/pager"))
        self.assertEqual(kwargs["labels"], ["alert"])
        self.assertIn("Finding 0f1e2d3c-aaaa-bbbb-cccc-000000000001", kwargs["body"])
        for secret in ("secret-bucket", "S3", "*"):
            self.assertNotIn(secret, kwargs["title"] + kwargs["body"])

    def test_skips_a_finding_that_already_has_an_issue(self):
        existing = issue("External access finding (10/15 20:00 KST)")
        existing.body = "Finding 0f1e2d3c-aaaa-bbbb-cccc-000000000001"
        r = repo([existing])
        record_finding(r, FINDING)
        r.create_issue.assert_not_called()
        existing.edit.assert_not_called()

    def test_closes_the_issue_when_resolved(self):
        existing = issue("External access finding (10/15 20:00 KST)")
        existing.body = "Finding 0f1e2d3c-aaaa-bbbb-cccc-000000000001"
        r = repo([existing])
        resolved = finding(status="RESOLVED")
        record_finding(r, resolved)
        existing.edit.assert_called_once_with(state="closed", state_reason="completed")
        r.create_issue.assert_not_called()

    def test_closes_the_issue_when_deleted(self):
        existing = issue("External access finding (10/15 20:00 KST)")
        existing.body = "Finding 0f1e2d3c-aaaa-bbbb-cccc-000000000001"
        deleted = finding(isDeleted=True)
        record_finding(repo([existing]), deleted)
        existing.edit.assert_called_once()

    def test_ignores_an_archived_finding_without_an_issue(self):
        r = repo([])
        archived = finding(status="ARCHIVED")
        record_finding(r, archived)
        r.create_issue.assert_not_called()

    def test_handler_skips_the_token_for_eventbridge(self):
        r = repo([])
        with (
            patch.dict(os.environ, {"GITHUB_MENTION": ""}, clear=False),
            patch.object(lambda_function, "connect", return_value=(r, BOT)),
        ):
            self.assertEqual(lambda_handler(FINDING, None), {"statusCode": 204})
        r.create_issue.assert_called_once()


INGRESS: dict = {
    "source": "aws.ec2",
    "detail-type": "AWS API Call via CloudTrail",
    "region": "ap-northeast-1",
    "detail": {
        "eventSource": "ec2.amazonaws.com",
        "eventName": "AuthorizeSecurityGroupIngress",
        "eventTime": "2026-10-15T11:00:00Z",
        "awsRegion": "ap-northeast-1",
        "eventID": "11111111-2222-3333-4444-555555555555",
        "requestParameters": {
            "groupId": "sg-0123456789abcdef0",
            "ipPermissions": {
                "items": [
                    {
                        "ipProtocol": "tcp",
                        "fromPort": 2376,
                        "toPort": 2376,
                        "ipRanges": {"items": [{"cidrIp": "0.0.0.0/0"}]},
                    }
                ]
            },
        },
    },
}


def ingress(**request: object) -> dict:
    return {**INGRESS, "detail": {**INGRESS["detail"], "requestParameters": request}}


class TestOpensToEveryone(unittest.TestCase):
    def test_finds_either_family_at_any_depth(self):
        self.assertTrue(opens_to_everyone(INGRESS["detail"]["requestParameters"]))
        self.assertTrue(
            opens_to_everyone(
                {"ModifySecurityGroupRulesRequest": {"Rules": [{"CidrIpv6": "::/0"}]}}
            )
        )

    def test_ignores_narrower_ranges(self):
        self.assertFalse(opens_to_everyone({"cidrIp": "13.209.1.56/29"}))
        self.assertFalse(opens_to_everyone({"cidrIp": "0.0.0.0/1"}))
        self.assertFalse(opens_to_everyone(None))


class TestRecordIngress(unittest.TestCase):
    def test_opens_an_issue_without_details(self):
        r = repo([])
        record_ingress(r, INGRESS, "@femiwiki/pager")
        kwargs = r.create_issue.call_args.kwargs
        self.assertEqual(kwargs["title"], "World-open inbound rule (10/15 20:00 KST)")
        self.assertIn("in ap-northeast-1", kwargs["body"])
        self.assertIn("region=ap-northeast-1#/events/1111", kwargs["body"])
        self.assertIn("Event 11111111-2222-3333-4444-555555555555", kwargs["body"])
        self.assertTrue(kwargs["body"].endswith("\n\n@femiwiki/pager"))
        self.assertEqual(kwargs["labels"], ["alert"])
        for secret in ("sg-0123456789abcdef0", "2376", "tcp"):
            self.assertNotIn(secret, kwargs["title"] + kwargs["body"])

    def test_skips_an_event_that_already_has_an_issue(self):
        existing = issue("World-open inbound rule (10/15 20:00 KST)")
        existing.body = "Event 11111111-2222-3333-4444-555555555555"
        r = repo([existing])
        record_ingress(r, INGRESS)
        r.create_issue.assert_not_called()

    def test_handler_opens_an_issue_for_a_world_open_rule(self):
        r = repo([])
        with (
            patch.dict(os.environ, {"GITHUB_MENTION": ""}, clear=False),
            patch.object(lambda_function, "connect", return_value=(r, BOT)) as c,
        ):
            self.assertEqual(lambda_handler(INGRESS, None), {"statusCode": 204})
            narrow = ingress(groupId="sg-1", cidrIp="10.0.0.0/8")
            self.assertEqual(lambda_handler(narrow, None), {"statusCode": 204})
        c.assert_called_once()
        r.create_issue.assert_called_once()


class TestAuthorized(unittest.TestCase):
    def test_matches_only_the_bearer_token(self):
        self.assertTrue(authorized("Bearer s3cret", "s3cret"))
        self.assertFalse(authorized("Bearer wrong", "s3cret"))
        self.assertFalse(authorized("", "s3cret"))


class TestRecord(unittest.TestCase):
    def test_opens_an_issue_on_first_firing(self):
        r = repo([])
        record(r, BOT, payload())
        r.get_issues.assert_called_once_with(state="open")
        kwargs = r.create_issue.call_args.kwargs
        self.assertEqual(kwargs["title"], "Disk almost full (10/1 21:00 KST)")
        self.assertIn("/ is 92% full", kwargs["body"])
        self.assertEqual(kwargs["labels"], ["alert"])

    def test_mentions_the_team_only_when_opening(self):
        r = repo([])
        record(r, BOT, payload(), "@femiwiki/pager")
        self.assertTrue(
            r.create_issue.call_args.kwargs["body"].endswith("\n\n@femiwiki/pager")
        )
        existing = issue("Disk almost full")
        record(repo([existing]), BOT, payload(), "@femiwiki/pager")
        self.assertNotIn("@femiwiki/pager", existing.create_comment.call_args.args[0])

    def test_comments_on_the_open_issue_for_the_same_rule(self):
        existing = issue("Disk almost full")
        r = repo([existing])
        record(r, BOT, payload())
        existing.create_comment.assert_called_once()
        r.create_issue.assert_not_called()

    def test_comments_on_an_open_issue_titled_with_a_start_time(self):
        existing = issue("Disk almost full (9/30 08:15 KST)")
        r = repo([existing])
        record(r, BOT, payload())
        existing.create_comment.assert_called_once()
        r.create_issue.assert_not_called()

    def test_ignores_a_rule_whose_name_extends_this_one(self):
        other = issue("Disk almost full on /data (9/30 08:15 KST)")
        r = repo([other])
        record(r, BOT, payload())
        other.create_comment.assert_not_called()
        r.create_issue.assert_called_once()

    def test_ignores_pull_requests_other_titles_and_people(self):
        pr = issue("Disk almost full", pull_request=object())
        other = issue("Out of memory")
        human = issue("Disk almost full", author="lens0021")
        r = repo([pr, other, human])
        record(r, BOT, payload())
        for i in (pr, other, human):
            i.create_comment.assert_not_called()
        r.create_issue.assert_called_once()

    def test_resolve_comments_on_the_open_issue(self):
        existing = issue("Disk almost full")
        record(repo([existing]), BOT, payload("resolved"))
        text = existing.create_comment.call_args.args[0]
        self.assertTrue(text.startswith("Resolved at 2026-10-01 13:30 UTC."))

    def test_resolve_without_an_open_issue_does_nothing(self):
        r = repo([])
        record(r, BOT, payload("resolved"))
        r.create_issue.assert_not_called()


class TestTitle(unittest.TestCase):
    def test_uses_the_earliest_firing_start_in_kst(self):
        p = payload()
        p["alerts"] = [
            alert(starts="2026-10-01T18:00:00Z"),
            alert(starts="2026-10-01T16:30:00.123456789Z"),
            alert("resolved", starts="2026-10-01T10:00:00Z"),
        ]
        self.assertEqual(
            title("Disk almost full", p), "Disk almost full (10/2 01:30 KST)"
        )


class TestRender(unittest.TestCase):
    def test_lists_start_rule_logs_and_id(self):
        p = payload()
        p["alerts"] = [alert(logs="https://femiwiki.grafana.net/explore")]
        text = render(p)
        self.assertIn("- Started: 2026-10-01 12:00 UTC", text)
        self.assertIn(
            "- Rule: https://femiwiki.grafana.net/alerting/grafana/abc/view", text
        )
        self.assertIn("- Logs: https://femiwiki.grafana.net/explore", text)
        self.assertIn("- id a1b2c3d4", text)


class TestHandler(unittest.TestCase):
    @patch.dict(lambda_function._secrets, {"WEBHOOK_TOKEN": "s3cret"})
    def test_refuses_a_wrong_token(self):
        response = lambda_handler(
            {"headers": {"authorization": "Bearer nope"}, "body": "{}"}, None
        )
        self.assertEqual(response["statusCode"], 401)

    @patch.dict(
        os.environ,
        {
            "GITHUB_REPOSITORY": "femiwiki/infra",
            "GITHUB_APP_CLIENT_ID": "Iv23abc",
            "GITHUB_MENTION": "@femiwiki/pager",
        },
    )
    @patch.dict(
        lambda_function._secrets,
        {"WEBHOOK_TOKEN": "s3cret", "GITHUB_APP_PRIVATE_KEY": "unused"},
    )
    def test_records_as_the_app_on_the_repository(self):
        app = MagicMock()
        app.get_app.return_value.slug = "femiwiki-alerts"
        app.get_repo_installation.return_value.id = 42
        github = app.get_github_for_installation.return_value
        with (
            patch.object(lambda_function, "GithubIntegration", return_value=app),
            patch.object(lambda_function, "record") as recorded,
        ):
            response = lambda_handler(
                {
                    "headers": {"Authorization": "Bearer s3cret"},
                    "body": json.dumps(payload()),
                },
                None,
            )
        self.assertEqual(response["statusCode"], 204)
        app.get_repo_installation.assert_called_once_with("femiwiki", "infra")
        app.get_github_for_installation.assert_called_once_with(42, {"issues": "write"})
        github.get_repo.assert_called_once_with("femiwiki/infra")
        recorded.assert_called_once_with(
            github.get_repo.return_value, BOT, payload(), "@femiwiki/pager"
        )


class SecretTest(unittest.TestCase):
    @patch.dict(os.environ, {"WEBHOOK_TOKEN_PARAMETER": "/lambda/grafana-github/t"})
    @patch.dict(lambda_function._secrets, clear=True)
    def test_reads_the_named_parameter_once(self):
        with patch.object(lambda_function, "ssm") as ssm:
            ssm.get_parameter.return_value = {"Parameter": {"Value": "v"}}
            self.assertEqual(lambda_function.secret("WEBHOOK_TOKEN"), "v")
            self.assertEqual(lambda_function.secret("WEBHOOK_TOKEN"), "v")
        ssm.get_parameter.assert_called_once_with(
            Name="/lambda/grafana-github/t", WithDecryption=True
        )


if __name__ == "__main__":
    unittest.main()
