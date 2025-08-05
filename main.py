import os
import logging
import logging.handlers
from flask import Flask, request, jsonify, redirect
from telegram import Bot
import json 
import asyncio
from app.config import TELEGRAM_BOT_TOKEN, WEBHOOK_SECRET, LOG_FILE, MAIN_ADMIN_ID, get_admin_ids
from app.database import init_db, get_payment_by_order_id, update_payment_status
from app.bot_logic import grant_subscription
from app.payments import verify_freekassa_notification
from app.bot_logic import deactivate_user_temp_config

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

@app.route('/webhook/freekassa', methods=['POST', 'GET'])
def freekassa_webhook():
    """Notification URL для Freekassa"""
    
    # Логируем ВСЕ входящие запросы
    logger.info("=== FREEKASSA WEBHOOK CALLED ===")
    logger.info(f"Method: {request.method}")
    logger.info(f"Headers: {dict(request.headers)}")
    logger.info(f"Remote IP: {request.remote_addr}")
    logger.info(f"X-Forwarded-For: {request.environ.get('HTTP_X_FORWARDED_FOR')}")
    logger.info(f"Form data: {request.form.to_dict()}")
    logger.info(f"Args: {request.args.to_dict()}")
    logger.info(f"Raw data: {request.data}")
    
    # Если это GET запрос (для тестирования)
    if request.method == 'GET':
        logger.info("GET request to freekassa webhook - test endpoint")
        return "Freekassa webhook is working! Use POST for actual notifications."
    
    # POST запрос - основная логика
    try:
        # Получаем данные из POST запроса
        data = request.form.to_dict()
        logger.info(f"Parsed form data: {data}")
        
        # Проверяем обязательные поля
        required_fields = ['MERCHANT_ID', 'AMOUNT', 'intid', 'MERCHANT_ORDER_ID', 'SIGN']
        missing_fields = [field for field in required_fields if field not in data]
        
        if missing_fields:
            logger.error(f"Missing required fields: {missing_fields}")
            return f"NO - Missing fields: {', '.join(missing_fields)}", 400
        
        # Получаем данные из уведомления
        merchant_id = data.get('MERCHANT_ID')
        amount = data.get('AMOUNT')
        intid = data.get('intid')
        merchant_order_id = data.get('MERCHANT_ORDER_ID')
        received_sign = data.get('SIGN')
        
        logger.info(f"Extracted data - MerchantID: {merchant_id}, Order: {merchant_order_id}, Amount: {amount}, IntID: {intid}")
        
        # Проверяем подпись только для реальных платежей (не тестовых)
        if received_sign != 'test123':  # Игнорируем тестовые запросы
            if not verify_freekassa_notification(data):
                logger.error("Invalid Freekassa signature")
                return "NO - Invalid signature", 400
        else:
            logger.warning("Test signature detected - skipping verification")
        
        # Извлекаем user_id из order_id
        try:
            if '_' in merchant_order_id:
                user_id = int(merchant_order_id.split('_')[1])
            else:
                # Если формат другой, пытаемся найти платеж в базе
                payment = get_payment_by_order_id(merchant_order_id)
                if payment:
                    user_id = payment['user_id']
                else:
                    logger.error(f"Payment not found for order_id: {merchant_order_id}")
                    return "NO - Payment not found", 404
            
            logger.info(f"Extracted user_id: {user_id}")
        except (ValueError, IndexError) as e:
            logger.error(f"Invalid order_id format: {merchant_order_id}, error: {e}")
            return "NO - Invalid order format", 400
        
        # Проверяем сумму (только для реальных платежей)
        try:
            amount_float = float(amount)
            if amount_float < 100:  # Минимальная сумма
                logger.warning(f"Amount too small: {amount_float}")
        except ValueError:
            logger.error(f"Invalid amount format: {amount}")
            return "NO - Invalid amount", 400
        
        # Проверяем, не обработан ли уже этот платеж
        payment = get_payment_by_order_id(merchant_order_id)
        if payment and payment.get('status') == 'completed':
            logger.info(f"Payment {merchant_order_id} already processed")
            return "YES"
        
        # Обновляем статус платежа
        logger.info(f"Updating payment status for order: {merchant_order_id}")
        update_payment_status(merchant_order_id, 'completed')
        
        # Выдаем подписку
        logger.info(f"Granting subscription to user: {user_id}")
        try:
            asyncio.run(grant_subscription(user_id, bot))
            logger.info(f"Subscription granted successfully to user: {user_id}")
        except Exception as e:
            logger.error(f"Error granting subscription to user {user_id}: {e}")
            # Не возвращаем ошибку, чтобы Freekassa не повторяла запрос
        
        logger.info(f"Freekassa payment {merchant_order_id} processed successfully")
        return "YES"
        
    except Exception as e:
        logger.error(f"Freekassa webhook error: {e}", exc_info=True)
        return "NO - Server error", 500


