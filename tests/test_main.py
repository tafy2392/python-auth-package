from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest  # type: ignore
import trio

from marathon_acme_trio import main
from marathon_acme_trio.crypto_utils import pem_to_cert

from .fake_marathon import FakeMarathon, FakeMarathonLb
from .helpers import generate_selfsigned, ignore_acme_insecure_warning, mkapp
from .pebble import bg_pebble


@pytest.fixture
async def fake_marathon(nursery):
    async with FakeMarathon() as fake_marathon:
        await fake_marathon.start_http(nursery)
        yield fake_marathon


@pytest.fixture
async def pebble(nursery, tmp_path, monkeypatch):
    # Unlike other pebble fixtures, this one also monkeypatches main to disable
    # cert verification.
    monkeypatch.setattr(main, "ACME_VERIFY_SSL", False)
    async with bg_pebble(nursery, tmp_path) as pebble:
        yield pebble


async def start_fake_mlb(nursery):
    fake_mlb = FakeMarathonLb()
    await fake_mlb.start_http(nursery)
    return fake_mlb


@pytest.fixture
def store_path(tmp_path):
    (tmp_path / "certs").mkdir()
    return tmp_path


def mkcfg(store_path, pebble, fake_marathon=None, **opts):
    opts.setdefault("acme_account_email", "acme@example.com")
    opts.setdefault("marathon_lb_urls", [])
    if fake_marathon is not None:
        assert "marathon_urls" not in opts
        opts["marathon_urls"] = [fake_marathon.base_url]
    return main.Config(store_path=store_path, acme_url=pebble.dir_url, **opts)


def from_today(**kw):
    return datetime.today() + timedelta(**kw)


def cert_path(store_path, domain):
    return store_path / f"certs/{domain}.pem"


def cert_bytes(store_path, domain):
    return cert_path(store_path, domain).read_bytes()


def load_cert(store_path, domain):
    return pem_to_cert(cert_bytes(store_path, domain))


def store_selfsigned(store_path, domain, valid_for_days):
    ss_pem = generate_selfsigned(domain, valid_for_days)
    cert_path(store_path, domain).write_bytes(ss_pem)
    return ss_pem


def cert_valid_for(store_path, domain, min_valid_days):
    cert = load_cert(store_path, domain)
    return cert.not_valid_after > from_today(days=min_valid_days)


def cert_renewed(store_path, domain, min_valid_days=10):
    return cert_valid_for(store_path, domain, min_valid_days)


@ignore_acme_insecure_warning
class TestRenew:
    async def test_renew_no_certs(self, pebble, store_path):
        """
        When there are no certs to renew, we exit cleanly after doing nothing.
        """
        cfg = mkcfg(store_path, pebble)
        await main.renew(cfg)

        # Lack of an exception above tells us that nothing went wrong, but in
        # order to know that something went *right* we need to check indirectly
        # by looking for the ACME client key.
        stored_key_bytes = (store_path / "client.key").read_bytes()
        assert b"RSA PRIVATE KEY" in stored_key_bytes

    async def test_renew_with_mixed_certs(self, pebble, store_path):
        """
        Any renewable certs are renewed, certs not yet within the renewal
        window are ignored.
        """
        keep70_pem = store_selfsigned(store_path, "keep70.com", 70)
        renew1_pem = store_selfsigned(store_path, "renew1.com", 1)
        renew5_pem = store_selfsigned(store_path, "renew5.com", 5)

        cfg = mkcfg(store_path, pebble)
        await main.renew(cfg)

        assert cert_bytes(store_path, "keep70.com") == keep70_pem

        assert cert_bytes(store_path, "renew1.com") != renew1_pem
        assert cert_renewed(store_path, "renew1.com")

        assert cert_bytes(store_path, "renew5.com") != renew5_pem
        assert cert_renewed(store_path, "renew5.com")

    async def test_renew_with_mlbs(self, nursery, pebble, store_path):
        """
        After certs are renewed, we reload any marathon-lb instances we've been
        pointed at.
        """
        keep70_pem = store_selfsigned(store_path, "keep70.com", 70)
        renew1_pem = store_selfsigned(store_path, "renew1.com", 1)
        renew5_pem = store_selfsigned(store_path, "renew5.com", 5)

        fake_mlb1 = await start_fake_mlb(nursery)
        fake_mlb2 = await start_fake_mlb(nursery)
        mlb_urls = [fake_mlb1.base_url, fake_mlb2.base_url]

        cfg = mkcfg(store_path, pebble, marathon_lb_urls=mlb_urls)
        await main.renew(cfg)

        assert cert_bytes(store_path, "keep70.com") == keep70_pem

        assert cert_bytes(store_path, "renew1.com") != renew1_pem
        assert cert_renewed(store_path, "renew1.com")

        assert cert_bytes(store_path, "renew5.com") != renew5_pem
        assert cert_renewed(store_path, "renew5.com")

        assert fake_mlb1.check_signalled_usr1()
        assert fake_mlb2.check_signalled_usr1()


