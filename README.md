sns-discord
========
A simple lambda function which subscribes AWS SNS to ping Femiwiki's Discord webhook.

```bash
# You need musl toolchain
rustup target add x86_64-unknown-linux-musl

# Build
cargo build

# Publish
zip -j lambda.zip target/x86_64-unknown-linux-musl/release/bootstrap
aws lambda update-function-code \
  --region us-east-1 \
  --function-name DiscordNoti \
  --zip-file fileb://lambda.zip \
  --publish
```
