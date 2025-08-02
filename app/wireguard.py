import os
import re
import subprocess
import logging
import tempfile
import ipaddress
import base64
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Tuple, Optional, Dict
from app.config import WG_SERVER_PUBLIC_KEY, WG_SERVER_ENDPOINT, WG_CLIENT_DNS

# Константы
WG_CMD = "/usr/bin/wg"
SUDO_CMD = "/usr/bin/sudo"
WG_INTERFACE = "wg0"
WG_CONFIG = f"/etc/wireguard/{WG_INTERFACE}.conf"
TEMP_CONFIG_DIR = "/tmp/wg_configs"

logger = logging.getLogger(__name__)

def validate_public_key(key: str) -> bool:
    try:
        missing_padding = len(key) % 4
        if missing_padding:
            key += '=' * (4 - missing_padding)
        decoded = base64.b64decode(key)
        return len(decoded) == 32
    except:
        return False

def validate_ip(ip: str) -> bool:
    """Валидация IP адреса"""
    try:
        ipaddress.IPv4Address(ip)
        return True
    except:
        return False

def sanitize_wg_name(name: str) -> str:
    """Приводит имя к WireGuard совместимому формату без дефисов и подчеркиваний"""
    # Заменяем все небезопасные символы на пустую строку или буквы
    clean_name = re.sub(r'[^a-zA-Z0-9]', '', name)
    # Если имя стало пустым, генерируем случайное
    if not clean_name:
        clean_name = f"client{int(time.time())}"
    return clean_name

def init_wireguard():
    """Инициализация директорий"""
    Path(TEMP_CONFIG_DIR).mkdir(parents=True, exist_ok=True, mode=0o700)
    if not os.path.exists(WG_CONFIG):
        with open(WG_CONFIG, 'w') as f:
            f.write(f"[Interface]\nPrivateKey = ...\nAddress = 10.10.10.1/24\nListenPort = 51820\n")

def generate_client_keys() -> Tuple[Optional[str], Optional[str]]:
    """Генерирует ключи клиента"""
    try:
        if not os.path.exists(WG_CMD):
            logger.error(f"WireGuard not found: {WG_CMD}")
            return None, None
        
        private_key = subprocess.check_output(
            [WG_CMD, "genkey"], 
            text=True, 
            timeout=10,
            stderr=subprocess.PIPE
        ).strip()
        
        public_key = subprocess.check_output(
            [WG_CMD, "pubkey"],
            input=private_key,
            text=True,
            timeout=10,
            stderr=subprocess.PIPE
        ).strip()
        
        logger.info("Keys generated successfully")
        return private_key, public_key
        
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.error(f"Key generation failed: {e}")
        return None, None

def generate_wireguard_config(client_private_key: str, client_ip: str, port: int = None) -> str:
    """Генерирует конфиг клиента"""
    endpoint = WG_SERVER_ENDPOINT
    if port and ':' in endpoint:
        ip_part = endpoint.split(':')[0]
        endpoint = f"{ip_part}:{port}"
        
    server_public_key = WG_SERVER_PUBLIC_KEY
    
    return f"""[Interface]
PrivateKey = {client_private_key}
Address = {client_ip}/32
DNS = {WG_CLIENT_DNS}

[Peer]
PublicKey = {server_public_key}
AllowedIPs = 0.0.0.0/0
Endpoint = {endpoint}
PersistentKeepalive = 25
"""

