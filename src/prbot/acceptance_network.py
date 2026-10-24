"""Remote acceptance only: cut and restore a real HTTPS tunnel to Redis."""

import asyncio
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx

from prbot.conversation_store import ConversationStore


class NetworkGate:
    def __init__(self, endpoint):
        target = urlsplit(endpoint)
        self.authority = target.netloc
        self.host = target.hostname
        self.port = target.port or 443
        self.connected = True
        self.writers = set()
        self.handlers = set()

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        self.writers.add(writer)
        upstream = None
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            line = header.split(b"\r\n", 1)[0].decode("ascii")
            # Only tunnel this run's configured service; never inspect TLS payloads.
            authority = f"{self.host}:{self.port}"
            if not self.connected or line != f"CONNECT {authority} HTTP/1.1":
                return
            remote_reader, upstream = await asyncio.open_connection(self.host, self.port)
            self.writers.add(upstream)
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()

            async def copy(source, destination):
                try:
                    while chunk := await source.read(65536):
                        destination.write(chunk)
                        await destination.drain()
                finally:
                    destination.close()

            await asyncio.gather(copy(reader, upstream), copy(remote_reader, writer))
        except (OSError, ValueError, asyncio.IncompleteReadError, asyncio.TimeoutError):
            pass
        finally:
            writer.close()
            self.writers.discard(writer)
            if upstream:
                upstream.close()
                self.writers.discard(upstream)
            self.handlers.discard(task)

    def disconnect(self):
        self.connected = False
        for writer in tuple(self.writers):
            writer.close()

    @asynccontextmanager
    async def serve(self):
        server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.proxy = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        try:
            yield self
        finally:
            server.close()
            await server.wait_closed()
            self.disconnect()
            pending = tuple(self.handlers)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)


class GatedArchive(ConversationStore):
    def __init__(self, values, gate):
        super().__init__(values)
        self.gate = gate

    def http_client(self):
        # Proxy only this client's Redis traffic; concurrent SDK/auth calls are untouched.
        return httpx.AsyncClient(
            timeout=20, follow_redirects=False, proxy=self.gate.proxy, trust_env=False
        )
