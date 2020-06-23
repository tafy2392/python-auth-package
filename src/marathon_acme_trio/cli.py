import logging
import sys

import click
import trio

from . import main

ACME_URL_ALIASES = {
    "le": "https://acme-v02.api.letsencrypt.org/directory",
    "le-staging": "https://acme-staging-v02.api.letsencrypt.org/directory",
    "pebble": "https://localhost:14000/dir",
}

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S%z",
)


def unalias_acme_url(ctx, param, value):
    return ACME_URL_ALIASES.get(value, value)


def split_on_comma(ctx, param, value):
    return [part for part in value.split(",") if part != ""]


def passthrough(ctx, param, value):
    return value


def opt(name, *args, **kw):
    if "default" in kw:
        kw["show_default"] = True
    else:
        kw["required"] = True
    kw.setdefault("envvar", name.upper())
    kw.setdefault("show_envvar", True)
    long_opt = "--" + name.replace("_", "-")
    return click.option(name, long_opt, *args, **kw)


def cfg_opt(name, *args, **kw):
    callback = kw.pop("callback", passthrough)

    def set_cfg(ctx, param, value):
        setattr(ctx.obj, name, callback(None, None, value))

    return opt(name, *args, callback=set_cfg, expose_value=False, **kw)


pass_config = click.make_pass_decorator(main.Config)

days_before_expiry_opt = cfg_opt(
    "days_before_expiry", default=main.DEFAULT_RENEW_AT_DAYS_LEFT, type=int,
)


@click.group()
@opt("store_path", "-s", type=click.Path(exists=True))
@opt("acme_url", default="le", callback=unalias_acme_url)
@opt("acme_account_email")
@opt("marathon_lb_urls", default="", callback=split_on_comma)
@opt("http01_port", default=5002, type=int)
@click.pass_context
def cli_main(ctx, **opts):
    """
    Manage TLS certificates for Marathon apps.
    """
    ctx.obj = main.Config(**opts)


@cli_main.command()
@pass_config
@days_before_expiry_opt
def renew(cfg):
    """
    Renew all certificates that are nearing expiry.
    """
    trio_run(main.renew, cfg)


@cli_main.command()
@pass_config
@click.argument("domains", nargs=-1)
def issue(cfg, domains):
    """
    Issue certificates for one or more domains.
    """
    trio_run(main.issue, cfg, domains)


@cli_main.command()
@pass_config
@cfg_opt("marathon_urls", callback=split_on_comma)
def issue_from_marathon(cfg):
    """
    Issue certificates for all domains from marathon that don't yet have one.
    """
    trio_run(main.issue_from_marathon, cfg)


@cli_main.command()
@pass_config
@cfg_opt("marathon_urls", callback=split_on_comma)
@cfg_opt("renew_interval", default=3600, type=int)
@days_before_expiry_opt
def run_service(cfg):
    """
    Start a long-running service to issue new certificates in response to
    marathon events and periodically renew old certificates.
    """
    trio_run(main.run_service, cfg)


def trio_run(*args):
    try:
        trio.run(*args)
    except trio.MultiError as e:
        msg = "\n  ".join(str(err) for err in e.exceptions)
        raise click.ClickException("multiple errors:\n  " + msg)
