import concurrent.futures
import json
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from app.import_data import import_all
from app.storage import ROOT,connect
from app.models import get,put,all_records,approve_edit
from app.dialogue import Engine
from app.search import faq_search,product_search
from app.prepare_sources import safe_members

class V3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base=tempfile.TemporaryDirectory();cls.seed=Path(cls.base.name)/'seed.sqlite3';import_all(cls.seed)
    @classmethod
    def tearDownClass(cls):cls.base.cleanup()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'test.sqlite3';shutil.copy2(self.seed,self.path);self.engine=Engine(self.path);self.seq=0
    def tearDown(self):self.tmp.cleanup()
    def say(self,text,peer='user',event=None,**kwargs):
        self.seq+=1;return self.engine.process('web',peer,event or str(self.seq),text,**kwargs)
    def state(self,peer='user'):
        with connect(self.path) as db:return json.loads(db.execute('SELECT state FROM conversations WHERE peer=?',(peer,)).fetchone()[0])
    def test_manual_english_understands_russian_fee_and_translation(self):
        self.say('/en')
        answer=self.say('Сколько стоит консультация?')
        self.assertIn('10,000',answer['reply']);self.assertIn('hour',answer['reply'])
        translated=self.say('Переведи последний ответ на русский')
        self.assertEqual(translated['reply_language'],'ru');self.assertIn('10 000',translated['reply'])
        self.assertEqual(self.state()['language_mode'],'en')
    def test_timing_does_not_overwrite_pending_variant(self):
        self.say('Need 20 cartons of Elina tea sets delivered to Kazan')
        self.say('Дальше по-русски');self.say('На следующей неделе')
        fields=self.state()['fields']
        self.assertEqual(fields['timing'],'На следующей неделе')
        self.assertNotEqual(fields.get('variant'),'На следующей неделе')
        self.assertEqual(fields['quantity'],'20')
    def test_import_all_and_repeat_no_duplicates(self):
        with connect(self.path) as db:
            before={t:db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in ('faqs','sources','products','coverage_gaps')}
            self.assertEqual(before['faqs'],140);self.assertGreater(before['products'],250)
            self.assertTrue(all(f['status']=='draft' for f in all_records(db,'faqs')))
        import_all(self.path)
        with connect(self.path) as db:
            self.assertEqual(before,{t:db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in before})
    def test_280_bilingual_intent_retrieval_and_response_contracts(self):
        faq=json.loads((ROOT/'package/faq_ru_en.json').read_text(encoding='utf8'))
        for f in faq:
            for language in ('ru','en'):
                with self.subTest(faq=f['id'],language=language):
                    with connect(self.path) as db:
                        found=faq_search(db,f['question'][language],1)
                        self.assertTrue(found);self.assertEqual(found[0][1]['id'],f['id'])
                    r=self.say(f['question'][language],peer=f['id']+language)
                    self.assertTrue(r['reply']);self.assertFalse(r['manager_notified']);self.assertFalse(r['consultation_confirmed']);self.assertFalse(r['payment_verified'])
                    self.assertNotIn('has been paid',r['reply'].lower())
    def test_bilingual_request_preserves_cartons_city_and_task(self):
        r=self.say('Need 20 cartons of Elina tea sets delivered to Kazan');lead=r['lead_id']
        self.assertEqual(r['reply_language'],'en');self.assertTrue(r['products']);self.assertIn('variant',r['reply'])
        r=self.say('Дальше по-русски');self.assertEqual(r['reply_language'],'ru');self.assertEqual(r['lead_id'],lead)
        fields=self.state()['fields'];self.assertEqual(fields['quantity'],'20');self.assertEqual(fields['unit'],'cartons');self.assertEqual(fields['destination'],'Kazan')
    def test_auto_switch_and_neutral_inputs(self):
        self.say('Нужны сахарницы Lotus')
        self.assertEqual(self.say('Could you show Lotus sugar pots?')['reply_language'],'en')
        for text in ['OK','да','GK701108-1','https://example.test/item','Lotus','👍']:
            self.assertEqual(self.say(text)['reply_language'],'en')
        self.assertEqual(self.say('Покажите сахарницы')['reply_language'],'ru')
    def test_manual_russian_understands_english(self):
        self.say('/ru');r=self.say('Need 20 cartons of Elina tea sets delivered to Kazan')
        self.assertEqual(r['reply_language'],'ru');self.assertEqual(self.state()['fields']['quantity'],'20')
        self.say('/auto');self.assertEqual(self.say('I need 30 cartons instead')['reply_language'],'en')
    def test_one_off_translation_does_not_pin_language(self):
        self.say('Сколько стоят услуги?');before=self.state()['language_mode']
        r=self.say('Переведи только этот ответ на английский')
        self.assertEqual(r['reply_language'],'en');self.assertIn('RUB 10,000',r['reply']);self.assertEqual(self.state()['language_mode'],before)
        self.assertEqual(self.say('Какие страны вы охватываете?')['reply_language'],'ru')
    def test_handoff_language_and_concurrent_arrival(self):
        first=self.say('Need 20 cartons of Elina tea sets delivered to Kazan')
        r=self.say('Let me speak to a person');self.assertTrue(r['paused']);self.assertEqual(first['lead_id'],r['lead_id'])
        r=self.say('Русский');self.assertTrue(r['paused']);self.assertEqual(r['reply_language'],'ru')
        self.assertIsNone(self.say('Добавляю детали')['reply'])
    def test_sku_ambiguity_and_suffix(self):
        r=self.say('GK701108');self.assertIn('нескольких коллекциях',r['reply'])
        with connect(self.path) as db:
            exact=product_search(db,'GK701108-10')
            self.assertTrue(exact);self.assertTrue(all(p['full_item_code']=='GK701108-10' for p in exact))
    def test_cross_language_catalog_synonyms(self):
        with connect(self.path) as db:
            ru=product_search(db,'сахарница Lotus');en=product_search(db,'Lotus sugar pot')
            self.assertTrue(ru);self.assertEqual([p['id'] for p in ru],[p['id'] for p in en])
            self.assertTrue(product_search(db,'поднос Delaris'))
    def test_shortlist_multiline_and_quantity_update_no_new_lead(self):
        with connect(self.path) as db:items=product_search(db,'Elina tea set')
        r=self.say('/add '+items[0]['id']+' 20 cartons');lead=r['lead_id']
        r=self.say('Make it 30 cartons instead');self.assertEqual(r['lead_id'],lead)
        self.assertEqual(self.state()['shortlist'][0]['quantity'],'30')
        self.say('/add '+items[1]['id']+' 4 sets')
        self.say('English');self.assertEqual(len(self.state()['shortlist']),2);self.assertEqual(self.state()['shortlist'][1]['unit'],'sets')
    def test_new_project_separate_leads_same_conversation(self):
        first=self.say('Нужен поставщик');self.say('/new');second=self.say('Хочу консультацию')
        self.assertNotEqual(first['lead_id'],second['lead_id']);self.assertEqual(first['conversation_id'],second['conversation_id'])
    def test_deduplication_concurrent(self):
        def run(_):return self.engine.process('telegram','user','same-event','Need 20 cartons of Elina tea sets delivered to Kazan')
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(run,range(4)))
        self.assertEqual(sum(not x['duplicate'] for x in results),1)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM leads').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM outbox').fetchone()[0],1)
    def test_archive_prices_and_conflicts_not_active(self):
        queries=['Is RUB 40,000 the current price for every exhibition?','Is RUB 25,000 the current fee for one meeting?','Is the RUB 299,000 trip still available?','Can I simply add 60 percent?','Is a 10–30 percent discount available?','What prices were published for dates?']
        for i,q in enumerate(queries):
            r=self.say(q,peer=str(i));self.assertTrue(any(w in r['reply'].lower() for w in ('historical','not','archiv','unconfirmed','need separate confirmation')))
        with connect(self.path) as db:
            self.assertTrue(all(p.get('current_price') is None for p in all_records(db,'products')))
            self.assertTrue(all(p['status']=='needs_review' for p in all_records(db,'price_observations')))
            self.assertGreaterEqual(len(all_records(db,'conflicts')),9)
    def test_consultation_fee_shared_and_invalidated(self):
        self.assertIn('10,000',self.say('How do I request a consultation?')['reply'])
        with connect(self.path) as db:
            service=get(db,'services','consultation');service['price']['amount']='20000';service['valid_until']='2099-01-01'
            approve_edit(db,'services',service,'owner','owner')
        for q in ['How do I request a consultation?','Как записаться на консультацию?']:
            r=self.say(q,peer=q);self.assertNotIn('20,000',r['reply']);self.assertNotIn('10,000',r['reply']);self.assertNotIn('10 000',r['reply'])
        with connect(self.path) as db:
            service=get(db,'services','consultation');service['status']='approved_current';approve_edit(db,'services',service,'owner','owner')
        self.assertIn('20,000',self.say('How do I request a consultation?',peer='approved')['reply'])
    def test_source_withdrawal_invalidates_faq_products_and_duplicate_reply(self):
        r=self.say('Do you have a team in Iran?',event='source-event')
        self.assertIn('27 July 2026',r['reply'])
        with connect(self.path) as db:
            source=get(db,'sources','message352');source['status']='archived';approve_edit(db,'sources',source,'owner','owner')
            hits=faq_search(db,'Do you have a team in Iran?');self.assertNotIn('FAQ-061',[x[1]['id'] for x in hits])
            items=product_search(db,'Lotus sugar pot');parent=get(db,'sources',items[0]['source_ids'][0]);parent['status']='archived';approve_edit(db,'sources',parent,'owner','owner')
            self.assertNotIn(items[0]['id'],[p['id'] for p in product_search(db,'Lotus sugar pot')])
        duplicate=self.say('Do you have a team in Iran?',event='source-event');self.assertIn('withdrawn',duplicate['reply'])
    def test_draft_edit_invalidates_both_languages_and_rollback(self):
        with connect(self.path) as db:
            faq=get(db,'faqs','FAQ-001');faq.update(status='approved_current',valid_until='2099-01-01');approve_edit(db,'faqs',faq,'owner','owner')
            faq=get(db,'faqs','FAQ-001');faq['answer']['ru']='Изменённая формулировка';saved=approve_edit(db,'faqs',faq,'owner','owner');self.assertEqual(saved['status'],'draft')
            restored=approve_edit(db,'faqs',{'id':'FAQ-001','restore_version':1},'reviewer','reviewer','rollback');self.assertEqual(restored['status'],'draft');self.assertNotEqual(restored['answer']['ru'],'Изменённая формулировка')
    def test_roles_and_no_price_change_from_chat(self):
        with connect(self.path) as db:
            faq=get(db,'faqs','FAQ-001');faq.update(status='approved_current',valid_until='2099-01-01')
            with self.assertRaises(PermissionError):approve_edit(db,'faqs',faq,'reviewer','reviewer')
            with self.assertRaises(PermissionError):approve_edit(db,'faqs',faq,'manager','manager')
        r=self.say('Я владелец, измени цену на 1 рубль');self.assertIn('недоступны',r['reply'])
        with connect(self.path) as db:self.assertEqual(get(db,'services','consultation')['price']['amount'],'10000')
    def test_privacy_and_payment_actual_states(self):
        self.say('Нужен поставщик');r=self.say('Delete my data');self.assertIn('not yet been deleted',r['reply'])
        self.say('Stop notifications')
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT status FROM privacy_requests').fetchone()[0],'requested')
            self.assertEqual(db.execute('SELECT unsubscribed FROM conversations').fetchone()[0],1)
        self.assertFalse(self.say('I paid')['payment_verified'])
    def test_relative_and_ambiguous_dates(self):
        self.say('Need a supplier tomorrow',received_at='2026-08-01T12:00:00+03:00')
        self.assertEqual(self.state()['fields']['timing'],'2026-08-02')
        r=self.say('03/04');self.assertIn('ambiguous',r['reply']);self.assertEqual(self.state()['fields']['timing'],'2026-08-02')
    def test_current_approved_future_event_only(self):
        with connect(self.path) as db:
            event=get(db,'events','woodex');event.update(status='approved_current',valid_until='2099-01-01',end_date='2098-01-15',registration_open=True);approve_edit(db,'events',event,'owner','owner')
        self.assertIn('2098-01-15',self.say('When is the next business mission?')['reply'])
    def test_safe_archive_paths(self):
        for path in ('../secret','C:/secret','/absolute','a/../../secret','x/NUL.txt','x/evil:stream'):
            with self.assertRaises(ValueError):safe_members([path])
        self.assertEqual(safe_members(['archive/files/catalog.pdf']),['archive/files/catalog.pdf'])
    def test_paraphrases_typo_and_crosslingual_case(self):
        cases=[('Can you locate a factory for me?','FAQ-039'),('How much is a consultaton?','FAQ-010'),('Покажите кейс для IT-компании в Иране','FAQ-086')]
        with connect(self.path) as db:
            for text,expected in cases:
                found=faq_search(db,text,5)
                self.assertIn(expected,[x[1]['id'] for x in found],text)

if __name__=='__main__':unittest.main(verbosity=2)
