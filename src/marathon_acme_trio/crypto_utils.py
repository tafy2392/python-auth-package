from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID  # type: ignore
from josepy import JWK  # type: ignore


def generate_rsa_key(key_size):
    return rsa.generate_private_key(
        public_exponent=65537, key_size=key_size, backend=default_backend(),
    )


def key_to_pem(key):
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )


def pem_to_jwk(pem):
    return JWK.load(pem)


def build_csr(key, name):
    subject_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    san = x509.SubjectAlternativeName([x509.DNSName(name)])
    return (
        x509.CertificateSigningRequestBuilder()
        .subject_name(subject_name)
        .add_extension(san, critical=False)
        .sign(key, hashes.SHA256(), default_backend())
    )


def build_csr_pem(key, name):
    csr = build_csr(key, name)
    return csr.public_bytes(serialization.Encoding.PEM)


def pem_to_cert(pem):
    return x509.load_pem_x509_certificate(pem, default_backend())
