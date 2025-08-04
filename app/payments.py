import uuid
import hashlib
import logging
import requests
from app.config import (
    FREKASSA_SHOP_ID, FREKASSA_API_KEY, FREKASSA_SECRET_KEY1, FREKASSA_SECRET_KEY2,
    KRYPTOCLOUD_API_TOKEN, KRYPTOCLOUD_SHOP_ID,
    SUBSCRIPTION_PRICE_RUB, SUBSCRIPTION_PRICE_USD,
    WEBHOOK_URL, WEBHOOK_SECRET
)
from app import database as db

logger = logging.getLogger(__name__)

def create_kryptocloud_payment(user_id):
    """Создает платеж через CryptoCloud (рабочая версия с улучшениями)"""
    order_id = f"{user_id}_{uuid.uuid4()}"
    url = "https://api.cryptocloud.plus/v2/invoice/create"
    
    headers = {
        "Authorization": KRYPTOCLOUD_API_TOKEN,
        "Content-Type": "application/json"  # Явно указываем Content-Type
    }
    
    payload = {
        "shop_id": KRYPTOCLOUD_SHOP_ID,
        "amount": SUBSCRIPTION_PRICE_USD,
        "order_id": order_id,
        "currency": "USD",  # Явно указываем валюту
        "webhook_url": f"{WEBHOOK_URL}/webhook/cryptocloud"
    }

    logger.info(f"Creating CryptoCloud payment for order {order_id}")
    
    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=10  # Добавляем таймаут
        )
        
        # Логируем ответ для диагностики
        logger.info(f"CryptoCloud API response: {response.status_code}, {response.text}")
        
        response.raise_for_status()  # Вызовет исключение для 4xx/5xx статусов
        
        data = response.json()
        if data.get("status") == "success":
            payment_url = data.get("result", {}).get("link")
            if payment_url:
                db.add_payment(
                    user_id=user_id,
                    amount=SUBSCRIPTION_PRICE_USD,
                    currency="USD",
                    payment_system="CryptoCloud",
                    order_id=order_id,
                    status="pending"
                )
                logger.info(f"CryptoCloud payment created: {order_id}")
                return payment_url, order_id
        
        logger.error(f"CryptoCloud API error response: {data}")
        return None, None

    except requests.exceptions.RequestException as e:
        logger.error(f"CryptoCloud request failed: {str(e)}")
        return None, None
    except Exception as e:
        logger.error(f"Unexpected error creating CryptoCloud payment: {str(e)}")
        return None, None

def create_freekassa_payment(user_id):
    """Создает платеж через Freekassa"""
    order_id = f"freekassa_{user_id}_{uuid.uuid4().hex[:8]}"
    
    # Сохраняем платеж в БД
    db.add_payment(
        user_id=user_id,
        amount=SUBSCRIPTION_PRICE_RUB,
        currency="RUB",
        payment_system="Freekassa",
        order_id=order_id,
        status="pending"
    )
    
    # Формируем подпись согласно документации
    # Формат: m:oa:secret_key:currency:o
    sign_str = f"{FREKASSA_SHOP_ID}:{SUBSCRIPTION_PRICE_RUB}:{FREKASSA_SECRET_KEY1}:RUB:{order_id}"
    sign = hashlib.md5(sign_str.encode()).hexdigest()
    
    # Правильный URL согласно документации
    payment_url = (
        f"https://pay.fk.money/"
        f"?m={FREKASSA_SHOP_ID}"
        f"&oa={SUBSCRIPTION_PRICE_RUB}"
        f"&currency=RUB"
        f"&o={order_id}"
        f"&s={sign}"
        f"&lang=ru"
    )
    
    logger.info(f"Freekassa payment created: {order_id}, URL: {payment_url}")
    return payment_url, order_id


def verify_freekassa_notification(data):
    """Проверяет подпись уведомления от Freekassa"""
    try:
        merchant_id = data.get('MERCHANT_ID')
        amount = data.get('AMOUNT')
        intid = data.get('intid')
        merchant_order_id = data.get('MERCHANT_ORDER_ID')
        received_sign = data.get('SIGN')
        
        logger.info(f"Signature check data:")
        logger.info(f"  MERCHANT_ID: {merchant_id}")
        logger.info(f"  AMOUNT: {amount}")
        logger.info(f"  intid: {intid}")
        logger.info(f"  MERCHANT_ORDER_ID: {merchant_order_id}")
        logger.info(f"  SIGN: {received_sign}")
        
        if not all([merchant_id, amount, intid, merchant_order_id, received_sign]):
            logger.error("Missing required fields in Freekassa notification")
            return False
        
        # Формируем подпись для проверки
        sign_str = f"{merchant_id}:{amount}:{FREKASSA_SECRET_KEY2}:{merchant_order_id}"
        expected_sign = hashlib.md5(sign_str.encode()).hexdigest()
        
        logger.info(f"Sign string: {sign_str}")
        logger.info(f"Expected sign: {expected_sign}")
        logger.info(f"Received sign: {received_sign}")
        
        return expected_sign.lower() == received_sign.lower()
        
    except Exception as e:
        logger.error(f"Error verifying Freekassa signature: {e}")
        return False

def check_payment_status(order_id, payment_system):
    """Проверяет статус платежа (общая функция)"""
    if payment_system == "Freekassa":
        # Логика проверки для Freekassa
        pass
    elif payment_system == "CryptoCloud":
        # Логика проверки для CryptoCloud
        pass
    return None
