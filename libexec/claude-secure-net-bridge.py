#!/usr/bin/python3
"""Expose host proxy bridges on private-network loopback, inside
the sandbox network namespace.

Forwards:
  127.0.0.1:7894 -> unix:/run/claude-secure/mihomo.sock  (internet)
  127.0.0.1:7895 -> unix:/run/claude-secure/lan.sock      (LAN only)

Also forwards SIGWINCH/etc to the child (no controlling tty in the
sandbox otherwise breaks resize) and reaps orphans (this runs as
PID 1 in the namespace).
"""

import asyncio
import contextlib
import os
import signal
import sys

FORWARDS = (
    (7894, "/run/claude-secure/mihomo.sock"),
    (7895, "/run/claude-secure/lan.sock"),
)
LISTEN_HOST = "127.0.0.1"

FORWARD_SIGNALS = (
    signal.SIGWINCH,
    signal.SIGHUP,
    signal.SIGQUIT,
    signal.SIGUSR1,
    signal.SIGUSR2,
    signal.SIGCONT,
)


async def copy_stream(reader, writer):
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


def make_handler(socket_path):
    async def handle_client(client_reader, client_writer):
        try:
            proxy_reader, proxy_writer = await asyncio.open_unix_connection(
                socket_path
            )
        except OSError:
            client_writer.close()
            await client_writer.wait_closed()
            return

        await asyncio.gather(
            copy_stream(client_reader, proxy_writer),
            copy_stream(proxy_reader, client_writer),
        )

    return handle_client


def reap_orphans():
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


async def main():
    servers = []
    for port, socket_path in FORWARDS:
        if not os.path.exists(socket_path):
            print(f"skip missing bridge: {socket_path}", file=sys.stderr)
            continue
        servers.append(
            await asyncio.start_server(
                make_handler(socket_path), LISTEN_HOST, port
            )
        )

    child = await asyncio.create_subprocess_exec(*sys.argv[2:])
    child_wait = asyncio.create_task(child.wait())
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    def forward(sig):
        if child.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(child.pid, sig)

    for sig in FORWARD_SIGNALS:
        with contextlib.suppress(ValueError, OSError):
            loop.add_signal_handler(sig, forward, sig)

    with contextlib.suppress(ValueError, OSError):
        loop.add_signal_handler(signal.SIGCHLD, reap_orphans)

    stop_wait = asyncio.create_task(stop.wait())

    async with contextlib.AsyncExitStack() as stack:
        for server in servers:
            await stack.enter_async_context(server)
        done, _ = await asyncio.wait(
            {child_wait, stop_wait}, return_when=asyncio.FIRST_COMPLETED
        )

    if stop_wait in done and child.returncode is None:
        child.terminate()
        try:
            await asyncio.wait_for(child.wait(), timeout=3)
        except asyncio.TimeoutError:
            child.kill()
            await child.wait()

    stop_wait.cancel()
    return child.returncode if child.returncode is not None else 143


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] != "--":
        raise SystemExit("usage: net-bridge.py -- command [args ...]")
    raise SystemExit(asyncio.run(main()))
