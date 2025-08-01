import os
from dotenv import load_dotenv

# Загружаем переменные из .env файла
load_dotenv()

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# URL вашего сервера, куда будут приходить веб-хуки. Должен быть HTTPS.
# Пример: https://your_domain.com
WEBHOOK_URL = os.getenv("WEBHOOK_URL") 
# Секретный токен для защиты веб-хуков
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "YOUR_SUPER_SECRET_STRING")

# --- Платежные системы ---
FREKASSA_SHOP_ID = os.getenv("FREKASSA_SHOP_ID")
FREKASSA_API_KEY = os.getenv("FREKASSA_API_KEY")
FREKASSA_SECRET_KEY1 = os.getenv("FREKASSA_SECRET_KEY1")
FREKASSA_SECRET_KEY2 = os.getenv("FREKASSA_SECRET_KEY2")
# Ваш токен из документации CryptoCloud. Обязательно в формате "Token <ваш_токен>"
KRYPTOCLOUD_API_TOKEN = os.getenv("KRYPTOCLOUD_API_TOKEN") 
KRYPTOCLOUD_SHOP_ID = os.getenv("KRYPTOCLOUD_SHOP_ID")

# --- Параметры подписки ---
SUBSCRIPTION_PRICE_RUB = 150  # Цена в рублях
SUBSCRIPTION_PRICE_USD = 2    # Цена в долларах
SUBSCRIPTION_DAYS = 30        # Длительность подписки в днях

# --- WireGuard ---
WG_SERVER_PUBLIC_KEY = os.getenv("WG_SERVER_PUBLIC_KEY")
WG_SERVER_ENDPOINT = os.getenv("WG_SERVER_ENDPOINT") # e.g., "vpn.yourdomain.com:51820"
WG_CLIENT_DNS = "8.8.8.8, 8.8.4.4"
# Сеть для клиентов. Убедитесь, что она соответствует настройкам сервера.
WG_CLIENT_NETWORK = "10.10.10.0/24" 

# --- Админ ---
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID"))

# --- База данных и Логи ---
DB_NAME = "wg_bot.db"
LOG_FILE = "logs/bot.log"

# Freekassa allowed IPs
FREKASSA_ALLOWED_IPS = ['168.119.157.136', '168.119.60.227', '138.201.88.124', '178.154.197.79']