@ignore_acme_insecure_warning
class TestIssue:
    async def test_issue_no_certs(self, pebble, store_path):
        """
        When there are no certs to issue, we exit cleanly after doing nothing.
        """
        cfg = mkcfg(store_path, pebble)
        await main.issue(cfg, [])

        # Lack of an exception above tells us that nothing went wrong, but in
        # order to know that something went *right* we need to check indirectly
        # by looking for the ACME client key.
        stored_key_bytes = (store_path / "client.key").read_bytes()
        assert b"RSA PRIVATE KEY" in stored_key_bytes

    async def test_issue_new(self, pebble, store_path):
        """
        If the domain we're asked to issue a cert for isn't in the store, we
        issue the cert.
        """
        cfg = mkcfg(store_path, pebble)
        await main.issue(cfg, ["example.com"])

        assert cert_valid_for(store_path, "example.com", 10)

    async def test_issue_existing(self, pebble, store_path):
        """
        If the domain we're asked to issue a cert for is already in the store,
        we don't issue the cert.
        """
        exists_pem = store_selfsigned(store_path, "exists.com", 70)

        cfg = mkcfg(store_path, pebble)
        await main.issue(cfg, ["exists.com"])

        assert cert_bytes(store_path, "exists.com") == exists_pem

    async def test_issue_multiple(self, pebble, store_path):
        """
        If we're asked to issue certs for multiple domains, we only issue for
        the ones we don't already have.
        """
        exists_pem = store_selfsigned(store_path, "exists.com", 70)

        cfg = mkcfg(store_path, pebble)
        await main.issue(cfg, ["new.com", "exists.com", "new2.com"])

        assert cert_bytes(store_path, "exists.com") == exists_pem

        assert cert_valid_for(store_path, "new.com", 10)
        assert cert_valid_for(store_path, "new2.com", 10)

    # TODO: Figure out how to test that we don't issue a cert multiple times.


@ignore_acme_insecure_warning
class TestIssueFromMarathon:
    async def test_issue_no_certs(self, pebble, fake_marathon, store_path):
        """
        When there are no certs to issue, we exit cleanly after doing nothing.
        """
        cfg = mkcfg(store_path, pebble, fake_marathon)
        await main.issue_from_marathon(cfg)

        # Lack of an exception above tells us that nothing went wrong, but in
        # order to know that something went *right* we need to check indirectly
        # by looking for the ACME client key.
        stored_key_bytes = (store_path / "client.key").read_bytes()
        assert b"RSA PRIVATE KEY" in stored_key_bytes

    async def test_issue_new(self, pebble, fake_marathon, store_path):
        """
        If the domain we're asked to issue a cert for isn't in the store, we
        issue the cert.
        """
        app = mkapp("/my-app_1", MARATHON_ACME_0_DOMAIN="example.com")
        await fake_marathon.add_app(app)

        cfg = mkcfg(store_path, pebble, fake_marathon)
        await main.issue_from_marathon(cfg)

        assert cert_valid_for(store_path, "example.com", 10)

    async def test_issue_existing(self, pebble, fake_marathon, store_path):
        """
        If the domain we're asked to issue a cert for is already in the store,
        we don't issue the cert.
        """
        exists_pem = store_selfsigned(store_path, "exists.com", 70)
        app = mkapp("/my-app_1", MARATHON_ACME_0_DOMAIN="exists.com")
        await fake_marathon.add_app(app)

        cfg = mkcfg(store_path, pebble, fake_marathon)
        await main.issue_from_marathon(cfg)

        assert cert_bytes(store_path, "exists.com") == exists_pem

    async def test_issue_multiple(self, pebble, fake_marathon, store_path):
        """
        If we're asked to issue certs for multiple domains, we only issue for
        the ones we don't already have.
        """
        exists_pem = store_selfsigned(store_path, "exists.com", 70)
        app1 = mkapp("/my-app_1", MARATHON_ACME_0_DOMAIN="exists.com")
        await fake_marathon.add_app(app1)
        app2 = mkapp("/my-app_2", MARATHON_ACME_0_DOMAIN="new.com")
        await fake_marathon.add_app(app2)
        app3 = mkapp("/my-app_3", MARATHON_ACME_0_DOMAIN="new2.com")
        await fake_marathon.add_app(app3)

        cfg = mkcfg(store_path, pebble, fake_marathon)
        await main.issue_from_marathon(cfg)

        assert cert_bytes(store_path, "exists.com") == exists_pem

        assert cert_valid_for(store_path, "new.com", 10)
        assert cert_valid_for(store_path, "new2.com", 10)

    # TODO: Figure out how to test that we don't issue a cert multiple times.


