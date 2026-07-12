# lambda

A monorepo of Femiwiki's AWS Lambda functions.

```bash
# Lint, type-check and test
uv sync
uvx ruff format --check .
uvx ruff check .
uvx ty check .
uv run python -m unittest discover -s sns-discord

# Make into zip file
zip -j lambda.zip sns-discord/lambda_function.py

# Publish
aws lambda update-function-code --function-name DiscordNoti \
  --zip-file fileb://lambda.zip --publish --region us-east-1
aws lambda update-function-code --function-name DiscordNoti \
  --zip-file fileb://lambda.zip --publish --region ap-northeast-1
```

&nbsp;

---

The source code of _femiwiki/lambda_ is primarily distributed under the
terms of the [GNU Affero General Public License v3.0] or any later version. See
[COPYRIGHT] for details.

[gnu affero general public license v3.0]: LICENSE
[copyright]: COPYRIGHT
