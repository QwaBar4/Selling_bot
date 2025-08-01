import os
import logging
import asyncio
import threading
from flask import Flask, request, jsonify
from telegram import Bot
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ConversationHandler
from app import config, database as db, wireguard
from app.bot_logic import grant_subscription

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask приложение для webhook'ов
webhook_app = Flask(__name__)

# Telegram бот
bot = Bot(token=config.TELEGRAM_BOT_TOKEN)

@webhook_app.route('/payment_webhook', methods=['POST'])
def handle_payment_webhook():
    """Обработчик уведомлений о платежах от PythonAnywhere"""
    try:
        data = request.json
        logger.info(f"Received payment webhook: {data}")
        
        # Проверяем секрет
        if data.get('secret') != config.BOT_WEBHOOK_SECRET:
            logger.error("Invalid webhook secret")
            return jsonify({"error": "Invalid secret"}), 403
            
        user_id = data.get('user_id')
        order_id = data.get('order_id')
        status = data.get('status')
        
        if status == 'success' and user_id:
            logger.info(f"Processing successful payment for user {user_id}")
            
            # Выдаем подписку в отдельном потоке
            asyncio.run(grant_subscription(user_id, bot))
            
            return jsonify({"status": "OK", "message": "Subscription granted"})
        else:
            return jsonify({"error": "Invalid payment data"}), 400
            
    except Exception as e:
        logger.error(f"Webhook processing error: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500

@webhook_app.route('/health')
def health():
    return jsonify({"status": "healthy", "service": "telegram_bot"})

def run_webhook_server():
    """Запуск Flask сервера для webhook'ов"""
    logger.info("Starting webhook server on port 8000...")
    webhook_app.run(host='0.0.0.0', port=8000, debug=False)

def run_telegram_bot():
    """Запуск Telegram бота"""
    logger.info("Starting Telegram bot...")
    
    # Импортируем основную логику бота
    from main import main as run_bot
    run_bot()

if __name__ == '__main__':
    # Инициализируем БД
    db.init_db()
    
    # Запускаем Flask сервер в отдельном потоке
    webhook_thread = threading.Thread(target=run_webhook_server, daemon=True)
    webhook_thread.start()
    
    # Запускаем Telegram бота в основном потоке
    run_telegram_bot()
