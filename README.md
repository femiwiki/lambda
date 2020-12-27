sns-discord
========
A simple lambda function which subscribes AWS SNS to ping Femiwiki's Discord webhook.

```bash
rustup target add x86_64-unknown-linux-musl

cargo build
zip -j lambda.zip target/x86_64-unknown-linux-musl/release/bootstrap
```
