import os
import json
import urllib3

http = urllib3.PoolManager()

WEBHOOK_TOKEN = os.environ['WEBHOOK_TOKEN']

def lambda_handler(event, context):
    message = event['Records'][0]['Sns']['Message']
    payload = {
        'content': f'<@&678974055365476392>\n{message}',
        'allowed_mentions': {
            'roles': ['678974055365476392']
        }
    }
    body = json.dumps(payload).encode('utf-8')
    resp = http.request(
        'POST',
        f'https://discord.com/api/webhooks/792425523639746611/{WEBHOOK_TOKEN}',
        body=body,
        headers={'Content-Type': 'application/json'},
    )
    print({
        'message': message,
        'status_code': resp.status,
        'response': resp.data,
    })
