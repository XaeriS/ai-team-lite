#!/usr/bin/env bash
# Build "AI Team Lite Setup.exe" on Linux: Windows Python + Windows packages + MinGit + app, wrapped by NSIS.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="${BUILD:-$ROOT/../build}"
VERSION="$(python3 -c "import re;print(re.search(r'__version__ = \"(.+?)\"', open('$ROOT/app/__init__.py').read()).group(1))")"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20251014/cpython-3.12.12+20251014-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
GIT_URL="https://github.com/git-for-windows/git/releases/download/v2.51.0.windows.1/MinGit-2.51.0-64-bit.zip"
mkdir -p "$BUILD/dl" "$BUILD/localwheels"
[ -f "$BUILD/dl/py.tar.gz" ] || curl -fsSL -o "$BUILD/dl/py.tar.gz" "$PY_URL"
[ -f "$BUILD/dl/mingit.zip" ] || curl -fsSL -o "$BUILD/dl/mingit.zip" "$GIT_URL"
command -v uv >/dev/null || pip install -q uv
STAGE="$BUILD/stage-lite"; rm -rf "$STAGE"; mkdir -p "$STAGE/git"
tar xzf "$BUILD/dl/py.tar.gz" -C "$STAGE"
SP="$STAGE/python/Lib/site-packages"
# dedicated launcher so the app shows as "AI Team Lite" in Task Manager and the installer can close it reliably
cp "$STAGE/python/pythonw.exe" "$STAGE/python/AI Team Lite.exe"
# proxy_tools (needed by pywebview) only exists as a source package: build a wheel for it
ls "$BUILD/localwheels"/proxy_tools-*.whl >/dev/null 2>&1 || uv pip wheel proxy_tools==0.1.0 --no-deps -w "$BUILD/localwheels" 2>/dev/null \
  || (python3 -m venv "$BUILD/wv" && "$BUILD/wv/bin/pip" -q install wheel setuptools && "$BUILD/wv/bin/pip" -q wheel proxy_tools==0.1.0 --no-deps -w "$BUILD/localwheels")
uv pip install --target "$SP" --python-platform x86_64-pc-windows-msvc --python-version 3.12 --only-binary :all: \
  --find-links "$BUILD/localwheels" -r "$ROOT/requirements.txt"
python3 -c "import zipfile,sys;zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$BUILD/dl/mingit.zip" "$STAGE/git"
python3 -c "import shutil,sys;shutil.copytree(sys.argv[1],sys.argv[2],ignore=shutil.ignore_patterns('__pycache__'))" "$ROOT/app" "$STAGE/app"
# Archify diagrams (item 78): portable Node.js (official Windows build, from the npm registry) + Archify v3.1.0 (MIT)
NODE_VER=22.22.0; ARCHIFY_TAG=v3.1.0
[ -f "$BUILD/dl/node-win-x64-$NODE_VER.tgz" ] || (cd "$BUILD/dl" && npm pack "node-win-x64@$NODE_VER" --silent)
mkdir -p "$STAGE/node" && tar xzf "$BUILD/dl/node-win-x64-$NODE_VER.tgz" -C "$STAGE/node" --strip-components=2 package/bin/node.exe
printf 'Node.js %s (MIT licence, https://github.com/nodejs/node/blob/main/LICENSE), used to draw Archify diagrams.\n' "$NODE_VER" > "$STAGE/node/README.txt"
[ -d "$BUILD/dl/archify-$ARCHIFY_TAG" ] || git clone -q --depth 1 --branch "$ARCHIFY_TAG" https://github.com/tt-a1i/archify "$BUILD/dl/archify-$ARCHIFY_TAG"
python3 -c "import shutil,sys;shutil.copytree(sys.argv[1],sys.argv[2],ignore=shutil.ignore_patterns('node_modules','.git'))" "$BUILD/dl/archify-$ARCHIFY_TAG/archify" "$STAGE/archify"
cp "$ROOT/README.md" "$STAGE/README.txt" 2>/dev/null || true
OUT="$BUILD/AI-Team-Lite-Setup-$VERSION.exe"
makensis -V2 -DVERSION="$VERSION" -DSTAGE="$STAGE" -DOUTFILE="$OUT" "$ROOT/installer/ai-team.nsi"
ls -la "$OUT"
