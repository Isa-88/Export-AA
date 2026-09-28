"""Deterministic demo engine. Client text is data, never executable instructions."""
import json
import re
import uuid
from datetime import date
from .storage import connect, documents, now

COUNTRIES = {'афган':'Афганистан','пакистан':'Пакистан','иран':'Иран','ирак':'Ирак',
 'оаэ':'ОАЭ','эмират':'ОАЭ','саудов':'Саудовская Аравия','катар':'Катар','узбек':'Узбекистан',
 'казах':'Казахстан','таджик':'Таджикистан','кыргыз':'Кыргызстан','киргиз':'Кыргызстан','туркмен':'Туркменистан'}
QUESTIONS = {
 'country':'Какая страна вас интересует?', 'product':'Какой товар или услуга вас интересует?',
 'volume':'Какой предполагается объём (или пока неизвестен)?',
 'timing':'Какие сроки вы рассматриваете (или пока неизвестны)?',
 'topic':'Какую тему вы хотите обсудить на консультации?',
 'slots':'Какие интервалы времени вам удобны?', 'timezone':'В каком вы часовом поясе?',
 'contact':'Как удобнее связаться: здесь или другим способом?',
 'participants':'Какие страны и участники задействованы (без банковских реквизитов)?',
 'question':'В чём состоит вопрос для специалиста?',
 'task':'Какая задача вам нужна: миссия, экспорт, поиск поставщика, сопровождение или консультация?'}
FLOWS = {'mission':['country','product','timing'], 'export':['product','country','volume','timing'],
 'supplier':['product','country','volume','timing'], 'partner':['product','country','timing'],
 'deal':['product','country','timing'], 'consultation':['topic','slots','timezone','contact'],
 'sanctions':['product','country','participants','question'], 'human':[]}
STATUSES = ['новая','требует уточнения','передана менеджеру','в работе','закрыта']

def match_topic(t):
    for topic, pattern in [
        ('human',r'человек|менеджер|оператор|живой|сотрудник'),
        ('sanctions',r'санкц|обход|законн|расч[её]т[ыа]|банк|плат[её]ж|наличн'),
        ('consultation',r'консультац|записаться|запись'), ('mission',r'мисси|делегац|поездк'),
        ('supplier',r'поставщик|производител|импорт|закуп|найти товар'),
        ('export',r'экспорт|продать|продаж|выход.*рын|покупател'),
        ('partner',r'партн[её]р|над[её]жн'), ('deal',r'сопровожд|поставк|сделк'),
        ('geography',r'географ|страны|направлен|работаете.*(?:в |с )'), ('personal',r'лично|личное'),
        ('size',r'маленьк|небольш|размер|крупн'), ('price',r'цен[ауы]|стоим|сколько|бесплатн'),
        ('services',r'услуг|помочь|помога|умеете')]:
        if re.search(pattern,t): return topic
    return None

def active(doc, mission=False):
    try:
        if doc['approval'] not in (['approved'] if mission else ['approved','owner_provided']): return False
        expiry = doc.get('valid_until')
        if expiry and date.fromisoformat(expiry) < date.today(): return False
        if mission and (not expiry or date.fromisoformat(doc['date']) < date.today()): return False
        return True
    except (KeyError, ValueError): return False

