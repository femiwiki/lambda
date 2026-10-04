import json
import os
import unittest
from unittest.mock import MagicMock, patch

import lambda_function
from lambda_function import authorized, lambda_handler, record, render, title

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
    @patch.dict(os.environ, {"WEBHOOK_TOKEN": "s3cret"})
    def test_refuses_a_wrong_token(self):
        response = lambda_handler(
            {"headers": {"authorization": "Bearer nope"}, "body": "{}"}, None
        )
        self.assertEqual(response["statusCode"], 401)

    @patch.dict(
        os.environ,
        {
            "WEBHOOK_TOKEN": "s3cret",
            "GITHUB_REPOSITORY": "femiwiki/infra",
            "GITHUB_APP_CLIENT_ID": "Iv23abc",
            "GITHUB_APP_PRIVATE_KEY": "unused",
            "GITHUB_MENTION": "@femiwiki/pager",
        },
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


if __name__ == "__main__":
    unittest.main()
