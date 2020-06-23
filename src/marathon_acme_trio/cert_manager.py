import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from acme.errors import ValidationError  # type: ignore

from . import crypto_utils as crypto
from .async_acme import AsyncAcme, get_http01

ACCOUNT_KEY_BITS = 2048
CERT_KEY_BITS = 2048
DEFAULT_RENEW_AT_DAYS_LEFT = 30

log = logging.getLogger()


@dataclass
class Challenge:
    id: str
    content: str


def generate_account_key():
    key = crypto.generate_rsa_key(ACCOUNT_KEY_BITS)
    return crypto.key_to_pem(key)


class CertManager:
    def __init__(self, store, challenge_channel, acme):
        self.store = store
        self.challenge_channel = challenge_channel
        self.acme = acme

    @classmethod
    async def build(cls, store, challenge_channel, dir_url, email, **kw):
        client_key_path = store.path / "client.key"
        if not await client_key_path.exists():
            await client_key_path.write_bytes(generate_account_key())
        account_key = crypto.pem_to_jwk(await client_key_path.read_bytes())
        acme = await AsyncAcme.build(dir_url, account_key, email, **kw)
        return cls(store, challenge_channel, acme)

    async def issue_cert(self, domain):
        # FIXME: Should we really log and swallow validation errors here? It's
        # convenient for the current use cases, but it makes it difficult for
        # the caller to determine whether a cert was actually issued.
        log.info(f"Attempting to issue certificate for {domain}")
        key = crypto.generate_rsa_key(CERT_KEY_BITS)
        csr_pem = crypto.build_csr_pem(key, domain)
        order = await self.acme.new_order(csr_pem)

        challenge = get_http01(order)
        await self._send_challenge(challenge)

        try:
            await self.acme.answer_challenge(challenge)
            finalized_order = await self.acme.poll_and_finalize(order)

            key_pem = crypto.key_to_pem(key).decode("utf-8")
            certs_pem = finalized_order.fullchain_pem
            await self.store.store(domain, certs_pem, key_pem)
            log.info(f"Successfully issued certificate for {domain}")
        except ValidationError as ve:
            # We can (theoretically, at least) have multiple authorizations
            # which each have multiple failed challenges. Let's log all of
            # them, even though we only really expect one.
            for authzr in ve.failed_authzrs:
                for chal in authzr.body.challenges:
                    log.warning(f"ValidationError for {domain}: {chal.error}")
            log.info(f"Failed to issue certificate for {domain}")
        finally:
            await self._clear_challenge(challenge)

    async def _send_challenge(self, challenge):
        token = challenge.chall.encode("token")
        content = challenge.chall.key_authorization(self.acme.account_key)
        await self.challenge_channel.send(Challenge(token, content))

    async def _clear_challenge(self, challenge):
        token = challenge.chall.encode("token")
        await self.challenge_channel.send(Challenge(token, None))

    async def renew_certs(self, days_before_expiry=DEFAULT_RENEW_AT_DAYS_LEFT):
        async for cert_path in self.store.iter_cert_paths():
            cert = crypto.pem_to_cert(await cert_path.read_bytes())
            renew_date = cert.not_valid_after - timedelta(
                days=days_before_expiry
            )
            # The domain is the filename with the .pem suffix stripped.
            domain = cert_path.with_suffix("").name
            if datetime.utcnow() >= renew_date:
                log.info(
                    f"Renewing certificate for {domain}"
                    f" (expires {cert.not_valid_after})"
                )
                await self.issue_cert(domain)
