"""Inbound provider adapters; no client sends are enabled in this prototype."""
import hashlib
import hmac

def verify_telegram(headers, secret):
    return bool(secret) and hmac.compare_digest(headers.get('X-Telegram-Bot-Api-Secret-Token',''),secret)

def verify_instagram(headers, raw, secret):
    expected = 'sha256=' + hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest()
    return bool(secret) and hmac.compare_digest(headers.get('X-Hub-Signature-256',''),expected)

def telegram_events(payload):
    if not isinstance(payload,dict): raise ValueError('Object required')
    message = payload.get('message',{})
    if not isinstance(message,dict): raise ValueError('Invalid message')
    if message.get('chat',{}).get('type') != 'private' or not isinstance(message.get('text'),str): return []
    if 'update_id' not in payload or 'id' not in message['chat']: raise ValueError('Missing identifiers')
    return [(str(message['chat']['id']),str(payload['update_id']),message['text'])]

def instagram_events(payload, account_id):
    if not isinstance(payload,dict): raise ValueError('Object required')
    if payload.get('object') != 'instagram': return []
    result = []
    for entry in payload.get('entry',[]):
        if str(entry.get('id')) != account_id: continue
        for event in entry.get('messaging',[]):
            message = event.get('message',{})
            if message.get('is_echo') or not isinstance(message.get('text'),str): continue
            if str(event.get('recipient',{}).get('id')) != account_id: continue
            if not message.get('mid') or not event.get('sender',{}).get('id'): raise ValueError('Missing identifiers')
            result.append((str(event['sender']['id']),str(message['mid']),message['text']))
    return result

def demo_delivery(db, item_id, fail=False):
    """A local delivery simulation never marks a message as actually sent."""
    row = db.execute('SELECT * FROM outbox WHERE id=?',(item_id,)).fetchone()
    if not row: raise ValueError('Unknown outbox item')
    if row['status'] in ('cancelled_handoff','blocked_source_review','demo_dead_letter'):raise ValueError('Blocked item requires review, not an automatic retry')
    attempts=row['attempts']+1
    status = ('demo_dead_letter' if attempts>=3 else 'demo_error') if fail else 'demo_delivered'
    db.execute('UPDATE outbox SET status=?,error=?,attempts=? WHERE id=?',
      (status,'Simulated provider unavailable; no external request performed' if fail else None,attempts,item_id))
    return {'status':status,'actually_sent':False}
