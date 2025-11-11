import json
import time
import os
import csv
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright
import requests
import argparse
import signal
import sys

TOKEN_FILE = 'token.json'

# === КОНФІГУРАЦІЯ ===
START_TIME = "18:00:00"  # Час початку моніторингу (HH:MM:SS), None = запуск одразу
MONITOR_DURATION_MINUTES = 15  # Скільки хвилин працює моніторинг
CHECK_INTERVAL_SECONDS = 30  # Інтервал перевірки в секундах
TOKEN_CACHE_SECONDS = 180  # Кешування токена на 3 хвилини
TOKEN_PREPARE_SECONDS = 30  # За скільки секунд до старту отримати токен
MAX_RETRIES = 3  # Максимальна кількість спроб при помилці
PRODUCT_ID = 9083470676173  # RLC Exclusive 1972 Chevy Nova SS
# PRODUCT_ID = 9083040727245  # Hot Wheels x Daniel Arsham 1973 Porsche 911 RSA
CSV_FILE = 'inventory_log.csv'  # Файл для збереження даних
REQUEST_TIMEOUT = 10  # Таймаут для HTTP запитів
PLAYWRIGHT_TIMEOUT = 5000  # Таймаут для Playwright (мс)

# Флаг для graceful shutdown
shutdown_flag = False


def signal_handler(sig, frame):
    """Обробник для Ctrl+C"""
    global shutdown_flag
    print("\n🛑 Отримано сигнал зупинки. Завершуємо роботу...")
    shutdown_flag = True


signal.signal(signal.SIGINT, signal_handler)


def load_token():
    """Завантажує токен з файлу, якщо він не застарілий"""
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, 'r') as f:
                data = json.load(f)
                if time.time() - data['updated'] < TOKEN_CACHE_SECONDS:
                    return data['token']
        except:
            pass
    return None


def save_token(token):
    """Зберігає токен у файл з позначкою часу"""
    data = {'token': token, 'updated': time.time()}
    with open(TOKEN_FILE, 'w') as f:
        json.dump(data, f)


def get_token_with_playwright():
    """Отримує новий токен через Playwright"""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled']
            )
            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36'
            )
            page = context.new_page()

            token = None

            def intercept(route, request):
                nonlocal token
                if 'product-inventory' in request.url:
                    auth = request.headers.get('authorization')
                    if auth and auth.startswith('Bearer '):
                        token = auth
                route.continue_()

            page.route('**/*', intercept)
            page.goto(
                'https://creations.mattel.com/checkouts/cn/hWN4eQSmROJAn1IYF6ZTjU27/en-us?auto_redirect=false&edge_redirect=true&skip_shop_pay=true')
            page.wait_for_timeout(PLAYWRIGHT_TIMEOUT)
            browser.close()

            return token
    except Exception as e:
        print(f"❌ Playwright помилка: {e}")
        return None


