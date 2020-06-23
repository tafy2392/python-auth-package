import pytest  # type: ignore

from marathon_acme_trio import crypto_utils
from marathon_acme_trio.async_acme import AsyncAcme, get_http01

from .helpers import ignore_acme_insecure_warning
from .pebble import bg_pebble


@pytest.fixture
async def pebble(nursery, tmp_path):
    async with bg_pebble(nursery, tmp_path) as pebble:
        yield pebble


@pytest.fixture
async def pebble_always_valid(nursery, tmp_path):
    async with bg_pebble(nursery, tmp_path, always_valid=True) as pebble:
        yield pebble


async def build_async_acme(account_key, email):
    return await AsyncAcme.build(
        "https://127.0.0.1:14000/dir", account_key, email, verify_ssl=False
    )


def generate_account_key():
    key_pem = crypto_utils.key_to_pem(crypto_utils.generate_rsa_key(2048))
    return crypto_utils.pem_to_jwk(key_pem)


@ignore_acme_insecure_warning
class TestAsyncAcme:
    async def test_get_or_create_account(self, pebble):
        """
        If an account already exists, we fetch it. If not, we create one.
        """
        account_key = generate_account_key()

        acme1 = await AsyncAcme._build(
            "https://127.0.0.1:14000/dir", account_key, verify_ssl=False
        )
        reg1 = await acme1.get_or_create_account("fake@example.com")
        assert reg1.body.contact == ("mailto:fake@example.com",)

        # New client, same account key.
        acme2 = await AsyncAcme._build(
            "https://127.0.0.1:14000/dir", account_key, verify_ssl=False
        )
        reg2 = await acme2.get_or_create_account("fake@example.com")
        assert reg2.body.contact == ("mailto:fake@example.com",)

        assert reg1.body == reg2.body

    async def test_build(self, pebble):
        """
        AsyncAcme.build() creates or fetches the account before returning.
        """
        account_key = generate_account_key()

        acme = await AsyncAcme.build(
            "https://127.0.0.1:14000/dir",
            account_key,
            "fake@example.com",
            verify_ssl=False,
        )

        # Reach deep into the client's internals to inspect its state.
        account = acme._client.net.account
        assert account.body.contact == ("mailto:fake@example.com",)

    async def test_order_process(self, pebble_always_valid):
        """
        If we ask pebble to treat challenges as always valid, we can get a cert
        using only AsyncAcme.
        """
        acme = await build_async_acme(generate_account_key(), "me@example.com")
        key = crypto_utils.generate_rsa_key(2048)
        csr_pem = crypto_utils.build_csr_pem(key, "example.com")
        order = await acme.new_order(csr_pem)
        challenge = get_http01(order)

        await acme.answer_challenge(challenge)
        finalized_order = await acme.poll_and_finalize(order)

        pem_lines = finalized_order.fullchain_pem.splitlines()
        assert [ln for ln in pem_lines if ln.startswith("---")] == [
            "-----BEGIN CERTIFICATE-----",
            "-----END CERTIFICATE-----",
            "-----BEGIN CERTIFICATE-----",
            "-----END CERTIFICATE-----",
        ]
