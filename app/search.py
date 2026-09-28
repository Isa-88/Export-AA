"""Local hybrid retrieval: bilingual concepts, word stems and fuzzy character matching."""
import difflib
import math
import re
from collections import Counter
from functools import lru_cache
from .models import all_records, get, valid, source_allowed

CONCEPTS={
 'price':r'цена|цены|цену|стоим\w*|сколько|price\w*|cost\w*|fee\w*|pricing|quote|quotation',
 'consultation':r'консультац\w*|consult\w*|booking', 'mission':r'мисси\w*|поезд\w*|mission\w*|trip\w*|tour\w*',
 'exhibition':r'выстав\w*|exhibit\w*', 'supplier':r'поставщи\w*|производител\w*|фабрик\w*|supplier\w*|manufactur\w*|factor\w*',
 'buyer':r'покупател\w*|buyer\w*', 'market':r'рын\w*|market\w*', 'research':r'исслед\w*|анализ\w*|research\w*',
 'partner':r'партн[её]р\w*|partner\w*', 'export':r'экспорт\w*|export\w*', 'import':r'импорт\w*|import\w*',
 'country':r'стран\w*|направлен\w*|countr\w*|destination\w*','iran':r'иран\w*|iran\w*','afghanistan':r'афган\w*|afghan\w*',
 'pakistan':r'пакистан\w*|pakistan\w*','russia':r'росси\w*|russia\w*','china':r'китай\w*|china',
 'tableware':r'посуд\w*|tableware|crockery','catalog':r'каталог\w*|catalog\w*','collection':r'коллекц\w*|collection\w*',
 'tea_set':r'чай\w* набор\w*|tea sets?','dinner_set':r'столов\w* набор\w*|dinner sets?',
 'sugar_pot':r'сахарниц\w*|sugar pots?|sugar bowls?','tray':r'поднос\w*|serving trays?|trays?',
 'cartons':r'картон\w*|короб\w*|cartons?|boxes','pieces':r'предмет\w*|штук\w*|pieces?|pcs',
 'quantity':r'количеств\w*|объ[её]м\w*|quantit\w*|volume\w*','delivery':r'достав\w*|shipping|deliver\w*|landed',
 'stock':r'налич\w*|склад\w*|stock|available|availability','discount':r'скид\w*|discount\w*',
 'material':r'материал\w*|из чего|material\w*|made of','dates':r'финик\w*|mazafati|мазафати|dates',
 'person':r'человек\w*|менеджер\w*|оператор\w*|human|person|manager|agent',
 'case':r'кейс\w*|отзыв\w*|case stud\w*|reviews?|testimonials?',
 'antiplagiat':r'антиплагиат\w*|antiplagiat','epotos':r'эпотос|epotos','textile':r'текстил\w*|textil\w*',
 'sanctions':r'санкц\w*|sanction\w*','bank':r'банк\w*|bank\w*','payment':r'плат[её]ж\w*|оплат\w*|pay\w*',
 'tax':r'налог\w*|пошлин\w*|tax\w*|dut\w*','certification':r'сертиф\w*|certif\w*',
 'documents':r'документ\w*|document\w*','guarantee':r'гарант\w*|guarantee\w*','contract':r'контракт\w*|contract\w*',
 'free':r'бесплат\w*|free','refund':r'возврат\w*|вернуть|refund\w*','deposit':r'предоплат\w*|deposits?',
 'recording':r'запись встречи|запись вебинара|recording\w*','language':r'язык\w*|language\w*',
 'english':r'англий\w*|english','russian':r'русск\w*|russian','delete':r'удал\w*|delet\w*',
 'change':r'исправ\w*|измен\w*|chang\w*|correct\w*','current':r'текущ\w*|действующ\w*|сейчас|current\w*|now',
 'historical':r'архив\w*|историч\w*|раньше|было|были|historical|archiv\w*|published|past',
 'minimum':r'минимал\w*|minimum|moq','time':r'срок\w*|долго|time|timing|lead time','visa':r'виз[аыу]|visa\w*',
 'flight':r'перел[её]т\w*|flight\w*','hotel':r'отел\w*|проживан\w*|hotel\w*|accommodation',
 'small':r'мал\w* бизнес\w*|small business\w*','photo':r'фото\w*|phot\w*|image\w*',
 'logistics':r'логист\w*|logistic\w*','item_code':r'артикул\w*|item code\w*|sku',
}
STOP=set('the a an to do you i my your we can what how is are in on for of and with me at this from мне вы я как что на в и для по с у ли it be does have which need please could would want'.split())

