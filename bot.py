import os, json, time, sqlite3, urllib.request, threading, re, traceback
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN=os.getenv('BOT_TOKEN','')
ADMIN_CHAT_ID=os.getenv('ADMIN_CHAT_ID','').strip()
DB_PATH=os.getenv('DB_PATH','/tmp/orders.sqlite3')
API=f'https://api.telegram.org/bot{TOKEN}/'
RATE=Decimal('114'); SHIPPING=Decimal('27'); DOMESTIC=Decimal('4')
LIMIT=Decimal('299'); FREE=Decimal('200'); DUTY=Decimal('0.13')
LOCK=threading.RLock()

def db():
    c=sqlite3.connect(DB_PATH,timeout=20)
    c.row_factory=sqlite3.Row
    c.execute('CREATE TABLE IF NOT EXISTS sessions (user_id INTEGER PRIMARY KEY, state TEXT NOT NULL DEFAULT "", draft TEXT NOT NULL DEFAULT "{}", cart TEXT NOT NULL DEFAULT "[]")')
    c.execute('CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, payload TEXT, created_at TEXT)')
    c.commit(); return c

def session(uid):
    with LOCK:
        c=db();c.execute('INSERT OR IGNORE INTO sessions(user_id) VALUES(?)',(uid,));c.commit()
        row=c.execute('SELECT * FROM sessions WHERE user_id=?',(uid,)).fetchone();c.close()
    return {'state':row['state'],'draft':json.loads(row['draft']),'cart':json.loads(row['cart'])}

def save(uid,s):
    with LOCK:
        c=db();c.execute('INSERT OR IGNORE INTO sessions(user_id) VALUES(?)',(uid,))
        c.execute('UPDATE sessions SET state=?, draft=?, cart=? WHERE user_id=?',(s['state'],json.dumps(s['draft'],ensure_ascii=False),json.dumps(s['cart'],ensure_ascii=False),uid))
        c.commit();c.close()

def api(method,payload):
    request=urllib.request.Request(API+method,data=json.dumps(payload,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=65) as response:return json.load(response)

def kb(rows):return {'inline_keyboard':[[{'text':t,'callback_data':d} for t,d in row] for row in rows]}
HOME=kb([[('➕ Добавить товар','add'),('🛒 Корзина','cart')],[('💰 Рассчитать','calc'),('🧾 Оформить заказ','checkout')],[('🗑 Очистить корзину','clear')]])
CART=kb([[('➕ Добавить товар','add'),('🗑 Удалить последний','remove')],[('💰 Рассчитать','calc'),('🧾 Оформить','checkout')],[('🏠 Меню','home')]])

def send(chat,text,markup=None):
    payload={'chat_id':chat,'text':text[:3900],'disable_web_page_preview':True}
    if markup:payload['reply_markup']=markup
    return api('sendMessage',payload)

def eur(value):return str(Decimal(value).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP))

def calculate(cart):
    prices=[]
    for item in cart:
        p=Decimal(str(item['price']));q=int(item['qty'])
        if not p.is_finite() or p<=0 or p>LIMIT or q<1:raise ValueError('В корзине некорректный товар.')
        prices.extend([p]*q)
    if not prices:raise ValueError('Корзина пуста. Сначала добавьте товар.')
    # Approximate first-fit-decreasing grouping, not necessarily the cheapest split.
    parcels=[]
    for price in sorted(prices,reverse=True):
        for parcel in parcels:
            if sum(parcel,Decimal('0'))+price<=LIMIT:
                parcel.append(price);break
        else:parcels.append([price])
    orders=(len(prices)+9)//10
    goods=sum(prices,Decimal('0'))
    duty=sum((sum(p,Decimal('0'))-FREE)*DUTY for p in parcels if sum(p,Decimal('0'))>FREE)
    local=DOMESTIC*orders; international=SHIPPING*len(parcels)
    total=goods+local+international+duty
    lines=[f'💰 Расчёт корзины',f'Вещей: {len(prices)}',f'Заказов до склада: {orders}',f'Посылок в Россию: {len(parcels)}','',f'Товары: {eur(goods)} €',f'Доставка до склада: {eur(local)} €',f'Доставка в РФ: {eur(international)} €',f'Пошлина: {eur(duty)} €','',f'ИТОГО: {eur(total)} €',f'ИТОГО В РУБЛЯХ: {eur(total*RATE)} ₽','']
    for i,p in enumerate(parcels,1):lines.append(f'Посылка {i}: {len(p)} шт., товары {eur(sum(p,Decimal("0")))} €')
    lines.append('\nПредварительный расчёт, курс 114 ₽/€. Доставка до склада: 4 € за каждые 10 вещей (при условии объединения заказов). Стоимость пересылки Почтой России отдельно не учтена. Распределение по посылкам не всегда самое дешёвое.')
    return '\n'.join(lines)

