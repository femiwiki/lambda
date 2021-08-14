sns-discord
========
A simple lambda function which subscribes AWS SNS to ping Femiwiki's Discord webhook.

```bash
# You need musl toolchain, in Ubuntu:
sudo apt-get install musl-tools

# Install musl target
rustup target add x86_64-unknown-linux-musl

# Build
cargo build --release

# Make into zip file
cp target/x86_64-unknown-linux-musl/release/sns-discord bootstrap
zip -j lambda.zip bootstrap
rm bootstrap

# Publish
aws lambda update-function-code --function-name DiscordNoti \
  --zip-file fileb://lambda.zip --publish --region us-east-1
aws lambda update-function-code --function-name DiscordNoti \
  --zip-file fileb://lambda.zip --publish --region ap-northeast-1
```

&nbsp;

--------

The source code of *femiwiki/sns-discord* is primarily distributed under the
terms of the [GNU Affero General Public License v3.0] or any later version. See
[COPYRIGHT] for details.

[GNU Affero General Public License v3.0]: LICENSE
[COPYRIGHT]: COPYRIGHT
