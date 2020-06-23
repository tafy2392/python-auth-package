import pytest  # type: ignore
import trio

from marathon_acme_trio import crypto_utils
from marathon_acme_trio.cert_manager import CertManager, generate_account_key
from marathon_acme_trio.storage import FilesystemStorageProvider

from .helpers import ignore_acme_insecure_warning
from .pebble import bg_pebble


@pytest.fixture
async def pebble(nursery, tmp_path):
    async with bg_pebble(nursery, tmp_path) as pebble:
        yield pebble


@pytest.fixture
async def pebble_av(nursery, tmp_path):
    async with bg_pebble(nursery, tmp_path, always_valid=True) as pebble:
        yield pebble


async def build_cm_and_channel(store_path, pebble):
    send, recv = trio.open_memory_channel(1)
    store = FilesystemStorageProvider(store_path)
    cm = await CertManager.build(
        store, send, pebble.dir_url, "me@example.com", verify_ssl=False
    )
    return cm, recv


def collect_challenges(nursery, channel):
    send, recv = trio.open_memory_channel(0)
    challenges = []

    async def catch_challenges():
        async for challenge in channel:
            challenges.append(challenge)
        await send.send(challenges)

    nursery.start_soon(catch_challenges)

    return recv


async def issue_cert_for_domain(domain, tmp_path, pebble_av):
    async with trio.open_nursery() as nursery:
        cm, chan = await build_cm_and_channel(tmp_path, pebble_av)
        collect_chan = collect_challenges(nursery, chan)
        await cm.issue_cert(domain)
        await cm.challenge_channel.aclose()
        assert len(await collect_chan.receive()) == 2


