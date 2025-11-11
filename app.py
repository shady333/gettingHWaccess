import json
import time
import os
import csv
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright
import requests
import argparse
import signal
import threading
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk
import io
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

TOKEN_FILE = 'token.json'

# === КОНФІГУРАЦІЯ ===
START_TIME = None  # Час початку моніторингу (HH:MM:SS), None = запуск одразу
MONITOR_DURATION_MINUTES = 60  # Скільки хвилин працює моніторинг
CHECK_INTERVAL_SECONDS = 30  # Інтервал перевірки в секундах
TOKEN_CACHE_SECONDS = 180  # Кешування токена на 3 хвилини
TOKEN_PREPARE_SECONDS = 30  # За скільки секунд до старту отримати токен
MAX_RETRIES = 3  # Максимальна кількість спроб при помилці
CSV_FILE = 'inventory_log.csv'  # Файл для збереження даних
REQUEST_TIMEOUT = 10  # Таймаут для HTTP запитів
PLAYWRIGHT_TIMEOUT = 5000  # Таймаут для Playwright (мс)

# Конфігурація продуктів
PRODUCTS = {
    9083040727245: {
        "name": "Hot Wheels x Daniel Arsham\n1973 Porsche 911 RSA",
        "image_url": "https://cdn.shopify.com/s/files/1/0568/1132/3597/files/wr9xdfnipg3tnyglifpn.jpg"
    },
    9083470676173: {
        "name": "RLC Exclusive\n1972 Chevy Nova SS",
        "image_url": "https://cdn.shopify.com/s/files/1/0568/1132/3597/files/z1iqcytnetlmhqhgrmyn.jpg"
    }
}

PRODUCT_ID = 9083470676173

# Флаг для graceful shutdown
shutdown_flag = False


class InventoryMonitorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Mattel Inventory Monitor")
        self.root.geometry("800x900")
        self.root.configure(bg='#1a1a1a')

        # Дані для моніторингу
        self.timestamps = []
        self.quantities = []
        self.initial_qty = None
        self.current_qty = None
        self.monitoring = False
        self.monitor_thread = None

        self.setup_ui()

    def setup_ui(self):
        # Контейнер для прокрутки
        main_frame = tk.Frame(self.root, bg='#1a1a1a')
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # === НАЗВА ПРОДУКТУ ===
        product_info = PRODUCTS.get(PRODUCT_ID, {"name": "Unknown Product"})
        title_label = tk.Label(
            main_frame,
            text=product_info["name"],
            font=('Arial', 18, 'bold'),
            fg='#ffffff',
            bg='#1a1a1a',
            justify=tk.CENTER
        )
        title_label.pack(pady=(0, 10))

        # === ФОТО ПРОДУКТУ ===
        self.image_label = tk.Label(main_frame, bg='#1a1a1a')
        self.image_label.pack(pady=10)
        self.load_product_image(product_info.get("image_url"))

        # === СТАТИСТИКА ===
        stats_frame = tk.Frame(main_frame, bg='#2a2a2a', relief=tk.RAISED, borderwidth=2)
        stats_frame.pack(fill=tk.X, pady=10)

        # Початкова кількість
        initial_frame = tk.Frame(stats_frame, bg='#2a2a2a')
        initial_frame.pack(side=tk.LEFT, expand=True, padx=20, pady=15)

        tk.Label(
            initial_frame,
            text="Initial QTY",
            font=('Arial', 12),
            fg='#aaaaaa',
            bg='#2a2a2a'
        ).pack()

        self.initial_label = tk.Label(
            initial_frame,
            text="---",
            font=('Arial', 28, 'bold'),
            fg='#4CAF50',
            bg='#2a2a2a'
        )
        self.initial_label.pack()

        # Поточна кількість
        current_frame = tk.Frame(stats_frame, bg='#2a2a2a')
        current_frame.pack(side=tk.LEFT, expand=True, padx=20, pady=15)

        tk.Label(
            current_frame,
            text="Current QTY",
            font=('Arial', 12),
            fg='#aaaaaa',
            bg='#2a2a2a'
        ).pack()

        self.current_label = tk.Label(
            current_frame,
            text="---",
            font=('Arial', 28, 'bold'),
            fg='#2196F3',
            bg='#2a2a2a'
        )
        self.current_label.pack()

        # Зміна
        change_frame = tk.Frame(stats_frame, bg='#2a2a2a')
        change_frame.pack(side=tk.LEFT, expand=True, padx=20, pady=15)

        tk.Label(
            change_frame,
            text="DIFF",
            font=('Arial', 12),
            fg='#aaaaaa',
            bg='#2a2a2a'
        ).pack()

        self.change_label = tk.Label(
            change_frame,
            text="---",
            font=('Arial', 28, 'bold'),
            fg='#FF9800',
            bg='#2a2a2a'
        )
        self.change_label.pack()

        # === СТАТУС ===
        self.status_label = tk.Label(
            main_frame,
            text="Ready to Start",
            font=('Arial', 11),
            fg='#aaaaaa',
            bg='#1a1a1a'
        )
        self.status_label.pack(pady=5)

        # === ГРАФІК ===
        graph_frame = tk.Frame(main_frame, bg='#2a2a2a')
        graph_frame.pack(fill=tk.BOTH, expand=True, pady=10)

        self.figure = Figure(figsize=(7, 4), facecolor='#2a2a2a')
        self.ax = self.figure.add_subplot(111)
        self.ax.set_facecolor('#1a1a1a')
        self.ax.set_xlabel('Time', color='#ffffff')
        self.ax.set_ylabel('Qty', color='#ffffff')
        self.ax.tick_params(colors='#ffffff')
        self.ax.grid(True, alpha=0.3, color='#555555')

        self.canvas = FigureCanvasTkAgg(self.figure, graph_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        # === КНОПКИ УПРАВЛІННЯ ===
        button_frame = tk.Frame(main_frame, bg='#1a1a1a')
        button_frame.pack(pady=10)

        self.start_button = tk.Button(
            button_frame,
            text="▶ Start",
            command=self.start_monitoring,
            font=('Arial', 12, 'bold'),
            bg='#4CAF50',
            fg='white',
            width=15,
            height=2,
            relief=tk.RAISED,
            cursor='hand2'
        )
        self.start_button.pack(side=tk.LEFT, padx=5)

        self.stop_button = tk.Button(
            button_frame,
            text="⏹ Stop",
            command=self.stop_monitoring,
            font=('Arial', 12, 'bold'),
            bg='#f44336',
            fg='white',
            width=15,
            height=2,
            relief=tk.RAISED,
            state=tk.DISABLED,
            cursor='hand2'
        )
        self.stop_button.pack(side=tk.LEFT, padx=5)

    def load_product_image(self, url):
        """Завантажує та відображає фото продукту"""
        try:
            response = requests.get(url, timeout=5)
            image_data = Image.open(io.BytesIO(response.content))
            # Зменшуємо розмір зображення
            image_data.thumbnail((250, 250), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image_data)
            self.image_label.configure(image=photo)
            self.image_label.image = photo
        except Exception as e:
            self.image_label.configure(text="❌ Не вдалося завантажити фото", fg='#ff0000')
            print(f"Помилка завантаження фото: {e}")

    def update_stats(self, qty):
        """Оновлює статистику на екрані"""
        if self.initial_qty is None:
            self.initial_qty = qty
            self.initial_label.configure(text=f"{qty:,}")

        self.current_qty = qty
        self.current_label.configure(text=f"{qty:,}")

        if self.initial_qty is not None:
            change = qty - self.initial_qty
            change_text = f"{change:+,}"
            color = '#f44336' if change < 0 else '#4CAF50' if change > 0 else '#FF9800'
            self.change_label.configure(text=change_text, fg=color)

        # Додаємо дані для графіка
        current_time = datetime.now()
        self.timestamps.append(current_time)
        self.quantities.append(qty)

        self.update_graph()

    def update_graph(self):
        """Оновлює графік"""
        self.ax.clear()

        if len(self.timestamps) > 0:
            # Форматуємо час для відображення
            time_labels = [t.strftime('%H:%M:%S') for t in self.timestamps]

            self.ax.plot(time_labels, self.quantities,
                         color='#2196F3', linewidth=2, marker='o', markersize=6)
            self.ax.fill_between(range(len(self.quantities)), self.quantities,
                                 alpha=0.3, color='#2196F3')

            self.ax.set_xlabel('Time', color='#ffffff', fontsize=10)
            self.ax.set_ylabel('Qty', color='#ffffff', fontsize=10)
            self.ax.tick_params(colors='#ffffff', labelsize=8)
            self.ax.grid(True, alpha=0.3, color='#555555')

            # Обертаємо підписи часу
            plt.setp(self.ax.xaxis.get_majorticklabels(), rotation=45, ha='right')

            self.figure.tight_layout()

        self.canvas.draw()

    def update_status(self, message, color='#aaaaaa'):
        """Оновлює статус"""
        self.status_label.configure(text=message, fg=color)

    def start_monitoring(self):
        """Запускає моніторинг в окремому потоці"""
        if not self.monitoring:
            self.monitoring = True
            self.start_button.configure(state=tk.DISABLED)
            self.stop_button.configure(state=tk.NORMAL)

            # Скидаємо дані
            self.timestamps = []
            self.quantities = []
            self.initial_qty = None
            self.current_qty = None

            self.monitor_thread = threading.Thread(target=self.run_monitor, daemon=True)
            self.monitor_thread.start()

    def stop_monitoring(self):
        """Зупиняє моніторинг"""
        global shutdown_flag
        shutdown_flag = True
        self.monitoring = False
        self.start_button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.DISABLED)
        self.update_status("STOPPED", '#FF9800')

    def run_monitor(self):
        """Основна логіка моніторингу (в окремому потоці)"""
        global shutdown_flag
        shutdown_flag = False

        self.update_status("🔑 Getting token...", '#2196F3')

        init_csv()
        token = load_token()

        if not token:
            token = get_token_with_playwright()
            if token:
                save_token(token)
            else:
                self.update_status("❌ Error on token", '#f44336')
                self.monitoring = False
                self.root.after(0, lambda: self.start_button.configure(state=tk.NORMAL))
                self.root.after(0, lambda: self.stop_button.configure(state=tk.DISABLED))
                return

        self.update_status("✅ Monitoring started", '#4CAF50')

        start_time = time.time()
        end_time = start_time + (MONITOR_DURATION_MINUTES * 60)
        consecutive_failures = 0
        previous_qty = None
        check_count = 0

        while time.time() < end_time and not shutdown_flag and self.monitoring:
            check_count += 1
            remaining = int((end_time - time.time()) / 60)

            self.root.after(0, lambda: self.update_status(
                f"🔄 Check #{check_count} (~{remaining} min left)", '#2196F3'
            ))

            # Перевіряємо токен
            if not token:
                token = load_token()
                if not token:
                    token = get_token_with_playwright()
                    if token:
                        save_token(token)

            # Отримуємо дані
            data = get_inventory(token)

            if data and data.get('totalInventory') is not None:
                qty = data.get('totalInventory')
                previous_qty = log_inventory(data, previous_qty)

                # Оновлюємо GUI в основному потоці
                self.root.after(0, lambda q=qty: self.update_stats(q))
                consecutive_failures = 0
            else:
                # Оновлюємо токен при помилці
                new_token = get_token_with_playwright()
                if new_token:
                    token = new_token
                    save_token(token)

                    # Повторюємо запит
                    data = get_inventory(token)
                    if data and data.get('totalInventory') is not None:
                        qty = data.get('totalInventory')
                        previous_qty = log_inventory(data, previous_qty)
                        self.root.after(0, lambda q=qty: self.update_stats(q))
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1
                else:
                    consecutive_failures += 1

                if consecutive_failures >= MAX_RETRIES:
                    self.root.after(0, lambda: self.update_status(
                        "❌ Max retries reached", '#f44336'
                    ))
                    break

            # Чекаємо до наступної ітерації
            for _ in range(CHECK_INTERVAL_SECONDS):
                if shutdown_flag or not self.monitoring:
                    break
                time.sleep(1)

        self.monitoring = False
        self.root.after(0, lambda: self.start_button.configure(state=tk.NORMAL))
        self.root.after(0, lambda: self.stop_button.configure(state=tk.DISABLED))
        self.root.after(0, lambda: self.update_status(
            f"✅ Monitoring finished ({check_count} checks)", '#4CAF50'
        ))


