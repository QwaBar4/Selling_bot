import sqlite3
import logging
from datetime import datetime, timedelta
from app.config import DB_NAME
import time 

logger = logging.getLogger(__name__)

def init_db():
    """Инициализация базы данных"""
    conn = sqlite3.connect(DB_NAME, timeout=30.0)
    cursor = conn.cursor()
    
    # Включаем WAL режим для лучшей конкурентности
    cursor.execute('PRAGMA journal_mode=WAL')
    cursor.execute('PRAGMA synchronous=NORMAL')
    cursor.execute('PRAGMA cache_size=10000')
    cursor.execute('PRAGMA temp_store=memory')
    
    # Таблица пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            first_name TEXT,
            subscription_end_date TEXT,
            subscription_active BOOLEAN DEFAULT 0,
            wireguard_config TEXT,
            client_ip TEXT,
            wg_easy_client_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица платежей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL,
            payment_system TEXT NOT NULL,
            order_id TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (telegram_id)
        )
    ''')
    
    # Таблица временных конфигов (один конфиг на пользователя)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS temp_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            config_text TEXT NOT NULL,
            client_ip TEXT NOT NULL,
            public_key TEXT NOT NULL,
            wg_easy_client_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT NOT NULL,
            is_active BOOLEAN DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES users (telegram_id)
        )
    ''')
    
    # Применяем миграции
    try:
        migrations = [
            ("ALTER TABLE payments ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'", 
             "SELECT status FROM payments LIMIT 1"),
            ("ALTER TABLE users ADD COLUMN subscription_active BOOLEAN DEFAULT 0",
             "SELECT subscription_active FROM users LIMIT 1"),
            ("ALTER TABLE users ADD COLUMN wg_easy_client_id TEXT",
             "SELECT wg_easy_client_id FROM users LIMIT 1"),
        ]
        
        for migration, check in migrations:
            try:
                cursor.execute(check)
            except sqlite3.OperationalError:
                cursor.execute(migration)
                logger.info(f"Applied migration: {migration}")
                
    except Exception as e:
        logger.error(f"Migration error: {e}")
        conn.rollback()
    else:
        conn.commit()
    
    conn.close()
    logger.info("Database initialized successfully")

def add_user(telegram_id: int, username: str, first_name: str):
    """Добавляет нового пользователя"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            INSERT OR IGNORE INTO users (telegram_id, username, first_name)
            VALUES (?, ?, ?)
        ''', (telegram_id, username, first_name))
        conn.commit()
        logger.info(f"User {telegram_id} added to database")
    except Exception as e:
        logger.error(f"Error adding user {telegram_id}: {e}")
    finally:
        conn.close()

def get_user(telegram_id: int) -> dict:
    """Получает данные пользователя"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT telegram_id, username, first_name, subscription_end_date, 
               wireguard_config, client_ip, wg_easy_client_id, subscription_active
        FROM users WHERE telegram_id = ?
    ''', (telegram_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            'telegram_id': row[0],
            'username': row[1],
            'first_name': row[2],
            'subscription_end_date': row[3],
            'wireguard_config': row[4],
            'client_ip': row[5],
            'wg_easy_client_id': row[6],
            'subscription_active': bool(row[7])
        }
    return None

def update_user_subscription(user_id: int, days: int, config_text: str, client_ip: str, wg_easy_client_id: str = None):
    """Обновляет подписку пользователя с поддержкой WG-Easy client ID"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    end_date = datetime.now() + timedelta(days=days)
    
    cursor.execute('''
        UPDATE users 
        SET subscription_end_date = ?, wireguard_config = ?, client_ip = ?, 
            wg_easy_client_id = ?, subscription_active = 1
        WHERE telegram_id = ?
    ''', (end_date.isoformat(), config_text, client_ip, wg_easy_client_id, user_id))
    
    conn.commit()
    conn.close()
    logger.info(f"Updated subscription for user {user_id} with WG-Easy client ID: {wg_easy_client_id}")

def add_temp_config(user_id: int, config_text: str, client_ip: str, public_key: str, wg_easy_client_id: str = None):
    """Добавляет временный конфиг с поддержкой WG-Easy client ID"""
    conn = sqlite3.connect(DB_NAME, timeout=30.0)
    cursor = conn.cursor()
    
    try:
        # Сначала удаляем существующий временный конфиг этого пользователя
        cursor.execute('DELETE FROM temp_configs WHERE user_id = ?', (user_id,))
        
        expires_at = datetime.now() + timedelta(minutes=10)
        created_at = datetime.now()
        
        # Добавляем новый временный конфиг
        cursor.execute('''
            INSERT INTO temp_configs (user_id, config_text, client_ip, public_key, wg_easy_client_id, created_at, expires_at, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        ''', (user_id, config_text, client_ip, public_key, wg_easy_client_id, created_at.isoformat(), expires_at.isoformat()))
        
        conn.commit()
        logger.info(f"Added temp config for user {user_id} with WG-Easy client ID: {wg_easy_client_id}")
        
    except Exception as e:
        logger.error(f"Error adding temp config for user {user_id}: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()

def remove_temp_config(user_id: int):
    """Удаляет временный конфиг пользователя"""
    conn = sqlite3.connect(DB_NAME, timeout=30.0)
    cursor = conn.cursor()
    
    try:
        cursor.execute('DELETE FROM temp_configs WHERE user_id = ?', (user_id,))
        conn.commit()
        logger.info(f"Removed temp config for user {user_id}")
        
    except Exception as e:
        logger.error(f"Error removing temp config for user {user_id}: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()

def get_temp_config(user_id: int) -> dict:
    """Получает временный конфиг пользователя"""
    conn = sqlite3.connect(DB_NAME, timeout=30.0)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT id, user_id, config_text, client_ip, public_key, wg_easy_client_id, created_at, expires_at, is_active
            FROM temp_configs 
            WHERE user_id = ?
        ''', (user_id,))
        
        row = cursor.fetchone()
        
        if row:
            return {
                'id': row[0],
                'user_id': row[1],
                'config_text': row[2],
                'client_ip': row[3],
                'public_key': row[4],
                'wg_easy_client_id': row[5],
                'created_at': row[6],
                'expires_at': row[7],
                'is_active': bool(row[8])
            }
        return None
        
    except Exception as e:
        logger.error(f"Error getting temp config for user {user_id}: {e}")
        return None
    finally:
        conn.close()