def cart_text(cart):
    if not cart:return '🛒 Корзина пуста.'
    lines=['🛒 Ваша корзина:']
    for i,item in enumerate(cart,1):
        lines.append(f'{i}. Размер: {item.get("size","не указан")} | {item["qty"]} шт. × {item["price"]} €\n{item["url"]}')
    return '\n\n'.join(lines)

def show_cart(chat,cart):
    text=cart_text(cart)
    if len(text)>3800:
        for start in range(0,len(text),3500):send(chat,text[start:start+3500])
        send(chat,'Действия с корзиной:',CART)
    else:send(chat,text,CART)

def on_callback(cb):
    uid=cb['from']['id'];chat=cb['message']['chat']['id'];action=cb['data'];s=session(uid)
    try:api('answerCallbackQuery',{'callback_query_id':cb['id']})
    except Exception:pass
    if action=='home':s['state']='';save(uid,s);send(chat,'Главное меню',HOME)
    elif action=='add':s['state']='url';s['draft']={};save(uid,s);send(chat,'Отправьте ссылку на товар (https://...). Отмена: /cancel')
    elif action=='cart':show_cart(chat,s['cart'])
    elif action=='calc':
        try:send(chat,calculate(s['cart']),CART)
        except (ValueError,InvalidOperation) as e:send(chat,str(e),HOME)
    elif action=='remove':
        if s['cart']:s['cart'].pop();save(uid,s)
        show_cart(chat,s['cart'])
    elif action=='clear':send(chat,'Очистить корзину?',kb([[('Да','clear_yes'),('Нет','cart')]]))
    elif action=='clear_yes':s.update({'cart':[],'state':'','draft':{}});save(uid,s);send(chat,'Корзина очищена.',HOME)
    elif action=='checkout':
        if not s['cart']:send(chat,'Сначала добавьте товары.',HOME);return
        s['state']='name';s['draft']={};save(uid,s);send(chat,'🧾 Укажите ФИО получателя. Отмена: /cancel')
    elif action=='submit':
        if s['state']!='confirm':send(chat,'Сначала заполните данные.',HOME);return
        submit(uid,chat,cb['from'],s)

def confirmation(chat,s):
    d=s['draft']
    show_cart(chat,s['cart'])
    send(chat,calculate(s['cart']))
    send(chat,f'Получатель: {d["name"]}\nТелефон: {d["phone"]}\nАдрес Почты России: {d["address"]}\n\nОтправить заявку менеджеру?',kb([[('✅ Отправить','submit')],[('❌ Отмена','home')]]))

def submit(uid,chat,from_user,s):
    if not ADMIN_CHAT_ID:send(chat,'Уведомления менеджеру пока не настроены. Добавьте ADMIN_CHAT_ID в Render. Корзина сохранена.',HOME);return
    d=s['draft'];username=from_user.get('username');profile='@'+username if username else f'tg://user?id={uid}'
    with LOCK:
        c=db();cur=c.execute('INSERT INTO orders(user_id,payload,created_at) VALUES(?,?,?)',(uid,json.dumps({'cart':s['cart'],'customer':d},ensure_ascii=False),datetime.now(timezone.utc).isoformat()));order_id=cur.lastrowid;c.commit();c.close()
    message=f'🔔 ЗАЯВКА #{order_id}\nTelegram: {profile} (ID {uid})\nФИО: {d["name"]}\nТелефон: {d["phone"]}\nАдрес Почты России: {d["address"]}\n\n'+cart_text(s['cart'])+'\n\n'+calculate(s['cart'])
    try:
        for i in range(0,len(message),3500):send(ADMIN_CHAT_ID,message[i:i+3500])
    except Exception as exc:
        print('Admin notification failed:',repr(exc),flush=True)
        send(chat,'Не удалось доставить заявку менеджеру. Попробуйте позже, корзина сохранена.',HOME);return
    s.update({'cart':[],'draft':{},'state':''});save(uid,s)
    send(chat,f'✅ Заявка #{order_id} отправлена! Менеджер свяжется для подтверждения.',HOME)