@ignore_acme_insecure_warning
class TestCertManager:
    async def test_build_without_key(self, tmp_path, pebble):
        """
        If there's no account key on disk, CertManager creates one.
        """
        store = FilesystemStorageProvider(tmp_path)
        cm = await CertManager.build(
            store, None, pebble.dir_url, "me@example.com", verify_ssl=False
        )

        # A new key has been created and stored.
        stored_key_bytes = (tmp_path / "client.key").read_bytes()
        assert b"RSA PRIVATE KEY" in stored_key_bytes
        # The key we're using is the one on disk.
        key = crypto_utils.pem_to_jwk(stored_key_bytes)
        assert cm.acme.account_key.thumbprint() == key.thumbprint()

    async def test_build_with_key(self, tmp_path, pebble):
        """
        If an account key already exists on disk, CertManager uses it.
        """
        stored_key_bytes = generate_account_key()
        (tmp_path / "client.key").write_bytes(stored_key_bytes)
        store = FilesystemStorageProvider(tmp_path)
        cm = await CertManager.build(
            store, None, pebble.dir_url, "me@example.com", verify_ssl=False
        )

        # The key we're using is the one on disk.
        key = crypto_utils.pem_to_jwk(stored_key_bytes)
        assert cm.acme.account_key.thumbprint() == key.thumbprint()

    async def test_issue_cert_new(self, nursery, tmp_path, pebble_av):
        """
        We can issue a new cert for a domain that does not yet have one.
        """
        cm, chan = await build_cm_and_channel(tmp_path, pebble_av)

        # We use an unbuffered channel to pass challenge objects around, so
        # issue_cert() will wait until they're received before continuing.
        collect_chan = collect_challenges(nursery, chan)

        # Issue the cert.
        await cm.issue_cert("example.com")

        await cm.challenge_channel.aclose()
        [challenge, delete] = await collect_chan.receive()
        assert challenge.content is not None
        assert delete.content is None
        assert challenge.id == delete.id

        # The stored PEM data contains key, cert, and CA cert.
        certs_pem = (tmp_path / "certs" / "example.com.pem").read_text()
        pem_lines = certs_pem.splitlines()
        assert [ln for ln in pem_lines if ln.startswith("---")] == [
            "-----BEGIN RSA PRIVATE KEY-----",
            "-----END RSA PRIVATE KEY-----",
            "-----BEGIN CERTIFICATE-----",
            "-----END CERTIFICATE-----",
            "-----BEGIN CERTIFICATE-----",
            "-----END CERTIFICATE-----",
        ]

    async def test_issue_cert_no_validation(self, nursery, tmp_path, pebble):
        """
        We can survive trying to issue a cert when validation fails.

        NOTE: This test uses the `pebble` fixture without a challenge server to
        ensure validation failure.
        """
        cm, chan = await build_cm_and_channel(tmp_path, pebble)

        # We use an unbuffered channel to pass challenge objects around, so
        # issue_cert() will wait until they're received before continuing.
        collect_chan = collect_challenges(nursery, chan)

        # Issue the cert.
        await cm.issue_cert("example.com")

        await cm.challenge_channel.aclose()
        [challenge, delete] = await collect_chan.receive()
        assert challenge.content is not None
        assert delete.content is None
        assert challenge.id == delete.id

        # Validation failed, so no certificate was stored.
        assert not (tmp_path / "certs" / "example.com.pem").exists()

    async def test_issue_cert_existing(self, nursery, tmp_path, pebble_av):
        """
        We can issue a replacement cert for a domain that already has one.
        """
        await issue_cert_for_domain("example.com", tmp_path, pebble_av)
        orig_pem = (tmp_path / "certs" / "example.com.pem").read_text()

        cm, chan = await build_cm_and_channel(tmp_path, pebble_av)
        collect_chan = collect_challenges(nursery, chan)

        # Issue the cert.
        await cm.issue_cert("example.com")

        await cm.challenge_channel.aclose()
        [challenge, delete] = await collect_chan.receive()
        assert challenge.content is not None
        assert delete.content is None
        assert challenge.id == delete.id

        certs_pem = (tmp_path / "certs" / "example.com.pem").read_text()
        pem_lines = certs_pem.splitlines()
        assert [ln for ln in pem_lines if ln.startswith("---")] == [
            "-----BEGIN RSA PRIVATE KEY-----",
            "-----END RSA PRIVATE KEY-----",
            "-----BEGIN CERTIFICATE-----",
            "-----END CERTIFICATE-----",
            "-----BEGIN CERTIFICATE-----",
            "-----END CERTIFICATE-----",
        ]

        assert certs_pem != orig_pem

    async def test_renew_certs(self, nursery, tmp_path, pebble_av):
        """
        We renew certs that are nearing expiry.
        """
        await issue_cert_for_domain("example.com", tmp_path, pebble_av)
        orig_pem = (tmp_path / "certs" / "example.com.pem").read_text()

        cm, chan = await build_cm_and_channel(tmp_path, pebble_av)
        collect_chan = collect_challenges(nursery, chan)

        # Fake "nearing expiry" by setting the renewal window to a decade
        # before expiry.
        await cm.renew_certs(days_before_expiry=3653)

        await cm.challenge_channel.aclose()
        assert len(await collect_chan.receive()) == 2

        certs_pem = (tmp_path / "certs" / "example.com.pem").read_text()
        assert certs_pem != orig_pem
        pem_lines = certs_pem.splitlines()
        assert [ln for ln in pem_lines if ln.startswith("---")] == [
            "-----BEGIN RSA PRIVATE KEY-----",
            "-----END RSA PRIVATE KEY-----",
            "-----BEGIN CERTIFICATE-----",
            "-----END CERTIFICATE-----",
            "-----BEGIN CERTIFICATE-----",
            "-----END CERTIFICATE-----",
        ]

    async def test_renew_certs_unnecessary(self, nursery, tmp_path, pebble_av):
        """
        We don't renew certs that aren't nearing expiry.
        """
        await issue_cert_for_domain("example.com", tmp_path, pebble_av)
        orig_pem = (tmp_path / "certs" / "example.com.pem").read_text()

        cm, chan = await build_cm_and_channel(tmp_path, pebble_av)
        collect_chan = collect_challenges(nursery, chan)

        await cm.renew_certs()

        await cm.challenge_channel.aclose()
        assert len(await collect_chan.receive()) == 0

        assert (tmp_path / "certs" / "example.com.pem").read_text() == orig_pem
