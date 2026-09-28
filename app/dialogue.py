"""One bilingual dialogue and action state machine, shared by all channel adapters."""
import json
import re
import uuid
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal, InvalidOperation
from .storage import connect, now
from . import language as lang
from .models import get, all_records, valid, put
from .search import faq_search, product_search, sources_ok, normalized

QUESTIONS={
 'product':lang.pair('Какой товар или услуга вас интересует?','What product or service are you interested in?'),
 'country':lang.pair('Какая страна вас интересует?','Which country are you interested in?'),
 'origin':lang.pair('Из какой страны планируется поставка?','What is the country of origin?'),
 'destination':lang.pair('В какую страну или город нужна поставка?','Which country or city is the delivery destination?'),
 'quantity':lang.pair('Какое количество и в каких единицах вам нужно?','What quantity and unit do you need?'),
 'timing':lang.pair('Какие сроки вы рассматриваете? Можно указать, что они пока неизвестны.','What timing do you have in mind? You can leave it unknown.'),
 'collection':lang.pair('Какую коллекцию и вариант вы рассматриваете?','Which collection and variant are you considering?'),
 'variant':lang.pair('Уточните полный артикул и вариант по карточке каталога.','Please select the full item code and variant from a catalogue card.'),
 'topic':lang.pair('Какую тему вы хотите обсудить на консультации?','What would you like to discuss in the consultation?'),
 'slots':lang.pair('Какие интервалы времени вам удобны?','What time slots would suit you?'),
 'timezone':lang.pair('Какой у вас часовой пояс?','What is your time zone?'),
 'contact':lang.pair('Как удобнее связаться: здесь или другим способом?','Would you prefer a reply here or another contact method?'),
 'participants':lang.pair('Какие участники и страны задействованы — без банковских реквизитов?','Which parties and countries are involved, without bank details?'),
 'question':lang.pair('В чём состоит вопрос для специалиста?','What would you like the specialist to review?')}
FLOWS={'catalog':['collection','quantity','destination','timing'],'export':['product','origin','destination','quantity','timing'],
 'supplier':['product','origin','destination','quantity','timing'],'mission':['country','product','timing'],
 'consultation':['topic','slots','timezone','contact'],'food':['quantity','destination','timing'],
 'partner':['product','origin','destination','timing'],'deal':['product','origin','destination','timing'],
 'sanctions':['product','origin','destination','participants','question'],'human':[]}
COUNTRIES=[('Афганистан','Afghanistan',r'афган\w*|afghanistan'),('Пакистан','Pakistan',r'пакистан\w*|pakistan'),
 ('Иран','Iran',r'иран\w*|iran'),('Ирак','Iraq',r'ирак\w*|iraq'),('ОАЭ','UAE',r'оаэ|uae|emirates'),
 ('Саудовская Аравия','Saudi Arabia',r'саудов\w*\s*арави\w*|saudi arabia'),('Катар','Qatar',r'катар\w*|qatar'),
 ('Узбекистан','Uzbekistan',r'узбек\w*|uzbekistan'),('Казахстан','Kazakhstan',r'казах\w*|kazakhstan'),
 ('Таджикистан','Tajikistan',r'таджик\w*|tajikistan'),('Кыргызстан','Kyrgyzstan',r'кыргыз\w*|киргиз\w*|kyrgyzstan'),
 ('Туркменистан','Turkmenistan',r'туркмен\w*|turkmenistan'),('Россия','Russia',r'росси\w*|russia'),
 ('Казань','Kazan',r'казан\w*|kazan'),('Москва','Moscow',r'москв\w*|moscow'),('Бразилия','Brazil',r'бразили\w*|brazil'),('Китай','China',r'кита[йяе]\w*|china')]
for ru,en,_ in COUNTRIES:lang.LABELS[en]=(ru,en)

def combine(*pairs):return {l:'\n\n'.join(p[l] for p in pairs if p and p.get(l)) for l in ('ru','en')}
def number(value):
    result=Decimal(value.replace(',','.'))
    if not result.is_finite() or result<=0 or result>Decimal('1000000000'):raise ValueError('Invalid quantity')
    return format(result.normalize(),'f')