@lru_cache(maxsize=8192)
def normalized(text):
    t=re.sub(r'\bIT\b',' information_technology ',text).lower().replace('ё','е')
    t=re.sub(r'\bsoftware\b|\bit[- ]компан\w*',' information_technology ',t)
    for canonical,pattern in CONCEPTS.items():t=re.sub(r'\b(?:'+pattern+r')\b',' '+canonical+' ',t,flags=re.I)
    return ' '.join(w for w in re.findall(r'[\w-]+',t) if w not in STOP)

@lru_cache(maxsize=8192)
def tokens(text):
    return {w if w.isascii() else w[:6] for w in normalized(text).split()}

def sources_ok(db,doc):
    for ref in doc.get('source_ids',[]):
        source=get(db,'sources',ref)
        if not source or source.get('status') in ('archived','expired'):return False
        parent=source.get('parent_source_id')
        if parent and not source_allowed(db,parent):return False
    return True

def faq_search(db,text,limit=3):
    docs=[d for d in all_records(db,'faqs') if valid(d) and sources_ok(db,d)]
    query=normalized(text);qt=tokens(text)
    exact=[d for d in docs if any(text.casefold().strip().rstrip('.?!')==q.casefold().strip().rstrip('.?!') for q in d['question'].values())]
    if exact:return [(1.,d) for d in exact[:limit]]
    if not query:return []
    if {'consultation','price'} <= qt and not qt.intersection({'free','refund','deposit'}):
        fee=next((d for d in docs if d['id']=='FAQ-009'),None)
        if fee:return [(.93,fee)]
    frequency=Counter(w for d in docs for w in set().union(*(tokens(v) for v in d['question'].values())))
    def weight(word):return math.log(1+len(docs)/(1+frequency[word]))
    results=[]
    for d in docs:
        score=0
        for q in d['question'].values():
            norm=normalized(q);dt=tokens(q)
            if text.casefold().strip().rstrip('.?!')==q.casefold().strip().rstrip('.?!'):score=1.;break
            overlap=sum(weight(w) for w in qt&dt)
            denom=sum(weight(w) for w in qt|dt) or 1
            lexical=overlap/denom
            fuzzy=difflib.SequenceMatcher(None,query,norm).ratio()
            score=max(score,0.72*lexical+0.28*fuzzy)
        if 'information_technology' in query and ('case' in query or 'iran' in query):
            if d['id']=='FAQ-086':score=max(score,.72)
            elif d['id']=='FAQ-081':score=max(score,.60)
        if score>=0.25:results.append((score,d))
    return sorted(results,key=lambda pair:pair[0],reverse=True)[:limit]

def product_search(db,query,limit=12):
    t=normalized(query);sku=re.search(r'GK\s*\d{6}(?:-\d+)?',query,re.I)
    exact_sku=re.sub(r'\s','',sku[0].upper()) if sku else None
    collections=[c for c in ('atusa','elina','lotus','roysa','sweety','larisa','sarina','delaris','niatus','elika','adlya','butterfly') if c in query.lower().replace('larissa','larisa')]
    kinds=[k for k in ('tea_set','dinner_set','sugar_pot','tray') if k in t]
    colors=[c for c in ('blue','green','silver','gold') if c in t]
    results=[]
    for d in all_records(db,'products'):
        if not valid(d) or not sources_ok(db,d):continue
        if exact_sku and d['full_item_code']!=exact_sku:continue
        if collections and d['collection'].lower() not in collections:continue
        if colors and d.get('finish') not in colors:continue
        names=normalized(' '.join(d['name'].values()))
        if kinds and not any(k in names for k in kinds):continue
        score=(10 if exact_sku else 0)+(4 if collections else 0)+(2 if kinds else 0)+(1 if colors else 0)
        if score or 'catalog' in t or 'tableware' in t:results.append((score,d))
    return [d for _,d in sorted(results,key=lambda x:(-x[0],x[1]['collection'],x[1]['full_item_code']))[:limit]]
