import dataclasses
import json
from datetime import datetime, timedelta

import pytest  # type: ignore
from click import ClickException
from click.testing import CliRunner
from trio import MultiError

from marathon_acme_trio import main
from marathon_acme_trio.cli import cli_main, pass_config, trio_run
from marathon_acme_trio.crypto_utils import pem_to_cert

from .fake_marathon import bg_fake_marathon_sync
from .helpers import generate_selfsigned, ignore_acme_insecure_warning, mkapp
from .pebble import bg_pebble_sync


@pytest.fixture
def runner():
    """
    Fixture providing a CliRunner with an isolated filesystem.
    """
    runner = CliRunner()
    with runner.isolated_filesystem():
        yield runner


@pytest.fixture
def pebble(tmp_path, monkeypatch):
    # Unlike other pebble fixtures, this one also monkeypatches main to disable
    # cert verification.
    monkeypatch.setattr(main, "ACME_VERIFY_SSL", False)
    with bg_pebble_sync(tmp_path) as pebble:
        yield pebble


@pytest.fixture
def fake_marathon():
    with bg_fake_marathon_sync() as fake_marathon:
        yield fake_marathon


@cli_main.command()
@pass_config
def dcfg(cfg):
    """
    Dump the config and options as JSON so we can inspect them in tests.
    """
    print(json.dumps(dataclasses.asdict(cfg)))


def invoke_dcfg_base(runner, opts, env={}):
    result = runner.invoke(cli_main, [*opts, "dcfg"], env=env)
    assert result.exit_code == 0
    return json.loads(result.output)


def invoke_dcfg(runner, opts, env={}):
    return invoke_dcfg_base(runner, ["-s", ".", *opts], env)


def check_opt_cli_and_env(runner, name):
    required_opts = []
    if name != "acme_account_email":
        required_opts.extend(["--acme-account-email", "acme@example.com"])
    opts = ["--" + name.replace("_", "-"), "cli-value", *required_opts]
    env = {name.upper(): "env-value"}
    # CLI only
    assert invoke_dcfg(runner, opts)[name] == "cli-value"
    # Envvar only
    assert invoke_dcfg(runner, required_opts, env=env)[name] == "env-value"
    # CLI trumps envvar
    assert invoke_dcfg(runner, opts, env=env)[name] == "cli-value"


class TestCLIMain:
    def test_no_subcommand_displays_usage(self, runner):
        """
        When run with no subcommand, usage is diplayed.
        """
        result = runner.invoke(cli_main, [])
        assert result.exit_code == 0
        assert result.output.splitlines()[0] == (
            "Usage: cli-main [OPTIONS] COMMAND [ARGS]..."
        )

    def test_store_path_must_exist(self, runner, tmp_path):
        """
        store_path is required and the path it specified must exist.
        """
        result = runner.invoke(cli_main, ["dcfg"])
        assert result != 0
        assert "Missing option '--store-path'" in result.output

        result = runner.invoke(cli_main, ["-s", str(tmp_path / "foo"), "dcfg"])
        assert result != 0
        print(f"{tmp_path}/foo")
        assert f"'{tmp_path}/foo' does not exist" in result.output

    def test_store_path_cli_env(self, runner, tmp_path):
        """
        store_path may be passed as an option or an envvar.
        """
        (tmp_path / "cli").mkdir()
        (tmp_path / "env").mkdir()
        clival = str(tmp_path / "cli")
        envval = str(tmp_path / "env")
        ropts = ["--acme-account-email", "acme@example.com"]
        opts = ["--store-path", clival, *ropts]
        env = {"STORE_PATH": envval}
        # CLI only
        assert invoke_dcfg_base(runner, opts)["store_path"] == clival
        # Envvar only
        assert invoke_dcfg_base(runner, ropts, env=env)["store_path"] == envval
        # CLI trumps envvar
        assert invoke_dcfg_base(runner, opts, env=env)["store_path"] == clival

    def test_acme_url_cli_env(self, runner):
        """
        acme_url may be passed as an option or an envvar.
        """
        check_opt_cli_and_env(runner, "acme_url")
        check_opt_cli_and_env(runner, "acme_account_email")


def invoke_subcommand(runner, store_path, *cmd_opts):
    # FIXME: Better to monkeypatch the default or something instead of
    # manipulating the opts we pass.
    if "--acme-account-email" not in cmd_opts:
        cmd_opts = ["--acme-account-email", "acme@example.com", *cmd_opts]
    if "--acme-url" not in cmd_opts:
        cmd_opts = ["--acme-url", "pebble", *cmd_opts]
    return runner.invoke(cli_main, ["-s", store_path, *cmd_opts])


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


