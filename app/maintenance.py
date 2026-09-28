"""Local backup and retention tools. Prune is a dry run unless --apply is supplied."""
import argparse
import sqlite3
from pathlib import Path
from datetime import datetime,timezone,timedelta
from .storage import ROOT,connect,now
from .server import load_env
import os

def backup(path,destination):
    destination=Path(destination).resolve();allowed=(ROOT/'backups').resolve()
    if not destination.is_relative_to(allowed):raise ValueError('Backup must stay in workspace/backups')
    destination.parent.mkdir(exist_ok=True)
    if destination.exists():raise ValueError('Refuse to overwrite a backup')
    with connect(path) as src:
        dst=sqlite3.connect(destination)
        try:src.backup(dst)
        finally:dst.close()
    return str(destination)

def prune(path,days,apply=False):
    if days<1:raise ValueError('Retention must be positive')
    cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
    with connect(path) as db:
        ids=[r[0] for r in db.execute('''SELECT c.id FROM conversations c LEFT JOIN messages m ON m.conversation_id=c.id
          GROUP BY c.id HAVING COALESCE(MAX(m.created_at),c.created_at) < ?''',(cutoff,))]
        if apply:
            for cid in ids:
                for table in ('unknown_questions','actions','messages','leads','outbox','incoming','privacy_requests'):
                    db.execute(f'DELETE FROM {table} WHERE conversation_id=?',(cid,))
                db.execute('DELETE FROM conversations WHERE id=?',(cid,))
            db.execute('INSERT INTO audit(action,target,created_at,actor) VALUES(?,?,?,?)',('retention_prune',str(len(ids)),now(),'local_cli'))
    return {'eligible_conversations':len(ids),'applied':apply,'cutoff':cutoff}

def main():
    load_env();p=argparse.ArgumentParser();p.add_argument('action',choices=['backup','prune']);p.add_argument('--database',default=os.getenv('DATABASE','data/assistant.sqlite3'));p.add_argument('--days',type=int,default=int(os.getenv('RETENTION_DAYS','180')));p.add_argument('--apply',action='store_true');args=p.parse_args()
    if args.action=='backup':print(backup(args.database,ROOT/'backups'/('assistant-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.sqlite3')))
    else:print(prune(args.database,args.days,args.apply))
if __name__=='__main__':main()
