from datetime import datetime, timedelta

import pytest  # type: ignore
import trio
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID  # type: ignore

from marathon_acme_trio.crypto_utils import generate_rsa_key, key_to_pem


def dir_tree(path, base=None):
    """
    Collect all nested subpaths of `path` and return them as a set of strings
    relative to `base` (which defaults to `path`).
    """

    if base is None:
        base = path
    found = set()
    for p in path.iterdir():
        found.add(str(p.relative_to(base)))
        if p.is_dir():
            found.update(dir_tree(p, base))
    return found


async def all_tasks_idle(cushion=0.0001):
    """
    Wait for all trio tasks to be idle.
    """
    await trio.testing.wait_all_tasks_blocked(cushion)


def mkapp(id, ports=None, group="external", **labels):
    """
    Build a marathon app dict.
    """
    if not labels:
        labels = {"MARATHON_ACME_0_DOMAIN": "example.com"}
    if group is not None:
        labels["HAPROXY_GROUP"] = group
    if ports is None:
        ports = [9000]
    ports = [{"port": p, "protocol": "tcp", "labels": {}} for p in ports]
    return {"id": id, "labels": labels, "portDefinitions": ports}


def generate_selfsigned(domain, valid_for_days):
    """
    Generate a self-signed certificate.

    https://cryptography.io/en/latest/x509/tutorial/#creating-a-self-signed-certificate
    """
    key = generate_rsa_key(2048)
    key_pem = key_to_pem(key)

    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)])
    cert = (
        x509.CertificateBuilder()
        .issuer_name(name)
        .subject_name(name)
        .not_valid_before(datetime.today() - timedelta(days=1))
        .not_valid_after(datetime.today() + timedelta(days=valid_for_days))
        .serial_number(x509.random_serial_number())
        .public_key(key.public_key())
        .sign(
            private_key=key,
            algorithm=hashes.SHA256(),
            backend=default_backend(),
        )
    )

    return b"".join([key_pem, cert.public_bytes(serialization.Encoding.PEM)])


def ignore_acme_insecure_warning(f_or_c):
    """
    There's no easy way to add pebble's CA to the acme client, so we disable
    cert verification for it.
    """
    filterstr = "ignore::urllib3.exceptions.InsecureRequestWarning"
    return pytest.mark.filterwarnings(filterstr)(f_or_c)
