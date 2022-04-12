use hyper::{body::to_bytes, client::HttpConnector, Body, Client, Request};
use hyper_rustls::HttpsConnector;
use lambda_runtime::{handler_fn, Context, Error};
use once_cell::sync::OnceCell;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::env;

static RED: u32 = 0xe83535;
static GREEN: u32 = 0x2daf32;
static GRAY: u32 = 0xb3b4bc;
static WEBHOOK_TOKEN: OnceCell<String> = OnceCell::new();
static CLIENT: OnceCell<Client<HttpsConnector<HttpConnector>>> = OnceCell::new();

#[derive(Serialize, Deserialize, Debug)]
struct Field {
    name: String,
    value: String,
    inline: bool,
}
#[derive(Debug)]
struct Embed {
    color: u32,
    description: String,
    fields: Vec<Field>,
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
            "fields": match serde_json::to_value(&post_data.embed.fields) {
                Ok(v) => v,
                Err(_) => Value::Null,
            },
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
    let (notify, color, summary, fields, dump) = if let Some(message) = maybe_message {
        // Check if message is valid JSON
        match serde_json::from_str::<Value>(message) {
            Ok(json) => {
                let state = &json["NewStateValue"];
                let test = &json["AlarmName"] == "_Test";
                let notify = state != "OK" && !test;
                let color = if test {
                    GRAY
                } else if notify {
                    RED
                } else {
                    GREEN
                };
                let reason = String::from(
                    json["NewStateReason"]
                        .as_str()
                        .unwrap_or("(메시지에 NewStateReason이 없습니다)"),
                );
                let fields = message_to_fields(&json);

                (notify, color, reason, fields, String::new())
            }
            Err(_) => (true, RED, String::new(), Vec::new(), message.to_string()),
        }
    } else {
        let dump = serde_json::to_string_pretty(&event)?;
        (
            true,
            RED,
            "알지 못하는 유형의 이벤트가 발생했습니다.".to_string(),
            Vec::new(),
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
            color,
            description: format!("```json\n{}\n```", dump),
            fields,
        },
    })
}

fn message_to_fields(message: &Value) -> Vec<Field> {
    let mut fields: Vec<Field> = Vec::new();
    if let Some(obj) = message.as_object() {
        for (key, value) in obj.iter() {
            if key == "NewStateReason" {
                continue;
            }
            fields.push(Field {
                name: key.to_string(),
                value: if value.is_string() {
                    String::from(value.as_str().unwrap_or(""))
                } else {
                    format!(
                        "```json\n{}\n```",
                        match serde_json::to_string_pretty(&value) {
                            Ok(v) => v,
                            Err(_) => value.to_string(),
                        }
                    )
                },
                inline: true,
            });
        }
    }
    fields
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_message() {
        let mut post_data;

        post_data = parse_message(&json!("arbitrary")).unwrap();
        assert_eq!(
            post_data.content,
            "<@&678974055365476392> 알지 못하는 유형의 이벤트가 발생했습니다.".to_string(),
            "An arbitrary message should be parsed as an alarm."
        );
        assert_eq!(post_data.embed.color, RED,);

        post_data = parse_message(&json!(
            {
                "Records": [{
                    "Sns": {
                        "Message": json!({
                            "NewStateValue":"ALARM",
                            "NewStateReason": "Threshold Crossed: 1 out of the last 1 datapoints was less than the threshold.",
                        }).to_string()
                    }
                }]
            }
            ))
            .unwrap();
        assert_eq!(
            post_data.content,
            "<@&678974055365476392> Threshold Crossed: 1 out of the last 1 datapoints was less than the threshold.".to_string(),
            "An alarm should be parsed as an alarm."
        );
        assert_eq!(post_data.embed.color, RED,);

        post_data = parse_message(&json!(
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
        .unwrap();
        assert_eq!(
            post_data.content,
            "(메시지에 NewStateReason이 없습니다)".to_string(),
            "An OK should be parsed as an OK."
        );
        assert_eq!(post_data.embed.color, GREEN,);

        post_data = parse_message(&json!(
            {
                "Records": [{
                    "Sns": {
                        "Message": json!({
                            "AWSAccountId": "302617221463",
                            "AlarmActions": [
                                "arn:aws:sns:ap-northeast-1:302617221463:CloudWatch_Alarms_Topic"
                            ],
                            "AlarmArn": "arn:aws:cloudwatch:ap-northeast-1:302617221463:alarm:Femiwiki CPU credit balance",
                            "AlarmConfigurationUpdatedTimestamp": "2021-05-17T02:32:05.144+0000",
                            "AlarmDescription": null,
                            "AlarmName": "Femiwiki CPU credit balance",
                            "InsufficientDataActions": [],
                            "NewStateReason": "Threshold Crossed: 1 out of the last 1 datapoints [71.58514626666667 (09/04/22 21:01:00)] was less than the threshold (72.0) (minimum 1 datapoint for OK -> ALARM transition).",
                            "NewStateValue": "ALARM",
                            "OKActions": [],
                            "OldStateValue": "OK",
                            "Region": "Asia Pacific (Tokyo)",
                            "StateChangeTime": "2022-04-09T21:06:48.198+0000",
                            "Trigger": {
                                "ComparisonOperator": "LessThanThreshold",
                                "DatapointsToAlarm": 1,
                                "Dimensions": [
                                    {
                                        "name": "InstanceId",
                                        "value": "i-0d6c06981a9aa5112"
                                    }
                                ],
                                "EvaluateLowSampleCountPercentile": "",
                                "EvaluationPeriods": 1,
                                "MetricName": "CPUCreditBalance",
                                "Namespace": "AWS/EC2",
                                "Period": 300,
                                "Statistic": "MINIMUM",
                                "StatisticType": "Statistic",
                                "Threshold": 72.0,
                                "TreatMissingData": "missing",
                                "Unit": null
                            }
                          }).to_string()
                    }
                }]
            }
        ))
        .unwrap();
        assert_eq!(
            post_data.content,
            "<@&678974055365476392> Threshold Crossed: 1 out of the last 1 datapoints [71.58514626666667 (09/04/22 21:01:00)] was less than the threshold (72.0) (minimum 1 datapoint for OK -> ALARM transition).".to_string(),
            "A test alarm should be parsed as a test alarm."
        );
        assert_eq!(post_data.embed.color, RED,);

        post_data = parse_message(&json!(
            {
                "Records": [{
                    "Sns": {
                        "Message": json!({
                            "AlarmName": "_Test",
                            "NewStateValue": "OK",
                            "NewStateReason": "테스트",
                        }).to_string()
                    }
                }]
            }
        ))
        .unwrap();
        assert_eq!(
            post_data.content,
            "테스트".to_string(),
            "A test alarm should be parsed as a test alarm."
        );
        assert_eq!(post_data.embed.color, GRAY,);
    }

    #[test]
    fn message_to_fields_test() {
        let mut fields;

        fields = message_to_fields(&json!({
            "NewStateValue": "ALARM",
            "OldStateValue": "OK",
            "NewStateReason": "Threshold Crossed: 1 out of the last 1 datapoints [71.58514626666667 (09/04/22 21:01:00)] was less than the threshold (72.0) (minimum 1 datapoint for OK -> ALARM transition).",
        }));
        assert_eq!(fields.len(), 2, "NewStateReason should be removed");
        assert_eq!(fields[1].value, "OK",);

        fields = message_to_fields(&json!({
            "InsufficientDataActions": [],
            "OKActions": [],
        }));
        assert_eq!(fields.len(), 2,);
        assert_eq!(fields[1].value, "```json\n[]\n```",);
    }
}
