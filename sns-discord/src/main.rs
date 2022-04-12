use hyper::{body::to_bytes, client::HttpConnector, Body, Client, Request};
use hyper_rustls::HttpsConnector;
use lambda_runtime::{handler_fn, Context, Error};
use once_cell::sync::OnceCell;
use serde_json::{json, Value};
use std::env;

static RED: u32 = 0xe83535;
static GREEN: u32 = 0x2daf32;
static WEBHOOK_TOKEN: OnceCell<String> = OnceCell::new();
static CLIENT: OnceCell<Client<HttpsConnector<HttpConnector>>> = OnceCell::new();

#[derive(Debug)]
struct Embed {
    color: u32,
    description: String,
}

#[derive(Debug)]
struct PostData {
    content: String,
    // We now only ship one embed, not multiple.
    embed: Embed,
}

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

    let post_data = match parse_message(&event) {
        Ok(post_data) => post_data,
        Err(e) => return Err(e),
    };

    //
    // Send alarm to the Discord
    //
    let payload = json!({
        "content": post_data.content,
        "embeds": [{
            "color": post_data.embed.color,
            "description": post_data.embed.description,
        }],
        "allowed_mentions": {
            "roles": ["678974055365476392"]
        },
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

/// Parse a message from event
fn parse_message(event: &Value) -> Result<PostData, Error> {
    let maybe_message = event["Records"][0]["Sns"]["Message"].as_str();
    let (notify, summary, dump) = if let Some(message) = maybe_message {
        // Check if message is valid JSON
        match serde_json::from_str::<Value>(message) {
            Ok(json) => {
                let state = &json["NewStateValue"];
                let dump = serde_json::to_string_pretty(&json)?;
                let notify = &json["AlarmName"] != "_Test" && state == "ALARM";

                if state == "ALARM" {
                    (
                        notify,
                        format!("알림이 발생하였습니다. ({})", &json["NewStateReason"]),
                        dump,
                    )
                } else if state == "OK" {
                    (
                        notify,
                        format!(
                            "알림 하나가 정상화 되었습니다. ({})",
                            &json["NewStateReason"]
                        ),
                        dump,
                    )
                } else {
                    (true, String::new(), dump)
                }
            }
            Err(_) => (true, String::new(), message.to_string()),
        }
    } else {
        let dump = serde_json::to_string_pretty(&event)?;
        (
            true,
            "알지 못하는 유형의 이벤트가 발생했습니다.".to_string(),
            dump,
        )
    };

    let content = format!(
        "{}{}",
        if notify {
            "<@&678974055365476392> "
        } else {
            ""
        },
        summary
    );

    Ok(PostData {
        content,
        embed: Embed {
            color: if notify { RED } else { GREEN },
            description: format!("```json\n{}\n```", dump),
        },
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_message() {
        assert_eq!(
            parse_message(&json!("arbitrary")).unwrap().content,
            "<@&678974055365476392> 알지 못하는 유형의 이벤트가 발생했습니다.".to_string(),
            "An arbitrary message should be parsed as an alarm."
        );

        assert_eq!(
            parse_message(&json!(
            {
                "Records": [{
                    "Sns": {
                        "Message": json!({
                            "NewStateValue":"ALARM",
                        }).to_string()
                    }
                }]
            }
            ))
            .unwrap()
            .content,
            "<@&678974055365476392> 알림이 발생하였습니다. (null)".to_string(),
            "An alarm should be parsed as an alarm."
        );

        assert_eq!(
            parse_message(&json!(
                {
                    "Records": [{
                        "Sns": {
                            "Message": json!({
                                "NewStateValue":"OK",
                            }).to_string()
                        }
                    }]
                }
            ))
            .unwrap()
            .content,
            "알림 하나가 정상화 되었습니다. (null)".to_string(),
            "An OK should be parsed as an OK."
        );

        assert_eq!(
            parse_message(&json!(
                {
                    "Records": [{
                        "Sns": {
                            "Message": json!({
                                "AlarmName": "_Test",
                                "NewStateValue":"OK",
                                "NewStateReason":"테스트",
                            }).to_string()
                        }
                    }]
                }
            ))
            .unwrap()
            .content,
            "알림 하나가 정상화 되었습니다. (\"테스트\")".to_string(),
            "A test alarm should be parsed as a test alarm."
        );
    }
}