def deactivate_temp_config(user_id: int):
    """Деактивирует временный конфиг пользователя"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('UPDATE temp_configs SET is_active = 0 WHERE user_id = ? AND is_active = 1', (user_id,))
    
    conn.commit()
    conn.close()
    logger.info(f"Deactivated temp config for user {user_id}")

def get_expired_temp_configs() -> list:
    """Получает истекшие временные конфиги"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    current_time = datetime.now().isoformat()
    
    cursor.execute('''
        SELECT user_id, config_text, client_ip, public_key, wg_easy_client_id, expires_at
        FROM temp_configs 
        WHERE expires_at < ? AND is_active = 1
    ''', (current_time,))
    
    rows = cursor.fetchall()
    conn.close()
    
    return [
        {
            'user_id': row[0],
            'config_text': row[1],
            'client_ip': row[2],
            'public_key': row[3],
            'wg_easy_client_id': row[4],
            'expires_at': row[5]
        }
        for row in rows
    ]

def get_expired_subscriptions() -> list:
    """Получает пользователей с истекшими подписками"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    current_time = datetime.now().isoformat()
    
    cursor.execute('''
        SELECT telegram_id, username, first_name, subscription_end_date, 
               wireguard_config, client_ip, wg_easy_client_id
        FROM users 
        WHERE subscription_end_date < ? AND subscription_active = 1
    ''', (current_time,))
    
    rows = cursor.fetchall()
    conn.close()
    
    return [
        {
            'telegram_id': row[0],
            'username': row[1],
            'first_name': row[2],
            'subscription_end_date': row[3],
            'wireguard_config': row[4],
            'client_ip': row[5],
            'wg_easy_client_id': row[6]
        }
        for row in rows
    ]

def deactivate_user_subscription(user_id: int):
    """Полностью деактивирует подписку пользователя"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            UPDATE users 
            SET subscription_end_date = NULL,
                wireguard_config = NULL,
                client_ip = NULL,
                wg_easy_client_id = NULL
            WHERE telegram_id = ?
        ''', (user_id,))
        
        conn.commit()
        logger.info(f"Подписка пользователя {user_id} полностью деактивирована в БД")
        
    except Exception as e:
        logger.error(f"Ошибка при деактивации подписки пользователя {user_id}: {e}")
        conn.rollback()
    finally:
        conn.close()



def add_payment(user_id: int, amount: float, currency: str, payment_system: str, order_id: str, status: str = 'pending'):
    """Добавляет запись о платеже с обработкой ошибок"""
    max_retries = 3
    retry_delay = 0.5
    
    for attempt in range(max_retries):
        conn = None
        try:
            conn = sqlite3.connect(DB_NAME, timeout=30.0)
            cursor = conn.cursor()
            
            created_at = datetime.now()  # Явно устанавливаем время создания
            
            cursor.execute('''
                INSERT INTO payments 
                (user_id, amount, currency, payment_system, order_id, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, amount, currency, payment_system, order_id, status, created_at.isoformat()))
            
            conn.commit()
            logger.info(f"Payment record added for user {user_id}")
            return  # Успешно добавлено
            
        except sqlite3.IntegrityError as e:
            logger.error(f"Payment record already exists for order {order_id}: {e}")
            if conn:
                conn.rollback()
            return  # Не повторяем при IntegrityError
            
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e) and attempt < max_retries - 1:
                logger.warning(f"Database locked, retrying in {retry_delay}s (attempt {attempt + 1}/{max_retries})")
                if conn:
                    conn.close()
                time.sleep(retry_delay)
                retry_delay *= 2  # Экспоненциальная задержка
                continue
            else:
                logger.error(f"Database error adding payment record: {e}")
                if conn:
                    conn.rollback()
                raise
                
        except Exception as e:
            logger.error(f"Error adding payment record: {e}")
            if conn:
                conn.rollback()
            raise
            
        finally:
            if conn:
                conn.close()
    
    # Если дошли сюда, значит все попытки исчерпаны
    raise sqlite3.OperationalError("Failed to add payment after maximum retries")

