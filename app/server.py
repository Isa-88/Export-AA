import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from datetime import date
from .storage import ROOT, connect, initialize, documents, now
from .core import STATUSES
from .dialogue import Engine
from .import_data import import_all
from .models import ENTITY_TABLES, all_records, get, approve_edit
from .search import product_search
from .language import summary
from .adapters import verify_telegram, verify_instagram, telegram_events, instagram_events, demo_delivery

def load_env():
    path = ROOT/'.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key,value = line.split('=',1)
                os.environ.setdefault(key.strip(),value.strip().strip('"').strip("'"))

class Application:
    def __init__(self, path, password):
        if len(password)<16: raise ValueError('ADMIN_PASSWORD must contain at least 16 characters')
        self.path = str(path); import_all(self.path); self.engine = Engine(self.path)
        self.password_hash = hashlib.scrypt(password.encode(),salt=b'local-prototype-login',n=16384,r=8,p=1)
        self.role_hashes={'owner':self.password_hash}
        for role in ('manager','reviewer'):
            value=os.getenv(role.upper()+'_PASSWORD')
            if value:
                if len(value)<16:raise ValueError('Role passwords must have at least 16 characters')
                hashed=hashlib.scrypt(value.encode(),salt=b'local-prototype-login',n=16384,r=8,p=1)
                if hashed in self.role_hashes.values():raise ValueError('Role passwords must differ')
                self.role_hashes[role]=hashed
        self.sessions = {}; self.limits = defaultdict(deque); self.lock = threading.Lock()

    def allowed(self, key, limit=60):
        with self.lock:
            now_sec = time.monotonic()
            if len(self.limits)>10000:
                self.limits = defaultdict(deque,{k:v for k,v in self.limits.items() if v and v[-1]>now_sec-60})
            q = self.limits[key]
            while q and q[0]<now_sec-60: q.popleft()
            if len(q)>=limit: return False
            q.append(now_sec); return True

