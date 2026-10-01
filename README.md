# lambda

A monorepo of Femiwiki's AWS Lambda functions.

```bash
# Lint, type-check and test
uv sync
uvx ruff format --check .
uvx ruff check .
uvx ty check .
uv run python -m unittest discover -s sns-discord
uv run python -m unittest discover -s mastodon-discord

# Make into zip file
zip -j lambda.zip sns-discord/lambda_function.py

# Publish
aws lambda update-function-code --function-name DiscordNoti \
  --zip-file fileb://lambda.zip --publish --region us-east-1
aws lambda update-function-code --function-name DiscordNoti \
  --zip-file fileb://lambda.zip --publish --region ap-northeast-1
```

## mastodon-discord

Relays mentions of the wiki's Mastodon status account from the accounts in
`ALLOWED_ACCTS` to a Discord channel, as a reply to the alert message a
Mastodon reply answers. It runs every minute from an EventBridge rule in femiwiki/infra `aws/lambda.tf`.
`deploy-mastodon-discord.yml` sets these variables, the two tokens from this
repository's secrets:

- `MASTODON_INSTANCE`: e.g. `https://mastodon.social`
- `MASTODON_TOKEN`: scopes `read:notifications read:statuses`
- `ALLOWED_ACCTS`: comma-separated, e.g. `lens0021,someone@example.org`
- `CURSOR_PARAMETER`: SSM parameter holding the last notification seen
- `DISCORD_BOT_TOKEN`: a bot that can view, read history in, and send to the
  channel, with the Message Content intent
- `DISCORD_CHANNEL_ID`: the channel Grafana posts alerts to

&nbsp;

---

The source code of _femiwiki/lambda_ is primarily distributed under the
terms of the [GNU Affero General Public License v3.0] or any later version. See
[COPYRIGHT] for details.

[gnu affero general public license v3.0]: LICENSE
[copyright]: COPYRIGHT
