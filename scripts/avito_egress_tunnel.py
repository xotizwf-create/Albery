#!/usr/bin/env python3
"""Обратный SOCKS5-туннель: сервер ходит в Авито через ЭТОТ компьютер.

Зачем. Авито не отдаёт поисковую выдачу датацентровому адресу прода: и обычный клиент, и
настоящий не-headless Chrome получают оттуда «Доступ ограничен: проблема с IP» (замер
18–19.08.2026). С домашнего адреса владельца те же страницы открываются нормально. Пока
не куплен резидентный прокси, выходом работает машина владельца.

Почему обратный, а не обычный прокси. Домашний компьютер сидит за NAT: сервер до него не
достучится. Поэтому соединение устанавливает компьютер — он подключается к серверу и просит
слушать порт НА СЕРВЕРЕ (127.0.0.1:1080). Всё, что сервер отправит в этот порт, приезжает
сюда по уже открытому каналу и уходит в интернет с домашнего адреса. Ни белого IP, ни правил
на роутере не нужно.

Порт на сервере поднимается ТОЛЬКО на петле: снаружи в него попасть нельзя, иначе мы бы
открыли чужим людям открытый прокси в свою квартиру.

Запуск (на компьютере владельца):

    python scripts/avito_egress_tunnel.py --env "<путь к .env со SSH-паролем>"

Проверка (на сервере):

    curl --socks5-hostname 127.0.0.1:1080 https://api.ipify.org   # ждём домашний адрес

Остановка — Ctrl+C. Туннель живёт, пока работает этот процесс: выключенный компьютер
означает выключенный канал Авито, и воркер увидит это как недоступность выхода.
"""
from __future__ import annotations

import argparse
import logging
import os
import select
import socket
import sys
import threading
import time

try:
    import paramiko
except ImportError:  # pragma: no cover - подсказка вместо трассировки
    print("Нужен paramiko: python -m pip install paramiko", file=sys.stderr)
    raise SystemExit(2) from None

SOCKS_VERSION = 5
NO_AUTH = 0x00
CMD_CONNECT = 0x01
ATYP_IPV4 = 0x01
ATYP_DOMAIN = 0x03
ATYP_IPV6 = 0x04
REPLY_OK = 0x00
REPLY_HOST_UNREACHABLE = 0x04
REPLY_CMD_NOT_SUPPORTED = 0x07

BUFFER = 32768
CONNECT_TIMEOUT_S = 20
IDLE_TIMEOUT_S = 300


