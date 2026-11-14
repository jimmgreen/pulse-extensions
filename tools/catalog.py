"""Validate the extension catalog, build index.json and publish it.

  python tools/catalog.py validate [--download]      # CI on every pull request
  python tools/catalog.py build --output out/         # index.json (unsigned)
  python tools/catalog.py publish --private-key KEY   # maintainer: build, sign, upload

Layout:
  publishers/<fingerprint>.json      one reviewed publisher (its public key)
  extensions/<id>/extension.json     one extension and its released versions
"""
import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pulse_sign  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CATALOG_REPO = 'jimmgreen/pulse-extensions'
CATALOG_TAG = 'catalog'
LOCALES = ('zh', 'en', 'tw')
CATEGORIES = ('fileops', 'preview', 'search', 'panel', 'tools')
GLYPHS = ('archive', 'film', 'image', 'doc', 'find', 'ocr', 'git', 'rename', 'duplicate', 'compress', 'cloud', 'puzzle')
RESOURCES = ('context.read', 'files.readCurrentDirectory', 'operations.own', 'settings.own', 'audit.own',
             'files.renameCurrentDirectory', 'files.readDirectory', 'files.watchDirectory', 'files.open',
             'context.events', 'files.transferCurrentSelection', 'files.deleteCurrentSelection',
             'files.transferToDirectory', 'files.transferFromDirectory', 'files.transferDirectoryTree')
ID = re.compile(r'^[a-z0-9][a-z0-9-]{0,30}(\.[a-z0-9][a-z0-9-]{0,30}){1,3}$')
VERSION = re.compile(r'^(0|[1-9]\d{0,4})\.(0|[1-9]\d{0,4})\.(0|[1-9]\d{0,4})$')
PULSE_VERSION = re.compile(r'^\d{1,3}\.\d{1,3}\.\d{1,4}$')
RELEASE_URL = re.compile(r'^https://github\.com/[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}/releases/download/'
                         r'[A-Za-z0-9._-]{1,100}/[A-Za-z0-9._-]{1,128}$')
HTTPS_URL = re.compile(r'^https://[A-Za-z0-9.-]+(/[A-Za-z0-9._~%/+-]*)?$')
COLOR = re.compile(r'^#[0-9A-F]{6}$')
SHA256 = re.compile(r'^[0-9a-f]{64}$')
MAX_PACKAGE = 256 * 1024 * 1024 + 256 * 1024


class CatalogError(ValueError):
    pass


def fail(where, message):
    raise CatalogError('%s: %s' % (where, message))


def strict_load(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                fail(path, 'duplicate field "%s"' % key)
            result[key] = value
        return result
    try:
        return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=pairs)
    except json.JSONDecodeError as error:
        fail(path, 'invalid JSON (%s)' % error)


def fields(where, value, required, optional=()):
    if not isinstance(value, dict):
        fail(where, 'must be an object')
    missing = [k for k in required if k not in value]
    extra = [k for k in value if k not in required and k not in optional]
    if missing:
        fail(where, 'missing ' + ', '.join(missing))
    if extra:
        fail(where, 'unknown field ' + ', '.join(extra))


def text(where, value, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value if c != '\n'):
        fail(where, 'must be non-empty text of at most %d characters' % limit)


def localized(where, value, limit):
    fields(where, value, ('zh', 'en'), ('tw',))
    for locale, item in value.items():
        text('%s.%s' % (where, locale), item, limit)


def version_key(value):
    return tuple(int(part) for part in value.split('.'))


def load_publishers():
    publishers = {}
    for path in sorted((ROOT / 'publishers').glob('*.json')):
        data = strict_load(path)
        fields(path, data, ('formatVersion', 'id', 'name', 'publicKey'), ('url', 'official'))
        if data['formatVersion'] != 1:
            fail(path, 'formatVersion must be 1')
        text('%s name' % path, data['name'], 60)
        if 'url' in data and not HTTPS_URL.match(data['url']):
            fail(path, 'url must be https')
        if 'official' in data and data['official'] is not True:
            fail(path, 'official may only be true')
        try:
            pin = pulse_sign.fingerprint(pulse_sign.hex_bytes(data['publicKey']))
        except ValueError as error:
            fail(path, str(error))
        if data['id'] != pin or path.stem != pin:
            fail(path, 'id and file name must equal the SHA-256 of publicKey (%s)' % pin)
        publishers[pin] = data
    return publishers


