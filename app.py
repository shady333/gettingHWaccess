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
MONITOR_DURATION_MINUTES = 720  # Скільки хвилин працює моніторинг
CHECK_INTERVAL_SECONDS = 60  # Інтервал перевірки в секундах
TOKEN_CACHE_SECONDS = 180  # Кешування токена на 3 хвилини
TOKEN_PREPARE_SECONDS = 30  # За скільки секунд до старту отримати токен
MAX_RETRIES = 3  # Максимальна кількість спроб при помилці
CSV_FILE = 'inventory_log.csv'  # Файл для збереження даних
REQUEST_TIMEOUT = 10  # Таймаут для HTTP запитів
PLAYWRIGHT_TIMEOUT = 5000  # Таймаут для Playwright (мс)

# Конфігурація продуктів
PRODUCTS = {
    9083040727245: {
        "name": "Hot Wheels x Daniel Arsham 1973 Porsche 911 RSA",
        "image_url": "https://cdn.shopify.com/s/files/1/0568/1132/3597/files/wr9xdfnipg3tnyglifpn.jpg"
    },
    9083470676173: {
        "name": "RLC Exclusive 1972 Chevy Nova SS",
        "image_url": "https://cdn.shopify.com/s/files/1/0568/1132/3597/files/z1iqcytnetlmhqhgrmyn.jpg"
    },
    9087523553485: {
        "name": "Hot Wheels x MoMA Citroën DS 23 Sedan",
        "image_url": "https://cdn.shopify.com/s/files/1/0568/1132/3597/files/kmiqqpvrcnxdgapeyipc.jpg"
    },
    9087523651789: {
        "name": "Hot Wheels x MoMA Jaguar E-Type Roadster",
        "image_url": "https://cdn.shopify.com/s/files/1/0568/1132/3597/files/isjzaxcntsw60cdeyaeb.jpg"
    },
    9058078523597: {
        "name": "RLC Exclusive Ford GT40 MkII",
        "image_url": "https://cdn.shopify.com/s/files/1/0568/1132/3597/files/lt7eyriud7xkbanryk8e.jpg"
    },
}

# Флаг для graceful shutdown
shutdown_flag = False


