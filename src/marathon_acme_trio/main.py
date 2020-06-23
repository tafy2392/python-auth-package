import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import trio
from trio_util import periodic  # type: ignore

from .cert_manager import DEFAULT_RENEW_AT_DAYS_LEFT, CertManager
from .http01_server import HTTP01Server
from .marathon_client import MarathonClient
from .marathon_util import MLBDomainUtils
from .storage import MLBStorageProvider

# There's no easy way to add pebble's CA to the acme client in tests, so we
# need to disable cert verification for it. We definitely want cert
# verification outside tests, though, so we make this a constant we can
# monkeypatch instead of a config option.
ACME_VERIFY_SSL = True


log = logging.getLogger()


@dataclass
class Config:
    # For all commands.
    store_path: Path
    acme_url: str
    acme_account_email: str
    marathon_lb_urls: List[str]
    http01_port: int = 5002
    # For commands that use Marathon.
    marathon_urls: Optional[List[str]] = None
    # For commands that renew certs.
    days_before_expiry: int = DEFAULT_RENEW_AT_DAYS_LEFT
    # For commands that start long-running services.
    renew_interval: Optional[int] = 3600


async def _build_and_start_http01(nursery, cfg, challenge_rx):
    http01 = HTTP01Server.build()
    await http01.start(nursery, challenge_rx, "0.0.0.0", port=cfg.http01_port)
    return http01


async def _build_cert_manager(cfg, challenge_tx):
    cert_store = MLBStorageProvider.with_fs_store(
        cfg.store_path, cfg.marathon_lb_urls
    )
    return await CertManager.build(
        cert_store,
        challenge_tx,
        cfg.acme_url,
        cfg.acme_account_email,
        verify_ssl=ACME_VERIFY_SSL,
    )


def _build_marathon_client(cfg):
    return MarathonClient(cfg.marathon_urls)


@asynccontextmanager
async def _http01_and_certman_and_nursery(cfg):
    challenge_tx, challenge_rx = trio.open_memory_channel(0)

    async with trio.open_nursery() as nursery:
        await _build_and_start_http01(nursery, cfg, challenge_rx)
        yield (await _build_cert_manager(cfg, challenge_tx), nursery)
        nursery.cancel_scope.cancel()


@asynccontextmanager
async def _http01_and_certman(cfg):
    async with _http01_and_certman_and_nursery(cfg) as (cert_manager, nursery):
        yield cert_manager


async def _periodic_renew(cert_manager, interval, days_before_expiry):
    async for _ in periodic(interval):
        log.info("Periodic renewal started")
        await cert_manager.renew_certs(days_before_expiry)
        log.info("Periodic renewal finished")


async def _issue(cert_manager, domains):
    existing = set(await cert_manager.store.list())
    for domain in domains:
        if domain in existing:
            continue
        existing.add(domain)
        await cert_manager.issue_cert(domain)


async def _issue_from_marathon(cert_manager, mc):
    apps = await mc.get_apps()
    domains = MLBDomainUtils()._apps_acme_domains(apps)
    await _issue(cert_manager, domains)


async def renew(cfg):
    async with _http01_and_certman(cfg) as cert_manager:
        await cert_manager.renew_certs(cfg.days_before_expiry)


async def issue(cfg, domains):
    async with _http01_and_certman(cfg) as cert_manager:
        await _issue(cert_manager, domains)


async def issue_from_marathon(cfg):
    mc = _build_marathon_client(cfg)
    async with _http01_and_certman(cfg) as cert_manager:
        await _issue_from_marathon(cert_manager, mc)


async def run_service(cfg):
    domains_tx, domains_rx = trio.open_memory_channel(0)

    mc = _build_marathon_client(cfg)
    async with _http01_and_certman_and_nursery(cfg) as (cert_manager, nursery):
        nursery.start_soon(
            _periodic_renew,
            cert_manager,
            cfg.renew_interval,
            cfg.days_before_expiry,
        )
        # There's a lot of attach/detach event noise, so we only watch for
        # events that indicate changes to apps.
        events_rx = await nursery.start(mc.event_stream, ["api_post_event"])
        await _issue_from_marathon(cert_manager, mc)
        async for event in events_rx:
            await _issue_from_marathon(cert_manager, mc)
