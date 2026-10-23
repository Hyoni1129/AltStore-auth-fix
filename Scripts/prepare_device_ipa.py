#!/usr/bin/env python3
"""Insert the connected device's UDID locally, before signing. Never run in CI."""
import argparse
import io
import os
from pathlib import Path
import plistlib
import re
import zipfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('source', type=Path)
parser.add_argument('destination', type=Path)
parser.add_argument('--udid', required=True)
args = parser.parse_args()
if os.environ.get('CI'):
    parser.error('Device-specific packaging must run on the local signing computer, not CI.')
if not re.fullmatch(r'(?:[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}|[0-9A-Fa-f]{40})', args.udid):
    parser.error('Invalid device UDID format')
if args.destination.exists() or args.source.resolve() == args.destination.resolve():
    parser.error('Use a new output filename; the source IPA is preserved.')
with zipfile.ZipFile(args.source) as source:
    infos = [name for name in source.namelist()
             if re.fullmatch(r'Payload/[^/]+\.app/Info\.plist', name)]
    if len(infos) != 1:
        parser.error('Expected exactly one main application Info.plist')
    info = plistlib.loads(source.read(infos[0]))
    if not str(info.get('CFBundleIdentifier', '')).startswith('com.rileytestut.AltStore'):
        parser.error('This local preparation script is only for the AltStore test build')
    info['ALTDeviceID'] = args.udid
    info.pop('ALTServerID', None)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as output:
        for entry in source.infolist():
            content = plistlib.dumps(info, sort_keys=False) if entry.filename == infos[0] else source.read(entry)
            output.writestr(entry, content)
args.destination.write_bytes(buffer.getvalue())
print('Prepared local device IPA. Source preserved. Sign this copy locally before installation.')
