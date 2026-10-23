#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."

patch_file="$PWD/Patches/AltSign-auth-fix.patch"
if git -C Dependencies/AltSign apply --check "$patch_file"; then
  git -C Dependencies/AltSign apply "$patch_file"
else
  git -C Dependencies/AltSign apply --reverse --check "$patch_file"
fi

# Exercise actual patched function bodies with an offline transport before compiling the app.
python3 Scripts/test_auth_flow.py

# Never embed an actual user's UDID, account, pairing file, or signing certificate in CI.
python3 - <<'PY'
import plistlib
from pathlib import Path
path = Path('AltStore/Info.plist')
with path.open('rb') as stream:
    info = plistlib.load(stream)
info.pop('ALTDeviceID', None)
info.pop('ALTServerID', None)
info['CFBundleDisplayName'] = 'AltStore Auth Test'
info['CFBundleVersion'] = '6901'
with path.open('wb') as stream:
    plistlib.dump(info, stream, sort_keys=False)
PY

xcodebuild -version
xcodebuild -resolvePackageDependencies -workspace AltStore.xcworkspace -scheme AltStore
xcodebuild build \
  -workspace AltStore.xcworkspace \
  -scheme AltStore \
  -configuration Release \
  -destination 'generic/platform=iOS' \
  -derivedDataPath build/DerivedData \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO \
  CODE_SIGN_IDENTITY='' DEVELOPMENT_TEAM='' \
  ENABLE_USER_SCRIPT_SANDBOXING=NO \
  2>&1 | tee build-auth.log

app='build/DerivedData/Build/Products/Release-iphoneos/AltStore.app'
test -d "$app"
mkdir -p build/Payload
ditto "$app" build/Payload/AltStore.app
(cd build && /usr/bin/zip -qry AltStore-auth-test.ipa Payload)
shasum -a 256 build/AltStore-auth-test.ipa > build/AltStore-auth-test.ipa.sha256
