import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent

def now():
    return datetime.now(timezone.utc).isoformat()

@contextmanager
def connect(path):
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        with db:
            yield db
    finally:
        db.close()

def initialize(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS conversations (
          id TEXT PRIMARY KEY, channel TEXT NOT NULL, peer TEXT NOT NULL,
          paused INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL, UNIQUE(channel,peer));
        CREATE TABLE IF NOT EXISTS messages (
          id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
          role TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS leads (
          id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL UNIQUE REFERENCES conversations(id),
          status TEXT NOT NULL, assignee TEXT NOT NULL DEFAULT '', fields TEXT NOT NULL,
          summary TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS incoming (
          channel TEXT NOT NULL, event_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
          result TEXT NOT NULL, PRIMARY KEY(channel,event_id));
        CREATE TABLE IF NOT EXISTS knowledge (id TEXT PRIMARY KEY, document TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS missions (id TEXT PRIMARY KEY, document TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS outbox (
          id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL, channel TEXT NOT NULL,
          text TEXT NOT NULL, status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS audit (
          id INTEGER PRIMARY KEY, action TEXT NOT NULL, target TEXT NOT NULL, created_at TEXT NOT NULL);
        ''')
        for table, filename in [('knowledge','knowledge.json'), ('missions','missions.json')]:
            if not db.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone():
                for item in json.loads((ROOT/'data'/filename).read_text(encoding='utf-8')):
                    db.execute(f'INSERT INTO {table} VALUES (?,?)', (item['id'], json.dumps(item, ensure_ascii=False)))

def documents(db, table):
    assert table in ('knowledge', 'missions')
    return [json.loads(r['document']) for r in db.execute(f'SELECT document FROM {table} ORDER BY id')]
