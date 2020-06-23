import httpx
import trio


class FilesystemStorageProvider:
    def __init__(self, base_path):
        self.path = trio.Path(base_path)
        self.cert_path = self.path / "certs"

    def _cert_path(self, domain):
        return self.cert_path / f"{domain}.pem"

    async def store(self, domain, certs_pem, key_pem):
        await self.cert_path.mkdir(parents=True, exist_ok=True)
        # It's important to write the entire file as atomically as possible,
        # otherwise we risk race conditions where some other coroutine reads a
        # partially-written file and fails to load the key/certs.
        await self._cert_path(domain).write_text(key_pem + certs_pem)

    async def delete(self, domain):
        await self._cert_path(domain).unlink()

    async def list(self):
        paths = await self.cert_path.glob("*.pem")
        return [p.with_suffix("").name for p in paths]

    async def iter_cert_paths(self):
        for p in await self.cert_path.glob("*.pem"):
            yield p


class MLBStorageProvider:
    """
    Storage provider that wraps another storage provider and calls the
    marathon-lb reload API after every store operation.
    """

    def __init__(self, wrapped_store, mlb_urls):
        self._store = wrapped_store
        self._mlb_urls = mlb_urls
        self.mlb_clients = [
            httpx.AsyncClient(base_url=url) for url in mlb_urls
        ]

    @classmethod
    def with_fs_store(cls, base_path, mlb_url):
        return cls(FilesystemStorageProvider(base_path), mlb_url)

    async def _mlb_usr1_single(self, client):
        resp = await client.post("/_mlb_signal/usr1")
        resp.raise_for_status()

    async def _mlb_usr1(self):
        async with trio.open_nursery() as nursery:
            for client in self.mlb_clients:
                nursery.start_soon(self._mlb_usr1_single, client)

    @property
    def path(self):
        return self._store.path

    async def store(self, domain, certs_pem, key_pem):
        await self._store.store(domain, certs_pem, key_pem)
        await self._mlb_usr1()

    async def delete(self, domain):
        await self._store.delete(domain)

    async def list(self):
        return await self._store.list()

    async def iter_cert_paths(self):
        async for p in self._store.iter_cert_paths():
            yield p
