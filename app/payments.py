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

    # URL для оплаты
    payment_url = (
        f"https://pay.freekassa.ru/?m={FREKASSA_SHOP_ID}"
        f"&oa={SUBSCRIPTION_PRICE_RUB}"
        f"&o={order_id}"
        f"&s={sign}"
        f"&currency=RUB"
    )

    logger.info(f"Freekassa payment created: {order_id}")
    return payment_url, order_id

def create_kryptocloud_payment(user_id):
    """Создает платеж через CryptoCloud"""
    order_id = f"crypto_{user_id}_{uuid.uuid4().hex[:8]}"

    url = "https://api.cryptocloud.plus/v2/invoice/create"
    headers = {"Authorization": f"Token {KRYPTOCLOUD_API_TOKEN}"}
    payload = {
        "shop_id": KRYPTOCLOUD_SHOP_ID,
        "amount": SUBSCRIPTION_PRICE_USD,
        "order_id": order_id,
        "webhook_url": f"{WEBHOOK_URL}/webhook/cryptocloud"
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

        if data.get("status") == "success":
            payment_url = data.get("result", {}).get("link")
            if payment_url:
                # Сохраняем платеж в БД
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

        logger.error(f"CryptoCloud API error: {data}")
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
