import subprocess
import logging
from app.config import WG_SERVER_PUBLIC_KEY, WG_SERVER_ENDPOINT, WG_CLIENT_DNS

logger = logging.getLogger(__name__)

def generate_wireguard_config(client_private_key, client_address):
    """Генерирует текстовую конфигурацию для клиента WireGuard."""
    return f"""[Interface]
PrivateKey = {client_private_key}
Address = {client_address}/32
DNS = {WG_CLIENT_DNS}

[Peer]
PublicKey = {WG_SERVER_PUBLIC_KEY}
AllowedIPs = 0.0.0.0/0
Endpoint = {WG_SERVER_ENDPOINT}
PersistentKeepalive = 25
"""

def generate_client_keys():
    """Генерирует приватный и публичный ключи для клиента."""
    try:
        private_key = subprocess.check_output("wg genkey", shell=True, text=True).strip()
        public_key = subprocess.check_output(f"echo '{private_key}' | wg pubkey", shell=True, text=True).strip()
        logger.info("Ключи для клиента WireGuard успешно сгенерированы.")
        return private_key, public_key
    except subprocess.CalledProcessError as e:
        logger.error(f"Ошибка при генерации ключей WireGuard: {e}")
        return None, None

def add_peer_to_server(client_public_key, client_address):
    """Добавляет нового пира на сервер WireGuard."""
    try:
        # Добавляем пира
        command = f"sudo wg set wg0 peer {client_public_key} allowed-ips {client_address}/32"
        subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        
        # Сохраняем конфигурацию
        subprocess.run("sudo wg-quick save wg0", shell=True, check=True, capture_output=True, text=True)
        
        logger.info(f"Пир {client_public_key} с IP {client_address} добавлен на сервер.")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Ошибка при добавлении пира WireGuard: {e.stderr}")
        return False
