"""Idempotent, conservative source import. No prices become current on import."""
import hashlib
import json
import re
from pathlib import Path
from .storage import ROOT, connect, initialize, now
from .models import migrate, put, all_records

COLLECTIONS=['Atusa','Elina','Lotus','Roysa','Sweety','Larisa','Sarina','Delaris','Niatus','Elika','Adlya','Butterfly']
def read(name,default=None):
    p=ROOT/name
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else default

def base(id,status='source_observed',**values):
    return {'id':id,'status':status,'version':1,'source_ids':[],'file_path':None,'message_id':None,'page':None,'row':None,
      'published_at':None,'extracted_at':now(),'reviewed_at':None,'valid_from':None,'valid_until':None,
      'source_language':'ru','audience':'public','risk_level':'low','approved_by':None,**values}

def import_all(path):
    initialize(path);migrate(path)
    faq=read('package/faq_ru_en.json',[]);posts=read('sources/posts.json',[]);docs=read('sources/document_text.json',[])
    with connect(path) as db:
        for id in ('owner_faq','product_design','language_policy','documents_manifest','SOURCE_REVIEW'):
            put(db,'sources',base(id,file_path='package/SOURCE_REVIEW.md' if id=='SOURCE_REVIEW' else 'package/MASTER_PROMPT.md',title=id))
        for post in posts:
            put(db,'sources',base(post['id'],'historical',**{k:v for k,v in post.items() if k not in ('id','status')},title=post['text'][:85] or post['id']))
        for doc in docs:
            put(db,'sources',base(doc['id'],**{k:v for k,v in doc.items() if k not in ('id','status','pages')},title=doc['filename']))
        # Every supplied reference gets its own resolvable evidence record; missing extraction is explicit.
        for item in faq:
            source_ids=[]
            for ref in item['source_refs']:
                if ref.startswith('message'):
                    source_ids.extend(re.findall(r'message\d+',ref));continue
                if ref.startswith('files/'):
                    filename=ref[6:].split(':')[0];doc=next((d for d in docs if d['filename']==filename),None)
                    put(db,'sources',base(ref,file_path=doc['file_path'] if doc else ref,source_language=doc['source_language'] if doc else None,
                      parent_source_id=doc['id'] if doc else None,title=ref,page=int(re.search(r':p(\d+)',ref)[1]) if re.search(r':p(\d+)',ref) else None,
                      extraction_available=bool(doc),risk_level='high' if 'платеж' in filename else 'medium',audience='internal' if 'платеж' in filename else 'public'))
                elif ref.startswith('photos/'):
                    put(db,'sources',base(ref,file_path='archive/ChatExport_2026-09-16/'+ref,title=ref))
                source_ids.append(ref)
            fact_ids=['consultation'] if item['id'] in ('FAQ-009','FAQ-010','FAQ-011','FAQ-013') else []
            put(db,'faqs',base(item['id'],'draft',**{k:v for k,v in item.items() if k not in ('id','status')},source_ids=source_ids,
              fact_ids=fact_ids,preview_only=True,audience='demo',risk_level='high' if item['category']=='regulated' else 'medium'))
        put(db,'services',base('consultation','approved_current',title={'ru':'Консультация','en':'Consultation'},
          description={'ru':'Запрос на консультацию; время и язык специалиста согласуются отдельно.','en':'Consultation request; time and specialist language require separate confirmation.'},
          price={'amount':'10000','currency':'RUB','unit':'hour'},source_ids=['owner_faq'],approved_by='owner via customer brief',reviewed_at=now()))
        for id,ru,en in [('missions','Бизнес-миссии','Business missions'),('sourcing','Поиск партнёров и поставщиков','Partner and supplier sourcing'),('export','Экспорт и импорт','Export and import support')]:
            put(db,'services',base(id,'source_observed',title={'ru':ru,'en':en},source_ids=['owner_faq'],price=None))
        products=[]
        for doc in docs:
            filename=doc['filename'];collection=next((c for c in COLLECTIONS if c.lower() in filename.lower().replace('larissa','larisa')),None)
            if not collection:continue
            finish=next((c.lower() for c in ('BLUE','GREEN','SILVER','GOLD') if c in filename.upper()),None)
            for page in doc['pages']:
                text=page['text'];seen=set()
                for match in re.finditer(r'GK\s*(\d{6})(?:\s*(-\d+))?\b',text,re.I):
                    sku='GK'+match[1]+(match[2] or '')
                    if sku in seen:continue
                    seen.add(sku)
                    before=text[max(0,match.start()-100):match.start()]
                    names=re.findall(r'(Tea Set|Dinner Set|Sugar Pot|Serving Tray|Serving Dish|Snack Dish|Candy Bowl|Oval Plate|Salt Shaker|Fruit Bowl|Plate|Bowl|Cup|Pitcher|Teapot)',before,re.I)
                    name=names[-1].title() if names else 'Item'
                    # Direct row association for DOCX; PDF values stay in source excerpt pending review.
                    row=next((r for r in page.get('rows',[]) if sku in re.sub(r'GK\s+','GK',r)),None)
                    context=text[max(0,match.start()-110):min(len(text),match.end()+100)]
                    numeric_code=None;units_per_carton=None;price=None
                    if row:
                        cells=[c.strip() for c in row.split('|')]
                        if len(cells)>=4:
                            numeric_code=cells[3] if cells[3].isdigit() else None
                            units_per_carton=cells[1] if cells[1].isdigit() else None
                            price=re.sub(r'\s','',cells[0])
                    key='|'.join(['unconfirmed-supplier',collection,sku,finish or 'unspecified',doc['sha256']])
                    id='product-'+hashlib.sha256(key.encode()).hexdigest()[:20]
                    record=base(id,collection=collection,full_item_code=sku,numeric_code=numeric_code,finish=finish,
                      name={'en':name,'ru':{'Tea Set':'Чайный набор','Dinner Set':'Столовый набор','Sugar Pot':'Сахарница','Serving Tray':'Поднос','Serving Dish':'Сервировочное блюдо','Candy Bowl':'Конфетница','Oval Plate':'Овальная тарелка','Salt Shaker':'Солонка','Snack Dish':'Блюдо для закусок'}.get(name,'Предмет каталога')},
                      supplier='unconfirmed-supplier',source_version=doc['sha256'],source_ids=[doc['id']],file_path=doc['file_path'],page=page['page'],
                      evidence=context,selling_unit=None,units_per_carton=units_per_carton,pieces_per_set=None,weight=None,dimensions=None,moq=None,stock=None,
                      current_price=None,source_language='en',price_status='needs_review',risk_level='medium')
                    products.append(record);put(db,'products',record)
                    if price:put(db,'price_observations',base('price-'+id,'needs_review',product_id=id,raw_value=price,raw_currency_header='PRICE/$; mixed Persian rial headers',currency=None,selling_unit=None,source_ids=[doc['id']],file_path=doc['file_path'],page=page['page'],evidence=row))
        # Visual observations made in this run; transcribed as evidence, never approved prices.
        for collection,filename,page,sku,value in [('Sarina','SARINA-$.pdf',7,'GK646387','8.15'),('Roysa','ROYSA-$.pdf',5,'GK701139','39,900,000')]:
            doc=next(d for d in docs if d['filename']==filename)
            put(db,'price_observations',base('visual-'+collection,'needs_review',raw_value=value,raw_currency_header='PRICE/$',currency=None,selling_unit=None,
              source_ids=[doc['id']],full_item_code=sku,collection=collection,file_path=doc['file_path'],page=page,reviewed_at=now(),review_kind='visual transcription only; commercial validity unconfirmed'))
        conflicts=[('sarina-price','SARINA 8.15 / GK646387','SARINA-$.pdf',7),('lotus-price','LOTUS 329.57 / GK701108; mixed currency headings','LOTUS-$.docx',1),
          ('roysa-price','ROYSA 39,900,000 / GK701139','ROYSA-$.pdf',5),('sweety-sku','SWEETY GK616383 vs GK646383','SWEETY-$.pdf',8),
          ('delaris-dimensions','Repeated dimensions 30 × 14 × 2.5','DELARIS-Silver-Rasha.pdf',None),('elika-cover','Cover only; no specifications','ELIKA-FINAL-RASHA.pdf',1),
          ('contacts','Two similar Russian phone numbers; none approved for notifications',None,None),('elecomp','3 days / 4 nights requires clarification',None,None),('dates-tiers','Overlapping order-value brackets; pack weight missing',None,None)]
        for id,title,file,page in conflicts:put(db,'conflicts',base(id,'needs_review',title=title,file_path='archive/ChatExport_2026-09-16/files/'+file if file else None,page=page,source_ids=['SOURCE_REVIEW'],audience='internal',risk_level='high'))
        for id,title,source,date_end in [('oilshow','Iran Oil Show','message119','2025-05-12'),('woodex','WOODEX','message226','2026-01-15'),('aix','AIx','message244','2026-01-15'),('elecomp','ELECOMP 29','message357','2026-09-03'),('july-mission','Afghanistan mission','message350','2026-07-15')]:
            put(db,'events',base(id,'historical',title=title,source_ids=[source],end_date=date_end,registration_open=False,price=None))
        for id,title,refs in [('antiplagiat','Антиплагиат / Antiplagiat',['message199','message203']),('epotos','ЭПОТОС / EPOTOS',['message237']),('zeto','ZETO',['message224']),('agreements','Preliminary agreements, not payments',['message315','message355'])]:
            put(db,'cases',base(id,'historical',title=title,source_ids=refs,guarantee=False))
        for post in posts:
            if post['id'] in ('message344','message192','message193','message216','message217'):
                put(db,'resources',base(post['id'],'historical',title=post['text'][:100],source_ids=[post['id']],links=re.findall(r'https?://[^\s]+',post['text']),availability_verified=False))
        for id,phone,ref in [('ru-6885','[contact removed]','message244'),('ru-6875','[contact removed]','message246'),('iran','[contact removed]','message352')]:
            put(db,'contacts',base(id,'needs_review',phone=phone,source_ids=[ref],notification_allowed=False,audience='internal'))
        for gap in read('sources/media_coverage.json',[]):
            put(db,'coverage_gaps',base(gap['id'],'needs_review',**{k:v for k,v in gap.items() if k not in ('id','status')},processing_status=gap['status'],audience='internal'))
        # Keep the visual-review evidence separate from original extracted layers.
        for id,ref,text in [('review-sarina','files/SARINA-$.pdf:p7','8.15 appears in row 27, PRICE/$; validity not confirmed'),('review-roysa','files/ROYSA-$.pdf:p5:ocr','39,900,000 visibly appears in row 18 beside GK701139; currency not confirmed'),('review-letter','photos/[email removed]','Letter dated 30 October 2025 reports a contract with an Iranian party with Interbrain support; authenticity not independently assessed')]:
            put(db,'sources',base(id,source_ids=[ref],title=id,text=text,reviewed_at=now(),review_kind='visual',status_note='Observation, not commercial approval'))
        report=read('sources/import_report.json',{})
        report.update(faq_count=len(faq),product_records=len(all_records(db,'products')))
        db.execute('INSERT OR IGNORE INTO imports VALUES(?,?,?)',('package-v3',json.dumps(report,ensure_ascii=False),now()))
    return report

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--database',default='data/assistant.sqlite3');args=p.parse_args();print(json.dumps(import_all(args.database),ensure_ascii=False))
