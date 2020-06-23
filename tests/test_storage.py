from marathon_acme_trio.storage import (
    FilesystemStorageProvider,
    MLBStorageProvider,
)

from .fake_marathon import FakeMarathonLb
from .helpers import dir_tree


async def start_fake_mlb(nursery):
    fake_mlb = FakeMarathonLb()
    await fake_mlb.start_http(nursery)
    return fake_mlb


class TestFilesystemStorageProvider:
    async def test_store(self, tmp_path):
        """
        The storage provider writes certs and keys to disk.
        """
        store = FilesystemStorageProvider(tmp_path)
        await store.store("example.com", "CERT\n", "KEY\n")

        assert dir_tree(tmp_path) == {"certs", "certs/example.com.pem"}
        cert_path = tmp_path / "certs" / "example.com.pem"
        assert cert_path.read_text() == "KEY\nCERT\n"

    async def test_delete(self, tmp_path):
        """
        The storage provider can delete certs it has stored.
        """
        store = FilesystemStorageProvider(tmp_path)
        await store.store("example.com", "CERT\n", "KEY\n")
        assert dir_tree(tmp_path) == {"certs", "certs/example.com.pem"}

        await store.delete("example.com")
        assert dir_tree(tmp_path) == {"certs"}

    async def test_list(self, tmp_path):
        """
        The storage provider can list certs it has stored.
        """
        store = FilesystemStorageProvider(tmp_path)
        await store.store("example.com", "CERT\n", "KEY\n")
        await store.store("step-3-profit.biz", "CERT\n", "KEY\n")

        assert set(await store.list()) == {"example.com", "step-3-profit.biz"}

    async def test_iter_cert_paths(self, tmp_path):
        """
        The storage provider can iterate over the certs it has stored.
        """
        store = FilesystemStorageProvider(tmp_path)
        await store.store("example.com", "CERT\n", "KEY\n")
        await store.store("step-3-profit.biz", "CERT\n", "KEY\n")

        certs = {"example.com.pem", "step-3-profit.biz.pem"}

        async for cert_path in store.iter_cert_paths():
            assert (await cert_path.read_text()) == "KEY\nCERT\n"
            certs.remove(str(cert_path.relative_to(tmp_path / "certs")))

        assert certs == set()


class TestMLBStorageProvider:
    async def test_store(self, nursery, tmp_path):
        """
        The storage provider writes certs and keys to disk, calling the
        marathon-lb reload API after each store.
        """
        fake_mlb = await start_fake_mlb(nursery)
        store = MLBStorageProvider.with_fs_store(tmp_path, [fake_mlb.base_url])
        await store.store("example.com", "CERT\n", "KEY\n")

        assert fake_mlb.check_signalled_usr1()

        assert dir_tree(tmp_path) == {"certs", "certs/example.com.pem"}
        cert_path = tmp_path / "certs" / "example.com.pem"
        assert cert_path.read_text() == "KEY\nCERT\n"

    async def test_store_multiple_mlbs(self, nursery, tmp_path):
        """
        The storage provider writes certs and keys to disk, calling the
        marathon-lb reload API after each store.
        """
        fake_mlb1 = await start_fake_mlb(nursery)
        fake_mlb2 = await start_fake_mlb(nursery)
        mlb_urls = [fake_mlb1.base_url, fake_mlb2.base_url]
        store = MLBStorageProvider.with_fs_store(tmp_path, mlb_urls)
        await store.store("example.com", "CERT\n", "KEY\n")

        assert fake_mlb1.check_signalled_usr1()
        assert fake_mlb2.check_signalled_usr1()

        assert dir_tree(tmp_path) == {"certs", "certs/example.com.pem"}
        cert_path = tmp_path / "certs" / "example.com.pem"
        assert cert_path.read_text() == "KEY\nCERT\n"

    async def test_delete(self, tmp_path):
        """
        The storage provider can delete certs it has stored.
        """
        store = MLBStorageProvider.with_fs_store(tmp_path, [])
        await store.store("example.com", "CERT\n", "KEY\n")
        assert dir_tree(tmp_path) == {"certs", "certs/example.com.pem"}

        await store.delete("example.com")
        assert dir_tree(tmp_path) == {"certs"}

    async def test_list(self, tmp_path):
        """
        The storage provider can list certs it has stored.
        """
        store = MLBStorageProvider.with_fs_store(tmp_path, [])
        await store.store("example.com", "CERT\n", "KEY\n")
        await store.store("step-3-profit.biz", "CERT\n", "KEY\n")

        assert set(await store.list()) == {"example.com", "step-3-profit.biz"}

    async def test_iter_cert_paths(self, tmp_path):
        """
        The storage provider can iterate over the certs it has stored.
        """
        store = MLBStorageProvider.with_fs_store(tmp_path, [])
        await store.store("example.com", "CERT\n", "KEY\n")
        await store.store("step-3-profit.biz", "CERT\n", "KEY\n")

        certs = {"example.com.pem", "step-3-profit.biz.pem"}

        async for cert_path in store.iter_cert_paths():
            assert (await cert_path.read_text()) == "KEY\nCERT\n"
            certs.remove(str(cert_path.relative_to(tmp_path / "certs")))

        assert certs == set()
