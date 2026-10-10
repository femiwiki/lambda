import os
import unittest
from unittest.mock import MagicMock, patch

import lambda_function
from lambda_function import (
    boost,
    fetch_mentions,
    lambda_handler,
    normalize_acct,
    parse_allowed,
)
from mastodon import MastodonAPIError, MastodonNotFoundError
from mastodon.return_types import Notification
from mastodon.types_base import try_cast_recurse

INSTANCE = "https://mastodon.social"


def notification(
    notification_id: str,
    acct: str,
    visibility: str = "public",
    reblogged: bool = False,
) -> Notification:
    status_id = str(int(notification_id) + 300)
    account = {"id": "1", "acct": acct, "username": acct, "display_name": ""}
    return try_cast_recurse(
        Notification,
        {
            "id": notification_id,
            "type": "mention",
            "created_at": "2026-09-30T00:01:00.000Z",
            "account": account,
            "status": {
                "id": status_id,
                "created_at": "2026-09-30T00:01:00.000Z",
                "visibility": visibility,
                "reblogged": reblogged,
                "url": f"https://mastodon.social/@{acct}/{status_id}",
                "content": "<p>확인했습니다</p>",
                "account": account,
            },
        },
    )


class NormalizeAcctTest(unittest.TestCase):
    def test_local_account_gets_the_instance(self):
        self.assertEqual(
            normalize_acct("@Lens0021", INSTANCE), "lens0021@mastodon.social"
        )

    def test_allowed_list(self):
        self.assertEqual(
            parse_allowed(" a , b@example.org,", INSTANCE),
            {"a@mastodon.social", "b@example.org"},
        )


class FetchMentionsTest(unittest.TestCase):
    def test_pages_oldest_first(self):
        pages = {
            "10": [notification("12", "a"), notification("11", "a")],
            "12": [notification("13", "a")],
            "13": [],
        }
        mastodon = MagicMock()
        mastodon.notifications.side_effect = lambda **kwargs: pages[kwargs["min_id"]]
        self.assertEqual(
            [n.id for n in fetch_mentions(mastodon, "10")], ["11", "12", "13"]
        )


@patch("builtins.print")
class BoostTest(unittest.TestCase):
    def boost(self, mastodon=None, **kwargs) -> MagicMock:
        mastodon = mastodon or MagicMock()
        boost(notification("1", "lens0021", **kwargs).status, mastodon)
        return mastodon

    def test_boosts_public_and_unlisted(self, _):
        for visibility in ("public", "unlisted"):
            mastodon = self.boost(visibility=visibility)
            mastodon.status_reblog.assert_called_once_with("301")

    def test_skips_private_and_direct(self, _):
        for visibility in ("private", "direct"):
            self.boost(visibility=visibility).status_reblog.assert_not_called()

    def test_skips_what_is_already_boosted(self, _):
        self.boost(reblogged=True).status_reblog.assert_not_called()

    def test_a_deleted_status_is_skipped(self, print_):
        mastodon = MagicMock()
        mastodon.status_reblog.side_effect = MastodonNotFoundError("gone")
        self.boost(mastodon)
        self.assertIn('"reason": "deleted"', print_.call_args.args[0])

    def test_a_missing_scope_fails(self, _):
        mastodon = MagicMock()
        mastodon.status_reblog.side_effect = MastodonAPIError(
            "Mastodon API returned error", 403, "Forbidden", "outside the scopes"
        )
        with self.assertRaises(MastodonAPIError):
            self.boost(mastodon)


ENV = {
    "MASTODON_INSTANCE": INSTANCE,
    "MASTODON_TOKEN": "m",
    "ALLOWED_ACCTS": "lens0021",
    "CURSOR_PARAMETER": "/mastodon-boost/cursor",
}


@patch.dict(os.environ, ENV)
@patch("builtins.print")
class HandlerTest(unittest.TestCase):
    def run_handler(self, cursor, pages, reblog=None):
        mastodon = MagicMock()
        mastodon.notifications.side_effect = lambda **kwargs: pages[
            kwargs.get("min_id")
        ]
        mastodon.status_reblog.side_effect = reblog
        self.mastodon = mastodon
        self.written = written = []
        with (
            patch.object(lambda_function, "Mastodon", return_value=mastodon),
            patch.object(lambda_function, "read_cursor", return_value=cursor),
            patch.object(
                lambda_function, "write_cursor", lambda p, v: written.append(v)
            ),
        ):
            lambda_handler({}, None)

    def test_first_run_only_sets_the_cursor(self, _):
        self.run_handler(None, {None: [notification("50", "a")]})
        self.mastodon.status_reblog.assert_not_called()
        self.assertEqual(self.written, ["50"])

    def test_boosts_only_allowed_accounts(self, _):
        self.run_handler(
            "10",
            {
                "10": [notification("12", "someone"), notification("11", "lens0021")],
                "12": [],
            },
        )
        self.mastodon.status_reblog.assert_called_once_with("311")
        self.assertEqual(self.written, ["11", "12"])

    def test_a_failure_keeps_the_cursor_on_that_mention(self, _):
        def reblog(status_id):
            if status_id == "312":
                raise MastodonAPIError("error", 503, "Unavailable", None)

        with self.assertRaises(MastodonAPIError):
            self.run_handler(
                "10",
                {
                    "10": [
                        notification("11", "lens0021"),
                        notification("12", "lens0021"),
                    ],
                    "12": [],
                },
                reblog,
            )
        self.assertEqual(self.written, ["11"])


if __name__ == "__main__":
    unittest.main()