def get_inventory(token):
    """Отримує дані про залишки товару"""
    url = "https://mattel-checkout-prd.fly.dev/api/product-inventory"
    querystring = {"productIds": f"gid://shopify/Product/{PRODUCT_ID}"}
    headers = {
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "uk,en-US;q=0.9,en;q=0.8,hr;q=0.7",
        "Authorization": token,
        "Content-Type": "application/json",
        "Origin": "https://extensions.shopifycdn.com",
        "Referer": "https://extensions.shopifycdn.com/",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "cross-site",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
    }

    try:
        response = requests.get(url, headers=headers, params=querystring, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            if data and len(data) > 0:
                total = data[0].get('totalInventory')
                variant_meta = data[0].get('variantMeta', {}).get('value', '[]')
                return {
                    'totalInventory': total,
                    'variantMeta': variant_meta,
                    'timestamp': time.time()
                }
        elif response.status_code == 401:
            print("⚠️ Токен застарілий (401)")
            return None
        else:
            print(f"⚠️ Несподіваний статус: {response.status_code}")
        return None
    except requests.exceptions.Timeout:
        print(f"⏱️ Таймаут запиту ({REQUEST_TIMEOUT}s)")
        return None
    except Exception as e:
        print(f"❌ Помилка запиту: {e}")
        return None


def init_csv():
    """Ініціалізує CSV файл з заголовками"""
    file_exists = os.path.exists(CSV_FILE)
    with open(CSV_FILE, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['time', 'qty', 'change', 'variant_info'])


def log_inventory(data, previous_qty):
    """Записує дані про залишки в CSV файл"""
    timestamp = datetime.now().strftime('%d.%m.%Y %H:%M:%S')
    qty = data.get('totalInventory', 0)

    # Обчислюємо зміну
    change = ''
    if previous_qty is not None:
        diff = qty - previous_qty
        if diff != 0:
            change = f"{diff:+d}"

    # Скорочена інформація про варіанти
    variant_info = ''
    try:
        variant_meta = json.loads(data.get('variantMeta', '[]'))
        if variant_meta:
            variant_info = f"SKU: {variant_meta[0].get('variant_sku', 'N/A')}"
    except:
        pass

    with open(CSV_FILE, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([timestamp, qty, change, variant_info])

    change_str = f" ({change})" if change else ""
    print(f"📊 [{timestamp}] Залишок: {qty}{change_str}")

    return qty


def wait_until_start_time(start_time_str):
    """Чекає до заданого часу, отримує токен заздалегідь"""
    if not start_time_str:
        return None

    try:
        # Парсимо час
        target_time = datetime.strptime(start_time_str, "%H:%M:%S").time()
        now = datetime.now()
        target_datetime = datetime.combine(now.date(), target_time)

        # Якщо час вже минув сьогодні, беремо завтрашній день
        if target_datetime <= now:
            target_datetime += timedelta(days=1)

        seconds_until_start = (target_datetime - now).total_seconds()

        print(f"⏰ Запланований старт: {target_datetime.strftime('%d.%m.%Y %H:%M:%S')}")
        print(f"⏳ Очікування: {int(seconds_until_start)} секунд ({int(seconds_until_start / 60)} хв)")

        # Час коли треба отримати токен (за 30 секунд до старту)
        token_time = target_datetime - timedelta(seconds=TOKEN_PREPARE_SECONDS)
        seconds_until_token = (token_time - datetime.now()).total_seconds()

        # Чекаємо до часу отримання токена
        if seconds_until_token > 0:
            print(f"⏳ Отримання токена заплановано на: {token_time.strftime('%H:%M:%S')}")

            # Відображаємо прогрес очікування
            while True:
                remaining = (token_time - datetime.now()).total_seconds()
                if remaining <= 0:
                    break

                if shutdown_flag:
                    print("\n🛑 Очікування скасовано")
                    return None

                # Виводимо прогрес кожні 10 секунд або останні 30 секунд
                if remaining > 30 and int(remaining) % 10 == 0:
                    print(f"⏳ До отримання токена: {int(remaining / 60)} хв {int(remaining % 60)} сек")
                elif remaining <= 30:
                    print(f"⏳ До отримання токена: {int(remaining)} сек", end='\r')

                time.sleep(1)

            print("\n🔑 Отримую токен перед стартом...")

        # Отримуємо токен
        token = get_token_with_playwright()
        if token:
            save_token(token)
            print(f"✅ Токен отримано: {token[:50]}...")
        else:
            print("❌ Не вдалося отримати токен")
            return None

        # Чекаємо до часу старту
        remaining = (target_datetime - datetime.now()).total_seconds()
        if remaining > 0:
            print(f"⏳ До старту моніторингу: {int(remaining)} сек")
            for _ in range(int(remaining)):
                if shutdown_flag:
                    print("\n🛑 Очікування скасовано")
                    return None
                time.sleep(1)

        print(f"🚀 СТАРТ о {datetime.now().strftime('%H:%M:%S')}")
        return token

    except ValueError:
        print(f"❌ Невірний формат часу: {start_time_str}. Використовуйте HH:MM:SS")
        return None


def monitor_inventory():
    """Основна функція моніторингу"""
    global shutdown_flag

    print("=" * 60)
    print(f"🚀 Запуск моніторингу на {MONITOR_DURATION_MINUTES} хвилин")
    print(f"⏱️  Інтервал перевірки: {CHECK_INTERVAL_SECONDS} секунд")
    print(f"📦 Product ID: {PRODUCT_ID}")
    print(f"📝 Файл логів: {CSV_FILE}")
    print("=" * 60)

    init_csv()

    # Чекаємо до заданого часу, якщо він вказаний
    token = None
    if START_TIME:
        token = wait_until_start_time(START_TIME)
        if shutdown_flag:
            return

    start_time = time.time()
    end_time = start_time + (MONITOR_DURATION_MINUTES * 60)

    consecutive_failures = 0
    previous_qty = None
    check_count = 0

    while time.time() < end_time and not shutdown_flag:
        check_count += 1
        remaining = int((end_time - time.time()) / 60)
        print(f"\n🔄 Перевірка #{check_count} (залишилось ~{remaining} хв)")

        # Перевіряємо чи потрібен новий токен
        if not token:
            token = load_token()
            if not token:
                print("🔑 Отримую новий токен...")
                token = get_token_with_playwright()
                if token:
                    save_token(token)
                    print(f"✅ Токен отримано: {token[:50]}...")
                else:
                    print("❌ Не вдалося отримати токен")
                    consecutive_failures += 1
                    if consecutive_failures >= MAX_RETRIES:
                        print(f"🛑 Досягнуто максимум спроб ({MAX_RETRIES}). Зупиняємось.")
                        break
                    time.sleep(5)
                    continue

        # Отримуємо дані про залишки
        data = get_inventory(token)

        if data and data.get('totalInventory') is not None:
            previous_qty = log_inventory(data, previous_qty)
            consecutive_failures = 0
        else:
            print("❌ Не вдалося отримати дані про залишки")
            consecutive_failures += 1

            # Якщо не вдалося, пробуємо оновити токен
            if consecutive_failures >= 2:
                print("🔄 Спроба оновити токен...")
                token = get_token_with_playwright()
                if token:
                    save_token(token)
                    print("✅ Токен оновлено")
                    consecutive_failures = 0
                elif consecutive_failures >= MAX_RETRIES:
                    print(f"🛑 Досягнуто максимум спроб ({MAX_RETRIES}). Зупиняємось.")
                    break

        # Чекаємо до наступної ітерації
        for _ in range(CHECK_INTERVAL_SECONDS):
            if shutdown_flag:
                break
            time.sleep(1)

    print("\n" + "=" * 60)
    print("✅ Моніторинг завершено")
    print(f"📊 Всього перевірок: {check_count}")
    print(f"📝 Дані збережено у файл: {CSV_FILE}")
    print("=" * 60)


# === ЗАПУСК ===
if __name__ == '__main__':
    # Парсинг аргументів командного рядка
    parser = argparse.ArgumentParser(description='Моніторинг залишків товарів Mattel')
    parser.add_argument('--duration', type=int,
                        help=f'Тривалість моніторингу в хвилинах (за замовчуванням: {MONITOR_DURATION_MINUTES})')
    parser.add_argument('--interval', type=int,
                        help=f'Інтервал перевірки в секундах (за замовчуванням: {CHECK_INTERVAL_SECONDS})')
    parser.add_argument('--product-id', type=int, help=f'ID продукту (за замовчуванням: {PRODUCT_ID})')
    parser.add_argument('--output', type=str, help=f'Файл для збереження (за замовчуванням: {CSV_FILE})')

    args = parser.parse_args()

    # Оновлюємо параметри якщо задані
    if args.duration:
        MONITOR_DURATION_MINUTES = args.duration
    if args.interval:
        CHECK_INTERVAL_SECONDS = args.interval
    if args.product_id:
        PRODUCT_ID = args.product_id
    if args.output:
        CSV_FILE = args.output

    # Запускаємо моніторинг
    monitor_inventory()