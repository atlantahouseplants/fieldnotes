"""
FieldNotes — Telegram Poller (Dev Mode)
Polls Telegram for new messages and feeds them into the processor.
Run alongside the server when HTTPS webhook isn't available.

Usage: python3 backend/poller.py
"""
import os, sys, asyncio, httpx

API_BASE = os.getenv("FIELDNOTES_API", "http://localhost:8765")


def read_env_var(name):
    """Read a variable from .env file."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    if not os.path.exists(env_path):
        return None
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                key, sep, val = line.partition('=')
                if key.strip() == name:
                    return val.strip().strip('"').strip("'")
    return None


async def main():
    token = read_env_var('TELEGRAM_BOT_TOKEN')
    if not token:
        print("TELEGRAM_BOT_TOKEN not found in .env")
        print("Add your bot token to .env file")
        return
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(f'https://api.telegram.org/bot{token}/deleteWebhook')
        print("Polling for messages. Send a test message to your bot!")
        print('Try: "Acme Office: all good, replaced filter"\n')
    
    offset = 0
    while True:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f'https://api.telegram.org/bot{token}/getUpdates',
                    json={'offset': offset, 'timeout': 20}
                )
                data = resp.json()
                
                if data.get('ok') and data.get('result'):
                    for update in data['result']:
                        offset = update['update_id'] + 1
                        message = update.get('message', {})
                        text = message.get('text', '')
                        chat_id = str(message.get('chat', {}).get('id', ''))
                        
                        if text:
                            preview = text[:80] + ('...' if len(text) > 80 else '')
                            print(f"\n📩 Received: '{preview}'")
                            
                            # Forward to FieldNotes API for processing
                            webhook_body = {
                                'message': {
                                    'chat': {'id': int(chat_id)},
                                    'text': text
                                }
                            }
                            api_resp = await client.post(
                                f'{API_BASE}/webhook/telegram',
                                json=webhook_body
                            )
                            result = api_resp.json()
                            if result.get('ok'):
                                print(f"   ✅ {result.get('account', '?')} — {result.get('status', '?')}")
                                for action in result.get('actions_created', []):
                                    print(f"      📌 {action}")
                            else:
                                print(f"   ❌ {result.get('detail', result.get('error', '?'))}")
        except KeyboardInterrupt:
            print("\nDone.")
            break
        except Exception as e:
            print(f"Poll error: {e}")
            await asyncio.sleep(5)


if __name__ == '__main__':
    asyncio.run(main())
