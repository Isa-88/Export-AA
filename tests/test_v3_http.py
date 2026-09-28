import hashlib
import json
import unittest
from tests import test_prototype as legacy
from app.storage import connect
from app.models import get
from app.adapters import demo_delivery

class V3HTTPTests(unittest.TestCase):
    setUp=legacy.HTTPTests.setUp
    tearDown=legacy.HTTPTests.tearDown
    request=legacy.HTTPTests.request
    login=legacy.HTTPTests.login
    def role_login(self,role):
        password='test-role-'+role+'-long-password'
        self.app.role_hashes[role]=hashlib.scrypt(password.encode(),salt=b'local-prototype-login',n=16384,r=8,p=1)
        code,data=self.request('/api/admin/login',{'password':password})
        self.assertEqual(code,200);return {'X-CSRF-Token':data['csrf']}
    def test_reviewer_cannot_read_clients_or_approve_prices(self):
        self.app.engine.process('web','private-client','e1','Need a supplier')
        headers=self.role_login('reviewer');code,state=self.request('/api/admin/state')
        self.assertEqual(code,200);self.assertEqual(state['leads'],[]);self.assertEqual(state['messages'],[])
        self.assertEqual(self.request('/api/admin/pause',{'id':'x','paused':True},headers)[0],403)
        with connect(self.app.path) as db:doc=get(db,'services','consultation')
        doc['valid_until']='2099-01-01'
        self.assertEqual(self.request('/api/admin/entity',{'table':'services','document':doc},headers)[0],403)
    def test_manager_can_handle_enquiry_but_cannot_edit_knowledge(self):
        r=self.app.engine.process('web','private-client','e1','Need a supplier')
        headers=self.role_login('manager')
        self.assertEqual(self.request('/api/admin/lead',{'id':r['lead_id'],'status':'в работе','assignee':'Test'},headers)[0],200)
        self.assertEqual(self.request('/api/admin/entity',{'table':'faqs','document':{'id':'FAQ-001'}},headers)[0],403)
    def test_deletion_is_real_and_conversation_scoped(self):
        own=self.app.engine.process('web','private-client','e1','Need a supplier');self.app.engine.process('web','other-client','e2','Need a supplier')
        self.app.engine.process('web','private-client','e3','Delete my data')
        with connect(self.app.path) as db:rid=db.execute('SELECT id FROM privacy_requests').fetchone()[0]
        headers=self.login();self.assertEqual(self.request('/api/admin/delete-data',{'id':rid},headers)[0],200)
        with connect(self.app.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM messages WHERE conversation_id=?',(own['conversation_id'],)).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM leads').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT status FROM privacy_requests').fetchone()[0],'completed')
            self.assertFalse(db.execute("SELECT 1 FROM conversations WHERE peer='private-client'").fetchone())
    def test_catalog_session_and_no_current_price(self):
        self.assertEqual(self.request('/api/catalog?q=Elina')[0],401)
        self.request('/api/session');code,data=self.request('/api/catalog?q=Elina')
        self.assertEqual(code,200);self.assertTrue(data['products'])
        self.assertTrue(all(p['current_price'] is None and p['stock'] is None for p in data['products']))
    def test_demo_delivery_dead_letter_and_handoff_block(self):
        r=self.app.engine.process('demo-telegram','peer','e','Need a supplier')
        with connect(self.app.path) as db:
            oid=db.execute('SELECT id FROM outbox').fetchone()[0]
            for _ in range(3):result=demo_delivery(db,oid,True)
            self.assertEqual(result['status'],'demo_dead_letter');self.assertFalse(result['actually_sent'])
            with self.assertRaises(ValueError):demo_delivery(db,oid,False)
        self.app.engine.process('demo-telegram','peer','e2','Details')
        headers=self.login();self.request('/api/admin/pause',{'id':r['conversation_id'],'paused':True},headers)
        with connect(self.app.path) as db:
            row=db.execute('SELECT * FROM outbox ORDER BY id DESC LIMIT 1').fetchone();self.assertEqual(row['status'],'cancelled_handoff')
    def test_sources_auth_and_bilingual_errors(self):
        code,data=self.request('/api/admin/source?id=message352');self.assertEqual(code,401);self.assertTrue(data['error_en'])
        self.login();code,data=self.request('/api/admin/source?id=message352');self.assertEqual(code,200);self.assertIn('text',data['source'])

if __name__=='__main__':unittest.main(verbosity=2)