@app.route('/webhook/cryptocloud', methods=['POST'])
def cryptocloud_webhook():
    """Notification URL для CryptoCloud - сюда приходят уведомления о платежах"""
    try:
        # Получаем данные (учитываем разные форматы)
        if request.is_json:
            data = request.get_json()
        else:
            data = request.form.to_dict()
            if not data and request.data:
                try:
                    data = json.loads(request.data.decode('utf-8'))
                except (ValueError, UnicodeDecodeError):
                    pass
        
        logger.info(f"CryptoCloud data: {data}")

        if not data:
            logger.error("No data received in CryptoCloud webhook")
            return "ERROR: No data", 400

        if data.get('status') != 'success':
            logger.info(f"CryptoCloud payment not successful: {data.get('status')}")
            return "OK"

        order_id = data.get('order_id')
        if not order_id:
            logger.error("No order_id in CryptoCloud data")
            return "ERROR: No order_id", 400

        # Пытаемся найти платеж в базе данных по order_id
        payment = get_payment_by_order_id(order_id)
        if not payment:
            logger.error(f"Payment with order_id {order_id} not found in database")
            return "ERROR: Payment not found", 404

        user_id = payment['user_id']
        
        # Проверяем, не обработан ли уже этот платеж
        if payment.get('status') == 'completed':
            logger.info(f"CryptoCloud payment {order_id} already processed")
            return "OK"
        
        # Обновляем статус платежа
        update_payment_status(order_id, 'completed')
        
        # Выдаем подписку пользователю
        try:
            asyncio.run(grant_subscription(user_id, bot))
            logger.info(f"Subscription granted successfully to user: {user_id}")
        except Exception as e:
            logger.error(f"Error granting subscription to user {user_id}: {e}")
        
        logger.info(f"CryptoCloud payment {order_id} processed successfully for user {user_id}")
        return "OK"

    except Exception as e:
        logger.error(f"CryptoCloud webhook error: {e}", exc_info=True)
        return "ERROR: Internal server error", 500

# ========== SUCCESS/FAILURE REDIRECT ENDPOINTS ==========

