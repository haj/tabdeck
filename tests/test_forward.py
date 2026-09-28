import asyncio

import pytest

from tabdeck.forward import Forwarders


async def echo_server():
    async def handle(r, w):
        while data := await r.read(1024):
            w.write(data)
            await w.drain()
        w.close()
    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def test_forwards_between_hosts_and_closes():
    server, port = await echo_server()
    fw = Forwarders(listen_host="::1")
    await fw.ensure({port})
    assert fw.active == {port}

    r, w = await asyncio.open_connection("::1", port)
    w.write(b"hello")
    await w.drain()
    assert await asyncio.wait_for(r.read(5), 2) == b"hello"
    w.close()

    await fw.ensure(set())
    assert fw.active == set()
    with pytest.raises(OSError):
        await asyncio.open_connection("::1", port)
    server.close()


async def test_bind_failure_is_recorded():
    blocker = await asyncio.start_server(lambda r, w: None, "::1", 0)
    port = blocker.sockets[0].getsockname()[1]
    fw = Forwarders(listen_host="::1")
    await fw.ensure({port})
    assert port in fw.failed and fw.active == set()
    blocker.close()
