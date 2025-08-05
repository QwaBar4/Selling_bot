import os
from dotenv import load_dotenv

# Загружаем переменные из .env файла
load_dotenv()

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# URL вашего VPS сервера для webhook'ов (ваш публичный IPv4)
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # например: http://YOUR_IPV4:5000
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "5f4d8e3b2a1c9f7e6d5c4b3a2f1e0d9c8b7a6d5f4e3c2b1a0f9e8d7c6b5a4")

# --- Платежные системы ---
FREKASSA_SHOP_ID = os.getenv("FREKASSA_SHOP_ID")
FREKASSA_API_KEY = os.getenv("FREKASSA_API_KEY")
FREKASSA_SECRET_KEY1 = os.getenv("FREKASSA_SECRET_KEY1")
FREKASSA_SECRET_KEY2 = os.getenv("FREKASSA_SECRET_KEY2")

KRYPTOCLOUD_API_TOKEN = os.getenv("KRYPTOCLOUD_API_TOKEN")
KRYPTOCLOUD_SHOP_ID = os.getenv("KRYPTOCLOUD_SHOP_ID")

# --- Параметры подписки ---
SUBSCRIPTION_PRICE_RUB = 150  # Цена в рублях
SUBSCRIPTION_PRICE_USD = 2    # Цена в долларах
SUBSCRIPTION_DAYS = 30        # Длительность подписки в днях

# --- WireGuard ---
WG_SERVER_PUBLIC_KEY = os.getenv("WG_SERVER_PUBLIC_KEY")
WG_SERVER_ENDPOINT = os.getenv("WG_SERVER_ENDPOINT")  # e.g., "YOUR_IPV4:51820"
WG_CLIENT_DNS = "8.8.8.8, 1.1.1.1"
WG_TEMP_CONFIG_DURATION_MINUTES = 10
WG_SERVER_PORT = 51820 

# Сеть для клиентов. Убедитесь, что она соответствует настройкам сервера.
WG_CLIENT_NETWORK = "10.10.10.0/24"

# --- Админ ---
ADMIN_TELEGRAM_IDS_STR = os.getenv('ADMIN_TELEGRAM_IDS', '0')
try:
    # Парсим строку с ID админов, разделенных запятой
    ADMIN_TELEGRAM_IDS = [int(id.strip()) for id in ADMIN_TELEGRAM_IDS_STR.split(',') if id.strip()]
    if not ADMIN_TELEGRAM_IDS or ADMIN_TELEGRAM_IDS == [0]:
        raise ValueError("No valid admin IDs provided")
except (ValueError, AttributeError) as e:
    raise ValueError(f"Invalid ADMIN_TELEGRAM_IDS format: {ADMIN_TELEGRAM_IDS_STR}. Use comma-separated integers.")

# Основной админ (первый в списке) для критических уведомлений
MAIN_ADMIN_ID = ADMIN_TELEGRAM_IDS[0]

def get_admin_ids() -> list:
    """Возвращает список ID администраторов"""
    return ADMIN_TELEGRAM_IDS

# --- База данных и Логи ---
DB_NAME = "wg_bot.db"
LOG_FILE = "logs/bot.log"

# Freekassa allowed IPs
FREKASSA_ALLOWED_IPS = [
    '168.119.157.136',
    '168.119.60.227',
    '138.201.88.124',
    '178.154.197.79'
]
