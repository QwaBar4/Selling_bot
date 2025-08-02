import os
import sqlite3
import ipaddress
import logging
import subprocess
import re
from datetime import datetime, timedelta
from typing import Optional
from app.config import DB_NAME, WG_CLIENT_NETWORK

logger = logging.getLogger(__name__)

# Добавляем константы для WireGuard
SUDO_CMD = "/usr/bin/sudo"
WG_CMD = "/usr/bin/wg"
WG_INTERFACE = "wg0"
WG_CONFIG = f"/etc/wireguard/{WG_INTERFACE}.conf"
DATABASE_FILE = DB_NAME

def validate_ip(ip: str) -> bool:
    """Валидация IP адреса"""
    try:
        ipaddress.IPv4Address(ip)
        return True
    except:
        return False

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
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS temp_configs (
            user_id INTEGER PRIMARY KEY,
            config_text TEXT NOT NULL,
            client_ip TEXT NOT NULL,
            public_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            is_active INTEGER DEFAULT 1
        )
        ''')
        
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

def get_all_used_ips():
    """Получает все используемые IP из всех источников"""
    used_ips = set()
    
    # 1. IP из основной таблицы users
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT client_ip FROM users WHERE client_ip IS NOT NULL")
        for row in cursor.fetchall():
            if row[0]:
                used_ips.add(row[0])
    
    # 2. IP из временных конфигов
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT client_ip FROM temp_configs WHERE is_active = 1")
        for row in cursor.fetchall():
            if row[0]:
                used_ips.add(row[0])
    
    # 3. IP из WireGuard runtime
    try:
        result = subprocess.run(
            [SUDO_CMD, WG_CMD, "show", WG_INTERFACE, "allowed-ips"],
            capture_output=True,
            text=True,
            timeout=10
        )
        for line in result.stdout.strip().split('\n'):
            if line.strip():
                parts = line.split('\t')
                if len(parts) >= 2:
                    for ip_range in parts[1].split(','):
                        ip = ip_range.strip().split('/')[0]
                        if validate_ip(ip) and ip.startswith('10.10.10.'):
                            used_ips.add(ip)
    except Exception as e:
        logger.warning(f"Runtime IP check failed: {e}")

    # 4. IP из конфигурационного файла
    if os.path.exists(WG_CONFIG):
        try:
            with open(WG_CONFIG, 'r') as f:
                for line in f:
                    match = re.search(r'AllowedIPs\s*=\s*(\d+\.\d+\.\d+\.\d+)', line)
                    if match and validate_ip(match.group(1)):
                        used_ips.add(match.group(1))
        except Exception as e:
            logger.warning(f"Config file check failed: {e}")

    return used_ips

def get_next_available_ip() -> Optional[str]:
    """Возвращает следующий свободный IP адрес"""
    try:
        used_ips = get_all_used_ips()
        logger.info(f"Найдены используемые IP: {sorted(used_ips)}")
        
        # Ищем свободный IP в диапазоне 10.10.10.2-254
        for i in range(2, 255):
            ip = f"10.10.10.{i}"
            if ip not in used_ips:
                logger.info(f"Найден свободный IP-адрес: {ip}")
                return ip
                
        logger.error("Нет доступных IP-адресов в диапазоне")
        return None
    except Exception as e:
        logger.error(f"Ошибка при поиске свободного IP: {e}")
        return None

def add_temp_config(user_id, config_text, client_ip, public_key):
    """Добавляет временный конфиг на 10 минут"""
    with sqlite3.connect(DATABASE_FILE) as conn:
        cursor = conn.cursor()
        
        expiry_time = datetime.now() + timedelta(minutes=10)
        
        cursor.execute('''
            INSERT OR REPLACE INTO temp_configs 
            (user_id, config_text, client_ip, public_key, created_at, expires_at, is_active)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        ''', (user_id, config_text, client_ip, public_key, datetime.now().isoformat(), expiry_time.isoformat()))
        
        conn.commit()
        logger.info(f"Временный конфиг добавлен для пользователя {user_id} с IP {client_ip}")

def get_temp_config(user_id):
    """Получает активный временный конфиг пользователя"""
    with sqlite3.connect(DATABASE_FILE) as conn:
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM temp_configs 
            WHERE user_id = ? AND is_active = 1 AND expires_at > ?
        ''', (user_id, datetime.now().isoformat()))
        
        result = cursor.fetchone()
        
        if result:
            return {
                'user_id': result[0],
                'config_text': result[1],
                'client_ip': result[2],
                'public_key': result[3],
                'created_at': result[4],
                'expires_at': result[5],
                'is_active': result[6]
            }
        return None

def deactivate_temp_config(user_id):
    """Деактивирует временный конфиг пользователя"""
    with sqlite3.connect(DATABASE_FILE) as conn:
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE temp_configs 
            SET is_active = 0 
            WHERE user_id = ? AND is_active = 1
        ''', (user_id,))
        
        conn.commit()
        logger.info(f"Временный конфиг деактивирован для пользователя {user_id}")

def cleanup_expired_temp_configs():
    """Очищает истекшие временные конфиги"""
    with sqlite3.connect(DATABASE_FILE) as conn:
        cursor = conn.cursor()
        
        # Получаем истекшие конфиги для удаления с сервера
        cursor.execute('''
            SELECT public_key FROM temp_configs 
            WHERE expires_at < ? AND is_active = 1
        ''', (datetime.now().isoformat(),))
        
        expired_configs = cursor.fetchall()
        
        # Деактивируем истекшие конфиги
        cursor.execute('''
            UPDATE temp_configs 
            SET is_active = 0 
            WHERE expires_at < ? AND is_active = 1
        ''', (datetime.now().isoformat(),))
        
        conn.commit()
        
        if expired_configs:
            logger.info(f"Деактивировано {len(expired_configs)} истекших временных конфигов")
        
        return [config[0] for config in expired_configs]