@ignore_acme_insecure_warning
class TestRenew:
    def test_renew_with_mixed_certs(self, pebble, runner, tmp_path):
        """
        Any renewable certs are renewed, certs not yet within the renewal
        window are ignored.
        """
        (tmp_path / "certs").mkdir()
        keep70_pem = store_selfsigned(tmp_path, "keep70.com", 70)
        renew1_pem = store_selfsigned(tmp_path, "renew1.com", 1)
        renew5_pem = store_selfsigned(tmp_path, "renew5.com", 5)

        result = invoke_subcommand(runner, tmp_path, "renew")
        print(result)
        print(result.output)
        assert result.exit_code == 0

        assert cert_path(tmp_path, "keep70.com").read_bytes() == keep70_pem

        assert cert_path(tmp_path, "renew1.com").read_bytes() != renew1_pem
        renew1 = load_cert(tmp_path, "renew1.com")
        assert renew1.not_valid_after > from_today(days=10)

        assert cert_path(tmp_path, "renew5.com").read_bytes() != renew5_pem
        renew5 = load_cert(tmp_path, "renew5.com")
        assert renew5.not_valid_after > from_today(days=10)

    def test_renew_days_before_expiry(self, pebble, runner, tmp_path):
        """
        The number of days before expiry we renew is configurable.
        """
        (tmp_path / "certs").mkdir()
        renew10_pem = store_selfsigned(tmp_path, "renew10.com", 10)
        renew20_pem = store_selfsigned(tmp_path, "renew20.com", 20)

        result = invoke_subcommand(
            runner, tmp_path, "renew", "--days-before-expiry", "12"
        )
        print(result)
        print(result.output)
        assert result.exit_code == 0

        assert cert_path(tmp_path, "renew20.com").read_bytes() == renew20_pem

        assert cert_path(tmp_path, "renew10.com").read_bytes() != renew10_pem
        renew1 = load_cert(tmp_path, "renew10.com")
        assert renew1.not_valid_after > from_today(days=10)

        result = invoke_subcommand(
            runner, tmp_path, "renew", "--days-before-expiry", "22"
        )
        print(result)
        print(result.output)
        assert result.exit_code == 0

        assert cert_path(tmp_path, "renew20.com").read_bytes() != renew20_pem
        renew1 = load_cert(tmp_path, "renew20.com")
        assert renew1.not_valid_after > from_today(days=20)


@ignore_acme_insecure_warning
class TestIssue:
    def test_issue_multiple(self, pebble, runner, tmp_path):
        """
        If we're asked to issue certs for multiple domains, we only issue for
        the ones we don't already have.
        """
        (tmp_path / "certs").mkdir()
        exists_pem = store_selfsigned(tmp_path, "exists.com", 70)

        domains = ["new.com", "exists.com", "new2.com"]
        result = invoke_subcommand(runner, tmp_path, "issue", *domains)
        print(result)
        print(result.output)
        assert result.exit_code == 0

        assert cert_path(tmp_path, "exists.com").read_bytes() == exists_pem

        new = load_cert(tmp_path, "new.com")
        assert new.not_valid_after > from_today(days=10)

        new2 = load_cert(tmp_path, "new2.com")
        assert new2.not_valid_after > from_today(days=10)


@ignore_acme_insecure_warning
class TestIssueFromMarathon:
    def test_issue_multiple(self, pebble, fake_marathon, runner, tmp_path):
        """
        If we're asked to issue certs for multiple domains, we only issue for
        the ones we don't already have.
        """
        (tmp_path / "certs").mkdir()
        exists_pem = store_selfsigned(tmp_path, "exists.com", 70)

        fake_marathon.add_apps(
            mkapp("/my-app_1", MARATHON_ACME_0_DOMAIN="exists.com"),
            mkapp("/my-app_2", MARATHON_ACME_0_DOMAIN="new.com"),
            mkapp("/my-app_3", MARATHON_ACME_0_DOMAIN="new2.com"),
        )

        result = invoke_subcommand(
            runner,
            tmp_path,
            "issue-from-marathon",
            "--marathon-urls",
            fake_marathon.base_url,
        )
        print(result)
        print(result.output)
        assert result.exit_code == 0

        assert cert_path(tmp_path, "exists.com").read_bytes() == exists_pem

        new = load_cert(tmp_path, "new.com")
        assert new.not_valid_after > from_today(days=10)

        new2 = load_cert(tmp_path, "new2.com")
        assert new2.not_valid_after > from_today(days=10)


@ignore_acme_insecure_warning
class TestRunService:
    # TODO: Test run-service once we figure out how to stop it cleanly.
    pass


def test_trio_run_translates_errors():
    """
    trio_run() calls trio.run() and if a MultiError is raised, converts it into
    an appropriate ClickException.
    """

    async def raise_multi():
        raise MultiError([ClickException("err1"), ClickException("err2")])

    with pytest.raises(ClickException) as errinfo:
        trio_run(raise_multi)
    assert errinfo.value.message == "multiple errors:\n  err1\n  err2"