def consultation_price(db):
    doc=get(db,'services','consultation')
    if not valid(doc,True) or not sources_ok(db,doc):return lang.pair('Текущая цена консультации требует согласования.','The current consultation fee needs approval.')
    p=doc['price'];amount=Decimal(p['amount'])
    return lang.pair(f"Консультация — {amount:,.0f} {p['currency']} за час.".replace(',',' '),f"A consultation costs {p['currency']} {amount:,.0f} per hour.")

def extract_fields(text,state,received_at):
    fields=state.setdefault('fields',{});t=text.lower();captured=set()
    labels={'имя':'name','name':'name','компания':'company','company':'company','товар':'product','product':'product','страна':'country','country':'country',
      'откуда':'origin','origin':'origin','куда':'destination','destination':'destination','бюджет':'budget','budget':'budget','сроки':'timing','timing':'timing',
      'тема':'topic','topic':'topic','время':'slots','slots':'slots','часовой пояс':'timezone','timezone':'timezone','контакт':'contact','contact':'contact','участники':'participants','parties':'participants'}
    for label,key in labels.items():
        m=re.search(r'(?:^|[;\n])\s*'+label+r'\s*:\s*([^;\n]+)',text,re.I)
        if m:fields[key]=m[1].strip()[:500];captured.add(key)
    for ru,en,pattern in COUNTRIES:
        m=re.search(r'\b(?:'+pattern+r')\b',text,re.I)
        if m:
            prefix=text[max(0,m.start()-25):m.start()].lower()
            key='origin' if re.search(r'(?:\bfrom|\bиз)\s*$',prefix) else 'destination' if re.search(r'(?:\bto|\binto|\bв|\bдо|достав\w*\s+в)\s*$',prefix) else 'country'
            fields[key]=en;captured.add(key)
    for collection in ('Elina','Lotus','Sarina','Roysa','Sweety','Atusa','Larisa','Delaris','Niatus','Elika','Adlya','Butterfly'):
        if collection.lower() in t.replace('larissa','larisa'):fields['collection']=collection;captured.add('collection')
    sku=re.search(r'\bGK\s*(\d{6})(-\d+)?\b',text,re.I)
    if sku:fields['sku']='GK'+sku[1]+(sku[2] or '');captured.add('sku')
    for word,key in [('blue','blue'),('синий','blue'),('green','green'),('зелёный','green'),('silver','silver'),('серебристый','silver'),('gold','gold'),('золотистый','gold')]:
        if re.search(r'\b'+word+r'\b',t):fields['variant']=key;captured.add('variant')
    quant=re.search(r'\b(\d+(?:[.,]\d+)?)\s*(cartons?|boxes|картон\w*|короб\w*|sets?|набор\w*|pieces?|pcs|шт\w*|предмет\w*|tonnes?|tons?|тонн\w*|kg|кг)\b',text,re.I)
    if quant:
        unit=quant[2].lower();unit='cartons' if re.match('carton|box|картон|короб',unit) else 'sets' if re.match('set|набор',unit) else 'tonnes' if re.match('ton|тонн',unit) else 'kg' if unit in ('kg','кг') else 'pieces'
        fields.update(quantity=number(quant[1]),unit=unit);captured.update(('quantity','unit'))
    if re.search(r'\b(supplier|поставщик)\b',t) and re.search(r"i am|i'm|мы|я",t):fields['role']='supplier'
    if re.search(r'\b(buyer|покупатель)\b',t) and re.search(r"i am|i'm|мы|я",t):fields['role']='buyer'
    product=re.search(r'(?:экспорт(?:ировать)?|продать|поставщик[а-я]*|export(?:ing)?|supplier of|sell(?:ing)?)\s+(.+?)(?:\s+(?:в|из|to|from|into)\s|[,;.!]|$)',text,re.I)
    if product:fields['product']=product[1].strip()[:200];captured.add('product')
    n=normalized(text)
    for token,ru,en in [('tea_set','Чайный набор','Tea set'),('dinner_set','Столовый набор','Dinner set'),('sugar_pot','Сахарница','Sugar pot'),('tray','Поднос','Serving tray')]:
        if token in n:fields['product']=en;lang.LABELS[en]=(ru,en);captured.add('product')
    if 'dates' in n:fields['product']='Mazafati dates';lang.LABELS['Mazafati dates']=('Финики Мазафати','Mazafati dates');captured.add('product')
    # Relative dates are resolved against the event timestamp, never the archive import date.
    if re.search(r'\btomorrow\b|\bзавтра\b',t):fields['timing']=(datetime.fromisoformat(received_at)+timedelta(days=1)).date().isoformat();captured.add('timing')
    elif re.search(r'\btoday\b|\bсегодня\b',t):fields['timing']=datetime.fromisoformat(received_at).date().isoformat();captured.add('timing')
    elif re.search(r'на следующей неделе|next week|через\s+\d+\s+(?:дн|недел|месяц)|in\s+\d+\s+(?:days?|weeks?|months?)',t):
        fields['timing']=text[:500];captured.add('timing')
    if re.search(r'\b\d{1,2}/\d{1,2}\b',t):state['ambiguous_date']=re.search(r'\b\d{1,2}/\d{1,2}\b',t)[0]
    iso=re.search(r'\b20\d{2}-\d{2}-\d{2}\b',t)
    if iso:date.fromisoformat(iso[0]);fields['timing']=iso[0];captured.add('timing');state.pop('ambiguous_date',None)
    return captured