class ProductColumn:
    """Клас для однієї колонки продукту"""

    def __init__(self, parent, column_id):
        self.column_id = column_id
        self.product_id = None
        self.initial_qty = None
        self.current_qty = None
        self.max_qty = None
        self.timestamps = []
        self.quantities = []

        # Створюємо фрейм для колонки
        self.frame = tk.Frame(parent, bg='#1a1a1a', relief=tk.RAISED, borderwidth=2)

        # Випадаюче меню для вибору продукту
        selector_frame = tk.Frame(self.frame, bg='#1a1a1a')
        selector_frame.pack(fill=tk.X, padx=10, pady=10)

        tk.Label(
            selector_frame,
            text=f"Column {column_id}:",
            font=('Arial', 10),
            fg='#aaaaaa',
            bg='#1a1a1a'
        ).pack(side=tk.LEFT, padx=5)

        self.product_var = tk.StringVar()
        product_options = [''] + [f"{pid} - {PRODUCTS[pid]['name'].split(chr(10))[0]}" for pid in PRODUCTS.keys()]

        self.product_selector = ttk.Combobox(
            selector_frame,
            textvariable=self.product_var,
            values=product_options,
            state='readonly',
            width=30
        )
        self.product_selector.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.product_selector.bind('<<ComboboxSelected>>', self.on_product_selected)

        # Контейнер для контенту (спочатку прихований)
        self.content_frame = tk.Frame(self.frame, bg='#1a1a1a')

        # Назва продукту
        self.title_label = tk.Label(
            self.content_frame,
            text="",
            font=('Arial', 16, 'bold'),
            fg='#ffffff',
            bg='#1a1a1a',
            justify=tk.CENTER,
            wraplength=250
        )
        self.title_label.pack(pady=(10, 10), fill=tk.X)

        # Контейнер для фото та статистики (горизонтально)
        info_frame = tk.Frame(self.content_frame, bg='#1a1a1a')
        info_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Ліва частина - фото продукту
        photo_frame = tk.Frame(info_frame, bg='#1a1a1a')
        photo_frame.pack(side=tk.LEFT, padx=(0, 10))

        self.image_label = tk.Label(photo_frame, bg='#1a1a1a')
        self.image_label.pack()

        # Права частина - статистика
        stats_frame = tk.Frame(info_frame, bg='#2a2a2a', relief=tk.RAISED, borderwidth=1)
        stats_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Initial QTY
        initial_frame = tk.Frame(stats_frame, bg='#2a2a2a')
        initial_frame.pack(pady=10)

        tk.Label(
            initial_frame,
            text="Initial QTY\n(at start)",
            font=('Arial', 14),
            fg='#aaaaaa',
            bg='#2a2a2a',
            justify=tk.CENTER
        ).pack()

        self.initial_label = tk.Label(
            initial_frame,
            text="---",
            font=('Arial', 20, 'bold'),
            fg='#4CAF50',
            bg='#2a2a2a'
        )
        self.initial_label.pack()

        # Current QTY
        current_frame = tk.Frame(stats_frame, bg='#2a2a2a')
        current_frame.pack(pady=10)

        tk.Label(
            current_frame,
            text="Current QTY\n(actual)",
            font=('Arial', 14),
            fg='#aaaaaa',
            bg='#2a2a2a',
            justify=tk.CENTER
        ).pack()

        self.current_label = tk.Label(
            current_frame,
            text="---",
            font=('Arial', 20, 'bold'),
            fg='#2196F3',
            bg='#2a2a2a'
        )
        self.current_label.pack()

        # Max QTY
        max_frame = tk.Frame(stats_frame, bg='#2a2a2a')
        max_frame.pack(pady=10)

        tk.Label(
            max_frame,
            text="Max QTY\n(total items)",
            font=('Arial', 14),
            fg='#aaaaaa',
            bg='#2a2a2a',
            justify=tk.CENTER
        ).pack()

        self.max_label = tk.Label(
            max_frame,
            text="---",
            font=('Arial', 20, 'bold'),
            fg='#9C27B0',
            bg='#2a2a2a'
        )
        self.max_label.pack()

        # DIFF
        change_frame = tk.Frame(stats_frame, bg='#2a2a2a')
        change_frame.pack(pady=10)

        tk.Label(
            change_frame,
            text="DIFF\n(change)",
            font=('Arial', 14),
            fg='#aaaaaa',
            bg='#2a2a2a',
            justify=tk.CENTER
        ).pack()

        self.change_label = tk.Label(
            change_frame,
            text="---",
            font=('Arial', 20, 'bold'),
            fg='#FF9800',
            bg='#2a2a2a'
        )
        self.change_label.pack()

    def on_product_selected(self, event=None):
        """Обробка вибору продукту"""
        selection = self.product_var.get()
        if selection == '':
            self.hide_content()
            self.product_id = None
        else:
            # Витягуємо ID продукту
            product_id = int(selection.split(' - ')[0])
            self.product_id = product_id
            self.load_product(product_id)
            self.show_content()

    def load_product(self, product_id):
        """Завантажує інформацію про продукт"""
        product_info = PRODUCTS.get(product_id, {})

        # Встановлюємо назву
        self.title_label.configure(text=product_info.get('name', 'Unknown'))

        # Завантажуємо фото
        self.load_product_image(product_info.get('image_url'))

        # Скидаємо статистику
        self.reset_stats()

    def load_product_image(self, url):
        """Завантажує та відображає фото продукту"""
        try:
            response = requests.get(url, timeout=5)
            image_data = Image.open(io.BytesIO(response.content))
            image_data.thumbnail((180, 180), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image_data)
            self.image_label.configure(image=photo)
            self.image_label.image = photo
        except Exception as e:
            self.image_label.configure(text="❌ Error", fg='#ff0000')
            print(f"Помилка завантаження фото: {e}")

    def show_content(self):
        """Показує контент колонки"""
        self.content_frame.pack(fill=tk.BOTH, expand=True)

    def hide_content(self):
        """Ховає контент колонки"""
        self.content_frame.pack_forget()

    def reset_stats(self):
        """Скидає статистику"""
        self.initial_qty = None
        self.current_qty = None
        self.max_qty = None
        self.timestamps = []
        self.quantities = []
        self.initial_label.configure(text="---")
        self.current_label.configure(text="---")
        self.max_label.configure(text="---")
        self.change_label.configure(text="---")

    def update_stats(self, qty, max_qty=None):
        """Оновлює статистику"""
        if self.initial_qty is None:
            self.initial_qty = qty
            self.initial_label.configure(text=f"{qty:,}")

        self.current_qty = qty
        self.current_label.configure(text=f"{qty:,}")

        # Оновлюємо max_qty якщо передано
        if max_qty is not None and self.max_qty is None:
            self.max_qty = max_qty
            self.max_label.configure(text=f"{max_qty:,}")

        if self.initial_qty is not None:
            change = qty - self.initial_qty
            change_text = f"{change:+,}"
            color = '#f44336' if change < 0 else '#4CAF50' if change > 0 else '#FF9800'
            self.change_label.configure(text=change_text, fg=color)

        # Додаємо дані для графіка
        current_time = datetime.now()
        self.timestamps.append(current_time)
        self.quantities.append(qty)


class InventoryMonitorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Mattel Multi-Product Inventory Monitor")
        self.root.geometry("1400x800")
        self.root.configure(bg='#1a1a1a')

        self.monitoring = False
        self.monitor_thread = None
        self.columns = []

        self.setup_ui()

    def setup_ui(self):
        # Головний контейнер
        main_frame = tk.Frame(self.root, bg='#1a1a1a')
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Заголовок
        header_label = tk.Label(
            main_frame,
            text="MATTEL INVENTORY MONITOR",
            font=('Arial', 16, 'bold'),
            fg='#ffffff',
            bg='#1a1a1a'
        )
        header_label.pack(pady=(0, 10))

        # Контейнер для колонок
        columns_frame = tk.Frame(main_frame, bg='#1a1a1a')
        columns_frame.pack(fill=tk.BOTH, expand=True)

        # Створюємо 3 колонки
        for i in range(3):
            column = ProductColumn(columns_frame, i + 1)
            column.frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
            self.columns.append(column)

            # За замовчуванням показуємо тільки першу колонку
            if i == 0:
                # Вибираємо перший продукт
                first_product = list(PRODUCTS.keys())[0]
                column.product_var.set(f"{first_product} - {PRODUCTS[first_product]['name'].split(chr(10))[0]}")
                column.on_product_selected()

        # Статус
        self.status_label = tk.Label(
            main_frame,
            text="Ready to Start",
            font=('Arial', 11),
            fg='#aaaaaa',
            bg='#1a1a1a'
        )
        self.status_label.pack(pady=10)

        # Графіки для всіх продуктів
        graphs_label = tk.Label(
            main_frame,
            text="INVENTORY GRAPHS",
            font=('Arial', 12, 'bold'),
            fg='#ffffff',
            bg='#1a1a1a'
        )
        graphs_label.pack(pady=(10, 5))

        # Контейнер для графіків
        self.graphs_frame = tk.Frame(main_frame, bg='#2a2a2a')
        self.graphs_frame.pack(fill=tk.BOTH, expand=True, pady=10)

        # Створюємо 3 графіки (по одному для кожної колонки)
        self.figures = []
        self.axes = []
        self.canvases = []

        for i in range(3):
            graph_container = tk.Frame(self.graphs_frame, bg='#2a2a2a')
            graph_container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

            figure = Figure(figsize=(4, 2.5), facecolor='#2a2a2a')
            ax = figure.add_subplot(111)
            ax.set_facecolor('#1a1a1a')
            ax.set_xlabel('Time', color='#ffffff', fontsize=8)
            ax.set_ylabel('Qty', color='#ffffff', fontsize=8)
            ax.tick_params(colors='#ffffff', labelsize=7)
            ax.grid(True, alpha=0.3, color='#555555')
            ax.set_title(f'Column {i + 1}', color='#aaaaaa', fontsize=9)

            canvas = FigureCanvasTkAgg(figure, graph_container)
            canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

            self.figures.append(figure)
            self.axes.append(ax)
            self.canvases.append(canvas)

        # Кнопки управління
        button_frame = tk.Frame(main_frame, bg='#1a1a1a')
        button_frame.pack(pady=10)

        self.start_button = tk.Button(
            button_frame,
            text="▶ Start All",
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
            text="⏹ Stop All",
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

    def on_graph_product_changed(self, event=None):
        """Обробка зміни продукту для графіка"""
        # Видалено - тепер графіки показуються для всіх колонок
        pass

    def update_stats_for_product(self, product_id, qty, max_qty=None):
        """Оновлює статистику для конкретного продукту"""
        for idx, column in enumerate(self.columns):
            if column.product_id == product_id:
                column.update_stats(qty, max_qty)
                # Оновлюємо графік для цієї колонки
                self.update_graph_for_column(idx)

    def update_graph_for_column(self, column_idx):
        """Оновлює графік для конкретної колонки"""
        if column_idx >= len(self.columns):
            return

        column = self.columns[column_idx]
        ax = self.axes[column_idx]
        figure = self.figures[column_idx]
        canvas = self.canvases[column_idx]

        ax.clear()

        # Якщо немає даних або продукт не вибраний
        if not column.product_id or len(column.timestamps) == 0:
            ax.text(0.5, 0.5, 'No data',
                    ha='center', va='center',
                    transform=ax.transAxes,
                    color='#666666', fontsize=10)
            ax.set_title(f'Column {column_idx + 1}', color='#aaaaaa', fontsize=9)
            canvas.draw()
            return

        # Отримуємо назву продукту для заголовка
        product_name = PRODUCTS.get(column.product_id, {}).get('name', 'Unknown').split('\n')[0]
        ax.set_title(product_name, color='#ffffff', fontsize=9, pad=5)

        timestamps = column.timestamps
        quantities = column.quantities

        # Якщо точок багато (>15), групуємо їх для кращої читабельності
        max_points = 15
        if len(timestamps) > max_points:
            step = len(timestamps) // max_points
            if step < 1:
                step = 1

            selected_indices = list(range(0, len(timestamps), step))
            if selected_indices[-1] != len(timestamps) - 1:
                selected_indices.append(len(timestamps) - 1)

            plot_timestamps = [timestamps[i] for i in selected_indices]
            plot_quantities = [quantities[i] for i in selected_indices]
            time_labels = [t.strftime('%H:%M') for t in plot_timestamps]

            # Малюємо лінію через всі точки
            ax.plot(range(len(quantities)), quantities,
                    color='#2196F3', linewidth=1.5, alpha=0.5)
            # Маркери тільки на вибраних точках
            ax.plot(selected_indices, plot_quantities,
                    color='#2196F3', marker='o', markersize=4,
                    linestyle='', markeredgecolor='white', markeredgewidth=0.5)

            ax.fill_between(range(len(quantities)), quantities,
                            alpha=0.2, color='#2196F3')

            ax.set_xticks(selected_indices)
            ax.set_xticklabels(time_labels, rotation=45, ha='right')
        else:
            time_labels = [t.strftime('%H:%M') for t in timestamps]

            ax.plot(range(len(quantities)), quantities,
                    color='#2196F3', linewidth=1.5, marker='o', markersize=4,
                    markeredgecolor='white', markeredgewidth=0.5)
            ax.fill_between(range(len(quantities)), quantities,
                            alpha=0.2, color='#2196F3')

            ax.set_xticks(range(len(quantities)))
            ax.set_xticklabels(time_labels, rotation=45, ha='right')

        ax.set_facecolor('#1a1a1a')
        ax.set_xlabel('Time', color='#ffffff', fontsize=7)
        ax.set_ylabel('Qty', color='#ffffff', fontsize=7)
        ax.tick_params(colors='#ffffff', labelsize=6)
        ax.grid(True, alpha=0.3, color='#555555')

        figure.tight_layout()
        canvas.draw()

    def update_status(self, message, color='#aaaaaa'):
        """Оновлює статус"""
        self.status_label.configure(text=message, fg=color)

    def start_monitoring(self):
        """Запускає моніторинг"""
        if not self.monitoring:
            # Перевіряємо чи є хоча б один вибраний продукт
            active_products = [col.product_id for col in self.columns if col.product_id is not None]
            if not active_products:
                self.update_status("⚠️ Select at least one product", '#FF9800')
                return

            self.monitoring = True
            self.start_button.configure(state=tk.DISABLED)
            self.stop_button.configure(state=tk.NORMAL)

            # Скидаємо статистику для всіх колонок
            for column in self.columns:
                if column.product_id is not None:
                    column.reset_stats()

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
        """Основна логіка моніторингу"""
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
        previous_qtys = {}
        check_count = 0

        # Отримуємо список активних продуктів
        active_products = [col.product_id for col in self.columns if col.product_id is not None]

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

            # Отримуємо дані для кожного активного продукту
            for product_id in active_products:
                data = get_inventory(token, product_id)

                if data and data.get('totalInventory') is not None:
                    qty = data.get('totalInventory')
                    max_qty = data.get('maxQuantity')
                    previous_qtys[product_id] = log_inventory(data, previous_qtys.get(product_id), product_id)

                    # Оновлюємо GUI
                    self.root.after(0,
                                    lambda pid=product_id, q=qty, mq=max_qty: self.update_stats_for_product(pid, q, mq))
                    consecutive_failures = 0
                else:
                    # Оновлюємо токен при помилці
                    new_token = get_token_with_playwright()
                    if new_token:
                        token = new_token
                        save_token(token)

                        data = get_inventory(token, product_id)
                        if data and data.get('totalInventory') is not None:
                            qty = data.get('totalInventory')
                            max_qty = data.get('maxQuantity')
                            previous_qtys[product_id] = log_inventory(data, previous_qtys.get(product_id), product_id)
                            self.root.after(0,
                                            lambda pid=product_id, q=qty, mq=max_qty: self.update_stats_for_product(pid,
                                                                                                                    q,
                                                                                                                    mq))
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


# === ДОПОМІЖНІ ФУНКЦІЇ ===

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


def get_inventory(token, product_id):
    url = "https://mattel-checkout-prd.fly.dev/api/product-inventory"
    querystring = {"productIds": f"gid://shopify/Product/{product_id}"}
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
                # Парсимо variantMeta для отримання max_qty
                max_qty = 0
                try:
                    variant_meta = data[0].get('variantMeta', {}).get('value', '[]')
                    variant_data = json.loads(variant_meta)

                    # Проходимо по варіантах
                    for variant in variant_data:
                        variant_inventory = variant.get('variant_inventory', [])

                        # Шукаємо максимальну кількість з варіантів
                        # Пріоритет: Available → Backordered
                        for entry in variant_inventory:
                            if entry.get("variant_inventorystatus") == "Available":
                                qty = entry.get("variant_qty", 0) or 0
                                max_qty = int(qty)
                                break  # Available має найвищий пріоритет

                        # Якщо знайшли Available, виходимо
                        if max_qty > 0:
                            break

                        # Якщо немає Available, шукаємо Backordered
                        for entry in variant_inventory:
                            if entry.get("variant_inventorystatus") == "Backordered":
                                qty = entry.get("variant_qty", 0) or 0
                                max_qty = int(qty)
                                break

                        if max_qty > 0:
                            break
                except Exception as e:
                    print(f"Error parsing variantMeta: {e}")

                return {
                    'totalInventory': data[0].get('totalInventory'),
                    'variantMeta': data[0].get('variantMeta', {}).get('value', '[]'),
                    'maxQuantity': max_qty,
                    'timestamp': time.time(),
                    'product_id': product_id
                }
        return None
    except:
        return None


def init_csv():
    file_exists = os.path.exists(CSV_FILE)
    with open(CSV_FILE, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['time', 'product_id', 'product_name', 'qty', 'max_qty', 'change', 'variant_info'])


def log_inventory(data, previous_qty, product_id):
    timestamp = datetime.now().strftime('%d.%m.%Y %H:%M:%S')
    qty = data.get('totalInventory', 0)
    max_qty = data.get('maxQuantity', 0)
    product_name = PRODUCTS.get(product_id, {}).get('name', 'Unknown').replace('\n', ' ')

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
        writer.writerow([timestamp, product_id, product_name, qty, max_qty, change, variant_info])

    return qty


# === ЗАПУСК GUI ===
if __name__ == '__main__':
    root = tk.Tk()
    app = InventoryMonitorGUI(root)
    root.mainloop()