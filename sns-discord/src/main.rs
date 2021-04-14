use hyper::{body::to_bytes, client::HttpConnector, Body, Client, Request};
use hyper_rustls::HttpsConnector;
use netlify_lambda::{lambda, Context};
use once_cell::sync::OnceCell;
use serde_json::{json, Value};
use std::{env, error::Error};

type LambdaError = Box<dyn Error + Sync + Send + 'static>;

static WEBHOOK_TOKEN: OnceCell<String> = OnceCell::new();
static CLIENT: OnceCell<Client<HttpsConnector<HttpConnector>>> = OnceCell::new();

#[lambda]
#[tokio::main]
async fn main(event: Value, _context: Context) -> Result<Value, LambdaError> {
    let webhook_token = WEBHOOK_TOKEN.get_or_try_init(|| env::var("WEBHOOK_TOKEN"))?;
    let client =
        CLIENT.get_or_init(|| Client::builder().build(HttpsConnector::with_native_roots()));

    let message = &event["Records"][0]["Sns"]["Message"];

    let payload = json!({
        "content": format!("<@&678974055365476392>\n{}", message),
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

    println!(
        "{}",
        json!({
            "message": message,
            "status_code": parts.status.as_u16(),
            "response": to_bytes(body).await?.to_vec(),
        })
    );

    Ok(Value::Null)
}