def update_config_file(action: str, public_key: str, client_ip: str = None) -> bool:
    """Обновление конфига через временный файл с явным sudo"""
    tmp_path = None
    peers_path = None
    
    try:
        # Создаем временный файл для полного конфига
        with tempfile.NamedTemporaryFile(mode='w', dir='/tmp', delete=False, prefix='wgcfgfull') as tmp_file:
            tmp_path = tmp_file.name
            
            # Создаем временный файл только для peers (для syncconf)
            with tempfile.NamedTemporaryFile(mode='w', dir='/tmp', delete=False, prefix='wgcfgpeers') as peers_file:
                peers_path = peers_file.name
            
            # Получаем текущий конфиг
            current_cfg = subprocess.run(
                [SUDO_CMD, "cat", WG_CONFIG],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True
            ).stdout
            
            # Модифицируем конфиг
            if action == "add":
                new_content = current_cfg + f"\n[Peer]\nPublicKey = {public_key}\nAllowedIPs = {client_ip}/32\n"
            elif action == "remove":
                # Более точное удаление peer секции
                pattern = rf'\n\[Peer\]\nPublicKey = {re.escape(public_key)}\nAllowedIPs = [^\n]+\n'
                new_content = re.sub(pattern, '', current_cfg)
            
            # Записываем полный конфиг
            tmp_file.write(new_content)
            tmp_file.flush()
            
            # Извлекаем только секции [Peer] для syncconf
            peers_only = ""
            lines = new_content.split('\n')
            in_peer_section = False
            
            for line in lines:
                if line.strip() == '[Peer]':
                    in_peer_section = True
                    peers_only += line + '\n'
                elif line.strip().startswith('[') and line.strip() != '[Peer]':
                    in_peer_section = False
                elif in_peer_section:
                    peers_only += line + '\n'
            
            # Записываем файл только с peers
            with open(peers_path, 'w') as pf:
                pf.write(peers_only)
            
            # Копируем полный конфиг
            copy_result = subprocess.run(
                [SUDO_CMD, "cp", tmp_path, WG_CONFIG],
                stderr=subprocess.PIPE,
                text=True
            )
            
            if copy_result.returncode != 0:
                logger.error(f"Copy failed: {copy_result.stderr}")
                return False
                
            # Устанавливаем права
            chmod_result = subprocess.run(
                [SUDO_CMD, "chmod", "600", WG_CONFIG],
                stderr=subprocess.PIPE,
                text=True
            )
            
            if chmod_result.returncode != 0:
                logger.error(f"Chmod failed: {chmod_result.stderr}")
                return False
                
            # Применяем изменения используя файл только с peers
            sync_result = subprocess.run(
                [SUDO_CMD, "wg", "syncconf", WG_INTERFACE, peers_path],
                stderr=subprocess.PIPE,
                text=True
            )
            
            if sync_result.returncode != 0:
                logger.error(f"Sync failed: {sync_result.stderr}")
                return False
                
            return True
            
    except Exception as e:
        logger.error(f"Config update error: {str(e)}")
        return False
    finally:
        # Очищаем временные файлы
        for path in [tmp_path, peers_path]:
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except:
                    pass

def add_peer(public_key: str, client_ip: str) -> bool:
    """Добавляет пира с проверками"""
    if not validate_public_key(public_key) or not validate_ip(client_ip):
        logger.error("Invalid public key or IP")
        return False

    try:
        # Проверяем, не существует ли уже такой пир
        existing_peers = list_peers()
        if public_key in existing_peers:
            logger.warning(f"Peer {public_key[:8]}... already exists")
            return True

        # ВАЖНО: Проверяем, не занят ли IP
        if is_ip_in_use(client_ip):
            logger.error(f"IP {client_ip} is already in use")
            return False

        # Runtime добавление
        result = subprocess.run(
            [SUDO_CMD, WG_CMD, "set", WG_INTERFACE,
             "peer", public_key,
             "allowed-ips", f"{client_ip}/32"],
            check=True,
            timeout=10,
            capture_output=True,
            text=True
        )

        # Обновление конфига
        if update_config_file("add", public_key, client_ip):
            logger.info(f"Added peer {public_key[:8]}... with IP {client_ip}")
            return True

    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to add peer: {e.stderr}")
    except subprocess.TimeoutExpired as e:
        logger.error(f"Timeout adding peer: {e}")

    return False