def infer_task(text,faq=None):
    t=text.lower();n=normalized(text)
    if re.search(r'санкц|sanction|which bank|через какой банк|обход|скрыть.*получател',t):return 'sanctions'
    if re.search(r'посуда|посуду|tableware|catalogue|каталог|\bgk\s*\d|tea sets?|sugar pots?|dinner sets?|cartons?|чайны\w* набор|сахарниц|поднос',t):return 'catalog'
    if re.search(r'финик|mazafati|dates',t):return 'food'
    for task,pattern in [('consultation',r'консультац|consultation|book.*consult'),('mission',r'мисси|выстав|mission|exhibition|^афганистан$'),('export',r'экспорт|export|sell.*abroad|дистрибьют|distributor'),('supplier',r'поставщи|производител|supplier|manufacturer|импорт|import|^пакистан$'),('partner',r'логист|logistics|партн[её]рство|partnership'),('deal',r'сопровожд|transaction support')]:
        if re.search(pattern,t):return task
    if faq:
        return {'consultation':'consultation','missions':'mission','exhibitions':'mission','export':'export','sourcing':'supplier','tableware':'catalog','food':'food','regulated':'sanctions'}.get(faq['category'])
    return None

def faq_reply(db,faq):
    if faq['id']=='FAQ-019':
        events=[e for e in all_records(db,'events') if valid(e,True) and sources_ok(db,e) and e.get('end_date','')>=date.today().isoformat() and e.get('registration_open') is True]
        if events:
            return {l:'\n'.join((e['title'].get(l) if isinstance(e['title'],dict) else str(e['title']))+' · '+e['end_date'] for e in events)+'\n'+('Участие и места согласуются отдельно.' if l=='ru' else 'Participation and availability require confirmation.') for l in ('ru','en')}
    if faq['id'] in ('FAQ-009','FAQ-010','FAQ-011','FAQ-013'):
        price=consultation_price(db)
        extra={
          'FAQ-009':lang.pair('Остальные услуги рассчитываются по задаче.','Other services are priced according to scope.'),
          'FAQ-010':lang.pair('Можно подготовить запрос; время будет подтверждено отдельно.','I can prepare a booking request; the time will be confirmed separately.'),
          'FAQ-011':lang.pair('Описание задачи можно передать бесплатно. Объём первичной оценки не уточнён; бесплатная экспертная консультация не обещается.','You can submit a description at no charge. The initial assessment scope is unconfirmed; a free expert consultation is not promised.'),
          'FAQ-013':lang.pair('Оплата в другой валюте не согласована. Пересчёт без источника курса не выполняется.','Payment in another currency is unconfirmed. No conversion is made without a dated exchange-rate source.')}
        return combine(price,extra[faq['id']])
    if faq['id']=='FAQ-099':return lang.pair('Автоматический подбор по фото не подключён. Могу найти позиции по коллекции, артикулу или типу предмета.','Photo matching is not connected. I can search by collection, item code or item type.')
    if faq['id']=='FAQ-137':return lang.pair('Да. Поиск использует общий каталог и двуязычные понятия. Артикулы и единицы сохраняются; непрочитанные поля отмечены.','Yes. Search uses one catalogue and bilingual concepts. Item codes and units are preserved; unread fields are flagged.')
    # Draft phrasing is available only as a visibly marked LOCAL preview. It is never an active offer.
    return dict(faq['answer'])

