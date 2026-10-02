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
uv run python -m unittest discover -s grafana-github

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
`deploy.yml` sets these variables, the two tokens from this
repository's secrets:

- `MASTODON_INSTANCE`: e.g. `https://mastodon.social`
- `MASTODON_TOKEN`: scopes `read:notifications read:statuses`
- `ALLOWED_ACCTS`: comma-separated, e.g. `lens0021,someone@example.org`
- `CURSOR_PARAMETER`: SSM parameter holding the last notification seen
- `DISCORD_BOT_TOKEN`: a bot that can view, read history in, and send to the
  channel, with the Message Content intent
- `DISCORD_CHANNEL_ID`: the channel Grafana posts alerts to

## grafana-github

Keeps one issue in femiwiki/infra per Grafana alert rule labelled
`impact=operators`. Grafana's webhook contact point calls the function URL from
femiwiki/infra `aws/lambda.tf`; the first firing opens an issue named after the
rule, and later notifications become comments. `deploy.yml` sets:

- `GITHUB_APP_CLIENT_ID`: the alerts GitHub App, installed on femiwiki/infra
  with Issues read and write
- `GITHUB_APP_PRIVATE_KEY`: that App's private key
- `GITHUB_MENTION`: `@femiwiki/pager`, mentioned in each new issue
- `GITHUB_REPOSITORY`: `femiwiki/infra`
- `WEBHOOK_TOKEN`: the bearer token Grafana sends

&nbsp;

---

The source code of _femiwiki/lambda_ is primarily distributed under the
terms of the [GNU Affero General Public License v3.0] or any later version. See
[COPYRIGHT] for details.

[gnu affero general public license v3.0]: LICENSE
[copyright]: COPYRIGHT