def load_env_file(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _recv_exactly(channel, count: int) -> bytes:
    chunks = b""
    while len(chunks) < count:
        part = channel.recv(count - len(chunks))
        if not part:
            raise ConnectionError("канал закрылся посреди приветствия SOCKS")
        chunks += part
    return chunks


def _read_target(channel) -> tuple[str, int]:
    """Разбирает запрос SOCKS5 CONNECT и возвращает (хост, порт)."""
    version, command, _reserved, address_type = _recv_exactly(channel, 4)
    if version != SOCKS_VERSION:
        raise ConnectionError(f"не SOCKS5: версия {version}")
    if command != CMD_CONNECT:
        channel.sendall(bytes([SOCKS_VERSION, REPLY_CMD_NOT_SUPPORTED, 0, ATYP_IPV4, 0, 0, 0, 0, 0, 0]))
        raise ConnectionError(f"поддерживается только CONNECT, пришло {command}")
    if address_type == ATYP_IPV4:
        host = socket.inet_ntoa(_recv_exactly(channel, 4))
    elif address_type == ATYP_DOMAIN:
        length = _recv_exactly(channel, 1)[0]
        host = _recv_exactly(channel, length).decode("idna")
    elif address_type == ATYP_IPV6:
        host = socket.inet_ntop(socket.AF_INET6, _recv_exactly(channel, 16))
    else:
        raise ConnectionError(f"неизвестный тип адреса {address_type}")
    port = int.from_bytes(_recv_exactly(channel, 2), "big")
    return host, port


_TUNNEL_LOCAL_IP: str | None = None
_DIRECT_ADDRESS_CACHE: dict[str, tuple[str, float]] = {}
_DIRECT_CACHE_TTL_S = 600


def _detect_tunnel_local_ip() -> str | None:
    """Каким локальным адресом машина выходит в интернет ПО УМОЛЧАНИЮ.

    На машине-выходе поднят VPN, и обычный трафик уходит его адресом. Зная этот адрес, мы
    отличаем «пошло в туннель» от «пошло напрямую», не зная ничего про конкретный VPN.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("1.1.1.1", 443))  # UDP-connect: пакетов не шлёт, только выбирает маршрут
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


def _connect_preferring_direct(host: str, port: int):
    """Соединение с предпочтением адреса, который идёт МИМО VPN.

    Авито отдаёт три A-записи, а исключение в VPN-клиенте прописывается на тот адрес,
    который клиент разрезолвил сам — остальные продолжают уходить в туннель, и с них
    прилетает «Доступ ограничен: проблема с IP». Поэтому здесь мы пробуем кандидатов и
    оставляем того, чьё соединение НЕ вышло адресом туннеля. Выбор кэшируется на 10 минут
    и пересматривается сам, если правила выхода поменялись.
    """
    cached = _DIRECT_ADDRESS_CACHE.get(host)
    if cached and time.time() - cached[1] < _DIRECT_CACHE_TTL_S:
        return socket.create_connection((cached[0], port), timeout=CONNECT_TIMEOUT_S)

    try:
        candidates = [info[4][0] for info in socket.getaddrinfo(host, port, socket.AF_INET,
                                                                socket.SOCK_STREAM)]
    except socket.gaierror:
        candidates = []
    seen: list[str] = []
    for address in candidates:
        if address not in seen:
            seen.append(address)
    if not seen:
        return socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_S)

    fallback = None
    for address in seen:
        try:
            sock = socket.create_connection((address, port), timeout=CONNECT_TIMEOUT_S)
        except OSError:
            continue
        local_ip = sock.getsockname()[0]
        if _TUNNEL_LOCAL_IP is None or local_ip != _TUNNEL_LOCAL_IP:
            _DIRECT_ADDRESS_CACHE[host] = (address, time.time())
            if len(seen) > 1:
                logging.info("%s: выбран %s (напрямую, локальный %s)", host, address, local_ip)
            return sock
        if fallback is None:
            fallback = sock  # держим на случай, если прямого не найдётся вовсе
        else:
            sock.close()
    if fallback is not None:
        logging.warning("%s: прямого маршрута нет, идём через туннель — Авито может ответить "
                        "блокировкой по IP", host)
        return fallback
    raise OSError(f"не удалось соединиться ни с одним адресом {host}")


def _pump(channel, sock) -> None:
    """Перекачивает байты в обе стороны, пока одна из сторон не закроется."""
    try:
        while True:
            readable, _, _ = select.select([channel, sock], [], [], IDLE_TIMEOUT_S)
            if not readable:
                return
            if channel in readable:
                data = channel.recv(BUFFER)
                if not data:
                    return
                sock.sendall(data)
            if sock in readable:
                data = sock.recv(BUFFER)
                if not data:
                    return
                channel.sendall(data)
    except (OSError, EOFError):
        return


def handle_channel(channel, origin) -> None:
    """Мы — SOCKS-сервер для сервера: он говорит, куда идти, мы идём отсюда."""
    sock = None
    try:
        version, method_count = _recv_exactly(channel, 2)
        methods = _recv_exactly(channel, method_count)
        if version != SOCKS_VERSION or NO_AUTH not in methods:
            channel.sendall(bytes([SOCKS_VERSION, 0xFF]))
            return
        channel.sendall(bytes([SOCKS_VERSION, NO_AUTH]))

        host, port = _read_target(channel)
        try:
            sock = _connect_preferring_direct(host, port)
        except OSError as exc:
            logging.warning("не дозвонились до %s:%s (%s)", host, port, exc.__class__.__name__)
            channel.sendall(bytes([SOCKS_VERSION, REPLY_HOST_UNREACHABLE, 0, ATYP_IPV4, 0, 0, 0, 0, 0, 0]))
            return
        sock.settimeout(None)
        bound_ip, bound_port = sock.getsockname()[:2]
        try:
            packed = socket.inet_aton(bound_ip)
        except OSError:
            packed = b"\x00\x00\x00\x00"
        channel.sendall(bytes([SOCKS_VERSION, REPLY_OK, 0, ATYP_IPV4]) + packed
                        + bound_port.to_bytes(2, "big"))
        logging.info("%s → %s:%s", origin, host, port)
        _pump(channel, sock)
    except (ConnectionError, OSError) as exc:
        logging.debug("канал закрыт: %s", exc)
    finally:
        try:
            channel.close()
        except Exception:  # noqa: BLE001
            pass
        if sock is not None:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass


def serve(host: str, username: str, password: str, *, port: int, remote_port: int) -> int:
    global _TUNNEL_LOCAL_IP
    _TUNNEL_LOCAL_IP = _detect_tunnel_local_ip()
    if _TUNNEL_LOCAL_IP:
        print(f"Обычный выход этой машины: {_TUNNEL_LOCAL_IP} "
              "(адреса Авито будем выбирать так, чтобы идти мимо него).")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, port=port, username=username, password=password,
                   look_for_keys=False, allow_agent=False, timeout=30)
    transport = client.get_transport()
    if transport is None:
        print("не удалось открыть SSH-транспорт", file=sys.stderr)
        return 1
    transport.set_keepalive(30)
    # Только петля сервера: открытый наружу порт превратил бы туннель в публичный прокси.
    transport.request_port_forward("127.0.0.1", remote_port)
    print(f"Туннель поднят: на сервере SOCKS5 на 127.0.0.1:{remote_port}, выход — этот компьютер.")
    print("Проверка на сервере: curl --socks5-hostname 127.0.0.1:%d https://api.ipify.org" % remote_port)
    print("Остановка — Ctrl+C.")
    try:
        while True:
            channel = transport.accept(1)
            if channel is None:
                if not transport.is_active():
                    print("SSH-соединение потеряно.", file=sys.stderr)
                    return 1
                continue
            threading.Thread(target=handle_channel, args=(channel, channel.origin_addr),
                             daemon=True, name="avito-egress").start()
    except KeyboardInterrupt:
        print("\nОстановлено. Канал Авито через этот компьютер больше не работает.")
        return 0
    finally:
        try:
            transport.cancel_port_forward("127.0.0.1", remote_port)
        except Exception:  # noqa: BLE001
            pass
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Обратный SOCKS5-туннель до прода Albery")
    parser.add_argument("--host", default=os.getenv("ALBERY_SSH_HOST", "186.246.7.32"))
    parser.add_argument("--port", type=int, default=int(os.getenv("ALBERY_SSH_PORT", "22")))
    parser.add_argument("--user", default=os.getenv("ALBERY_SSH_USER", "root"))
    parser.add_argument("--remote-port", type=int, default=1080)
    parser.add_argument("--env", help="файл с паролем SSH (ключ вида PROD_PASSWORD/SSH_PASSWORD)")
    parser.add_argument("--password-var", default="", help="имя переменной с паролем в файле --env")
    parser.add_argument("--retry", action="store_true", help="переподключаться при обрыве связи")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    password = os.getenv("ALBERY_SSH_PASSWORD", "")
    if not password and args.env:
        values = load_env_file(args.env)
        candidates = ([args.password_var] if args.password_var else
                      ["PROD_PASSWORD", "PROD_SSH_PASSWORD", "SSH_PASSWORD", "SERVER_PASSWORD",
                       "root_password"])
        for key in candidates:
            if values.get(key):
                password = values[key]
                break
    if not password:
        print("Пароль не найден: задайте ALBERY_SSH_PASSWORD или --env с файлом, "
              "где лежит PROD_PASSWORD. Пароль не печатается и никуда не пишется.", file=sys.stderr)
        return 2

    while True:
        try:
            code = serve(args.host, args.user, password, port=args.port,
                         remote_port=args.remote_port)
        except Exception as exc:  # noqa: BLE001
            logging.error("туннель упал: %s: %s", type(exc).__name__, exc)
            code = 1
        if not args.retry or code == 0:
            return code
        logging.info("переподключение через 10 секунд")
        time.sleep(10)


if __name__ == "__main__":
    raise SystemExit(main())
