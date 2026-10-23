import os
import json
import time
import urllib.request
from decimal import Decimal, InvalidOperation

TOKEN = os.environ['BOT_TOKEN']
API = f'https://api.telegram.org/bot{TOKEN}/'
RATE = Decimal('114')
SHIPPING = Decimal('27')
DOMESTIC = Decimal('4')
LIMIT = Decimal('299')
DUTY_FREE = Decimal('200')
DUTY_RATE = Decimal('0.13')


def api(method, payload):
    req = urllib.request.Request(API + method, data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=65) as response:
        return json.load(response)


def send(chat_id, message):
    api('sendMessage', {'chat_id': chat_id, 'text': message})


def calculate(raw):
    items = []
    for line in raw.replace(';', '\n').splitlines():
        line = line.strip().replace(',', '.')
        if not line:
            continue
        parts = line.split()
        if len(parts) not in (1, 2):
            raise ValueError('Каждая строка: цена или цена количество. Например: 35 2')
        try:
            price = Decimal(parts[0])
            qty = int(parts[1]) if len(parts) == 2 else 1
        except (InvalidOperation, ValueError):
            raise ValueError('Неверная цена или количество.')
        if not price.is_finite() or price <= 0 or qty < 1 or qty > 100:
            raise ValueError('Цена должна быть положительной, количество от 1 до 100.')
        if price > LIMIT:
            raise ValueError('Один товар дороже 299 € отправить нельзя.')
        items.extend([price] * qty)
        if len(items) > 200:
            raise ValueError('За один расчёт максимум 200 товаров.')
    if not items:
        raise ValueError('Пришли цены товаров.')
    parcels = []
    for price in sorted(items, reverse=True):
        for parcel in parcels:
            if len(parcel) < 10 and sum(parcel) + price <= LIMIT:
                parcel.append(price)
                break
        else:
            parcels.append([price])
    goods = sum(items)
    duty = sum((sum(p) - DUTY_FREE) * DUTY_RATE for p in parcels if sum(p) > DUTY_FREE)
    shipping = SHIPPING * len(parcels)
    domestic = DOMESTIC * len(parcels)
    total = goods + duty + shipping + domestic
    money = lambda v: f'{v:.2f}'
    lines = [f'Товаров: {len(items)} | Посылок: {len(parcels)}', f'Товары: {money(goods)} €', f'Доставка в РФ: {money(shipping)} €', f'Доставка по стране: {money(domestic)} €', f'Пошлина: {money(duty)} €', f'ИТОГО: {money(total)} € = {money(total * RATE)} ₽', '', 'Разбивка посылок:']
    for index, parcel in enumerate(parcels, 1):
        lines.append(f'{index}) {len(parcel)} шт., {money(sum(parcel))} €')
    lines.append('\nКурс: 114 ₽/€. Расчёт предварительный.')
    lines.append('Допущение: 4 € за внутренний заказ на каждую посылку.')
    return '\n'.join(lines)


def main():
    offset = None
    print('Bot started', flush=True)
    while True:
        try:
            updates = api('getUpdates', {'timeout': 50, 'offset': offset, 'allowed_updates': ['message']})
            for update in updates.get('result', []):
                offset = update['update_id'] + 1
                msg = update.get('message', {})
                chat_id = msg.get('chat', {}).get('id')
                text = msg.get('text', '').strip()
                if not chat_id or not text:
                    continue
                if text.startswith('/start') or text.startswith('/help'):
                    send(chat_id, 'Привет! Отправь цены в евро: каждая строка — цена и необязательное количество.\nНапример:\n45 2\n89\n120\n\nБот разделит товары по посылкам до 299 € и максимум 10 товаров, посчитает доставку и пошлину.')
                else:
                    try:
                        send(chat_id, calculate(text))
                    except ValueError as error:
                        send(chat_id, f'Ошибка: {error}\nПример ввода:\n45 2\n89\n120')
        except Exception as error:
            print('Polling error:', repr(error), flush=True)
            time.sleep(5)


if __name__ == '__main__':
    main()
