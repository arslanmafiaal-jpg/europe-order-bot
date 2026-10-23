import os, json, time, sqlite3, urllib.request, urllib.error, threading, re, html
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ.get('BOT_TOKEN', '')
ADMIN_CHAT_ID = os.environ.get('ADMIN_CHAT_ID', '').strip()
API = f'https://api.telegram.org/bot{TOKEN}/'
DB = os.environ.get('DB_PATH', '/tmp/orders.sqlite3')
RATE, SHIPPING, DOMESTIC = Decimal('114'), Decimal('27'), Decimal('4')
LIMIT, DUTY_FREE, DUTY_RATE = Decimal('299'), Decimal('200'), Decimal('0.13')
LOCK = threading.RLock()


def db():
    conn = sqlite3.connect(DB, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=20000')
    conn.execute('CREATE TABLE IF NOT EXISTS sessions (user_id INTEGER PRIMARY KEY, state TEXT NOT NULL DEFAULT "", draft TEXT NOT NULL DEFAULT "{}", cart TEXT NOT NULL DEFAULT "[]")')
    conn.execute('CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, payload TEXT, created_at TEXT)')
    conn.commit()
    return conn


def session(uid):
    with LOCK:
        c=db(); c.execute('INSERT OR IGNORE INTO sessions(user_id) VALUES(?)',(uid,)); c.commit()
        r=c.execute('SELECT * FROM sessions WHERE user_id=?',(uid,)).fetchone(); c.close()
        return dict(state=r['state'], draft=json.loads(r['draft']), cart=json.loads(r['cart']))


def save(uid,s):
    with LOCK:
        c=db(); c.execute('INSERT OR IGNORE INTO sessions(user_id) VALUES(?)',(uid,))
        c.execute('UPDATE sessions SET state=?,draft=?,cart=? WHERE user_id=?',(s['state'],json.dumps(s['draft'],ensure_ascii=False),json.dumps(s['cart'],ensure_ascii=False),uid)); c.commit();c.close()


def api(method, payload):
    req=urllib.request.Request(API+method,data=json.dumps(payload,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=65) as res: return json.load(res)


def keyboard(rows):
    return {'inline_keyboard':[[{'text':title,'callback_data':data} for title,data in row] for row in rows]}

HOME=keyboard([[('➕ Добавить товар','add'),('🛒 Корзина','cart')],[('💰 Рассчитать','calc'),('🧾 Оформить заказ','checkout')],[('❌ Очистить корзину','clear')]])
CART=keyboard([[('➕ Добавить','add'),('🗑 Удалить последний','remove')],[('💰 Рассчитать','calc'),('🧾 Оформить','checkout')],[('🏠 Меню','home')]])


def send(chat,text,kb=None):
    payload={'chat_id':chat,'text':text[:3900],'disable_web_page_preview':True}
    if kb: payload['reply_markup']=kb
    return api('sendMessage',payload)


def money(x): return str(x.quantize(Decimal('0.01'),rounding=ROUND_HALF_UP))


def estimate(cart):
    # Orders: at most 10 items each. Parcels: max 299 EUR in goods, no item count limit.
    prices=[]
    for item in cart: prices.extend([Decimal(item['price'])]*int(item['qty']))
    if not prices: raise ValueError('Корзина пуста.')
    orders=(len(prices)+9)//10
    parcels=[]
    for p in sorted(prices,reverse=True):
        for parcel in parcels:
            if sum(parcel)+p<=LIMIT:
                parcel.append(p);break
        else: parcels.append([p])
    goods=sum(prices)
    duty=sum((sum(p)-DUTY_FREE)*DUTY_RATE for p in parcels if sum(p)>DUTY_FREE)
    domestic=DOMESTIC*orders
    shipping=SHIPPING*len(parcels)
    total=goods+duty+domestic+shipping
    return f'Товаров: {len(prices)} | Заказов до склада: {orders} | Посылок в РФ: {len(parcels)}\nТовары: {money(goods)} €\nДоставка до склада: {money(domestic)} €\nДоставка в РФ: {money(shipping)} €\nПошлина: {money(duty)} €\n\nИТОГО: {money(total)} € / {money(total*RATE)} ₽\n\n'+'\n'.join(f'Посылка {i}: {len(p)} шт., товары {money(sum(p))} €' for i,p in enumerate(parcels,1))+'\n\nПредварительный расчёт по курсу 114 ₽/€. Заказы группируются по 10 товаров, посылки по 299 € стоимости товаров. Разделение не всегда минимизирует итоговую стоимость.'


def cart_text(cart):
    if not cart:return '🛒 Корзина пока пуста.'
    lines=['🛒 Ваша корзина:']
    for i,item in enumerate(cart,1):
        lines.append(f"{i}. {item['qty']} шт. × {item['price']} €\n{item['url']}")
    return '\n\n'.join(lines)[:3700]


def begin_add(uid,chat):
    s=session(uid);s['state']='url';s['draft']={};save(uid,s)
    send(chat,'Пришлите ссылку на товар (https://...). Для отмены: /cancel')


def on_callback(cb):
    uid=cb['from']['id'];chat=cb['message']['chat']['id'];action=cb['data'];s=session(uid)
    try:api('answerCallbackQuery',{'callback_query_id':cb['id']})
    except Exception:pass
    if action=='home':s['state']='';save(uid,s);send(chat,'Главное меню',HOME)
    elif action=='add':begin_add(uid,chat)
    elif action=='cart':send(chat,cart_text(s['cart']),CART)
    elif action=='calc':
        try:send(chat,estimate(s['cart']),CART)
        except ValueError as e:send(chat,str(e),HOME)
    elif action=='remove':
        if s['cart']:s['cart'].pop();save(uid,s)
        send(chat,cart_text(s['cart']),CART)
    elif action=='clear':send(chat,'Точно очистить корзину?',keyboard([[('Да, очистить','clear_yes'),('Нет','cart')]]))
    elif action=='clear_yes':s['cart']=[];s['state']='';s['draft']={};save(uid,s);send(chat,'Корзина очищена.',HOME)
    elif action=='checkout':
        if not s['cart']:send(chat,'Сначала добавьте товары.',HOME);return
        s['state']='name';s['draft']={};save(uid,s)
        send(chat,'🧾 Оформление заказа. Укажите ФИО получателя.\nОтменить оформление: /cancel')
    elif action=='submit':
        if s['state']!='confirm':send(chat,'Сначала заполните данные.',HOME);return
        submit(uid,chat,cb['from'],s)


def confirmation(chat,s):
    d=s['draft'];send(chat,cart_text(s['cart'])+'\n\n'+estimate(s['cart'])+'\n\nПолучатель: '+d['name']+'\nТелефон: '+d['phone']+'\nАдрес Почты России: '+d['address']+'\n\nОтправить заявку менеджеру?',keyboard([[('✅ Отправить заявку','submit')],[('❌ Отмена','home')]]))


def submit(uid,chat,from_user,s):
    if not ADMIN_CHAT_ID:
        send(chat,'Приём заявок пока не настроен: администратору нужно добавить ADMIN_CHAT_ID в Render. Корзина сохранена.',HOME);return
    d=s['draft'];uname=from_user.get('username','');profile=f'@{uname}' if uname else f'tg://user?id={uid}'
    with LOCK:
        c=db();cur=c.execute('INSERT INTO orders(user_id,payload,created_at) VALUES(?,?,?)',(uid,json.dumps({'cart':s['cart'],'customer':d},ensure_ascii=False),datetime.now(timezone.utc).isoformat()));order_id=cur.lastrowid;c.commit();c.close()
    message=f'🔔 НОВАЯ ЗАЯВКА #{order_id}\nTelegram: {profile} (ID {uid})\nФИО: {d["name"]}\nТелефон: {d["phone"]}\nАдрес Почты России: {d["address"]}\n\n'+cart_text(s['cart'])+'\n\n'+estimate(s['cart'])
    try:
        # Telegram messages max 4096 chars; split without losing item links.
        for i in range(0,len(message),3500):send(ADMIN_CHAT_ID,message[i:i+3500])
    except Exception as e:
        print('Admin notification failed:',repr(e),flush=True)
        send(chat,'Не удалось доставить заявку менеджеру. Попробуйте ещё раз позже, корзина сохранена.',HOME);return
    s['cart']=[];s['draft']={};s['state']='';save(uid,s)
    send(chat,f'✅ Заявка #{order_id} отправлена менеджеру! С вами свяжутся для подтверждения стоимости и доставки.',HOME)


def on_message(msg):
    if 'text' not in msg:return
    chat=msg['chat']['id'];uid=msg['from']['id'];txt=msg['text'].strip();s=session(uid)
    if txt.startswith('/id'):
        send(chat,f'Ваш Telegram chat ID: {chat}\nДля уведомлений в личку напишите /id боту из личного чата и укажите этот ID в ADMIN_CHAT_ID на Render.');return
    if txt.startswith('/start') or txt.startswith('/help'):
        s['state']='';save(uid,s);send(chat,'👋 Добро пожаловать! Добавляйте товары по ссылкам, собирайте корзину и оформляйте заявку. Стоимость предварительная.',HOME);return
    if txt.startswith('/cancel'):
        s['state']='';s['draft']={};save(uid,s);send(chat,'Действие отменено. Корзина сохранена.',HOME);return
    if txt.startswith('/cart'):send(chat,cart_text(s['cart']),CART);return
    state=s['state']
    if not state:send(chat,'Выберите действие:',HOME);return
    if state=='url':
        if not re.match(r'^https?://[^\s.]+\.[^\s]+$',txt,re.I) or len(txt)>1200:
            send(chat,'Нужна обычная ссылка на товар, начинающаяся с https://. Попробуйте снова.');return
        s['draft']['url']=txt;s['state']='price';save(uid,s);send(chat,'Укажите цену ОДНОГО товара в евро, например 49.90');return
    if state=='price':
        try:p=Decimal(txt.replace(',','.').replace('€','').strip())
        except InvalidOperation:p=Decimal('-1')
        if not p.is_finite() or p<=0 or p>LIMIT:
            send(chat,'Введите цену от 0,01 до 299 € (один товар дороже 299 € не проходит).');return
        s['draft']['price']=money(p);s['state']='qty';save(uid,s);send(chat,'Сколько штук? Введите число от 1 до 100.');return
    if state=='qty':
        try:q=int(txt)
        except ValueError:q=0
        if q<1 or q>100:send(chat,'Введите количество от 1 до 100.');return
        if sum(i['qty'] for i in s['cart'])+q>200:send(chat,'Для тестовой версии максимум 200 вещей в корзине.');return
        s['cart'].append({'url':s['draft']['url'],'price':s['draft']['price'],'qty':q});s['draft']={};s['state']='';save(uid,s)
        send(chat,'✅ Товар добавлен!\n\n'+cart_text(s['cart']),CART);return
    if state=='name':
        if len(txt)<3 or len(txt)>120:send(chat,'Укажите ФИО (от 3 до 120 символов).');return
        s['draft']['name']=txt;s['state']='phone';save(uid,s);send(chat,'Введите контактный телефон, например +79991234567.');return
    if state=='phone':
        digits=re.sub(r'\D','',txt)
        if len(digits)<10 or len(digits)>15:send(chat,'Укажите корректный номер телефона (10–15 цифр).');return
        s['draft']['phone']=txt[:40];s['state']='address';save(uid,s);send(chat,'Укажите полный адрес доставки Почтой России: индекс, регион, город/населённый пункт, улица, дом, квартира (если есть).');return
    if state=='address':
        if len(txt)<15 or len(txt)>350:send(chat,'Укажите полный адрес с индексом (от 15 до 350 символов).');return
        s['draft']['address']=txt;s['state']='confirm';save(uid,s);confirmation(chat,s);return
    if state=='confirm':send(chat,'Подтвердите заявку кнопкой ниже или отмените через /cancel.',keyboard([[('✅ Отправить заявку','submit'),('❌ Отмена','home')]]))


class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ('/','/health'):self.send_response(404);self.end_headers();return
        self.send_response(200);self.send_header('Content-Type','text/plain')
        self.end_headers();self.wfile.write(b'OK')
    def log_message(self,*args):pass


def poll():
    offset=None
    while True:
        try:
            for update in api('getUpdates',{'timeout':45,'offset':offset,'allowed_updates':['message','callback_query']}).get('result',[]):
                offset=update['update_id']+1
                try:
                    if 'callback_query' in update:on_callback(update['callback_query'])
                    elif 'message' in update:on_message(update['message'])
                except Exception as e:print('Update error:',repr(e),flush=True)
        except Exception as e:print('Polling error:',repr(e),flush=True);time.sleep(5)


if __name__=='__main__':
    c=db();c.close()
    if TOKEN:threading.Thread(target=poll,daemon=True).start()
    else:print('BOT_TOKEN is not configured',flush=True)
    ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('PORT','10000'))),Health).serve_forever()