class Engine:
    def __init__(self,path):self.path=str(path)

    def process(self,channel,peer,event_id,text,received_at=None):
        if channel not in ('web','telegram','instagram','demo-telegram','demo-instagram'):raise ValueError('Unknown channel')
        if not all(isinstance(x,str) and x.strip() for x in (peer,event_id,text)) or len(text)>4000 or len(peer)>200 or len(event_id)>240:raise ValueError('Invalid message')
        stamp=received_at or now();datetime.fromisoformat(stamp)
        with connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT * FROM incoming WHERE channel=? AND event_id=?',(channel,event_id)).fetchone()
            if previous:
                owner=db.execute('SELECT peer FROM conversations WHERE id=?',(previous['conversation_id'],)).fetchone()
                if not owner or owner['peer']!=peer:raise ValueError('Event belongs to another conversation')
                result=json.loads(previous['result']);result['duplicate']=True
                if any(not sources_ok(db,{'source_ids':[ref]}) for ref in result.get('source_ids',[])):
                    result['reply']=lang.pair('Источник отозван; ответ больше не актуален.','The source was withdrawn; this reply is no longer current.')[result.get('reply_language','ru')]
                return result
            c=db.execute('SELECT * FROM conversations WHERE channel=? AND peer=?',(channel,peer)).fetchone()
            if not c:
                cid=str(uuid.uuid4());db.execute('INSERT INTO conversations(id,channel,peer,created_at) VALUES(?,?,?,?)',(cid,channel,peer,stamp))
                c=db.execute('SELECT * FROM conversations WHERE id=?',(cid,)).fetchone()
            cid=c['id'];state=json.loads(c['state']);state.setdefault('project_key','initial');state.setdefault('fields',{});state.setdefault('shortlist',[])
            cmd=lang.update(text,state);reply_language=state['reply_language'];detected=state['detected_input_language'];paused=bool(c['paused'])
            cur=db.execute('INSERT INTO messages(conversation_id,role,text,created_at,original_language) VALUES(?,?,?,?,?)',(cid,'client',text,stamp,detected or reply_language));message_id=cur.lastrowid
            actions=[];refs=[];faq_ids=[];preview=False;products=[];reply=None;system_control=False
            def action(kind,result):
                db.execute('INSERT OR IGNORE INTO actions(conversation_id,event_id,kind,result,created_at) VALUES(?,?,?,?,?)',(cid,event_id,kind,json.dumps(result,ensure_ascii=False),now()));actions.append(kind)
            t=text.lower().strip()
            if cmd in ('ru','en','auto','menu'):
                system_control=True
                reply=lang.pair('Выберите Русский, English или Auto. Контекст заявки сохранён.','Choose Русский, English or Auto. Your enquiry context is retained.') if cmd=='menu' else lang.pair('Режим языка: '+state['language_mode']+'. Контекст сохранён.','Language mode: '+state['language_mode']+'. Your context is retained.')
                action('update_language',{'mode':state['language_mode'],'reply_language':reply_language})
                if state.get('task'):reply=combine(reply,{l:lang.summary(state,l) for l in ('ru','en')})
            elif cmd=='once':
                system_control=True
                target='en' if reply_language=='ru' else 'ru'
                if re.search(r'русск|russian',t):target='ru'
                elif re.search(r'англий|english',t):target='en'
                last=state.get('last_reply_pair')
                if last and all(sources_ok(db,{'source_ids':[r]}) for r in state.get('last_source_ids',[])):
                    reply=last;reply_language=target;action('translate_reply',{'one_time':True,'language':target})
                else:reply=lang.pair('Нет доступного предыдущего ответа для перевода.','No available previous reply to translate.')
            elif re.search(r'(удал\w*.*(?:данн|переписк)|delete.*(?:data|history)|/delete)',t):
                system_control=True;rid=str(uuid.uuid4())
                db.execute('INSERT INTO privacy_requests VALUES(?,?,?,?,?,NULL)',(rid,cid,'deletion','requested',now()))
                action('request_data_deletion',{'id':rid,'status':'requested'})
                reply=lang.pair('Запрос на удаление данных сохранён. Данные ещё не удалены; команда выполнит запрос и зафиксирует результат.','Your data-deletion request was saved. Data has not yet been deleted; the team will process the request and record the result.')
            elif re.search(r'/unsubscribe|отпис|отключ\w* уведом|stop notifications|unsubscribe',t):
                system_control=True;db.execute('UPDATE conversations SET unsubscribed=1 WHERE id=?',(cid,));action('unsubscribe',{'completed':True})
                reply=lang.pair('Уведомления для этого профиля отключены. История и текущая заявка сохранены.','Notifications are disabled for this profile. Your history and enquiry are retained.')
            elif paused:
                reply=None
            elif re.search(r'чуж\w*|игнориру\w*.*правил|системн.*промпт|ignore.*instructions|other.*(?:conversations|leads)|я владелец.*(?:цен|прав)|i am.*owner.*(?:price|change)|измени.*цен|change.*(?:approved|price)',t):
                reply=lang.pair('Чужие данные и настройки недоступны из клиентского чата. Изменения базы требуют входа в панель и прав сотрудника.','Other clients’ data and settings are not accessible from this chat. Knowledge changes require authorised team access.')
            elif re.search(r'\b(human|person|manager|agent)\b|человек|менеджер|оператор',t) and not re.search(r'язык|language|english.*(?:consult|specialist)',t):
                state['task']=state.get('task') or 'human';paused=True;action('request_handoff',{'queue':'local_team','manager_notified':False})
                reply=combine({l:lang.summary(state,l) for l in ('ru','en')},lang.pair('Запрос на человека сохранён в очереди команды. Автоответы приостановлены. Внешнее уведомление не отправлено.','Your human-handover request was saved in the team queue. Automated replies are paused. No external notification was sent.'))
            elif re.search(r'^[\s\W]*[\u0600-\u06ff\u4e00-\u9fff]',text):
                reply=lang.pair('Могу продолжить на русском или английском. Выберите Русский или English.','I can continue in Russian or English. Please choose Русский or English.')
            elif t in ('/new','новая заявка','новый проект','new enquiry','new project'):
                state.update(project_key=str(uuid.uuid4()),fields={},shortlist=[]);state.pop('task',None);state.pop('pending',None)
                action('new_project',{'project_key':state['project_key']});reply=lang.pair('Начат отдельный проект. Предыдущая заявка сохранена. Какая у вас задача?','A separate project was started. Your previous enquiry is retained. What would you like to achieve?')
            elif re.search(r'моя заявка|моей заявк|статус.*заявк|my enquiry|my order|my request|/summary|заявку на двух|enquiry in both',t):
                lead=db.execute('SELECT * FROM leads WHERE conversation_id=? AND project_key=?',(cid,state['project_key'])).fetchone()
                reply={l:lang.summary(state,l)+(f"\n{lang.label(lead['status'],l)}" if lead else '') for l in ('ru','en')}
                if 'двух' in t or 'both' in t:reply={l:reply['ru']+'\n\n'+reply['en'] for l in ('ru','en')}
                action('read_own_lead',{'found':bool(lead)})
            elif re.search(r'я оплатил|i paid|payment sent',t):
                reply=lang.pair('Сообщение об оплате сохранено в истории. Оплата не подтверждена: нужна проверка команды.','Your payment statement was recorded in the history. Payment is not verified; the team must check it.')
                action('record_payment_claim',{'payment_verified':False})
            elif t.startswith('/add '):
                parts=text.split();p=get(db,'products',parts[1]) if len(parts)>=2 else None
                if not p or not valid(p) or not sources_ok(db,p):raise ValueError('Product is not available for a shortlist')
                qty=number(parts[2]) if len(parts)>2 else None;unit=parts[3] if len(parts)>3 else None
                if unit not in (None,'cartons','sets','pieces','kg','tonnes'):raise ValueError('Invalid unit')
                item={'product_id':p['id'],'collection':p['collection'],'sku':p['full_item_code'],'variant':p.get('finish'),'quantity':qty,'unit':unit,'source_ids':p['source_ids'],'page':p['page']}
                existing=next((x for x in state['shortlist'] if x['product_id']==p['id']),None)
                if existing:existing.update(item)
                else:state['shortlist'].append(item)
                state['task']='catalog';state['fields'].update(collection=p['collection'],sku=p['full_item_code'])
                action('prepare_shortlist',{'items':len(state['shortlist'])});refs=p['source_ids']
                reply=combine(lang.pair('Позиция сохранена в подборку. Цена и наличие требуют подтверждения.','The item was saved to your shortlist. Price and stock require confirmation.'),{l:lang.summary(state,l) for l in ('ru','en')})
            else:
                captured=extract_fields(text,state,stamp)
                if state.get('ambiguous_date'):
                    raw=state['ambiguous_date'];reply=lang.pair(f'Дата {raw} неоднозначна. Укажите её в формате YYYY-MM-DD.',f'The date {raw} is ambiguous. Please use YYYY-MM-DD.');state['pending']='timing'
                else:
                    hits=faq_search(db,text)
                    faq=hits[0][1] if hits and hits[0][0]>=0.37 else None
                    exact=bool(hits and hits[0][0]>=0.94)
                    pending=state.get('pending')
                    is_question='?' in text or bool(re.search(r'\b(сколько|почему|как|what|how|why|can you|could you)\b',t))
                    if pending and not is_question and not exact:
                        if pending not in captured and not captured:
                            state['fields'][pending]=text[:500]
                        if pending=='quantity' and re.fullmatch(r'\d+',text.strip()):
                            state['fields']['quantity']=number(text.strip())
                        if pending=='contact' and re.search(r'здесь|here',t):state['fields']['contact']='channel'
                        faq=None
                    # Explicit quantity corrections preserve all other fields and line items.
                    if re.search(r'исправ|измен.*колич|change.*quantit|make it|instead',t) and not captured.intersection({'quantity'}):
                        state['pending']='quantity';reply=QUESTIONS['quantity']
                    else:
                        task=infer_task(text,faq)
                        if task and (not state.get('task') or re.search(r'хочу|нужен|need|want',t)):state['task']=task
                        if 'quantity' in captured and len(state['shortlist'])==1:
                            state['shortlist'][0].update(quantity=state['fields']['quantity'],unit=state['fields']['unit'])
                        if 'quantity' in captured and len(state['shortlist'])>1:
                            reply=lang.pair('В подборке несколько строк. Уточните позицию для изменения количества в каталоге; остальные количества сохранены.','Your shortlist has several lines. Select the item in the catalogue to update its quantity; other quantities are retained.')
                        if faq and not reply:
                            faq_ids=[faq['id']];refs=faq['source_ids'];reply=faq_reply(db,faq);preview=faq['status']=='draft'
                            if faq['id']=='FAQ-017':state['preferred_human_language']='en';state['fields']['preferred_human_language']='en'
                            # Additional distinct FAQ questions in one turn share the same context.
                            for clause in re.split(r'[?\n]|;|\band\b|\bи\b',text,flags=re.I):
                                extra=faq_search(db,clause,1) if len(clause.strip())>12 else []
                                if extra and extra[0][0]>.60 and extra[0][1]['id'] not in faq_ids and len(faq_ids)<3:
                                    f=extra[0][1];reply=combine(reply,faq_reply(db,f));faq_ids.append(f['id']);refs.extend(f['source_ids']);preview|=f['status']=='draft'
                            counterpart=faq['question']['ru' if detected=='en' else 'en']
                            db.execute('UPDATE messages SET translation_language=?,translation=?,translation_status=? WHERE id=?',('ru' if detected=='en' else 'en',counterpart,'matched_intent_not_literal_translation',message_id))
                        # Product retrieval is independent of the language used by a document.
                        if state.get('task')=='catalog' and (not faq or faq['id'] in ('FAQ-096','FAQ-097','FAQ-098','FAQ-100','FAQ-118')):
                            fields=state['fields'];query=' '.join(str(fields.get(k,'')) for k in ('collection','sku','variant','product'))
                            products=product_search(db,query or text)
                            refs.extend(r for p in products for r in p['source_ids'])
                            if not reply:reply=lang.pair('Ищу по архивным каталогам. Цена, наличие и единицы продажи не подтверждены.','I am searching the archived catalogues. Price, stock and selling units are not confirmed.')
                            if fields.get('sku') and len({p['collection'] for p in products})>1:
                                choices=', '.join(sorted({p['collection'] for p in products}));reply=combine(reply,lang.pair(f"Артикул {fields['sku']} встречается в нескольких коллекциях: {choices}. Какую выбрать?",f"Item code {fields['sku']} appears in several collections: {choices}. Which one do you mean?"));state['pending']='collection'
                            elif products:
                                lines=[f"{p['collection']} · {p['full_item_code']} · {p.get('finish') or '—'} · p.{p['page']}" for p in products[:6]]
                                reply=combine(reply,{'ru':'\n'.join(lines),'en':'\n'.join(lines)})
                        if not reply and state.get('task'):
                            if state['task']=='consultation':reply=consultation_price(db)
                            elif state['task']=='mission':reply=lang.pair('Актуальная утверждённая программа не найдена. Архивные даты и цены не действуют как предложение.','No current approved programme was found. Archived dates and prices are not active offers.')
                            elif state['task']=='sanctions':reply=lang.pair('Сделку должен проверить специалист. Банковские реквизиты на этом этапе не присылайте.','A specialist must review the transaction. Do not send bank details at this stage.')
                            else:reply=lang.pair('Подготовлю описание задачи для команды; условия требуют согласования.','I can prepare the details for the team; terms require confirmation.')
                        if state.get('task') and not (products and state.get('pending')=='collection' and state['fields'].get('sku')):
                            fields=state['fields'];missing=next((k for k in FLOWS.get(state['task'],[]) if not fields.get(k)),None)
                            if state['task']=='catalog' and fields.get('quantity') and not fields.get('unit'):missing='quantity'
                            elif state['task']=='catalog' and products and not state['shortlist'] and (not fields.get('sku') or len(products)>1):missing='variant'
                            state['pending']=missing
                            # Keep informational FAQ answers concise; start qualification on requests and follow-ups.
                            if missing and (not exact or faq_ids and faq_ids[0] in ('FAQ-010','FAQ-098')):
                                if not any('?' in p for p in (reply or {}).values()):reply=combine(reply,QUESTIONS[missing])
                            elif not missing:
                                reply=combine(reply,{l:lang.summary(state,l) for l in ('ru','en')},lang.pair('Черновик заявки сохранён. Проверьте данные; можно исправить их или запросить человека.','Your enquiry draft was saved. Please check the details; you can correct them or request a person.'))
                                if state['task']=='consultation':reply=combine(reply,lang.pair('Встреча пока не подтверждена.','The appointment is not confirmed.'))
                                if state['task']=='sanctions':paused=True;action('request_handoff',{'reason':'specialist_review'});reply=combine(reply,lang.pair('Вопрос в локальной очереди специалиста; автоответы на паузе.','The question is in the local specialist queue; automatic replies are paused.'))
                        if not reply:
                            if re.search(r'^(hi|hello|привет|здравств|/start)',t):reply=lang.pair('Здравствуйте! Я ИИ-помощник Арслана-Али. Помогу с информацией, подбором материалов и подготовкой заявки. Какая у вас задача?',"Hello! I’m Arslan-Ali’s AI assistant. I can help you find information and materials and prepare an enquiry. What would you like to achieve?")
                            else:
                                reply=lang.pair('Не нашёл достаточно точного ответа в источниках. Можно уточнить вопрос или попросить человека.','I could not find a sufficiently precise answer in the sources. Please clarify or ask for a person.')
                                db.execute('INSERT INTO unknown_questions(conversation_id,message_id,created_at) VALUES(?,?,?)',(cid,message_id,stamp))
            lead_id=None
            if state.get('task'):
                current=db.execute('SELECT * FROM leads WHERE conversation_id=? AND project_key=?',(cid,state['project_key'])).fetchone()
                lead_id=current['id'] if current else str(uuid.uuid4());fields=dict(state['fields'])
                fields.update(task=state['task'],shortlist=state['shortlist'],language_mode=state['language_mode'],reply_language=state['reply_language'],preferred_human_language=state.get('preferred_human_language'),source_ids=list(set(refs)|set(state.get('source_ids',[]))))
                fields.setdefault('contact','channel');state['source_ids']=fields['source_ids']
                status=current['status'] if current else ('требует уточнения' if state.get('pending') else 'новая')
                if paused and status not in ('в работе','закрыта'):status='передана менеджеру'
                ru=lang.summary(state,'ru');en=lang.summary(state,'en')
                db.execute('''INSERT INTO leads(id,conversation_id,status,fields,summary,created_at,updated_at,summary_ru,summary_en,project_key)
                  VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(conversation_id,project_key) DO UPDATE SET fields=excluded.fields,summary=excluded.summary,
                  summary_ru=excluded.summary_ru,summary_en=excluded.summary_en,status=excluded.status,updated_at=excluded.updated_at''',
                  (lead_id,cid,status,json.dumps(fields,ensure_ascii=False),ru,stamp,now(),ru,en,state['project_key']))
                action('update_lead' if current else 'save_lead',{'lead_id':lead_id,'saved':True})
            if reply:
                if not system_control and not db.execute("SELECT 1 FROM messages WHERE conversation_id=? AND role='assistant'",(cid,)).fetchone():
                    if 'ИИ-помощник' not in reply['ru'] and 'AI assistant' not in reply['en']:
                        reply=combine(lang.pair('Я ИИ-помощник Арслана-Али.',"I’m Arslan-Ali’s AI assistant."),reply)
                # Store both authored language variants, not an invented translation of the user.
                if cmd!='once':state['last_reply_pair']=reply;state['last_source_ids']=list(set(refs))
                rendered=reply[reply_language]
                db.execute('INSERT INTO messages(conversation_id,role,text,created_at,original_language,translation_language,translation,translation_status,source_refs,metadata) VALUES(?,?,?,?,?,?,?,?,?,?)',
                  (cid,'system' if system_control else 'assistant',rendered,now(),reply_language,'ru' if reply_language=='en' else 'en',reply['ru' if reply_language=='en' else 'en'],'authored_bilingual',json.dumps(list(set(refs))),json.dumps({'preview':preview,'faq_ids':faq_ids})))
                if channel!='web':db.execute('INSERT INTO outbox(conversation_id,channel,text,status,created_at) VALUES(?,?,?,?,?)',(cid,channel,rendered,'demo_not_sent',now()))
            else:rendered=None
            db.execute('UPDATE conversations SET state=?,paused=?,language_mode=?,detected_input_language=?,reply_language=?,preferred_human_language=? WHERE id=?',
              (json.dumps(state,ensure_ascii=False),int(paused),state['language_mode'],detected,state['reply_language'],state.get('preferred_human_language'),cid))
            result={'conversation_id':cid,'reply':rendered,'reply_language':reply_language,'language_mode':state['language_mode'],'detected_input_language':detected,
              'paused':paused,'lead_id':lead_id,'lead_saved':bool(lead_id),'manager_notified':False,'consultation_confirmed':False,'payment_verified':False,
              'duplicate':False,'mode':'demo','draft_preview':preview,'faq_ids':faq_ids,'source_ids':list(set(refs)),'products':products,'actions':actions}
            db.execute('INSERT INTO incoming VALUES(?,?,?,?)',(channel,event_id,cid,json.dumps(result,ensure_ascii=False)))
            return result
