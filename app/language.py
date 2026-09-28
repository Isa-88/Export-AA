"""Shared RU/EN state. Language changes never replace business state."""
import re

def detect(text,previous='ru'):
    clean=re.sub(r'https?://\S+|[\w.+-]+@[\w.-]+|\bGK\s*\d+(?:-\d+)?\b','',text,flags=re.I)
    clean=re.sub(r'[«“"].*?[»”"]','',clean)
    words=re.findall(r'[a-zа-яё]+',clean.lower())
    neutral={'ok','okay','yes','no','да','нет','ага','угу','auto','elina','lotus','sarina','roysa','sweety','atusa','larisa','larissa','delaris','niatus','elika','gk','utc','cm','pcs','rub','usd'}
    significant=[w for w in words if w not in neutral and len(w)>1]
    if not significant:return None
    ru=sum(len(w) for w in significant if re.search('[а-яё]',w));en=sum(len(w) for w in significant if re.search('[a-z]',w))
    if re.search(r'\b(покаж|найд|нуж|хочу|подбер|скаж|сколько|переведи|сравни)',clean,re.I):return 'ru'
    if re.search(r'\b(please|need|find|show|compare|want|how|what|could|can you)\b',clean,re.I):return 'en'
    if ru>en*1.3:return 'ru'
    if en>ru*1.3:return 'en'
    return None

def command(text):
    t=text.lower().strip().rstrip('.!')
    if t in ('/language','language','язык'):return 'menu'
    if t in ('/auto','auto','авто') or re.search(r'сам определяй язык|автоматическ.*(?:язык|режим)|automatically detect.*language',t):return 'auto'
    if re.search(r'переведи.*(?:только|этот ответ|последний ответ|предыдущий ответ)|translate.*(?:only|just|this reply|this answer|last reply|last answer|previous reply|previous answer)',t):return 'once'
    if t in ('/ru','русский','russian') or re.search(r'(?:дальше|отвечай|продолжай|переключись|говори).*?(?:по-русски|русский)|(?:reply|respond|switch|continue).*?(?:in |to )russian',t):return 'ru'
    if t in ('/en','english','английский') or re.search(r'(?:дальше|отвечай|продолжай|переключись|говори|можно).*?(?:по-английски|на английском|на английский)|(?:reply|respond|switch|continue|speak).*?(?:in |to )english',t):return 'en'
    return None

def update(text,state):
    state.setdefault('language_mode','auto');state.setdefault('reply_language','ru')
    cmd=command(text);detected=detect(text,state['reply_language'])
    state['detected_input_language']=detected
    if cmd in ('ru','en','auto'):state['language_mode']=cmd
    if state['language_mode'] in ('ru','en'):state['reply_language']=state['language_mode']
    elif detected and cmd not in ('menu','once') and (cmd!='auto' or text.strip().lower() not in ('auto','/auto','авто')):state['reply_language']=detected
    state.setdefault('preferred_human_language',None)
    return cmd

LABELS={
 'task':('Задача','Task'),'role':('Роль','Role'),'product':('Товар','Product'),'country':('Страна интереса','Target country'),
 'origin':('Откуда','Origin'),'destination':('Куда','Destination'),'collection':('Коллекция','Collection'),'sku':('Артикул','SKU'),
 'variant':('Вариант','Variant'),'quantity':('Количество','Quantity'),'unit':('Единица','Unit'),'timing':('Сроки','Timing'),
 'name':('Имя','Name'),'company':('Компания','Company'),'contact':('Связь','Contact'),'budget':('Бюджет','Budget'),
 'topic':('Тема','Topic'),'slots':('Интервалы','Time slots'),'timezone':('Часовой пояс','Time zone'),'participants':('Участники','Parties'),
 'question':('Вопрос','Question'),'preferred_human_language':('Язык специалиста — пожелание','Preferred specialist language'),
 'consultation':('Консультация','Consultation'),'mission':('Миссия / выставка','Mission / exhibition'),
 'export':('Экспорт','Export'),'supplier':('Поиск поставщика','Supplier sourcing'),'partner':('Партнёрство','Partnership'),
 'catalog':('Посуда / подборка','Tableware / shortlist'),'food':('Товарный запрос','Product enquiry'),'deal':('Сопровождение','Transaction support'),
 'sanctions':('Вопрос специалисту','Specialist review'),'human':('Связь с человеком','Human handover'),
 'cartons':('картонов','cartons'),'sets':('наборов','sets'),'pieces':('предметов','pieces'),'tonnes':('тонн','tonnes'),'kg':('кг','kg'),
 'channel':('В исходном канале','In this channel'),'unknown':('неизвестно','unknown'),
 'новая':('новая','new'),'требует уточнения':('требует уточнения','needs clarification'),'передана менеджеру':('передана менеджеру','in the team queue'),
 'в работе':('в работе','in progress'),'закрыта':('закрыта','closed')}

def label(key,lang):return LABELS.get(str(key),(str(key),str(key)))[lang=='en']

def summary(state,lang):
    fields=state.get('fields',{});lines=[]
    for key,value in {'task':state.get('task'),**fields}.items():
        if value is not None and value!='':lines.append(f'{label(key,lang)}: {label(value,lang)}')
    for i,item in enumerate(state.get('shortlist',[]),1):
        lines.append(f"{i}. {item['collection']} · {item['sku']} · {item.get('variant') or '—'} · {item.get('quantity') or '—'} {label(item.get('unit') or 'unknown',lang)}")
    return '\n'.join(lines) or ('Пока нет данных.' if lang=='ru' else 'No details yet.')

def pair(ru,en):return {'ru':ru,'en':en}
