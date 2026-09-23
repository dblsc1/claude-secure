#!/usr/bin/env python3
"""SOCKS5 relay that only allows RFC1918 / loopback destinations.

Runs on the HOST (outside the sandbox). Listens on a unix socket
that the sandbox already sees via the existing claude-secure
runtime dir bind mount. Speaks minimal SOCKS5 CONNECT, checks the
resolved destination IP against a private-network allowlist, and
only then opens a real TCP connection and splices bytes.
"""

import asyncio
import contextlib
import ipaddress
import os
import socket
import struct
import sys

RUNTIME_DIR = os.environ.get(
    "XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"
)
SOCK_PATH = os.path.join(RUNTIME_DIR, "claude-secure", "lan.sock")


def is_allowed(ip_str):
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if ip.version != 4:
        return False
    nets = (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
    )
    return any(ip in ipaddress.ip_network(n) for n in nets)


async def copy(reader, writer):
    try:
        while data := await reader.read(64 * 1024):
            writer.write(data)
            await writer.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()


async def read_exact(reader, n):
    data = await reader.readexactly(n)
    return data


async def handle(reader, writer):
    try:
        await _handle(reader, writer)
    except (asyncio.IncompleteReadError, ConnectionError, OSError):
        pass
    finally:
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()


async def _handle(reader, writer):
    # greeting: VER NMETHODS METHODS...
    ver_nm = await read_exact(reader, 2)
    ver, nmethods = ver_nm
    if ver != 0x05:
        return
    await read_exact(reader, nmethods)
    writer.write(b"\x05\x00")  # no-auth
    await writer.drain()

    # request: VER CMD RSV ATYP ...
    head = await read_exact(reader, 4)
    ver, cmd, _rsv, atyp = head
    if ver != 0x05 or cmd != 0x01:
        writer.write(b"\x05\x07\x00\x01" + b"\x00" * 6)
        await writer.drain()
        return

    if atyp == 0x01:
        raw = await read_exact(reader, 4)
        host = socket.inet_ntoa(raw)
    elif atyp == 0x03:
        length = (await read_exact(reader, 1))[0]
        host = (await read_exact(reader, length)).decode()
    elif atyp == 0x04:
        raw = await read_exact(reader, 16)
        host = socket.inet_ntop(socket.AF_INET6, raw)
    else:
        writer.write(b"\x05\x08\x00\x01" + b"\x00" * 6)
        await writer.drain()
        return

    port = struct.unpack("!H", await read_exact(reader, 2))[0]

    try:
        infos = await asyncio.get_event_loop().getaddrinfo(
            host, port, family=socket.AF_INET, type=socket.SOCK_STREAM
        )
        target_ip = infos[0][4][0]
    except OSError:
        writer.write(b"\x05\x04\x00\x01" + b"\x00" * 6)
        await writer.drain()
        return

    if not is_allowed(target_ip):
        writer.write(b"\x05\x02\x00\x01" + b"\x00" * 6)
        await writer.drain()
        return

    try:
        remote_reader, remote_writer = await asyncio.open_connection(
            target_ip, port
        )
    except OSError:
        writer.write(b"\x05\x05\x00\x01" + b"\x00" * 6)
        await writer.drain()
        return

    writer.write(b"\x05\x00\x00\x01" + b"\x00" * 4 + b"\x00\x00")
    await writer.drain()

    await asyncio.gather(
        copy(reader, remote_writer),
        copy(remote_reader, writer),
    )


async def main():
    sock_dir = os.path.dirname(SOCK_PATH)
    os.makedirs(sock_dir, mode=0o700, exist_ok=True)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(SOCK_PATH)

    server = await asyncio.start_unix_server(handle, path=SOCK_PATH)
    os.chmod(SOCK_PATH, 0o600)

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
