from flask import Flask, jsonify
import json
import time
import threading
import os
from playwright.sync_api import sync_playwright

app = Flask(__name__)
TOKEN_FILE = 'token.json'
TOKEN_LOCK = threading.Lock()

def load_token():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'r') as f:
            data = json.load(f)
            if time.time() - data['updated'] < 300:  # 5 хв
                return data['token']
    return None

def save_token(token):
    with TOKEN_LOCK:
        data = {'token': token, 'updated': time.time()}
        with open(TOKEN_FILE, 'w') as f:
            json.dump(data, f)

def update_token_job():
    def run():
        while True:
            print("Оновлюю токен...")
            token = get_token_with_playwright()
            if token:
                save_token(token)
                print(f"Токен оновлено: {token[:50]}...")
            else:
                print("Не вдалося отримати токен")
            time.sleep(240)  # кожні 4 хв

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

def get_token_with_playwright():
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
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

            # Йдемо на товар (будь-який)
            page.goto('https://creations.mattel.com/checkouts/cn/hWN4eQSmROJAn1IYF6ZTjU27/en-us?auto_redirect=false&edge_redirect=true&skip_shop_pay=true')
            page.wait_for_timeout(3000)
            # page.click('button:has-text("Add to Bag")', timeout=5000)
            # page.wait_for_timeout(2000)
            # page.goto('https://creations.mattel.com/cart')
            # page.wait_for_timeout(2000)
            # page.click('button:has-text("Checkout")', timeout=5000)
            # page.wait_for_timeout(7000)  # чекаємо shop.app

            browser.close()

            print("Token:", token)

            return token
    except Exception as e:
        print("Playwright помилка:", e)
        return None


# === API ===
@app.route('/get_token')
def get_token():
    token = load_token()
    if token:
        return jsonify({'token': token})
    return jsonify({'error': 'Token not ready'}), 503

# === Запуск ===
if __name__ == '__main__':
    update_token_job()  # запускаємо оновлення
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))