def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'LocalAssistant/1'
        def log_message(self, fmt, *args): pass  # Never log tokens, query strings or client text.

        def respond(self, status, body, content_type='application/json; charset=utf-8', cookie=None):
            if status>=400 and isinstance(body,dict) and 'error' in body:
                body['error_en']={401:'Sign in is required or the password is incorrect.',403:'Access denied. Check your role and security token.',429:'Too many requests. Please wait a minute.',400:'Check the input format and required fields.',413:'The request is too large.',503:'This integration is not enabled.',409:'This action is not available in the local demo.'}.get(status,'The request could not be completed. Please try again.')
            raw = json.dumps(body,ensure_ascii=False).encode() if content_type.startswith('application/json') else body.encode() if isinstance(body,str) else body
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('X-Frame-Options','DENY')
            self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.send_header('Referrer-Policy','no-referrer')
            if cookie: self.send_header('Set-Cookie',cookie)
            self.end_headers(); self.wfile.write(raw)

        def session(self, role):
            cookie = SimpleCookie()
            try: cookie.load(self.headers.get('Cookie',''))
            except Exception: return None
            token = cookie.get('aa_'+role)
            if not token: return None
            s = app.sessions.get(token.value)
            return s if s and s['role']==role and s['expires']>time.time() else None

        def new_session(self, role):
            token = secrets.token_urlsafe(32)
            session = {'role':role,'peer':secrets.token_urlsafe(32),'csrf':secrets.token_urlsafe(32),'expires':time.time()+8*3600}
            app.sessions[token] = session
            for key in list(app.sessions):
                if app.sessions[key]['expires']<time.time(): app.sessions.pop(key,None)
            return session, f'aa_{role}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800'

        def guard(self, role, write=False):
            s = self.session(role)
            if not s: self.respond(401,{'error':'Требуется вход'}); return None
            if write and not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),s['csrf']):
                self.respond(403,{'error':'Неверный CSRF-токен'}); return None
            return s

        def do_GET(self):
            try: self.get()
            except (ValueError, KeyError, TypeError): self.respond(400,{'error':'Некорректный запрос'})
            except Exception: self.respond(500,{'error':'Внутренняя ошибка'})

        def get(self):
            parsed = urlparse(self.path); path = parsed.path
            # Strict host allowlist prevents DNS rebinding against the local service.
            if self.headers.get('Host','').split(':')[0] not in ('127.0.0.1','localhost'):
                return self.respond(403,{'error':'Local access only'})
            assets = {'/':'index.html','/admin':'admin.html','/app.js':'app.js','/admin.js':'admin.js','/style.css':'style.css'}
            if path in assets:
                file = ROOT/'static'/assets[path]
                mime = 'text/html' if file.suffix=='.html' else 'text/css' if file.suffix=='.css' else 'application/javascript'
                return self.respond(200,file.read_bytes(),mime+'; charset=utf-8')
            if path=='/api/session':
                s = self.session('web'); cookie=None
                if not s: s,cookie=self.new_session('web')
                return self.respond(200,{'csrf':s['csrf'],'mode':'demo'},cookie=cookie)
            if path=='/api/health': return self.respond(200,{'ok':True,'mode':'demo','real_sending':False})
            if path=='/api/history':
                s = self.guard('web')
                if not s: return
                with connect(app.path) as db:
                    c = db.execute("SELECT * FROM conversations WHERE channel='web' AND peer=?",(s['peer'],)).fetchone()
                    rows = [dict(r) for r in db.execute('SELECT role,text,created_at,original_language,metadata,source_refs FROM messages WHERE conversation_id=? ORDER BY id',(c['id'],))] if c else []
                    state=json.loads(c['state']) if c else {}
                    return self.respond(200,{'messages':rows,'paused':bool(c['paused']) if c else False,'language_mode':c['language_mode'] if c else 'auto','reply_language':c['reply_language'] if c else 'ru','summary':{l:summary(state,l) for l in ('ru','en')},'shortlist':state.get('shortlist',[])})
            if path=='/api/catalog':
                s=self.guard('web')
                if not s:return
                query=parse_qs(parsed.query).get('q',['catalog'])[0][:300]
                with connect(app.path) as db:
                    items=product_search(db,query,60)
                    return self.respond(200,{'products':items,'mode':'demo','current_prices_available':False})
            if path=='/webhooks/instagram':
                if os.getenv('WEBHOOKS_ENABLED')!='true': return self.respond(503,{'error':'Webhooks disabled'})
                q = parse_qs(parsed.query); token = os.getenv('INSTAGRAM_VERIFY_TOKEN','')
                if token and q.get('hub.mode')==['subscribe'] and hmac.compare_digest(q.get('hub.verify_token',[''])[0],token):
                    return self.respond(200,q.get('hub.challenge',[''])[0],'text/plain')
                return self.respond(403,{'error':'Invalid verification token'})
            if path=='/api/admin/state':
                s = self.guard('admin')
                if not s: return
                with connect(app.path) as db:
                    data={'csrf':s['csrf'],'role':s.get('member_role','owner'),'statuses':STATUSES,
                      'leads':[dict(r) for r in db.execute('SELECT l.*,c.channel FROM leads l JOIN conversations c ON c.id=l.conversation_id ORDER BY l.updated_at DESC')],
                      'conversations':[dict(r) for r in db.execute('SELECT * FROM conversations ORDER BY created_at DESC')],
                      'messages':[dict(r) for r in db.execute('SELECT * FROM messages ORDER BY id')],
                      'knowledge':documents(db,'knowledge'),'missions':documents(db,'missions'),
                      'outbox':[dict(r) for r in db.execute('SELECT * FROM outbox ORDER BY id DESC')],
                      'audit':[dict(r) for r in db.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 50')],
                      'entities':{t:all_records(db,t) for t in ENTITY_TABLES},
                      'privacy_requests':[dict(r) for r in db.execute('SELECT * FROM privacy_requests ORDER BY created_at DESC')],
                      'unknown_questions':[dict(r) for r in db.execute('SELECT u.*,m.text,m.original_language FROM unknown_questions u JOIN messages m ON m.id=u.message_id ORDER BY u.id DESC')],
                      'imports':[dict(r) for r in db.execute('SELECT * FROM imports')],
                      'revisions':[dict(r) for r in db.execute('SELECT id,entity_type,entity_id,version,actor,action,created_at FROM approvals ORDER BY id DESC LIMIT 100')],
                      'metrics':{'conversations':db.execute('SELECT count(*) FROM conversations').fetchone()[0],
                        'leads':db.execute('SELECT count(*) FROM leads').fetchone()[0],
                        'handoffs':db.execute("SELECT count(*) FROM actions WHERE kind='request_handoff'").fetchone()[0],
                        'unknown_questions':db.execute('SELECT count(*) FROM unknown_questions').fetchone()[0],
                        'languages':[dict(r) for r in db.execute('SELECT reply_language,count(*) AS count FROM conversations GROUP BY reply_language')],
                        'notification_sent':0,'appointments_confirmed':0}}
                    if s.get('member_role')=='reviewer':
                        for key in ('leads','conversations','messages','privacy_requests','outbox','unknown_questions'):data[key]=[]
                    return self.respond(200,data)
            if path=='/api/admin/source':
                if not self.guard('admin'):return
                source_id=parse_qs(parsed.query).get('id',[''])[0]
                with connect(app.path) as db:
                    source=get(db,'sources',source_id)
                    if not source:return self.respond(404,{'error':'Source not found'})
                    result={'source':source}
                    filepath=source.get('file_path') or ''
                    documents_path=ROOT/'sources'/'document_text.json'
                    if documents_path.exists():
                        records=json.loads(documents_path.read_text(encoding='utf-8'))
                        doc=next((d for d in records if d['file_path']==filepath),None)
                        if doc:result['pages']=[p for p in doc['pages'] if not source.get('page') or p['page']==source['page']]
                    return self.respond(200,result)
            self.respond(404,{'error':'Не найдено'})

        def do_POST(self):
            try: self.post()
            except PermissionError: self.respond(403,{'error':'Недостаточно прав для этого действия'})
            except (ValueError, KeyError, TypeError, AttributeError): self.respond(400,{'error':'Проверьте формат и обязательные поля'})
            except Exception: self.respond(500,{'error':'Ошибка обработки. Повторите запрос с тем же event_id.'})

        def post(self):
            path = urlparse(self.path).path
            if self.headers.get('Host','').split(':')[0] not in ('127.0.0.1','localhost'):
                return self.respond(403,{'error':'Local access only'})
            if not app.allowed(self.client_address[0],120): return self.respond(429,{'error':'Слишком много запросов; попробуйте через минуту'})
            origin = self.headers.get('Origin')
            if origin and origin not in ('http://'+self.headers.get('Host',''),): return self.respond(403,{'error':'Invalid origin'})
            if self.headers.get_content_type() != 'application/json': return self.respond(415,{'error':'JSON required'})
            size = int(self.headers.get('Content-Length','0'))
            if size<1 or size>65536: return self.respond(413,{'error':'Размер запроса недопустим'})
            self.connection.settimeout(10)
            raw = self.rfile.read(size)
            if path.startswith('/webhooks/'):
                if os.getenv('WEBHOOKS_ENABLED')!='true': return self.respond(503,{'error':'Webhooks disabled'})
                if path=='/webhooks/telegram':
                    if not verify_telegram(self.headers,os.getenv('TELEGRAM_WEBHOOK_SECRET','')): return self.respond(403,{'error':'Invalid secret'})
                    events = telegram_events(json.loads(raw)); channel='telegram'
                elif path=='/webhooks/instagram':
                    if not verify_instagram(self.headers,raw,os.getenv('INSTAGRAM_APP_SECRET','')): return self.respond(403,{'error':'Invalid signature'})
                    account=os.getenv('INSTAGRAM_ACCOUNT_ID','')
                    if not account: return self.respond(503,{'error':'Account is not configured'})
                    events=instagram_events(json.loads(raw),account); channel='instagram'
                else: return self.respond(404,{'error':'Unknown adapter'})
                for peer,eid,text in events: app.engine.process(channel,peer,eid,text)
                return self.respond(200,{'ok':True,'mode':'demo','actually_sent':False})
            body = json.loads(raw)
            if not isinstance(body,dict): raise ValueError('JSON object required')
            if path=='/api/admin/login':
                if not app.allowed('login:'+self.client_address[0],5): return self.respond(429,{'error':'Повторите вход через минуту'})
                supplied=body.get('password','')
                if not isinstance(supplied,str) or len(supplied)>1024: raise ValueError('Password invalid')
                digest=hashlib.scrypt(supplied.encode(),salt=b'local-prototype-login',n=16384,r=8,p=1)
                member_role=next((role for role,stored in app.role_hashes.items() if hmac.compare_digest(digest,stored)),None)
                if not member_role:return self.respond(401,{'error':'Неверный пароль'})
                s,cookie=self.new_session('admin')
                s['member_role']=member_role
                return self.respond(200,{'csrf':s['csrf'],'role':member_role},cookie=cookie)
            if path=='/api/chat':
                s = self.guard('web',True)
                if not s: return
                if not app.allowed('chat:'+s['peer'],30): return self.respond(429,{'error':'Слишком часто; подождите минуту'})
                return self.respond(200,app.engine.process('web',s['peer'],s['peer']+':'+body['event_id'],body['text']))
            if path.startswith('/api/admin/'):
                s = self.guard('admin',True)
                if not s: return
                member_role=s.get('member_role','owner')
                if member_role=='reviewer' and path not in ('/api/admin/entity','/api/admin/logout'):raise PermissionError('Reviewer cannot access client actions')
                if member_role=='manager' and path in ('/api/admin/entity','/api/admin/document','/api/admin/delete-data'):raise PermissionError('Owner/reviewer required')
                if path=='/api/admin/logout':
                    s['expires']=0
                    return self.respond(200,{'ok':True},cookie='aa_admin=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
                with connect(app.path) as db:
                    db.execute('BEGIN IMMEDIATE')
                    if path=='/api/admin/entity':
                        if body.get('table') not in ENTITY_TABLES:raise ValueError('Unknown table')
                        updated=approve_edit(db,body['table'],body['document'],member_role,member_role,body.get('action','save'))
                        body['id']=updated['id']
                    elif path=='/api/admin/delete-data':
                        if member_role!='owner':raise PermissionError('Owner only')
                        request=db.execute("SELECT * FROM privacy_requests WHERE id=? AND status='requested'",(body['id'],)).fetchone()
                        if not request:raise ValueError('Unknown pending request')
                        cid=request['conversation_id']
                        identity=db.execute('SELECT peer FROM conversations WHERE id=?',(cid,)).fetchone()['peer']
                        for table in ('messages','leads','outbox','unknown_questions','actions'):db.execute(f'DELETE FROM {table} WHERE conversation_id=?',(cid,))
                        db.execute('UPDATE incoming SET result=? WHERE conversation_id=?',(json.dumps({'reply':None,'duplicate':True,'deleted':True}),cid))
                        db.execute("UPDATE conversations SET state='{}',peer=?,paused=1,unsubscribed=1,preferred_human_language=NULL WHERE id=?",('deleted-'+secrets.token_urlsafe(24),cid))
                        for session in app.sessions.values():
                            if session.get('peer')==identity:session['expires']=0
                        db.execute("UPDATE privacy_requests SET status='completed',completed_at=? WHERE id=?",(now(),body['id']))
                    elif path=='/api/admin/lead':
                        if body['status'] not in STATUSES or not isinstance(body.get('assignee',''),str) or len(body.get('assignee',''))>100: raise ValueError('Invalid lead')
                        row=db.execute('SELECT * FROM leads WHERE id=?',(body['id'],)).fetchone()
                        if not row: raise ValueError('Unknown lead')
                        db.execute('UPDATE leads SET status=?,assignee=?,updated_at=? WHERE id=?',(body['status'],body.get('assignee',''),now(),body['id']))
                        if body['status'] in ('передана менеджеру','в работе'):
                            db.execute('UPDATE conversations SET paused=1 WHERE id=?',(row['conversation_id'],))
                    elif path=='/api/admin/pause':
                        if not isinstance(body['paused'],bool): raise ValueError('Boolean required')
                        db.execute('UPDATE conversations SET paused=? WHERE id=?',(int(body['paused']),body['id']))
                        if body['paused']:db.execute("UPDATE outbox SET status='cancelled_handoff' WHERE conversation_id=? AND status='demo_not_sent'",(body['id'],))
                    elif path=='/api/admin/reply':
                        c=db.execute('SELECT * FROM conversations WHERE id=?',(body['id'],)).fetchone()
                        text=body['text']
                        if not c or not isinstance(text,str) or not text.strip() or len(text)>4000: raise ValueError('Invalid reply')
                        if c['channel']!='web': return self.respond(409,{'error':'Внешние отправки не подключены. Ответьте клиенту в исходном канале вручную.'})
                        db.execute('INSERT INTO messages(conversation_id,role,text,created_at) VALUES (?,?,?,?)',(c['id'],'manager',text,now()))
                        db.execute('UPDATE conversations SET paused=1 WHERE id=?',(c['id'],))
                    elif path=='/api/admin/document':
                        table=body['table']; doc=body['document']
                        if table not in ('knowledge','missions'): raise ValueError('Invalid table')
                        required=['id','title','source','approval']+(['answer'] if table=='knowledge' else ['country','date','details','valid_until'])
                        if not all(isinstance(doc.get(k),str) and 0<len(doc[k])<=6000 for k in required): raise ValueError('Missing fields')
                        if doc['approval'] not in ('draft','owner_provided','approved','rejected'): raise ValueError('Invalid approval')
                        if table=='knowledge' and not db.execute('SELECT 1 FROM knowledge WHERE id=?',(doc['id'],)).fetchone(): raise ValueError('Unknown FAQ id')
                        if doc.get('valid_until'): date.fromisoformat(doc['valid_until'])
                        if table=='missions':
                            date.fromisoformat(doc['date'])
                            if doc['approval']=='approved' and doc['valid_until']<date.today().isoformat(): raise ValueError('Expired mission')
                        doc['updated_at']=date.today().isoformat()
                        db.execute(f'INSERT INTO {table} VALUES (?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document',(doc['id'],json.dumps(doc,ensure_ascii=False)))
                        body['id']=doc['id']
                    elif path=='/api/admin/demo-delivery':
                        result=demo_delivery(db,body['id'],body.get('fail') is True)
                    elif path=='/api/admin/demo-event':
                        db.commit()  # Engine owns its atomic transaction; no outer write lock.
                        channel=body['channel']; payload=body['payload']
                        if channel=='telegram': events=telegram_events(payload)
                        elif channel=='instagram': events=instagram_events(payload,'demo-account')
                        else: raise ValueError('Unknown channel')
                        results=[app.engine.process('demo-'+channel,*event) for event in events]
                        return self.respond(200,{'results':results,'mode':'demo','actually_sent':False})
                    else: return self.respond(404,{'error':'Не найдено'})
                    db.execute('INSERT INTO audit(action,target,created_at,actor) VALUES (?,?,?,?)',(path,str(body.get('id','')),now(),member_role))
                return self.respond(200,{'ok':True})
            return self.respond(404,{'error':'Не найдено'})
    return Handler

def main():
    load_env()
    if os.getenv('APP_MODE','demo')!='demo': raise SystemExit('Only demo mode is supported. Real sends require a separate approved deployment.')
    host=os.getenv('HOST','127.0.0.1')
    if host not in ('127.0.0.1','localhost'): raise SystemExit('This prototype binds to localhost only.')
    password=os.getenv('ADMIN_PASSWORD') or secrets.token_urlsafe(24)
    app=Application(ROOT/os.getenv('DATABASE','data/assistant.sqlite3'),password)
    port=int(os.getenv('PORT','8080'))
    print(f'Local demo: http://{host}:{port} | Team: http://{host}:{port}/admin',flush=True)
    if not os.getenv('ADMIN_PASSWORD'): print('Generated team password (this run only): '+password,flush=True)
    server=ThreadingHTTPServer((host,port),make_handler(app))
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__=='__main__': main()