async def cert_exists(store_path, domain, timeout=5):
    with trio.fail_after(timeout):
        while not cert_path(store_path, domain).exists():
            await trio.sleep(0.01)


async def cert_content_changed(store_path, domain, old_content, timeout=5):
    with trio.fail_after(timeout):
        while cert_bytes(store_path, domain) == old_content:
            await trio.sleep(0.01)


@asynccontextmanager
async def run_service(cfg):
    async with trio.open_nursery() as nursery:
        nursery.start_soon(main.run_service, cfg)
        yield
        nursery.cancel_scope.cancel()


@ignore_acme_insecure_warning
class TestRunService:
    async def test_renews_at_startup(self, pebble, fake_marathon, store_path):
        """
        When run-service starts, it renews any renewable certs.
        """
        keep70_pem = store_selfsigned(store_path, "keep70.com", 70)
        renew1_pem = store_selfsigned(store_path, "renew1.com", 1)

        cfg = mkcfg(store_path, pebble, fake_marathon)
        async with run_service(cfg):
            # Interactions with pebble are difficult to wait for, so instead we
            # poll for changes to the cert we expect to see renewed.
            await cert_content_changed(store_path, "renew1.com", renew1_pem)

        assert cert_bytes(store_path, "keep70.com") == keep70_pem

        assert cert_bytes(store_path, "renew1.com") != renew1_pem
        assert cert_renewed(store_path, "renew1.com")

    async def test_issues_at_startup(self, pebble, fake_marathon, store_path):
        """
        When run-service starts, it issues any new certs we need.
        """
        app = mkapp("/my-app_1", MARATHON_ACME_0_DOMAIN="example.com")
        await fake_marathon.add_app(app)

        cfg = mkcfg(store_path, pebble, fake_marathon)
        async with run_service(cfg):
            # Interactions with pebble are difficult to wait for, so instead we
            # poll for the existence of the cert we expect to see issued.
            await cert_exists(store_path, "example.com")

        assert cert_valid_for(store_path, "example.com", 10)

    async def test_issues_on_event(self, pebble, fake_marathon, store_path):
        """
        While run-service is running, it issues new certs in response to
        marathon events.
        """
        old = mkapp("/old-app", MARATHON_ACME_0_DOMAIN="old.com")
        await fake_marathon.add_app(old)

        cfg = mkcfg(store_path, pebble, fake_marathon)
        async with run_service(cfg):
            # Wait for a cert for an existing app to be issued so we know we're
            # past startup.
            await cert_exists(store_path, "old.com")

            new = mkapp("/new-app", MARATHON_ACME_0_DOMAIN="new.com")
            await fake_marathon.add_app(new)

            # Wait for a new new app's cert to exist.
            await cert_exists(store_path, "new.com")

        assert cert_valid_for(store_path, "old.com", 10)
        assert cert_valid_for(store_path, "new.com", 10)

    async def test_periodic_renewal(self, pebble, fake_marathon, store_path):
        """
        While run-service is running, it periodically renews any renewable
        certs.
        """
        renew1_pem = store_selfsigned(store_path, "renew1.com", 1)

        cfg = mkcfg(store_path, pebble, fake_marathon, renew_interval=1)
        async with run_service(cfg):
            # Wait for an existing cert to be renewed so we know we're past
            # startup.
            await cert_content_changed(store_path, "renew1.com", renew1_pem)

            renew2_pem = store_selfsigned(store_path, "renew2.com", 1)
            await cert_content_changed(store_path, "renew2.com", renew2_pem)

        assert cert_renewed(store_path, "renew1.com")
        assert cert_renewed(store_path, "renew2.com")
