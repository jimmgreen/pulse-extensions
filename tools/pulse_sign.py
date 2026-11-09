"""Pulse extension signing: key generation, manifest signatures and catalog signatures.

Windows CNG RSA-PKCS1-SHA256, byte-compatible with Pulse's package_signature.cpp
(manifest prefix) and catalog verification (catalog prefix). Keys are CNG blobs:
RSAFULLPRIVATEBLOB (keep it secret) and RSAPUBLICBLOB (publish it). A publisher
ID is the lowercase SHA-256 of the exact public blob.
"""
import argparse
import ctypes
import hashlib
import json
import os
import struct
import sys
from pathlib import Path

MANIFEST_PREFIX = b'Pulse.Extension.Manifest.Signature\0v1\0'
CATALOG_PREFIX = b'Pulse.Extension.Catalog.Signature\0v1\0'
ALGORITHM = 'RSA-PKCS1-SHA256'
MAX_SIGNED_BYTES = {MANIFEST_PREFIX: 65536, CATALOG_PREFIX: 4 * 1024 * 1024}


def hex_bytes(value):
    if (not isinstance(value, str) or not value or len(value) > 8192 or len(value) % 2 or
            any(c not in '0123456789abcdef' for c in value)):
        raise ValueError('Invalid canonical hex')
    return bytes.fromhex(value)


def public_blob(blob):
    if len(blob) < 24:
        raise ValueError('Invalid public key')
    magic, bits, exponent, modulus, prime1, prime2 = struct.unpack('<6I', blob[:24])
    if (magic != 0x31415352 or not 2048 <= bits <= 4096 or bits % 8 or modulus != bits // 8 or
            not 1 <= exponent <= 8 or prime1 or prime2 or len(blob) != 24 + exponent + modulus):
        raise ValueError('Only RSA 2048-4096 CNG public blobs are accepted')
    return blob


def fingerprint(public):
    return hashlib.sha256(public_blob(public)).hexdigest()


class Cng:
    def __init__(self):
        if os.name != 'nt':
            raise ValueError('CNG signature operations require Windows')
        self.dll = ctypes.WinDLL('bcrypt.dll')
        void, ulong = ctypes.c_void_p, ctypes.c_ulong
        for name, args in {
            'BCryptOpenAlgorithmProvider': [ctypes.POINTER(void), ctypes.c_wchar_p, ctypes.c_wchar_p, ulong],
            'BCryptCloseAlgorithmProvider': [void, ulong],
            'BCryptImportKeyPair': [void, void, ctypes.c_wchar_p, ctypes.POINTER(void), void, ulong, ulong],
            'BCryptExportKey': [void, void, ctypes.c_wchar_p, void, ulong, ctypes.POINTER(ulong), ulong],
            'BCryptDestroyKey': [void],
            'BCryptSignHash': [void, void, void, ulong, void, ulong, ctypes.POINTER(ulong), ulong],
            'BCryptVerifySignature': [void, void, void, ulong, void, ulong, ulong],
            'BCryptGenerateKeyPair': [void, ctypes.POINTER(void), ulong, ulong],
            'BCryptFinalizeKeyPair': [void, ulong],
        }.items():
            fn = getattr(self.dll, name)
            fn.argtypes, fn.restype = args, ctypes.c_long
        self.algorithm, self.key = void(), void()
        self.check(self.dll.BCryptOpenAlgorithmProvider(ctypes.byref(self.algorithm), 'RSA', None, 0))

    @staticmethod
    def check(status):
        if status < 0:
            raise ValueError('CNG operation failed: %08x' % (status & 0xffffffff))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self.key:
            self.dll.BCryptDestroyKey(self.key)
        if self.algorithm:
            self.dll.BCryptCloseAlgorithmProvider(self.algorithm, 0)

    def generate(self, bits):
        self.check(self.dll.BCryptGenerateKeyPair(self.algorithm, ctypes.byref(self.key), bits, 0))
        self.check(self.dll.BCryptFinalizeKeyPair(self.key, 0))

    def import_key(self, blob, private=False):
        if len(blob) > 8192:
            raise ValueError('Key exceeds size limit')
        buffer = ctypes.create_string_buffer(blob)
        self.check(self.dll.BCryptImportKeyPair(self.algorithm, None,
            'RSAFULLPRIVATEBLOB' if private else 'RSAPUBLICBLOB', ctypes.byref(self.key), buffer, len(blob), 0))

    def export(self, private=False):
        size = ctypes.c_ulong()
        kind = 'RSAFULLPRIVATEBLOB' if private else 'RSAPUBLICBLOB'
        self.check(self.dll.BCryptExportKey(self.key, None, kind, None, 0, ctypes.byref(size), 0))
        result = ctypes.create_string_buffer(size.value)
        self.check(self.dll.BCryptExportKey(self.key, None, kind, result, size.value, ctypes.byref(size), 0))
        return result.raw[:size.value]

    def digest_op(self, prefix, data, signature=None):
        class Padding(ctypes.Structure):
            _fields_ = [('algorithm', ctypes.c_wchar_p)]
        padding = Padding('SHA256')
        digest = hashlib.sha256(prefix + data).digest()
        digest_buffer = ctypes.create_string_buffer(digest)
        if signature is not None:
            buffer = ctypes.create_string_buffer(signature)
            self.check(self.dll.BCryptVerifySignature(self.key, ctypes.byref(padding), digest_buffer,
                                                      len(digest), buffer, len(signature), 2))
            return None
        size = ctypes.c_ulong()
        self.check(self.dll.BCryptSignHash(self.key, ctypes.byref(padding), digest_buffer, len(digest),
                                           None, 0, ctypes.byref(size), 2))
        result = ctypes.create_string_buffer(size.value)
        self.check(self.dll.BCryptSignHash(self.key, ctypes.byref(padding), digest_buffer, len(digest),
                                           result, size.value, ctypes.byref(size), 2))
        return result.raw[:size.value]