def answer(db, text, state):
    t = text.lower().strip()
    if re.search(r'[\u0600-\u06ff]', text):
        return 'أنا مساعد أرسلان علي. يمكنني تسجيل طلبك للفريق. هل تفضل العربية أم الروسية؟', state, False
    if re.search('[a-zA-Z]', text) and not re.search('[а-яА-ЯёЁ]',text) and '@' not in text and not state.get('pending'):
        return "I am Arslan-Ali’s assistant. I can take your request for the team. Would you prefer English or Russian?", state, False
    if re.search(r'игнориру|системн.*(промпт|правил)|чужи[ех]|покажи.*(диалог|баз)|измени.*правил|парол|секрет|инструкци.*вместо', t):
        return 'Я помощник Арслана-Али. Чужие данные и служебные настройки недоступны в чате. По какой услуге у вас вопрос?', state, False
    fields = state.setdefault('fields', {})
    topic = match_topic(t)
    old_task = state.get('task')
    if state.get('pending') and topic not in ('human','sanctions','price') and '?' not in text and not re.search(r'сколько|стоим|цена|хочу|сменить|другая задача',t):
        topic = None
    # Capture explicitly labelled fields from any message, including corrections.
    labels = {'имя':'name','компания':'company','товар':'product','страна':'country','объём':'volume',
      'объем':'volume','бюджет':'budget','сроки':'timing','тема':'topic','время':'slots',
      'часовой пояс':'timezone','контакт':'contact','участники':'participants','вопрос':'question'}
    for label,key in labels.items():
        m = re.search(r'(?:^|[;\n])\s*'+label+r'\s*:\s*([^;\n]+)',text,re.I)
        if m: fields[key] = m.group(1).strip()[:500]
    for stem,country in COUNTRIES.items():
        if stem in t:
            fields['country'] = country
            break
    if not fields.get('country'):
        m = re.search(r'\b(?:в|из|для|с)\s+([А-ЯЁ][а-яё]+)', text)
        if m: fields['country'] = m.group(1)
    m = re.search(r'(?:экспорт(?:ировать)?|продать|прода[её]м|поставщик[а-я]*|производител[а-я]*)\s+(.+?)(?:\s+(?:в|из|для)\s|[,;.!]|$)',text,re.I)
    if m and not fields.get('product'): fields['product'] = m.group(1).strip()[:200]
    m = re.search(r'\b\d+[\d .,]*\s*(?:тонн\w*|кг|шт\w*|контейнер\w*)',t)
    if m: fields['volume'] = m.group(0)
    m = re.search(r'(?:до|через)\s+\d+\s*\w+|в (?:октябре|ноябре|декабре|январе|феврале|марте|апреле|мае|июне|июле|августе|сентябре)',t)
    if m: fields['timing'] = m.group(0)
    pending = state.get('pending')
    if pending and (not topic or topic == old_task) and '?' not in text and not re.search(r'сколько|стоим|цена',t):
        if pending not in fields: fields[pending] = text[:500]
    faq = {d['id']:d['answer'] for d in documents(db,'knowledge') if active(d)}
    def knowledge(key): return faq.get(key,'Актуального согласованного ответа пока нет. Команда должна уточнить сведения.')
    if topic == 'human':
        state['task'] = old_task or 'human'
        return 'Запрос на человека сохранён в очереди команды. Автоответы приостановлены. Внешнее уведомление менеджеру не отправлено.', state, True
    response = ''
    if topic:
        response = knowledge({'supplier':'partner','deal':'services'}.get(topic,topic))
        if topic in FLOWS:
            state['task'] = topic
            if old_task and old_task != topic:
                state.pop('pending',None)
        if re.search(r'сколько|стоим|цен[ауы]',t) and topic not in ('price','consultation'): response = knowledge('price') + ' ' + response
    if topic == 'mission':
        missions = [d for d in documents(db,'missions') if active(d,True) and fields.get('country') == d.get('country')]
        response = '\n'.join(f"{d['title']}: {d['date']}. {d['details']}" for d in missions) if missions else 'Актуальной утверждённой программы для вашего запроса пока нет. Даты, места и стоимость не подтверждены; примем запрос на программу.'
    if fields.get('country') and fields['country'] not in set(COUNTRIES.values()):
        response += ' Это направление не подтверждено в названном списке; команда отдельно проверит возможность помощи.'
    task = state.get('task')
    if task:
        required = FLOWS[task]
        missing = next((key for key in required if not fields.get(key)),None)
        state['pending'] = missing
        if missing: response += ' ' + QUESTIONS[missing]
        else:
            response += ' Заявка сохранена в очереди команды. Внешнее уведомление менеджеру не отправлено.'
            if task == 'consultation': response += ' Это запрос на запись, встреча пока не подтверждена.'
            if task == 'sanctions':
                response += ' Вопрос направлен на ручной разбор в локальной панели; автоответы приостановлены.'
                return response.strip(), state, True
    elif not response:
        if re.search(r'^(привет|здравств|добрый|/start)',t):
            response = 'Я помощник Арслана-Али. ' + knowledge('services') + ' ' + QUESTIONS['task']
        else:
            response = 'Точного ответа на этот вопрос в доступных сведениях нет. Можно передать вопрос человеку. ' + QUESTIONS['task']
    elif topic in ('price','services','geography','size','personal'):
        response += ' ' + QUESTIONS['task']
    return response.strip(), state, False