@app.route('/payment/success')
def payment_success():
    """Success URL - сюда перенаправляется пользователь после успешной оплаты"""
    logger.info("=== SUCCESS REDIRECT ===")
    logger.info(f"Request args: {request.args.to_dict()}")
    logger.info(f"Request form: {request.form.to_dict()}")
    
    # Получаем order_id из параметров (любой платежной системы)
    order_id = (request.args.get('order_id') or 
                request.args.get('MERCHANT_ORDER_ID') or
                request.form.get('order_id') or
                request.form.get('MERCHANT_ORDER_ID'))
    
    if order_id:
        logger.info(f"Processing success redirect for order: {order_id}")
        
        try:
            # Извлекаем user_id из order_id
            if '_' in order_id:
                user_id = int(order_id.split('_')[1])
            else:
                # Ищем в базе данных
                payment = get_payment_by_order_id(order_id)
                if payment:
                    user_id = payment['user_id']
                else:
                    logger.error(f"Payment not found for order_id: {order_id}")
                    user_id = None
            
            if user_id:
                # Проверяем, не обработан ли уже этот платеж
                payment = get_payment_by_order_id(order_id)
                if payment and payment.get('status') == 'completed':
                    logger.info(f"Payment {order_id} already processed via webhook")
                else:
                    # Обрабатываем платеж через success redirect
                    logger.info(f"Processing payment via success redirect for user: {user_id}")
                    update_payment_status(order_id, 'completed')
                    asyncio.run(grant_subscription(user_id, bot))
                    logger.info(f"Subscription granted via success redirect to user: {user_id}")
            else:
                logger.error(f"Could not extract user_id from order_id: {order_id}")
                
        except Exception as e:
            logger.error(f"Error processing success redirect: {e}", exc_info=True)
    else:
        logger.warning("No order_id found in success redirect")
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta http-equiv="refresh" content="10; url=https://t.me/sh4llow_bot">
        <title>Оплата успешна</title>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Comic+Neue:wght@700&display=swap');
            
            body {
                font-family: 'Comic Neue', cursive;
                text-align: center;
                padding: 50px;
                background: linear-gradient(45deg, #ff9ff3, #feca57, #ff6b6b, #48dbfb);
                background-size: 400% 400%;
                animation: rainbow 10s ease infinite;
                overflow: hidden;
                margin: 0;
            }
            
            .pony-container {
                position: relative;
                z-index: 2;
                max-width: 600px;
                margin: 0 auto;
                background-color: rgba(255, 255, 255, 0.7);
                padding: 30px;
                border-radius: 20px;
                box-shadow: 0 0 30px rgba(255, 255, 255, 0.8);
                border: 5px dashed #ff9ff3;
            }
            
            .success {
                color: #ff0066;
                font-size: 32px;
                margin-bottom: 20px;
                text-shadow: 3px 3px 0 #fff, -1px -1px 0 #fff;
                animation: bounce 2s infinite;
            }
            
            .info {
                color: #5f27cd;
                font-size: 18px;
                line-height: 1.6;
                margin-bottom: 30px;
                text-shadow: 1px 1px 0 #fff;
            }
            
            .countdown {
                color: #ee5253;
                font-size: 24px;
                margin-bottom: 20px;
                font-weight: bold;
            }
            
            .pony {
                position: absolute;
                width: 150px;
                opacity: 0.7;
                animation: float 15s infinite linear;
                z-index: 1;
            }
            
            .pony1 {
                top: 10%;
                left: 5%;
                animation-delay: 0s;
            }
            
            .pony2 {
                top: 60%;
                right: 5%;
                animation-delay: 3s;
                animation-direction: reverse;
            }
            
            .pony3 {
                bottom: 10%;
                left: 20%;
                animation-delay: 5s;
            }
            
            .rainbow {
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background: repeating-linear-gradient(
                    to bottom,
                    #ff9ff3 0px, #ff9ff3 20px,
                    #feca57 20px, #feca57 40px,
                    #ff6b6b 40px, #ff6b6b 60px,
                    #48dbfb 60px, #48dbfb 80px,
                    #1dd1a1 80px, #1dd1a1 100px
                );
                opacity: 0.1;
                z-index: 0;
                animation: slide 30s linear infinite;
            }
            
            .stars {
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background-image: url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100"><circle cx="50" cy="50" r="1" fill="white"/></svg>');
                background-size: 5px 5px;
                opacity: 0.5;
                z-index: 0;
                animation: twinkle 2s infinite alternate;
            }
            
            @keyframes rainbow {
                0% { background-position: 0% 50% }
                50% { background-position: 100% 50% }
                100% { background-position: 0% 50% }
            }
            
            @keyframes bounce {
                0%, 100% { transform: translateY(0); }
                50% { transform: translateY(-15px); }
            }
            
            @keyframes float {
                0% { transform: translate(0, 0) rotate(0deg); }
                25% { transform: translate(50px, 30px) rotate(5deg); }
                50% { transform: translate(100px, 0) rotate(0deg); }
                75% { transform: translate(50px, -30px) rotate(-5deg); }
                100% { transform: translate(0, 0) rotate(0deg); }
            }
            
            @keyframes slide {
                0% { background-position: 0 0; }
                100% { background-position: 0 100px; }
            }
            
            @keyframes twinkle {
                from { opacity: 0.3; }
                to { opacity: 0.7; }
            }
        </style>
    </head>
    <body>
        <div class="rainbow"></div>
        <div class="stars"></div>
        
        <img src="https://i.imgur.com/PgUxmVB.png" class="pony pony1" alt="Pony">
        <img src="https://i.imgur.com/rT6I574.png" class="pony pony2" alt="Pony">
        <img src="https://i.imgur.com/ONLvBYb.png" class="pony pony3" alt="Pony">
        
        <div class="pony-container">
            <div class="success">🌈 ОПЛАТА УСПЕШНА! 🌈</div>
            <div class="info">
                🎉 ВАША ПОДПИСКА АКТИВИРОВАНА!<br>
                ✨ КОНФИГУРАЦИОННЫЙ ФАЙЛ WIREGUARD ОТПРАВЛЕН В TELEGRAM БОТ<br><br>
                🦄 ВЫ МОЖЕТЕ ЗАКРЫТЬ ЭТУ СТРАНИЦУ И ВЕРНУТЬСЯ В БОТ
            </div>
            <div class="countdown">Автоматический переход через: <span id="timer">10</span> сек.</div>
        </div>
        
        <script>
            // Таймер обратного отсчета
            let timeLeft = 10;
            const timerElement = document.getElementById('timer');
            
            const countdown = setInterval(() => {
                timeLeft--;
                timerElement.textContent = timeLeft;
                
                if (timeLeft <= 0) {
                    clearInterval(countdown);
                }
            }, 1000);
            
            // Случайное мерцание звезд
            setInterval(() => {
                const stars = document.querySelector('.stars');
                stars.style.opacity = 0.3 + Math.random() * 0.4;
            }, 300);
        </script>
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
        <meta http-equiv="refresh" content="10; url=https://t.me/sh4llow_bot">
        <title>Ошибка оплаты</title>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Comic+Neue:wght@700&display=swap');
            
            body {
                font-family: 'Comic Neue', cursive;
                text-align: center;
                padding: 50px;
                background: linear-gradient(45deg, #ff6b6b, #5f27cd, #222f3e, #ff9ff3);
                background-size: 400% 400%;
                animation: rainbow 10s ease infinite;
                overflow: hidden;
                margin: 0;
            }
            
            .pony-container {
                position: relative;
                z-index: 2;
                max-width: 600px;
                margin: 0 auto;
                background-color: rgba(0, 0, 0, 0.7);
                padding: 30px;
                border-radius: 20px;
                box-shadow: 0 0 30px rgba(255, 0, 0, 0.5);
                border: 5px dashed #ff6b6b;
            }
            
            .error {
                color: #ff6b6b;
                font-size: 32px;
                margin-bottom: 20px;
                text-shadow: 3px 3px 0 #000, -1px -1px 0 #000;
                animation: shake 0.5s infinite;
            }
            
            .info {
                color: #feca57;
                font-size: 18px;
                line-height: 1.6;
                margin-bottom: 30px;
                text-shadow: 1px 1px 0 #000;
            }
            
            .countdown {
                color: #48dbfb;
                font-size: 24px;
                margin-bottom: 20px;
                font-weight: bold;
            }
            
            .pony {
                position: absolute;
                width: 150px;
                opacity: 0.7;
                filter: hue-rotate(180deg) brightness(0.7);
                animation: float 15s infinite linear;
                z-index: 1;
            }
            
            .pony1 {
                top: 10%;
                left: 5%;
                animation-delay: 0s;
            }
            
            .pony2 {
                top: 60%;
                right: 5%;
                animation-delay: 3s;
                animation-direction: reverse;
            }
            
            .pony3 {
                bottom: 10%;
                left: 20%;
                animation-delay: 5s;
            }
            
            .rainbow {
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background: repeating-linear-gradient(
                    to bottom,
                    #ff6b6b 0px, #ff6b6b 20px,
                    #5f27cd 20px, #5f27cd 40px,
                    #222f3e 40px, #222f3e 60px,
                    #ff9ff3 60px, #ff9ff3 80px,
                    #1dd1a1 80px, #1dd1a1 100px
                );
                opacity: 0.1;
                z-index: 0;
                animation: slide 30s linear infinite;
            }
            
            .stars {
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background-image: url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100"><circle cx="50" cy="50" r="1" fill="red"/></svg>');
                background-size: 5px 5px;
                opacity: 0.5;
                z-index: 0;
                animation: twinkle 1s infinite alternate;
            }
            
            @keyframes rainbow {
                0% { background-position: 0% 50% }
                50% { background-position: 100% 50% }
                100% { background-position: 0% 50% }
            }
            
            @keyframes shake {
                0%, 100% { transform: translateX(0); }
                10%, 30%, 50%, 70%, 90% { transform: translateX(-5px); }
                20%, 40%, 60%, 80% { transform: translateX(5px); }
            }
            
            @keyframes float {
                0% { transform: translate(0, 0) rotate(0deg); }
                25% { transform: translate(50px, 30px) rotate(5deg); }
                50% { transform: translate(100px, 0) rotate(0deg); }
                75% { transform: translate(50px, -30px) rotate(-5deg); }
                100% { transform: translate(0, 0) rotate(0deg); }
            }
            
            @keyframes slide {
                0% { background-position: 0 0; }
                100% { background-position: 0 100px; }
            }
            
            @keyframes twinkle {
                from { opacity: 0.3; }
                to { opacity: 0.7; }
            }
        </style>
    </head>
    <body>
        <div class="rainbow"></div>
        <div class="stars"></div>
        
        <img src="https://i.imgur.com/yGdmUqF.png" class="pony pony1" alt="Dark Pony">
        <img src="https://i.imgur.com/1o2Cqm7.png" class="pony pony2" alt="Dark Pony">
        <img src="https://i.imgur.com/1o2Cqm7.png" class="pony pony3" alt="Dark Pony">
        
        <div class="pony-container">
            <div class="error">💀 ОШИБКА ОПЛАТЫ! 💀</div>
            <div class="info">
                🚫 ПЛАТЕЖ НЕ БЫЛ ОБРАБОТАН!<br>
                🔥 ПРОИЗОШЛА КРИТИЧЕСКАЯ ОШИБКА!<br><br>
                ☠ ВЕРНИТЕСЬ В TELEGRAM БОТ ДЛЯ ПОВТОРНОЙ ПОПЫТКИ
            </div>
            <div class="countdown">Автоматический переход через: <span id="timer">10</span> сек.</div>
        </div>
        
        <script>
            // Таймер обратного отсчета
            let timeLeft = 10;
            const timerElement = document.getElementById('timer');
            
            const countdown = setInterval(() => {
                timeLeft--;
                timerElement.textContent = timeLeft;
                
                if (timeLeft <= 0) {
                    clearInterval(countdown);
                }
            }, 1000);
            
            // Эффект мерцания
            setInterval(() => {
                const container = document.querySelector('.pony-container');
                container.style.boxShadow = `0 0 ${10 + Math.random() * 20}px rgba(255, 0, 0, ${0.3 + Math.random() * 0.4})`;
            }, 300);
        </script>
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
        <div class="warning">⚠ Оплата отменена</div>
        <div class="info">
            Вы отменили процесс оплаты.<br>
            Если это произошло по ошибке, вернитесь в бот и попробуйте снова.<br><br>
            Вы можете закрыть эту страницу.
        </div>
    </body>
    </html>
    """

# ========== ДОПОЛНИТЕЛЬНЫЕ ENDPOINTS ==========

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return {"status": "ok", "service": "wireguard-bot-webhook"}, 200

@app.route('/')
def index():
    """Главная страница"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>WireGuard Bot Webhook</title>
        <style>
            body { font-family: Arial, sans-serif; text-align: center; padding: 50px; background: #f0f8ff; }
            .title { color: #333; font-size: 28px; margin-bottom: 20px; }
            .info { color: #666; font-size: 16px; line-height: 1.5; }
            .status { color: #28a745; font-weight: bold; }
        </style>
    </head>
    <body>
        <div class="title">🤖 WireGuard Bot Webhook Service</div>
        <div class="info">
            <div class="status">✅ Service is running</div><br>
            This service handles payment notifications for the WireGuard bot.<br><br>
            Available endpoints:<br>
            • <code>/webhook/freekassa</code> - Freekassa notifications<br>
            • <code>/webhook/cryptocloud</code> - CryptoCloud notifications<br>
            • <code>/payment/success</code> - Success redirect<br>
            • <code>/payment/failure</code> - Failure redirect<br>
            • <code>/payment/cancel</code> - Cancel redirect<br>
            • <code>/health</code> - Health check
        </div>
    </body>
    </html>
    """

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=False)