# === ДОПОМІЖНІ ФУНКЦІЇ (ті самі що і раніше) ===

def load_token():
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
    data = {'token': token, 'updated': time.time()}
    with open(TOKEN_FILE, 'w') as f:
        json.dump(data, f)


def get_token_with_playwright():
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled']
            )
            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
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
        print(f"Playwright error: {e}")
        return None


def get_inventory(token):
    url = "https://mattel-checkout-prd.fly.dev/api/product-inventory"
    querystring = {"productIds": f"gid://shopify/Product/{PRODUCT_ID}"}
    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }

    try:
        response = requests.get(url, headers=headers, params=querystring, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            if data and len(data) > 0:
                return {
                    'totalInventory': data[0].get('totalInventory'),
                    'variantMeta': data[0].get('variantMeta', {}).get('value', '[]'),
                    'timestamp': time.time()
                }
        return None
    except:
        return None


def init_csv():
    file_exists = os.path.exists(CSV_FILE)
    with open(CSV_FILE, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['time', 'qty', 'change', 'variant_info'])


def log_inventory(data, previous_qty):
    timestamp = datetime.now().strftime('%d.%m.%Y %H:%M:%S')
    qty = data.get('totalInventory', 0)

    change = ''
    if previous_qty is not None:
        diff = qty - previous_qty
        if diff != 0:
            change = f"{diff:+d}"

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

    return qty


# === ЗАПУСК GUI ===
if __name__ == '__main__':
    root = tk.Tk()
    app = InventoryMonitorGUI(root)
    root.mainloop()