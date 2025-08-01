import sqlite3
import ipaddress
import logging
from datetime import datetime, timedelta
from app.config import DB_NAME, WG_CLIENT_NETWORK

logger = logging.getLogger(__name__)

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        
        # Создаем таблицу users
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                subscription_end_date TEXT,
                wireguard_config TEXT,
                client_ip TEXT UNIQUE
            )
        """)
        
        # Создаем таблицу payments
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                order_id TEXT UNIQUE NOT NULL,
                amount REAL NOT NULL,
                currency TEXT,
                payment_status TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                payment_system TEXT NOT NULL,
                additional_data TEXT,
                FOREIGN KEY (user_id) REFERENCES users (telegram_id)
            )
        """)
        
        # Проверяем и добавляем недостающие колонки в существующую таблицу
        cursor.execute("PRAGMA table_info(payments)")
        existing_columns = [column[1] for column in cursor.fetchall()]
        
        required_columns = {
            'user_id': 'INTEGER',
            'order_id': 'TEXT',
            'payment_status': 'TEXT',
            'payment_system': 'TEXT',
            'additional_data': 'TEXT'
        }
        
        for column_name, column_type in required_columns.items():
            if column_name not in existing_columns:
                logger.info(f"Добавляем колонку {column_name} в таблицу payments")
                cursor.execute(f"ALTER TABLE payments ADD COLUMN {column_name} {column_type}")
        
        conn.commit()
    
    logger.info("База данных успешно инициализирована.")

def get_user(telegram_id):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        columns = [description[0] for description in cursor.description]
        user_data = cursor.fetchone()
        return dict(zip(columns, user_data)) if user_data else None

def add_user(telegram_id, username, first_name):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO users (telegram_id, username, first_name) VALUES (?, ?, ?)",
            (telegram_id, username, first_name)
        )
        conn.commit()

def update_user_subscription(telegram_id, days, wireguard_config, client_ip):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        user = get_user(telegram_id)
        
        # Рассчитываем новую дату окончания подписки
        current_end_date_str = user.get('subscription_end_date')
        start_date = datetime.now()
        
        if current_end_date_str:
            current_end_date = datetime.fromisoformat(current_end_date_str)
            if current_end_date > start_date:
                start_date = current_end_date
        
        new_end_date = start_date + timedelta(days=days)
        
        cursor.execute("""
            UPDATE users 
            SET subscription_end_date = ?, wireguard_config = ?, client_ip = ? 
            WHERE telegram_id = ?
        """, (new_end_date.isoformat(), wireguard_config, client_ip, telegram_id))
        
        conn.commit()
    
    logger.info(f"Подписка для пользователя {telegram_id} обновлена. Новая дата окончания: {new_end_date.isoformat()}")

def get_payment(order_id):
    """Получить платеж по order_id"""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, user_id, amount, currency, payment_system, order_id, 
                   payment_status as status, created_at
            FROM payments 
            WHERE order_id = ?
        """, (order_id,))
        
        columns = [description[0] for description in cursor.description]
        payment_data = cursor.fetchone()
        return dict(zip(columns, payment_data)) if payment_data else None

def add_payment(user_id, amount, currency, payment_system, order_id, status):
    """Добавить новый платеж"""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO payments (user_id, amount, currency, payment_system, order_id, payment_status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, amount, currency, payment_system, order_id, status))
        conn.commit()
    
    logger.info(f"Платеж {order_id} добавлен для пользователя {user_id}")

def update_payment_status(order_id, status):
    """Обновить статус платежа"""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE payments 
            SET payment_status = ? 
            WHERE order_id = ?
        """, (status, order_id))
        conn.commit()
    
    logger.info(f"Статус платежа {order_id} обновлен на '{status}'.")

def get_next_available_ip():
    """Находит следующий свободный IP-адрес в заданной подсети."""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT client_ip FROM users WHERE client_ip IS NOT NULL")
        used_ips = {row[0] for row in cursor.fetchall()}
    
    network = ipaddress.ip_network(WG_CLIENT_NETWORK)
    
    # Начинаем со второго адреса, т.к. первый часто шлюз
    for ip in list(network.hosts())[1:]:
        if str(ip) not in used_ips:
            logger.info(f"Найден свободный IP-адрес: {ip}")
            return str(ip)
    
    logger.error("Свободные IP-адреса в пуле закончились.")
    return None
