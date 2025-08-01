import logging
from telegram import Bot
from app import config, database as db, wireguard

logger = logging.getLogger(__name__)

async def grant_subscription(user_id: int, bot: Bot):
    """
    Выдает подписку пользователю: генерирует конфиг,
    добавляет пира на сервер и отправляет файл пользователю.
    """
    logger.info(f"Начинаем процесс выдачи подписки для пользователя {user_id}")
    try:
        client_ip = db.get_next_available_ip()
        if not client_ip:
            logger.error(f"Не удалось выделить IP-адрес для пользователя {user_id}.")
            await bot.send_message(config.ADMIN_TELEGRAM_ID, f"‼️ Закончились IP-адреса! Не удалось выдать подписку {user_id}.")
            await bot.send_message(user_id, "Произошла ошибка на сервере. Администратор уже уведомлен.")
            return

        client_private_key, client_public_key = wireguard.generate_client_keys()
        if not client_private_key:
            await bot.send_message(config.ADMIN_TELEGRAM_ID, f"‼️ Не удалось сгенерировать ключи для {user_id}.")
            return

        if wireguard.add_peer_to_server(client_public_key, client_ip):
            config_text = wireguard.generate_wireguard_config(client_private_key, client_ip)
            db.update_user_subscription(user_id, config.SUBSCRIPTION_DAYS, config_text, client_ip)

            await bot.send_message(
                user_id,
                "✅ Оплата прошла успешно! Ваша подписка активирована.\n\n"
                "Вот ваш персональный конфигурационный файл. "
                "Импортируйте его в приложение WireGuard на вашем устройстве."
            )
            await bot.send_document(
                user_id,
                document=bytes(config_text, 'utf-8'),
                filename=f"wg_{user_id}.conf"
            )
            logger.info(f"Конфиг успешно отправлен пользователю {user_id}.")
        else:
            logger.error(f"Не удалось добавить пира на сервер для пользователя {user_id}")
            await bot.send_message(config.ADMIN_TELEGRAM_ID, f"‼️ Ошибка WG: не удалось добавить пира для {user_id}.")
            await bot.send_message(user_id, "Произошла критическая ошибка. Администратор уведомлен.")

    except Exception as e:
        logger.exception(f"Исключение при выдаче подписки {user_id}: {e}")
        await bot.send_message(config.ADMIN_TELEGRAM_ID, f"‼️ Исключение при выдаче подписки {user_id}: {e}")
