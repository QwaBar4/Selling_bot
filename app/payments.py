import uuid
import hashlib
import hmac
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
    """Создает инвойс на оплату через CryptoCloud API v2."""
    order_id = f"{user_id}_{uuid.uuid4()}"  # Формат: user_id_uuid
    url = "https://api.cryptocloud.plus/v2/invoice/create"
    headers = {"Authorization": KRYPTOCLOUD_API_TOKEN}
    payload = {
        "shop_id": KRYPTOCLOUD_SHOP_ID,
        "amount": SUBSCRIPTION_PRICE_USD,
        "order_id": order_id
    }
    
    try:
        logger.info(f"Создание инвойса CryptoCloud для заказа {order_id}")
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
                logger.info(f"Инвойс CryptoCloud {order_id} успешно создан.")
                return payment_url, order_id
        
        logger.error(f"Ошибка ответа API CryptoCloud: {data}")
        return None, None

    except Exception as e:
        logger.error(f"Ошибка создания платежа CryptoCloud: {e}")
        return None, None


def create_freekassa_payment(user_id):
    payment_id = str(uuid.uuid4())  # Генерируем обычный UUID
    
    # Сохраняем в БД СНАЧАЛА
    db.add_payment(
        user_id=user_id,
        amount=SUBSCRIPTION_PRICE_RUB,
        currency="RUB",
        payment_system='Freekassa',
        payment_id=payment_id,
        status='pending'
    )
    
    # Затем формируем подпись
    sign_str = f"{FREKASSA_SHOP_ID}:{SUBSCRIPTION_PRICE_RUB}:{FREKASSA_SECRET_KEY1}:RUB:{payment_id}"
    sign = hashlib.md5(sign_str.encode()).hexdigest()
    
    payment_url = (
        f"https://pay.freekassa.ru/?m={FREKASSA_SHOP_ID}"
        f"&oa={SUBSCRIPTION_PRICE_RUB}"
        f"&o={payment_id}"
        f"&s={sign}"
        f"&currency=RUB"
    )
    return payment_url, payment_id

def verify_freekassa_notification(data):
    """Проверяет подпись уведомления от Freekassa"""
    sign_str = (
        f"{FREKASSA_SHOP_ID}:{data.get('AMOUNT')}:"
        f"{FREKASSA_SECRET_KEY2}:{data.get('MERCHANT_ORDER_ID')}"
    )
    sign = hashlib.md5(sign_str.encode()).hexdigest()
    return sign == data.get('SIGN')