def update_payment_status(order_id: str, status: str):
    """Обновляет статус платежа с обработкой ошибок"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            UPDATE payments 
            SET status = ? 
            WHERE order_id = ?
        ''', (status, order_id))
        conn.commit()
        logger.info(f"Payment status updated for order {order_id}")
    except Exception as e:
        logger.error(f"Error updating payment status: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()

def get_payment_by_order_id(order_id: str) -> dict:
    """Получает платеж по order_id"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT user_id, amount, currency, payment_system, order_id, status, created_at
        FROM payments WHERE order_id = ?
    ''', (order_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            'user_id': row[0],
            'amount': row[1],
            'currency': row[2],
            'payment_system': row[3],
            'order_id': row[4],
            'status': row[5],
            'created_at': row[6]
        }
    return None

def get_user_stats() -> dict:
    """Получает статистику пользователей"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Общее количество пользователей
    cursor.execute('SELECT COUNT(*) FROM users')
    total_users = cursor.fetchone()[0]
    
    # Активные подписки
    current_time = datetime.now().isoformat()
    cursor.execute('SELECT COUNT(*) FROM users WHERE subscription_end_date > ? AND subscription_active = 1', (current_time,))
    active_subscriptions = cursor.fetchone()[0]
    
    # Активные временные конфиги
    cursor.execute('SELECT COUNT(*) FROM temp_configs WHERE expires_at > ? AND is_active = 1', (current_time,))
    active_temp_configs = cursor.fetchone()[0]
    
    # Успешные платежи за последние 30 дней
    thirty_days_ago = (datetime.now() - timedelta(days=30)).isoformat()
    cursor.execute('SELECT COUNT(*) FROM payments WHERE status = "completed" AND created_at > ?', (thirty_days_ago,))
    recent_payments = cursor.fetchone()[0]
    
    conn.close()
    
    return {
        'total_users': total_users,
        'active_subscriptions': active_subscriptions,
        'active_temp_configs': active_temp_configs,
        'recent_payments': recent_payments
    }


def get_user_by_username(username: str):
    """Получает пользователя по username"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT telegram_id, username, first_name, subscription_end_date, 
               wireguard_config, client_ip, wg_easy_client_id, subscription_active
        FROM users 
        WHERE username = ? COLLATE NOCASE
    """, (username,))
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            'telegram_id': row[0],
            'username': row[1],
            'first_name': row[2],
            'subscription_end_date': row[3],
            'wireguard_config': row[4],
            'client_ip': row[5],
            'wg_easy_client_id': row[6],
            'subscription_active': bool(row[7])
        }
    return None

def get_all_users() -> list:
    """Получает всех пользователей из базы данных"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT telegram_id, username, first_name, subscription_end_date, 
               subscription_active, created_at
        FROM users
        ORDER BY created_at DESC
    ''')
    
    rows = cursor.fetchall()
    conn.close()
    
    return [
        {
            'telegram_id': row[0],
            'username': row[1],
            'first_name': row[2],
            'subscription_end_date': row[3],
            'subscription_active': bool(row[4]),
            'created_at': row[5]
        }
        for row in rows
    ]

def get_all_users_with_subscriptions():
    """Получает всех пользователей с подписками для админки"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT telegram_id, username, first_name, subscription_end_date, client_ip, subscription_active
        FROM users 
        WHERE subscription_end_date IS NOT NULL
        ORDER BY subscription_end_date DESC
    """)
    
    rows = cursor.fetchall()
    conn.close()
    
    users = []
    for row in rows:
        users.append({
            'telegram_id': row[0],
            'username': row[1] or 'Без username',
            'first_name': row[2] or 'Без имени',
            'subscription_end_date': row[3],
            'client_ip': row[4],
            'subscription_active': bool(row[5])
        })
    
    return users

def delete_user_subscription(telegram_id: int):
    """Удаляет подписку пользователя"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE users 
        SET subscription_end_date = NULL,
            wireguard_config = NULL,
            client_ip = NULL,
            wg_easy_client_id = NULL,
            subscription_active = 0
        WHERE telegram_id = ?
    """, (telegram_id,))
    
    conn.commit()
    conn.close()
    
    logger.info(f"Подписка пользователя {telegram_id} удалена из БД")