def load_extensions(publishers):
    extensions = []
    for path in sorted((ROOT / 'extensions').glob('*/extension.json')):
        data = strict_load(path)
        fields(path, data, ('formatVersion', 'id', 'publisher', 'name', 'summary', 'description', 'category',
                            'icon', 'license', 'versions'), ('homepage', 'source', 'tags'))
        if data['formatVersion'] != 1:
            fail(path, 'formatVersion must be 1')
        if not ID.match(data['id']) or path.parent.name != data['id']:
            fail(path, 'id must be a dotted lowercase name equal to its folder name')
        if data['publisher'] not in publishers:
            fail(path, 'publisher %s has no publishers/<fingerprint>.json' % data['publisher'])
        if data['id'].startswith('pulse.') and not publishers[data['publisher']].get('official'):
            fail(path, 'the pulse.* namespace is reserved for official extensions')
        localized('%s name' % path, data['name'], 40)
        localized('%s summary' % path, data['summary'], 120)
        localized('%s description' % path, data['description'], 1200)
        if data['category'] not in CATEGORIES:
            fail(path, 'category must be one of ' + ', '.join(CATEGORIES))
        fields('%s icon' % path, data['icon'], ('glyph', 'color'))
        if data['icon']['glyph'] not in GLYPHS or not COLOR.match(data['icon']['color']):
            fail(path, 'icon.glyph must be one of %s and icon.color #RRGGBB (upper case)' % ', '.join(GLYPHS))
        text('%s license' % path, data['license'], 80)
        for key in ('homepage', 'source'):
            if key in data and not HTTPS_URL.match(data[key]):
                fail(path, '%s must be an https URL' % key)
        if 'tags' in data and (not isinstance(data['tags'], list) or len(data['tags']) > 8):
            fail(path, 'tags must be a list of at most 8 items')
        for tag in data.get('tags', []):
            text('%s tag' % path, tag, 20)
        if not isinstance(data['versions'], list) or not 1 <= len(data['versions']) <= 20:
            fail(path, 'versions must list 1-20 releases')
        seen = set()
        for index, release in enumerate(data['versions']):
            where = '%s versions[%d]' % (path, index)
            fields(where, release, ('version', 'released', 'minPulse', 'sdkPreview', 'manifestSha256',
                                    'resources', 'package', 'signature', 'notes'))
            if not VERSION.match(release['version']) or release['version'] in seen:
                fail(where, 'version must be a unique MAJOR.MINOR.PATCH')
            seen.add(release['version'])
            try:
                datetime.strptime(release['released'], '%Y-%m-%d')
            except (TypeError, ValueError):
                fail(where, 'released must be YYYY-MM-DD')
            if not PULSE_VERSION.match(release['minPulse']):
                fail(where, 'minPulse must look like 1.0.60')
            if type(release['sdkPreview']) is not int or not 1 <= release['sdkPreview'] <= 99:
                fail(where, 'sdkPreview must be an integer')
            if not SHA256.match(release['manifestSha256']):
                fail(where, 'manifestSha256 must be 64 lowercase hex digits')
            if (not isinstance(release['resources'], list) or
                    any(item not in RESOURCES for item in release['resources']) or
                    len(set(release['resources'])) != len(release['resources'])):
                fail(where, 'resources may only contain ' + ', '.join(RESOURCES))
            for key, limit in (('package', MAX_PACKAGE), ('signature', 16384)):
                fields('%s %s' % (where, key), release[key], ('url', 'size', 'sha256'))
                item = release[key]
                if not RELEASE_URL.match(item['url']):
                    fail(where, '%s.url must be a GitHub release asset URL (mirrors only accelerate those)' % key)
                if type(item['size']) is not int or not 0 < item['size'] <= limit:
                    fail(where, '%s.size out of range' % key)
                if not SHA256.match(item['sha256']):
                    fail(where, '%s.sha256 must be 64 lowercase hex digits' % key)
            if not release['package']['url'].endswith('.pulse-ext') or not release['signature']['url'].endswith('.sig.json'):
                fail(where, 'package must be a .pulse-ext and signature a .sig.json')
            localized('%s notes' % where, release['notes'], 600)
        if [r['version'] for r in data['versions']] != sorted(seen, key=version_key, reverse=True):
            fail(path, 'versions must be listed newest first')
        extensions.append(data)
    ids = [item['id'] for item in extensions]
    if len(ids) != len(set(ids)):
        fail('extensions', 'duplicate extension id')
    return extensions


def fetch(url, limit):
    request = urllib.request.Request(url, headers={'User-Agent': 'pulse-extensions-catalog'})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise CatalogError('%s exceeds %d bytes' % (url, limit))
    return data


