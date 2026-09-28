from __future__ import annotations

import asyncio
import logging
from functools import partial

log = logging.getLogger(__name__)


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


class Forwarders:
    def __init__(self, listen_host: str, target_host: str = "127.0.0.1"):
        self.listen_host = listen_host
        self.target_host = target_host
        self._servers: dict[int, asyncio.base_events.Server] = {}
        self.failed: set[int] = set()

    @property
    def active(self) -> set[int]:
        return set(self._servers)

    async def ensure(self, ports: set[int]) -> None:
        for port in list(self._servers):
            if port not in ports:
                self._servers.pop(port).close()
        self.failed &= ports
        for port in ports:
            if port in self._servers or port in self.failed:
                continue
            try:
                self._servers[port] = await asyncio.start_server(
                    partial(self._handle, port), self.listen_host, port)
                log.info("forwarding %s:%d -> %s:%d", self.listen_host, port, self.target_host, port)
            except OSError as e:
                log.warning("cannot forward port %d: %s", port, e)
                self.failed.add(port)

    async def _handle(self, port: int, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
        try:
            up_reader, up_writer = await asyncio.open_connection(self.target_host, port)
        except OSError:
            writer.close()
            return
        await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))

    async def close(self) -> None:
        await self.ensure(set())
