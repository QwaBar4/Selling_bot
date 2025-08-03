import uuid
import hashlib
import hmac
import logging
import requests
from app.config import (
    FREKASSA_SHOP_ID, FREKASSA_API_KEY, FREKASSA_SECRET_KEY1, FREKASSA_SECRET_KEY2,
    KRYPTOCLOUD_API_TOKEN, KRYPTOCLOUD_SHOP_ID,
    SUBSCRIPTION_PRICE_RUB, SUBSCRIPTION_PRICE_USD,
    WEBHOOK_URL
)
from app import database as db

logger = logging.getLogger(__name__)

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

    # Формируем подпись
    sign_str = f"{FREKASSA_SHOP_ID}:{SUBSCRIPTION_PRICE_RUB}:{FREKASSA_SECRET_KEY1}:RUB:{order_id}"
    sign = hashlib.md5(sign_str.encode()).hexdigest()

    # URL для оплаты с добавлением success/failure URL
    payment_url = (
        f"https://pay.freekassa.ru/?m={FREKASSA_SHOP_ID}"
        f"&oa={SUBSCRIPTION_PRICE_RUB}"
        f"&o={order_id}"
        f"&s={sign}"
        f"&currency=RUB"
        f"&success_url={WEBHOOK_URL}/payment/success"
        f"&failure_url={WEBHOOK_URL}/payment/failure"
    )

    logger.info(f"Freekassa payment created: {order_id}")
    return payment_url, order_id

def create_kryptocloud_payment(user_id):
    """Создает платеж через CryptoCloud"""
    order_id = f"crypto_{user_id}_{uuid.uuid4().hex[:8]}"

    url = "https://api.cryptocloud.plus/v2/invoice/create"
    headers = {
        "Authorization": f"Token {KRYPTOCLOUD_API_TOKEN}",
        "Content-Type": "application/json"
    }
    
    # Убедимся, что amount передается как число, а не строка
    try:
        amount = float(SUBSCRIPTION_PRICE_USD)
    except (ValueError, TypeError):
        logger.error(f"Invalid SUBSCRIPTION_PRICE_USD: {SUBSCRIPTION_PRICE_USD}")
        return None, None
    
    payload = {
        "shop_id": KRYPTOCLOUD_SHOP_ID,
        "amount": amount,
        "order_id": order_id,
        "currency": "USD",  # Явно указываем валюту
        "webhook_url": f"{WEBHOOK_URL}/webhook/cryptocloud",
        "success_url": f"{WEBHOOK_URL}/payment/success",
        "cancel_url": f"{WEBHOOK_URL}/payment/cancel"
    }

    logger.info(f"CryptoCloud request payload: {payload}")
    logger.info(f"CryptoCloud headers: {headers}")

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        
        # Логируем детали ответа
        logger.info(f"CryptoCloud response status: {response.status_code}")
        logger.info(f"CryptoCloud response headers: {dict(response.headers)}")
        
        try:
            response_text = response.text
            logger.info(f"CryptoCloud response body: {response_text}")
        except:
            logger.warning("Could not log response body")

        if response.status_code == 200:
            try:
                data = response.json()
                logger.info(f"CryptoCloud parsed response: {data}")
                
                if data.get("status") == "success":
                    result = data.get("result", {})
                    payment_url = result.get("link") or result.get("url")
                    
                    if payment_url:
                        # Сохраняем платеж в БД
                        db.add_payment(
                            user_id=user_id,
                            amount=amount,
                            currency="USD",
                            payment_system="CryptoCloud",
                            order_id=order_id,
                            status="pending"
                        )
                        logger.info(f"CryptoCloud payment created: {order_id}")
                        return payment_url, order_id
                    else:
                        logger.error(f"No payment URL in CryptoCloud response: {result}")
                else:
                    logger.error(f"CryptoCloud API returned error status: {data}")
            except ValueError as e:
                logger.error(f"CryptoCloud response is not valid JSON: {e}")
        else:
            logger.error(f"CryptoCloud API HTTP error: {response.status_code}")
            try:
                error_data = response.json()
                logger.error(f"CryptoCloud error details: {error_data}")
            except:
                logger.error(f"CryptoCloud error response (raw): {response.text}")

        return None, None

    except requests.exceptions.Timeout:
        logger.error("CryptoCloud API timeout")
        return None, None
    except requests.exceptions.ConnectionError as e:
        logger.error(f"CryptoCloud connection error: {e}")
        return None, None
    except Exception as e:
        logger.error(f"CryptoCloud payment creation error: {e}")
        return None, None

def verify_freekassa_notification(data):
    """Проверяет подпись уведомления от Freekassa"""
    sign_str = (
        f"{FREKASSA_SHOP_ID}:{data.get('AMOUNT')}:"
        f"{FREKASSA_SECRET_KEY2}:{data.get('MERCHANT_ORDER_ID')}"
    )
    sign = hashlib.md5(sign_str.encode()).hexdigest()
    return sign == data.get('SIGN')

def test_cryptocloud_connection():
    """Тестирует подключение к CryptoCloud API"""
    url = "https://api.cryptocloud.plus/v2/invoice/create"
    headers = {
        "Authorization": f"Token {KRYPTOCLOUD_API_TOKEN}",
        "Content-Type": "application/json"
    }
    
    # Минимальный тестовый запрос
    test_payload = {
        "shop_id": KRYPTOCLOUD_SHOP_ID,
        "amount": 1.0,
        "order_id": f"test_{uuid.uuid4().hex[:8]}",
        "currency": "USD"
    }
    
    try:
        response = requests.post(url, headers=headers, json=test_payload, timeout=10)
        logger.info(f"CryptoCloud test response: {response.status_code} - {response.text}")
        return response.status_code, response.text
    except Exception as e:
        logger.error(f"CryptoCloud test error: {e}")
        return None, str(e)
