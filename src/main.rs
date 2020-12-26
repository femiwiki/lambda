use hyper::{body::to_bytes, Body, Client, Request};
use netlify_lambda::{lambda, Context};
use serde_json::{json, Value};
use std::error::Error;
use std::{env, fmt};

type LambdaError = Box<dyn Error + Sync + Send + 'static>;

#[derive(Debug)]
struct UnexpectedJSONFormatError;

impl Error for UnexpectedJSONFormatError {}

impl fmt::Display for UnexpectedJSONFormatError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        fmt::Display::fmt(
            "an error occurred when reading a JSON due to unexpected format",
            f,
        )
    }
}

#[lambda]
#[tokio::main]
async fn main(event: Value, _context: Context) -> Result<Value, LambdaError> {
    let &message = &event["Records"][0]["Sns"]["Message"]
        .as_str()
        .ok_or(UnexpectedJSONFormatError)?;
    let payload = json!({
        "content": "<@&678974055365476392>\n".to_owned() + message,
        "allowed_mentions": {
            "roles": ["678974055365476392"]
        }
    });
    let body = payload.to_string();

    let webhook_token = env::var("WEBHOOK_TOKEN")?;

    let client = Client::new();
    let req = Request::builder()
        .method("POST")
        .uri(format!(
            "https://discord.com/api/webhooks/792425523639746611/{}",
            webhook_token
        ))
        .header("Content-Type", "application/json")
        .body(Body::from(body))?;
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
