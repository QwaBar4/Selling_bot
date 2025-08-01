import os
import logging
import logging.handlers
from flask import Flask, request, jsonify, redirect
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

# ========== WEBHOOK ENDPOINTS (для уведомлений о платежах) ==========

@app.route('/webhook/freekassa', methods=['POST'])
def freekassa_webhook():
    """Notification URL для Freekassa - сюда приходят уведомления о платежах"""
    try:
        client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr)
        logger.info(f"Freekassa webhook from IP: {client_ip}")
        
        data = request.form.to_dict()
        logger.info(f"Freekassa data: {data}")

        if not verify_freekassa_notification(data):
            logger.error("Invalid Freekassa signature")
            return "NO", 400

        order_id = data.get('MERCHANT_ORDER_ID')
        if not order_id:
            logger.error("No MERCHANT_ORDER_ID in Freekassa data")
            return "NO", 400

        try:
            user_id = int(order_id.split('_')[1])
        except (ValueError, IndexError):
            logger.error(f"Invalid order_id format: {order_id}")
            return "NO", 400

        from app.database import update_payment_status
        update_payment_status(order_id, 'completed')
        asyncio.run(grant_subscription(user_id, bot))
        
        logger.info(f"Freekassa payment {order_id} processed successfully")
        return "YES"

    except Exception as e:
        logger.error(f"Freekassa webhook error: {e}", exc_info=True)
        return "NO", 500

@app.route('/webhook/cryptocloud', methods=['POST'])
def cryptocloud_webhook():
    """Notification URL для CryptoCloud - сюда приходят уведомления о платежах"""
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

        try:
            user_id = int(order_id.split('_')[1])
        except (ValueError, IndexError):
            logger.error(f"Invalid order_id format: {order_id}")
            return "ERROR", 400

        from app.database import update_payment_status
        update_payment_status(order_id, 'completed')
        asyncio.run(grant_subscription(user_id, bot))
        
        logger.info(f"CryptoCloud payment {order_id} processed successfully")
        return "OK"

    except Exception as e:
        logger.error(f"CryptoCloud webhook error: {e}", exc_info=True)
        return "ERROR", 500

# ========== SUCCESS/FAILURE REDIRECT ENDPOINTS ==========

@app.route('/payment/success')
def payment_success():
    """Success URL - сюда перенаправляется пользователь после успешной оплаты"""
    logger.info("User redirected to success page")
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Оплата успешна</title>
        <style>
            body { font-family: Arial, sans-serif; text-align: center; padding: 50px; background: #f0f8ff; }
            .success { color: #28a745; font-size: 24px; margin-bottom: 20px; }
            .info { color: #666; font-size: 16px; line-height: 1.5; }
        </style>
    </head>
    <body>
        <div class="success">✅ Оплата прошла успешно!</div>
        <div class="info">
            Ваша подписка активирована.<br>
            Конфигурационный файл WireGuard отправлен в Telegram бот.<br><br>
            Вы можете закрыть эту страницу и вернуться в бот.
        </div>
    </body>
    </html>
    """

@app.route('/payment/failure')
def payment_failure():
    """Failure URL - сюда перенаправляется пользователь при неудачной оплате"""
    logger.info("User redirected to failure page")
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Ошибка оплаты</title>
        <style>
            body { font-family: Arial, sans-serif; text-align: center; padding: 50px; background: #fff5f5; }
            .error { color: #dc3545; font-size: 24px; margin-bottom: 20px; }
            .info { color: #666; font-size: 16px; line-height: 1.5; }
        </style>
    </head>
    <body>
        <div class="error">❌ Оплата не прошла</div>
        <div class="info">
            Произошла ошибка при обработке платежа.<br>
            Попробуйте еще раз или обратитесь в поддержку.<br><br>
            Вернитесь в Telegram бот для повторной попытки.
        </div>
    </body>
    </html>
    """

@app.route('/payment/cancel')
def payment_cancel():
    """Cancel URL - сюда перенаправляется пользователь при отмене оплаты"""
    logger.info("User cancelled payment")
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Оплата отменена</title>
        <style>
            body { font-family: Arial, sans-serif; text-align: center; padding: 50px; background: #fffbf0; }
            .warning { color: #ffc107; font-size: 24px; margin-bottom: 20px; }
            .info { color: #666; font-size: 16px; line-height: 1.5; }
        </style>
    </head>
    <body>
        <div class="warning">⚠️ Оплата отменена</div>
        <div class="info">
            Вы отменили процесс оплаты.<br>
            Если это произошло по ошибке, вернитесь в бот и попробуйте снова.<br><br>
            Вы можете закрыть эту страницу.
        </div>
    </body>
    </html>
    """

# ========== UTILITY ENDPOINTS ==========

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
        "status": "running",
        "endpoints": {
            "freekassa_webhook": "/webhook/freekassa",
            "cryptocloud_webhook": "/webhook/cryptocloud",
            "success_url": "/payment/success",
            "failure_url": "/payment/failure",
            "cancel_url": "/payment/cancel"
        }
    })

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=False)
