import os
import logging
import logging.handlers
from flask import Flask, request, jsonify
from telegram import Bot
import asyncio
from app.config import TELEGRAM_BOT_TOKEN, WEBHOOK_SECRET, LOG_FILE
from app.database import init_db
from app.bot_logic import grant_subscription
from app.payments import verify_freekassa_notification

# Настройка логирования
log_dir = "logs"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

log_handler = logging.handlers.RotatingFileHandler(
    LOG_FILE,
    maxBytes=10*1024*1024,
    backupCount=5
)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[log_handler, logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
bot = Bot(token=TELEGRAM_BOT_TOKEN)

@app.route('/webhook/freekassa', methods=['POST'])
def freekassa_webhook():
    """Обработка уведомлений от Freekassa"""
    try:
        # Проверяем IP отправителя
        client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr)
        logger.info(f"Freekassa webhook from IP: {client_ip}")
        
        data = request.form.to_dict()
        logger.info(f"Freekassa data: {data}")

        # Проверяем подпись
        if not verify_freekassa_notification(data):
            logger.error("Invalid Freekassa signature")
            return "NO", 400

        order_id = data.get('MERCHANT_ORDER_ID')
        if not order_id:
            logger.error("No MERCHANT_ORDER_ID in Freekassa data")
            return "NO", 400

        # Извлекаем user_id из order_id (формат: freekassa_USER_ID_HASH)
        try:
            user_id = int(order_id.split('_')[1])
        except (ValueError, IndexError):
            logger.error(f"Invalid order_id format: {order_id}")
            return "NO", 400

        # Обновляем статус платежа в БД
        from app.database import update_payment_status
        update_payment_status(order_id, 'completed')

        # Выдаем подписку пользователю
        asyncio.run(grant_subscription(user_id, bot))
        
        logger.info(f"Freekassa payment {order_id} processed successfully")
        return "YES"

    except Exception as e:
        logger.error(f"Freekassa webhook error: {e}", exc_info=True)
        return "NO", 500

@app.route('/webhook/cryptocloud', methods=['POST'])
def cryptocloud_webhook():
    """Обработка уведомлений от CryptoCloud"""
    try:
        data = request.get_json() or request.form.to_dict()
        logger.info(f"CryptoCloud data: {data}")

        if data.get('status') != 'success':
            logger.info(f"CryptoCloud payment not successful: {data.get('status')}")
            return "OK"

        order_id = data.get('order_id')
        if not order_id:
            logger.error("No order_id in CryptoCloud data")
            return "ERROR", 400

        # Извлекаем user_id из order_id (формат: crypto_USER_ID_HASH)
        try:
            user_id = int(order_id.split('_')[1])
        except (ValueError, IndexError):
            logger.error(f"Invalid order_id format: {order_id}")
            return "ERROR", 400

        # Обновляем статус платежа в БД
        from app.database import update_payment_status
        update_payment_status(order_id, 'completed')

        # Выдаем подписку пользователю
        asyncio.run(grant_subscription(user_id, bot))
        
        logger.info(f"CryptoCloud payment {order_id} processed successfully")
        return "OK"

    except Exception as e:
        logger.error(f"CryptoCloud webhook error: {e}", exc_info=True)
        return "ERROR", 500

@app.route('/health')
def health():
    """Проверка здоровья сервиса"""
    return jsonify({
        "status": "healthy",
        "service": "wireguard_bot_webhook_server"
    })

@app.route('/')
def index():
    """Главная страница"""
    return jsonify({
        "service": "WireGuard Bot Webhook Server",
        "status": "running"
    })

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=False)