def sign(prefix, data, private):
    if len(data) > MAX_SIGNED_BYTES[prefix]:
        raise ValueError('Signed content exceeds size limit')
    with Cng() as cng:
        cng.import_key(private, True)
        public = public_blob(cng.export())
        signature = cng.digest_op(prefix, data)
    return {'formatVersion': 1, 'algorithm': ALGORITHM, 'publisher': hashlib.sha256(public).hexdigest(),
            'signature': signature.hex()}


def verify(prefix, data, signature, public):
    if len(data) > MAX_SIGNED_BYTES[prefix]:
        raise ValueError('Signed content exceeds size limit')
    if (not isinstance(signature, dict) or
            set(signature) != {'formatVersion', 'algorithm', 'publisher', 'signature'} or
            signature['formatVersion'] != 1 or signature['algorithm'] != ALGORITHM):
        raise ValueError('Unsupported signature format')
    if signature['publisher'] != fingerprint(public):
        raise ValueError('Signature publisher does not match the public key')
    with Cng() as cng:
        cng.import_key(public)
        cng.digest_op(prefix, data, hex_bytes(signature['signature']))


def keygen(out_dir, bits):
    out_dir.mkdir(parents=True, exist_ok=True)
    private_path, public_path = out_dir / 'publisher.private.blob', out_dir / 'publisher.public.blob'
    if private_path.exists() or public_path.exists():
        raise ValueError('Refusing to overwrite an existing key in %s' % out_dir)
    with Cng() as cng:
        cng.generate(bits)
        private, public = cng.export(True), cng.export()
    with private_path.open('xb') as stream:
        stream.write(private)
    with public_path.open('xb') as stream:
        stream.write(public)
    return {'private': str(private_path), 'public': str(public_path), 'publisher': fingerprint(public),
            'publicKey': public.hex()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    gen = commands.add_parser('keygen', help='create a new publisher key pair (never commit the private blob)')
    gen.add_argument('--out-dir', type=Path, required=True)
    gen.add_argument('--bits', type=int, default=3072, choices=(2048, 3072, 4096))
    for name in ('sign-manifest', 'sign-catalog'):
        command = commands.add_parser(name)
        command.add_argument('input', type=Path)
        command.add_argument('--private-key', type=Path, required=True)
        command.add_argument('--output', type=Path, required=True)
    for name in ('verify-manifest', 'verify-catalog'):
        command = commands.add_parser(name)
        command.add_argument('input', type=Path)
        command.add_argument('--signature', type=Path, required=True)
        command.add_argument('--public-key', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == 'keygen':
        print(json.dumps(keygen(args.out_dir, args.bits), indent=2))
        return 0
    prefix = MANIFEST_PREFIX if args.command.endswith('manifest') else CATALOG_PREFIX
    data = args.input.read_bytes()
    if args.command.startswith('sign'):
        result = sign(prefix, data, args.private_key.read_bytes())
        args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'signed': True, 'publisher': result['publisher']}))
    else:
        verify(prefix, data, json.loads(args.signature.read_text(encoding='utf-8')), args.public_key.read_bytes())
        print(json.dumps({'verified': True}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
