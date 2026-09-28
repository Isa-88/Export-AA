"""Versioned business entities and additive migrations for the v1 SQLite file."""
import json
from datetime import date
from .storage import connect, now

ENTITY_TABLES=('sources','faqs','products','price_observations','services','events','cases','resources','contacts','coverage_gaps','conflicts')
STATUSES=('draft','source_observed','approved_current','historical','needs_review','expired','archived')

def migrate(path):
    with connect(path) as db:
        for table in ENTITY_TABLES:
            db.execute(f'CREATE TABLE IF NOT EXISTS {table}(id TEXT PRIMARY KEY, document TEXT NOT NULL)')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS approvals(id INTEGER PRIMARY KEY, entity_type TEXT, entity_id TEXT, version INTEGER, document TEXT, actor TEXT, action TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS actions(id INTEGER PRIMARY KEY, conversation_id TEXT, event_id TEXT, kind TEXT, result TEXT, created_at TEXT, UNIQUE(conversation_id,event_id,kind));
        CREATE TABLE IF NOT EXISTS unknown_questions(id INTEGER PRIMARY KEY, conversation_id TEXT, message_id INTEGER, created_at TEXT);
        CREATE TABLE IF NOT EXISTS privacy_requests(id TEXT PRIMARY KEY, conversation_id TEXT, kind TEXT, status TEXT, created_at TEXT, completed_at TEXT);
        CREATE TABLE IF NOT EXISTS imports(id TEXT PRIMARY KEY, report TEXT, created_at TEXT);
        ''')
        for table,columns in {
          'conversations':{'language_mode':"TEXT NOT NULL DEFAULT 'auto'",'detected_input_language':'TEXT','reply_language':"TEXT NOT NULL DEFAULT 'ru'",'preferred_human_language':'TEXT','unsubscribed':'INTEGER NOT NULL DEFAULT 0'},
          'messages':{'original_language':'TEXT','translation_language':'TEXT','translation':'TEXT','translation_status':"TEXT NOT NULL DEFAULT 'not_requested'",'source_refs':"TEXT NOT NULL DEFAULT '[]'",'metadata':"TEXT NOT NULL DEFAULT '{}'"},
          'leads':{'summary_ru':"TEXT NOT NULL DEFAULT ''",'summary_en':"TEXT NOT NULL DEFAULT ''"},
          'audit':{'actor':"TEXT NOT NULL DEFAULT 'owner'"}
          ,'outbox':{'attempts':'INTEGER NOT NULL DEFAULT 0'}
        }.items():
            present={r['name'] for r in db.execute(f'PRAGMA table_info({table})')}
            for name,definition in columns.items():
                if name not in present:db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
        if not db.execute('SELECT 1 FROM migrations WHERE version=3').fetchone():
            db.executescript('''
            ALTER TABLE leads RENAME TO leads_v1;
            CREATE TABLE leads(id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
              status TEXT NOT NULL, assignee TEXT NOT NULL DEFAULT '', fields TEXT NOT NULL, summary TEXT NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL, summary_ru TEXT NOT NULL DEFAULT '', summary_en TEXT NOT NULL DEFAULT '',
              project_key TEXT NOT NULL DEFAULT 'initial', UNIQUE(conversation_id,project_key));
            INSERT INTO leads(id,conversation_id,status,assignee,fields,summary,created_at,updated_at,summary_ru,summary_en)
              SELECT id,conversation_id,status,assignee,fields,summary,created_at,updated_at,summary_ru,summary_en FROM leads_v1;
            DROP TABLE leads_v1;
            ''')
            db.execute('INSERT INTO migrations VALUES(3,?)',(now(),))

def get(db,table,id):
    if table not in ENTITY_TABLES:raise ValueError('Unknown entity')
    row=db.execute(f'SELECT document FROM {table} WHERE id=?',(id,)).fetchone()
    return json.loads(row[0]) if row else None

def all_records(db,table):
    if table not in ENTITY_TABLES:raise ValueError('Unknown entity')
    return [json.loads(r[0]) for r in db.execute(f'SELECT document FROM {table} ORDER BY id')]

def put(db,table,doc,replace=False):
    if table not in ENTITY_TABLES:raise ValueError('Unknown entity')
    clause='DO UPDATE SET document=excluded.document' if replace else 'DO NOTHING'
    db.execute(f'INSERT INTO {table} VALUES(?,?) ON CONFLICT(id) {clause}',(doc['id'],json.dumps(doc,ensure_ascii=False)))

def valid(doc,current=False):
    if not doc or doc.get('status') in ('archived','expired','needs_review'):return False
    if current and doc.get('status')!='approved_current':return False
    today=date.today().isoformat()
    return (not doc.get('valid_from') or doc['valid_from']<=today) and (not doc.get('valid_until') or doc['valid_until']>=today)

def source_allowed(db,ref):
    doc=get(db,'sources',ref)
    return doc is not None and doc.get('status') not in ('archived','expired')

def approve_edit(db,table,doc,actor,role,action='save'):
    if table not in ENTITY_TABLES or table in ('coverage_gaps',) and role=='manager':raise PermissionError('Role cannot edit this entity')
    if role not in ('owner','reviewer'):raise PermissionError('Review role required')
    old=get(db,table,doc.get('id'))
    if not old:raise ValueError('Existing entity required')
    if action=='rollback':
        revision=db.execute('SELECT document FROM approvals WHERE entity_type=? AND entity_id=? AND version=? ORDER BY id DESC LIMIT 1',
          (table,doc['id'],doc.get('restore_version'))).fetchone()
        if not revision:raise ValueError('Revision not found')
        doc=json.loads(revision[0]);doc['status']='draft'  # Restored content requires a fresh approval.
    status=doc.get('status','draft')
    if status not in STATUSES:raise ValueError('Invalid status')
    if status=='approved_current' and role!='owner':raise PermissionError('Only owner approves current facts')
    if table=='sources' and status=='approved_current':raise ValueError('Approve an extracted fact, not an entire archive source')
    if table=='faqs':
        for field in ('question','answer'):
            if not isinstance(doc.get(field),dict) or not all(isinstance(doc[field].get(lang),str) and doc[field][lang].strip() for lang in ('ru','en')):
                raise ValueError('Both language versions are required')
        if doc['answer']!=old['answer']:status='draft'
    if table=='services' and doc.get('price')!=old.get('price'):status='draft'
    if table=='price_observations' and status=='approved_current':
        if not all(doc.get(k) for k in ('currency','selling_unit','valid_until','supplier_confirmation')):raise ValueError('Price requires confirmed currency, unit, validity and supplier evidence')
    if status=='approved_current':
        if not isinstance(doc.get('source_ids'),list) or not doc['source_ids']:raise ValueError('Evidence is required')
        if not all(source_allowed(db,ref) for ref in doc.get('source_ids',[])):raise ValueError('Source revoked or missing')
        if not doc.get('valid_until'):raise ValueError('Approval expiry is required')
    for key in ('valid_from','valid_until'):
        if doc.get(key):date.fromisoformat(doc[key])
    db.execute('INSERT INTO approvals(entity_type,entity_id,version,document,actor,action,created_at) VALUES(?,?,?,?,?,?,?)',
      (table,old['id'],old.get('version',1),json.dumps(old,ensure_ascii=False),actor,action,now()))
    doc.update(status=status,version=old.get('version',1)+1,reviewed_at=now(),approved_by=actor if status=='approved_current' else None)
    put(db,table,doc,True)
    if table=='sources' and status=='archived':
        db.execute("UPDATE outbox SET status='blocked_source_review',error='Source withdrawn; regenerate before delivery' WHERE status IN ('demo_not_sent','demo_error')")
    # Changes in shared price invalidate BOTH language variants in dependent FAQ.
    if table=='services' and doc.get('price')!=old.get('price'):
        for faq in all_records(db,'faqs'):
            if 'consultation' in faq.get('fact_ids',[]):
                faq.update(status='draft',version=faq.get('version',1)+1,approved_by=None)
                put(db,'faqs',faq,True)
    return doc