def on_message(msg):
    if 'text' not in msg:return
    chat=msg['chat']['id'];uid=msg['from']['id'];txt=msg['text'].strip();s=session(uid)
    if txt.startswith('/id'):send(chat,f'Ваш Telegram chat ID: {chat}');return
    if txt.startswith(('/start','/help')):s['state']='';save(uid,s);send(chat,'👋 Добро пожаловать! Соберите корзину и получите предварительный расчёт.',HOME);return
    if txt.startswith('/cancel'):s['state']='';s['draft']={};save(uid,s);send(chat,'Действие отменено, корзина сохранена.',HOME);return
    if txt.startswith('/cart'):show_cart(chat,s['cart']);return
    state=s['state']
    if not state:send(chat,'Выберите действие:',HOME);return
    if state=='url':
        if len(txt)>1200 or not re.match(r'^https?://[^\s.]+\.[^\s]+$',txt,re.I):send(chat,'Отправьте ссылку, начинающуюся с https://');return
        s['draft']['url']=txt;s['state']='size';save(uid,s);send(chat,'Укажите размер товара, например 42, XL, 128 см или «без размера».');return
    if state=='size':
        if not 1<=len(txt)<=80:send(chat,'Укажите размер (до 80 символов).');return
        s['draft']['size']=txt;s['state']='price';save(uid,s);send(chat,'Укажите цену одной вещи в евро, например 49.90');return
    if state=='price':
        try:p=Decimal(txt.replace(',','.').replace('€','').strip())
        except InvalidOperation:p=Decimal('-1')
        if not p.is_finite() or p<=0 or p>LIMIT:send(chat,'Цена одной вещи должна быть от 0,01 до 299 €.');return
        s['draft']['price']=eur(p);s['state']='qty';save(uid,s);send(chat,'Количество одинаковых вещей этого размера (1–100)?');return
    if state=='qty':
        try:q=int(txt)
        except ValueError:q=0
        if not 1<=q<=100:send(chat,'Введите количество от 1 до 100.');return
        if sum(int(i['qty']) for i in s['cart'])+q>200:send(chat,'В тестовой версии максимум 200 вещей в корзине.');return
        s['cart'].append({'url':s['draft']['url'],'size':s['draft']['size'],'price':s['draft']['price'],'qty':q})
        s['draft']={};s['state']='';save(uid,s);send(chat,'✅ Товар добавлен!',CART);return
    if state=='name':
        if not 3<=len(txt)<=120:send(chat,'Введите полное ФИО (3–120 символов).');return
        s['draft']['name']=txt;s['state']='phone';save(uid,s);send(chat,'Введите номер телефона, например +79991234567');return
    if state=='phone':
        if not 10<=len(re.sub(r'\D','',txt))<=15:send(chat,'Введите телефон (10–15 цифр).');return
        s['draft']['phone']=txt[:40];s['state']='address';save(uid,s);send(chat,'Введите полный адрес Почты России: индекс, регион, населённый пункт, улица, дом, квартира (если есть).');return
    if state=='address':
        if not 15<=len(txt)<=350 or not re.search(r'\b\d{6}\b',txt):send(chat,'Введите полный адрес с шестизначным почтовым индексом.');return
        s['draft']['address']=txt;s['state']='confirm';save(uid,s);confirmation(chat,s);return
    if state=='confirm':send(chat,'Подтвердите заявку кнопкой или отмените через /cancel.',kb([[('✅ Отправить','submit'),('❌ Отмена','home')]]))

class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ('/','/health'):self.send_response(404);self.end_headers();return
        self.send_response(200);self.end_headers();self.wfile.write(b'OK')
    def log_message(self,*args):pass

def poll():
    offset=None
    while True:
        try:
            updates=api('getUpdates',{'timeout':45,'offset':offset,'allowed_updates':['message','callback_query']}).get('result',[])
            for update in updates:
                offset=update['update_id']+1
                try:
                    if 'callback_query' in update:on_callback(update['callback_query'])
                    elif 'message' in update:on_message(update['message'])
                except Exception:traceback.print_exc()
        except Exception:traceback.print_exc();time.sleep(5)

if __name__=='__main__':
    c=db();c.close()
    if TOKEN:threading.Thread(target=poll,daemon=True).start()
    else:print('BOT_TOKEN missing',flush=True)
    ThreadingHTTPServer(('0.0.0.0',int(os.getenv('PORT','10000'))),Health).serve_forever()
