import io
import json
import os
import unittest
import urllib.parse
from unittest.mock import MagicMock, patch

import lambda_function
from lambda_function import build_email, lambda_handler

VERP = "wiki-femiwiki-1x-t2b0k8-AbCdEfGh+/IjKlMn@femiwiki.com"


def notification(
    source: str = VERP, bounce_type: str = "Permanent", **recipient: str
) -> dict:
    return {
        "notificationType": "Bounce",
        "bounce": {
            "bounceType": bounce_type,
            "bounceSubType": "General",
            "bouncedRecipients": [
                {
                    "emailAddress": "someone@example.com",
                    "action": "failed",
                    "status": "5.1.1",
                    "diagnosticCode": "smtp; 550 5.1.1 user unknown",
                    **recipient,
                }
            ],
            "timestamp": "2026-10-10T05:31:09.767Z",
            "feedbackId": "0100-abc",
        },
        "mail": {"source": source, "destination": ["someone@example.com"]},
    }


def event(*notifications: dict) -> dict:
    return {"Records": [{"Sns": {"Message": json.dumps(n)}} for n in notifications]}


def response(body: dict) -> MagicMock:
    r = MagicMock()
    r.__enter__.return_value = io.BytesIO(json.dumps(body).encode())
    return r


class TestBuildEmail(unittest.TestCase):
    def test_reports_the_verp_address_and_status(self):
        email = build_email(notification())
        assert email is not None
        lines = email.split("\n")
        self.assertEqual(lines[0], f"To: {VERP}")
        self.assertEqual(
            lines[1], "Subject: Permanent/General: smtp; 550 5.1.1 user unknown"
        )
        self.assertIn("Status: 5.1.1", lines)
        self.assertIn(
            'Content-Type: multipart/report; report-type=delivery-status; boundary="ses"',
            lines,
        )

    def test_keeps_the_subject_on_one_line(self):
        email = build_email(notification(diagnosticCode="smtp; 550\r\n user unknown"))
        assert email is not None
        self.assertEqual(
            email.split("\n")[1], "Subject: Permanent/General: smtp; 550 user unknown"
        )

    def test_falls_back_to_a_permanent_status(self):
        email = build_email(notification(status=""))
        assert email is not None
        self.assertIn("Status: 5.0.0", email.split("\n"))

    def test_skips_transient_bounces(self):
        self.assertIsNone(build_email(notification(bounce_type="Transient")))

    def test_skips_mail_not_sent_from_a_verp_address(self):
        self.assertIsNone(build_email(notification(source="admin@femiwiki.com")))

    def test_skips_other_notifications(self):
        self.assertIsNone(build_email({"notificationType": "Complaint"}))


@patch.dict(
    os.environ,
    {
        "API_URL": "https://femiwiki.com/api.php",
        "TOKEN_PARAMETER": "/mediawiki/bounce_handler/token",
    },
)
class TestHandler(unittest.TestCase):
    def setUp(self):
        lambda_function._token = None
        ssm = patch.object(lambda_function, "ssm")
        self.ssm = ssm.start()
        self.ssm.get_parameter.return_value = {"Parameter": {"Value": "t0ken"}}
        self.addCleanup(ssm.stop)

    def test_posts_the_report_with_the_token(self):
        with patch.object(
            lambda_function.urllib.request,
            "urlopen",
            return_value=response({"bouncehandler": {"submitted": "job"}}),
        ) as urlopen:
            lambda_handler(event(notification()), None)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://femiwiki.com/api.php")
        self.assertNotIn("bot", request.get_header("User-agent").lower())
        form = urllib.parse.parse_qs(request.data.decode())
        self.assertEqual(form["action"], ["bouncehandler"])
        self.assertEqual(form["bouncehandlertoken"], ["t0ken"])
        self.assertTrue(form["email"][0].startswith(f"To: {VERP}\n"))
        self.ssm.get_parameter.assert_called_once_with(
            Name="/mediawiki/bounce_handler/token", WithDecryption=True
        )

    def test_raises_when_the_api_refuses_so_lambda_retries(self):
        with (
            patch.object(
                lambda_function.urllib.request,
                "urlopen",
                return_value=response({"error": {"code": "invalid-ip"}}),
            ),
            self.assertRaises(lambda_function.RefusedError),
        ):
            lambda_handler(event(notification()), None)

    def test_does_not_call_the_api_for_a_skipped_bounce(self):
        with patch.object(lambda_function.urllib.request, "urlopen") as urlopen:
            lambda_handler(event(notification(bounce_type="Transient")), None)
        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