class Engine:
    def __init__(self, path): self.path = path

    def process(self, channel, peer, event_id, text):
        if channel not in ('web','telegram','instagram','demo-telegram','demo-instagram'): raise ValueError('Unknown channel')
        if not all(isinstance(x,str) and x.strip() for x in (peer,event_id,text)): raise ValueError('Required strings')
        if len(text)>4000 or len(peer)>200 or len(event_id)>200: raise ValueError('Message too long')
        with connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM incoming WHERE channel=? AND event_id=?',(channel,event_id)).fetchone()
            if previous:
                c = db.execute('SELECT peer FROM conversations WHERE id=?',(previous['conversation_id'],)).fetchone()
                if c['peer'] != peer: raise ValueError('Event collision')
                result = json.loads(previous['result']); result['duplicate'] = True
                return result
            c = db.execute('SELECT * FROM conversations WHERE channel=? AND peer=?',(channel,peer)).fetchone()
            if not c:
                cid = str(uuid.uuid4())
                db.execute('INSERT INTO conversations(id,channel,peer,created_at) VALUES (?,?,?,?)',(cid,channel,peer,now()))
                c = db.execute('SELECT * FROM conversations WHERE id=?',(cid,)).fetchone()
            cid = c['id']; state = json.loads(c['state'])
            db.execute('INSERT INTO messages(conversation_id,role,text,created_at) VALUES (?,?,?,?)',(cid,'client',text,now()))
            reply, paused = None, bool(c['paused'])
            if not paused:
                reply,state,paused = answer(db,text,state)
                if not db.execute("SELECT 1 FROM messages WHERE conversation_id=? AND role='assistant'",(cid,)).fetchone() and re.search('[а-яА-ЯёЁ]',reply) and not reply.startswith('Я помощник'):
                    reply = 'Я помощник Арслана-Али. ' + reply
                db.execute('INSERT INTO messages(conversation_id,role,text,created_at) VALUES (?,?,?,?)',(cid,'assistant',reply,now()))
                if channel != 'web':
                    db.execute('INSERT INTO outbox(conversation_id,channel,text,status,created_at) VALUES (?,?,?,?,?)',(cid,channel,reply,'demo_not_sent',now()))
            db.execute('UPDATE conversations SET paused=?,state=? WHERE id=?',(int(paused),json.dumps(state,ensure_ascii=False),cid))
            lead = None
            if state.get('task'):
                fields = dict(state.get('fields',{})); fields['task'] = state['task']
                fields.setdefault('contact','Ответить в исходном канале')
                summary = ' · '.join(f'{k}: {v}' for k,v in fields.items())
                current = db.execute('SELECT * FROM leads WHERE conversation_id=?',(cid,)).fetchone()
                lead = current['id'] if current else str(uuid.uuid4())
                status = current['status'] if current else ('требует уточнения' if state.get('pending') else 'новая')
                if paused and (not current or current['status'] not in ('в работе','закрыта')): status = 'передана менеджеру'
                db.execute('''INSERT INTO leads(id,conversation_id,status,fields,summary,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(conversation_id) DO UPDATE SET
                    fields=excluded.fields,summary=excluded.summary,status=excluded.status,updated_at=excluded.updated_at''',
                    (lead,cid,status,json.dumps(fields,ensure_ascii=False),summary,now(),now()))
            result = {'conversation_id':cid,'reply':reply,'paused':paused,'lead_id':lead,'duplicate':False,
              'lead_saved':bool(lead),'manager_notified':False,'consultation_confirmed':False,'mode':'demo'}
            db.execute('INSERT INTO incoming VALUES (?,?,?,?)',(channel,event_id,cid,json.dumps(result,ensure_ascii=False)))
            return result