def check_download(extension, release, publishers):
    where = '%s %s' % (extension['id'], release['version'])
    package = fetch(release['package']['url'], MAX_PACKAGE)
    signature = fetch(release['signature']['url'], 16384)
    for name, data, item in (('package', package, release['package']), ('signature', signature, release['signature'])):
        if len(data) != item['size'] or hashlib.sha256(data).hexdigest() != item['sha256']:
            fail(where, '%s size or sha256 does not match the download' % name)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        names = archive.namelist()
        if 'manifest.json' not in names or 'package.json' not in names:
            fail(where, 'package lacks manifest.json or package.json')
        manifest_bytes = archive.read('manifest.json')
        manifest = json.loads(manifest_bytes.decode('utf-8'))
        native = manifest.get('native') or {}
        expected = {'manifest.json', 'package.json', native.get('library')} | {f['path'] for f in native.get('files', [])}
        if set(names) != expected:
            fail(where, 'package members do not match the manifest declaration')
        for name in expected - {'manifest.json', 'package.json'}:
            declared = native['sha256'] if name == native.get('library') else next(
                f['sha256'] for f in native['files'] if f['path'] == name)
            if hashlib.sha256(archive.read(name)).hexdigest() != declared:
                fail(where, '%s does not match its manifest hash' % name)
        library = archive.read(native['library']) if native.get('library') else b''
    if manifest.get('id') != extension['id']:
        fail(where, 'manifest id %r differs from the catalog id' % manifest.get('id'))
    if sorted(manifest.get('resources', [])) != sorted(release['resources']):
        fail(where, 'catalog resources differ from the manifest resources')
    if hashlib.sha256(manifest_bytes).hexdigest() != release['manifestSha256']:
        fail(where, 'manifestSha256 does not match the packaged manifest')
    if len(library) < 0x40 or library[:2] != b'MZ':
        fail(where, 'entry library is not a PE image')
    pe = int.from_bytes(library[0x3c:0x40], 'little')
    if library[pe:pe + 4] != b'PE\0\0' or int.from_bytes(library[pe + 4:pe + 6], 'little') != 0x8664:
        fail(where, 'entry library must be x64')
    public = pulse_sign.hex_bytes(publishers[extension['publisher']]['publicKey'])
    try:
        pulse_sign.verify(pulse_sign.MANIFEST_PREFIX, manifest_bytes, json.loads(signature.decode('utf-8')), public)
    except ValueError as error:
        fail(where, 'manifest signature does not verify: %s' % error)


def build_index(publishers, extensions):
    def publisher(item):
        entry = {'id': item['id'], 'name': item['name'], 'publicKey': item['publicKey'],
                 'official': bool(item.get('official'))}
        if 'url' in item:
            entry['url'] = item['url']
        return entry
    used = sorted({item['publisher'] for item in extensions})
    return {
        'formatVersion': 1,
        'generated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'publishers': [publisher(publishers[pin]) for pin in used],
        'extensions': sorted(extensions, key=lambda item: (not publishers[item['publisher']].get('official'), item['id'])),
    }


def dump(value):
    return (json.dumps(value, ensure_ascii=False, indent=1, sort_keys=False) + '\n').encode('utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    validate = commands.add_parser('validate')
    validate.add_argument('--download', action='store_true', help='download every package and verify it')
    validate.add_argument('--only', action='append', default=[], help='extension id to download-check')
    build = commands.add_parser('build')
    build.add_argument('--output', type=Path, required=True)
    publish = commands.add_parser('publish')
    publish.add_argument('--private-key', type=Path, required=True)
    publish.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    try:
        publishers = load_publishers()
        extensions = load_extensions(publishers)
        if args.command == 'validate':
            if args.download:
                for extension in extensions:
                    if args.only and extension['id'] not in args.only:
                        continue
                    for release in extension['versions']:
                        check_download(extension, release, publishers)
                        print('verified %s %s' % (extension['id'], release['version']))
            print('catalog ok: %d publishers, %d extensions' % (len(publishers), len(extensions)))
            return 0
        index = dump(build_index(publishers, extensions))
        if args.command == 'build':
            args.output.mkdir(parents=True, exist_ok=True)
            (args.output / 'index.json').write_bytes(index)
            print('wrote %s' % (args.output / 'index.json'))
            return 0
        for extension in extensions:
            for release in extension['versions']:
                check_download(extension, release, publishers)
        signature = pulse_sign.sign(pulse_sign.CATALOG_PREFIX, index, args.private_key.read_bytes())
        official = [pin for pin, item in publishers.items() if item.get('official')]
        if signature['publisher'] not in official:
            fail('publish', 'the catalog must be signed with an official publisher key')
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            (out / 'index.json').write_bytes(index)
            (out / 'index.sig.json').write_bytes(dump(signature))
            if args.dry_run:
                print('dry run: index.json %d bytes, signed by %s' % (len(index), signature['publisher']))
                return 0
            exists = subprocess.run(['gh', 'release', 'view', CATALOG_TAG, '--repo', CATALOG_REPO],
                                    capture_output=True).returncode == 0
            if not exists:
                subprocess.run(['gh', 'release', 'create', CATALOG_TAG, '--repo', CATALOG_REPO, '--title',
                                'Extension catalog', '--notes', 'Signed index of reviewed Pulse extensions. '
                                'Updated by tools/catalog.py publish.', '--latest'], check=True)
            subprocess.run(['gh', 'release', 'upload', CATALOG_TAG, str(out / 'index.json'),
                            str(out / 'index.sig.json'), '--clobber', '--repo', CATALOG_REPO], check=True)
        print('published catalog: %d extensions' % len(extensions))
        return 0
    except CatalogError as error:
        print('error: %s' % error, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