def is_ip_in_use(ip: str) -> bool:
    """Проверяет, используется ли IP в runtime и конфиге"""
    try:
        # Проверка runtime состояния
        result = subprocess.run(
            [SUDO_CMD, WG_CMD, "show", WG_INTERFACE, "allowed-ips"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if f"{ip}/32" in result.stdout:
            return True
        
        # Проверка конфигурационного файла
        if os.path.exists(WG_CONFIG):
            with open(WG_CONFIG, 'r') as f:
                content = f.read()
                if f"AllowedIPs = {ip}/32" in content:
                    return True
                    
        return False
    except Exception as e:
        logger.error(f"IP check failed: {e}")
        return True  # В случае ошибки считаем IP занятым

# Алиас для совместимости с bot_logic.py
def add_peer_to_server(public_key: str, client_ip: str) -> bool:
    if not validate_public_key(public_key) or not validate_ip(client_ip):
        logger.error("Invalid public key or IP")
        return False

    try:
        # Проверяем, не существует ли уже такой пир
        existing_peers = list_peers()
        if public_key in existing_peers:
            logger.warning(f"Peer {public_key[:8]}... already exists")
            return True

        # Проверяем, не используется ли IP-адрес другим клиентом
        if is_ip_in_use(client_ip):
            logger.error(f"IP {client_ip} is already in use")
            return False

        # Runtime добавление
        result = subprocess.run(
            [SUDO_CMD, WG_CMD, "set", WG_INTERFACE,
             "peer", public_key,
             "allowed-ips", f"{client_ip}/32"],
            check=True,
            timeout=10,
            capture_output=True,
            text=True
        )

        # Обновление конфига
        if update_config_file("add", public_key, client_ip):
            logger.info(f"Added peer {public_key[:8]}... with IP {client_ip}")
            return True

    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to add peer: {e.stderr}")
    except subprocess.TimeoutExpired as e:
        logger.error(f"Timeout adding peer: {e}")

    return False


def remove_peer(public_key: str) -> bool:
    """Удаляет пира"""
    if not validate_public_key(public_key):
        logger.error("Invalid public key")
        return False
    
    try:
        # Runtime удаление
        subprocess.run(
            [SUDO_CMD, WG_CMD, "set", WG_INTERFACE, "peer", public_key, "remove"],
            check=True,
            timeout=10,
            capture_output=True
        )
        
        # Обновление конфига
        if update_config_file("remove", public_key):
            logger.info(f"Removed peer {public_key[:8]}...")
            return True
        
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.error(f"Failed to remove peer: {e}")
    
    return False

# Алиас для совместимости
def remove_peer_from_server(public_key: str) -> bool:
    """Алиас для remove_peer"""
    return remove_peer(public_key)

def create_client(client_ip: str) -> Optional[Dict[str, str]]:
    """Создает клиента"""
    if not validate_ip(client_ip):
        logger.error(f"Invalid client IP: {client_ip}")
        return None
    
    priv_key, pub_key = generate_client_keys()
    if not pub_key or not add_peer(pub_key, client_ip):
        return None
    
    return {
        'private_key': priv_key,
        'public_key': pub_key,
        'ip': client_ip,
        'config': generate_wireguard_config(priv_key, client_ip)
    }

def cleanup_config_file(config_path: str, delay: int = 3600):
    """Отложенная очистка файла"""
    def cleanup():
        time.sleep(delay)
        try:
            os.unlink(config_path)
            logger.info(f"Cleaned up: {config_path}")
        except:
            pass
    
    threading.Thread(target=cleanup, daemon=True).start()

def save_client_config(client_data: Dict[str, str], user_id: str) -> str:
    """Сохраняет конфиг клиента"""
    safe_name = sanitize_wg_name(f"client{user_id}")
    config_path = f"{TEMP_CONFIG_DIR}/{safe_name}.conf"
    
    try:
        with open(config_path, 'w') as f:
            f.write(client_data['config'])
        
        os.chmod(config_path, 0o600)  # Только владелец может читать
        cleanup_config_file(config_path)  # Автоочистка через час
        
        return config_path
    except Exception as e:
        logger.error(f"Failed to save config: {e}")
        raise

# Дополнительные функции для совместимости
def get_next_available_ip() -> Optional[str]:
    """Возвращает следующий свободный IP с двойной проверкой"""
    try:
        used_ips = set()
        
        # 1. Проверяем runtime состояние
        try:
            result = subprocess.run(
                [SUDO_CMD, WG_CMD, "show", WG_INTERFACE, "allowed-ips"],
                capture_output=True,
                text=True,
                timeout=10
            )
            for line in result.stdout.strip().split('\n'):
                if line.strip():
                    parts = line.split('\t')
                    if len(parts) >= 2:
                        for ip_range in parts[1].split(','):
                            ip = ip_range.strip().split('/')[0]
                            if validate_ip(ip) and ip.startswith('10.10.10.'):
                                used_ips.add(ip)
        except Exception as e:
            logger.warning(f"Runtime IP check failed: {e}")

        # 2. Проверяем конфигурационный файл
        if os.path.exists(WG_CONFIG):
            with open(WG_CONFIG, 'r') as f:
                for line in f:
                    match = re.search(r'AllowedIPs\s*=\s*(\d+\.\d+\.\d+\.\d+)', line)
                    if match and validate_ip(match.group(1)):
                        used_ips.add(match.group(1))

        logger.info(f"Used IPs: {sorted(used_ips)}")
        
        # Ищем свободный IP
        for i in range(2, 255):
            ip = f"10.10.10.{i}"
            if ip not in used_ips:
                # Двойная проверка перед возвратом
                if not is_ip_in_use(ip):
                    logger.info(f"Selected available IP: {ip}")
                    return ip
                else:
                    used_ips.add(ip)  # Добавляем в использованные, если занят
                    
        logger.error("No available IPs in range")
        return None
    except Exception as e:
        logger.error(f"IP allocation failed: {e}")
        return None

def create_temp_client() -> Optional[Dict[str, str]]:
    """Создает временного клиента с автоматическим IP"""
    next_ip = get_next_available_ip()
    if not next_ip:
        return None
    
    return create_client(next_ip)

def get_peer_info(public_key: str) -> Optional[Dict[str, str]]:
    """Получает информацию о пире"""
    try:
        result = subprocess.run(
            [SUDO_CMD, WG_CMD, "show", WG_INTERFACE, "dump"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        for line in result.stdout.strip().split('\n')[1:]:  # Пропускаем заголовок
            parts = line.split('\t')
            if len(parts) >= 4 and parts[0] == public_key:
                return {
                    'public_key': parts[0],
                    'endpoint': parts[2] if parts[2] != '(none)' else None,
                    'allowed_ips': parts[3],
                    'latest_handshake': parts[4] if len(parts) > 4 else None
                }
        
        return None
    except Exception as e:
        logger.error(f"Failed to get peer info: {e}")
        return None

def list_peers() -> list:
    """Возвращает список всех пиров"""
    try:
        result = subprocess.run(
            [SUDO_CMD, WG_CMD, "show", WG_INTERFACE, "peers"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        return [peer.strip() for peer in result.stdout.strip().split('\n') if peer.strip()]
    except Exception as e:
        logger.error(f"Failed to list peers: {e}")
        return []

def is_peer_active(public_key: str) -> bool:
    """Проверяет активность пира"""
    peer_info = get_peer_info(public_key)
    if not peer_info:
        return False
    
    # Проверяем, был ли недавний handshake (в течение 3 минут)
    if peer_info.get('latest_handshake'):
        try:
            handshake_time = int(peer_info['latest_handshake'])
            current_time = int(time.time())
            return (current_time - handshake_time) < 180  # 3 минуты
        except:
            pass
    
    return False

def diagnose_connection() -> Dict[str, any]:
    """Диагностика состояния WireGuard"""
    try:
        # Статус интерфейса
        wg_status = subprocess.run([SUDO_CMD, WG_CMD, "show", WG_INTERFACE], 
                                  capture_output=True, text=True, timeout=10)
        
        # Проверка iptables
        iptables_check = subprocess.run([SUDO_CMD, "iptables", "-L", "FORWARD", "-v"], 
                                       capture_output=True, text=True, timeout=10)
        
        # IP forwarding
        ip_forward = subprocess.run(["sysctl", "net.ipv4.ip_forward"], 
                                   capture_output=True, text=True, timeout=5)
        
        return {
            "wg_status": wg_status.stdout,
            "iptables": iptables_check.stdout,
            "ip_forward": ip_forward.stdout,
            "interface_up": WG_INTERFACE in subprocess.run(["ip", "link"], 
                                                          capture_output=True, text=True).stdout
        }
    except Exception as e:
        logger.error(f"Diagnosis failed: {e}")
        return {"error": str(e)}
