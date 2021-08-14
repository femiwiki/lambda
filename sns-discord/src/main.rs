use hyper::{body::to_bytes, client::HttpConnector, Body, Client, Request};
use hyper_rustls::HttpsConnector;
use lambda_runtime::{handler_fn, Context, Error};
use once_cell::sync::OnceCell;
use serde_json::{json, Value};
use std::env;

static WEBHOOK_TOKEN: OnceCell<String> = OnceCell::new();
static CLIENT: OnceCell<Client<HttpsConnector<HttpConnector>>> = OnceCell::new();

#[tokio::main]
async fn main() -> Result<(), Error> {
    let func = handler_fn(func);
    lambda_runtime::run(func).await?;
    Ok(())
}

async fn func(event: Value, _context: Context) -> Result<Value, Error> {
    //
    // Initialize global variable
    //
    let webhook_token = WEBHOOK_TOKEN.get_or_try_init(|| env::var("WEBHOOK_TOKEN"))?;
    let client =
        CLIENT.get_or_init(|| Client::builder().build(HttpsConnector::with_native_roots()));

    //
    // Parse a message from event
    //
    let maybe_message = event["Records"][0]["Sns"]["Message"].as_str();
    let (notify, summary, dump) = if let Some(message) = maybe_message {
        // Check if message is valid JSON
        match serde_json::from_str::<Value>(message) {
            Ok(json) => {
                let state = &json["NewStateValue"];
                let dump = serde_json::to_string_pretty(&json)?;

                if state == "ALARM" {
                    (true, "알림이 발생하였습니다.", dump)
                } else if state == "OK" {
                    (false, "알림 하나가 정상화 되었습니다.", dump)
                } else {
                    (true, "", dump)
                }
            }
            Err(_) => (true, "", message.to_string()),
        }
    } else {
        let dump = serde_json::to_string_pretty(&event)?;
        (true, "알지 못하는 유형의 이벤트가 발생했습니다.", dump)
    };

    let content = format!(
        "{}{}\n```json\n{}\n```",
        if notify {
            "<@&678974055365476392> "
        } else {
            ""
        },
        summary,
        dump
    );

    //
    // Send alarm to the Discord
    //
    let payload = json!({
        "content": content,
        "allowed_mentions": {
            "roles": ["678974055365476392"]
        }
    });
    let req = Request::builder()
        .method("POST")
        .uri(format!(
            "https://discord.com/api/webhooks/792425523639746611/{}",
            webhook_token
        ))
        .header("Content-Type", "application/json")
        .body(Body::from(payload.to_string()))?;
    let (parts, body) = client.request(req).await?.into_parts();

    //
    // Leave a log to cloudtrail
    //
    println!(
        "{}",
        json!({
            "event": event,
            "status_code": parts.status.as_u16(),
            "response": to_bytes(body).await?.to_vec(),
        })
    );

    Ok(Value::Null)
}
