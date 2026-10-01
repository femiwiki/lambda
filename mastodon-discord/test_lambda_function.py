import os
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import lambda_function
from lambda_function import (
    fetch_mentions,
    find_alert_message,
    format_content,
    html_to_text,
    lambda_handler,
    normalize_acct,
    parse_allowed,
    snowflake,
)
from mastodon.return_types import Notification, Status
from mastodon.types_base import try_cast_recurse

INSTANCE = "https://mastodon.social"


def account(acct: str) -> dict:
    return {"id": "1", "acct": acct, "username": acct, "display_name": ""}


def status(status_id: str, acct: str, in_reply_to_id: str | None = "200") -> dict:
    return {
        "id": status_id,
        "created_at": "2026-09-30T00:01:00.000Z",
        "in_reply_to_id": in_reply_to_id,
        "url": f"https://mastodon.social/@{acct}/{status_id}",
        "content": '<p><span class="h-card"><a href="https://mastodon.social/@femiwiki_status">@<span>femiwiki_status</span></a></span> 확인했습니다</p>',
        "account": account(acct),
    }


def notification(notification_id: str, acct: str, **kwargs) -> Notification:
    return try_cast_recurse(
        Notification,
        {
            "id": notification_id,
            "type": "mention",
            "created_at": "2026-09-30T00:01:00.000Z",
            "account": account(acct),
            "status": status(str(int(notification_id) + 300), acct, **kwargs),
        },
    )


ALERT_POST = try_cast_recurse(
    Status,
    {
        "id": "200",
        "created_at": "2026-09-30T00:00:05.000Z",
        "content": "<p>⚠️ 페미위키에 접속할 수 없습니다.</p><p>id a1b2c3d4</p>",
        "account": account("femiwiki_status"),
    },
)


def discord_message(message_id: str, timestamp: str, content: str) -> dict:
    return {"id": message_id, "timestamp": timestamp, "content": content}


def discord_session(messages: list[dict]) -> MagicMock:
    session = MagicMock()
    session.get.return_value.json.return_value = messages
    return session


class NormalizeAcctTest(unittest.TestCase):
    def test_local_account_gets_the_instance(self):
        self.assertEqual(
            normalize_acct("@Lens0021", INSTANCE), "lens0021@mastodon.social"
        )

    def test_remote_account_is_kept(self):
        self.assertEqual(
            normalize_acct("lens@example.org", INSTANCE), "lens@example.org"
        )

    def test_allowed_list(self):
        self.assertEqual(
            parse_allowed(" a , b@example.org,", INSTANCE),
            {"a@mastodon.social", "b@example.org"},
        )


class HtmlToTextTest(unittest.TestCase):
    def test_paragraphs_and_links(self):
        self.assertEqual(
            html_to_text('<p>one<br>two</p><p><a href="https://x.org">x</a></p>'),
            "one  \ntwo\n\n[x](https://x.org)",
        )


class FormatContentTest(unittest.TestCase):
    def test_strips_the_leading_mention(self):
        content = format_content(notification("1", "lens0021").status, INSTANCE)
        self.assertEqual(
            content,
            "**lens0021** (@lens0021@mastodon.social) 마스토돈 답글\n"
            "확인했습니다\n"
            "<https://mastodon.social/@lens0021/301>",
        )

    def test_stays_under_the_discord_limit(self):
        reply = try_cast_recurse(
            Status,
            {**status("301", "lens0021"), "content": "<p>" + "가" * 3000 + "</p>"},
        )
        self.assertLessEqual(len(format_content(reply, INSTANCE)), 2000)


class FindAlertMessageTest(unittest.TestCase):
    def test_picks_the_message_sent_with_the_post(self):
        session = discord_session(
            [
                # A repeat sent half an hour later carries the same id.
                discord_message("3", "2026-09-30T00:30:05+00:00", "-# id a1b2c3d4"),
                discord_message("2", "2026-09-30T00:00:06+00:00", "-# id a1b2c3d4"),
                discord_message("1", "2026-09-29T23:00:00+00:00", "-# id a1b2c3d4"),
                discord_message("4", "2026-09-30T00:00:05+00:00", "-# id ffffffff"),
            ]
        )
        self.assertEqual(find_alert_message(ALERT_POST, session, "42"), "2")
        self.assertEqual(
            session.get.call_args.kwargs["params"]["around"],
            snowflake(datetime(2026, 9, 30, 0, 0, 5, tzinfo=UTC)),
        )

    def test_no_id_in_the_parent(self):
        parent = try_cast_recurse(Status, {**ALERT_POST, "content": "<p>그냥 글</p>"})
        session = discord_session([])
        self.assertIsNone(find_alert_message(parent, session, "42"))
        session.get.assert_not_called()

    def test_no_matching_message(self):
        self.assertIsNone(find_alert_message(ALERT_POST, discord_session([]), "42"))


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


ENV = {
    "MASTODON_INSTANCE": INSTANCE,
    "MASTODON_TOKEN": "m",
    "ALLOWED_ACCTS": "lens0021",
    "CURSOR_PARAMETER": "/mastodon-discord/cursor",
    "DISCORD_BOT_TOKEN": "d",
    "DISCORD_CHANNEL_ID": "42",
}


@patch.dict(os.environ, ENV)
class HandlerTest(unittest.TestCase):
    def run_handler(self, cursor, pages, messages=()):
        mastodon = MagicMock()
        mastodon.notifications.side_effect = lambda **kwargs: pages[
            kwargs.get("min_id")
        ]
        mastodon.status.return_value = ALERT_POST
        session = discord_session(list(messages))
        written = []
        with (
            patch.object(lambda_function, "Mastodon", return_value=mastodon),
            patch.object(lambda_function.requests, "Session", return_value=session),
            patch.object(lambda_function, "read_cursor", return_value=cursor),
            patch.object(
                lambda_function, "write_cursor", lambda p, v: written.append(v)
            ),
        ):
            lambda_handler({}, None)
        posts = [call.kwargs["json"] for call in session.post.call_args_list]
        return posts, written

    def test_first_run_only_sets_the_cursor(self):
        posts, written = self.run_handler(None, {None: [notification("50", "a")]})
        self.assertEqual(posts, [])
        self.assertEqual(written, ["50"])

    def test_relays_only_allowed_accounts(self):
        posts, written = self.run_handler(
            "10",
            {
                "10": [notification("12", "someone"), notification("11", "lens0021")],
                "12": [],
            },
            [discord_message("2", "2026-09-30T00:00:06+00:00", "-# id a1b2c3d4")],
        )
        self.assertEqual(len(posts), 1)
        self.assertIn("확인했습니다", posts[0]["content"])
        self.assertEqual(posts[0]["message_reference"]["message_id"], "2")
        self.assertEqual(posts[0]["allowed_mentions"], {"parse": []})
        self.assertEqual(written, ["11", "12"])

    def test_a_mention_that_is_not_a_reply_is_posted_plainly(self):
        posts, _ = self.run_handler(
            "10",
            {"10": [notification("11", "lens0021", in_reply_to_id=None)], "11": []},
        )
        self.assertNotIn("message_reference", posts[0])


if __name__ == "__main__":
    unittest.main()
