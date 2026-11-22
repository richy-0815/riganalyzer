#!/bin/sh
# Abhaengigkeiten des rideanalyzer-Images auffrischen.
#
#   werkzeuge/update-deps.sh
#
# 1. requirements.lock neu aus requirements.txt (neueste Versionen, mit sha256 je Datei, fuer linux/arm64 + Python 3.14)
# 2. Basis-Image python:3.14-slim: aktuellen Digest von Docker Hub holen und im Dockerfile eintragen
# 3. pip-audit gegen die neue Lockdatei (bekannte Schwachstellen)
# Danach: Diff ansehen, committen, Branch pushen -> QA baut das Image; passt alles -> master.
# Empfehlung: alle 2-4 Wochen, und sofort bei einer Sicherheitsmeldung zu Python/Debian/einem Paket.
set -eu
cd "$(dirname "$0")/../files"

echo "== requirements.lock"
uv pip compile requirements.txt --python-version 3.14 --python-platform aarch64-unknown-linux-gnu \
    --generate-hashes --no-header --upgrade -o requirements.lock -q

echo "== Basis-Image"
TOKEN=$(curl -fsS "https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/python:pull" \
    | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
DIGEST=$(curl -fsSI -H "Authorization: Bearer $TOKEN" \
    -H "Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json" \
    https://registry-1.docker.io/v2/library/python/manifests/3.14-slim | tr -d '\r' | awk -F': ' 'tolower($1)=="docker-content-digest"{print $2}')
case "$DIGEST" in sha256:*) ;; *) echo "Digest nicht ermittelbar" >&2; exit 1 ;; esac
sed -i.bak -E "s|^FROM python:3.14-slim@sha256:[0-9a-f]+|FROM python:3.14-slim@${DIGEST}|" Dockerfile && rm -f Dockerfile.bak
echo "   $DIGEST"

echo "== pip-audit"
uvx pip-audit -r requirements.lock --disable-pip --no-deps --progress-spinner off 2>&1 | grep -v "^WARNING:pip_audit"

echo
git --no-pager diff --stat -- requirements.lock Dockerfile
git --no-pager diff -U0 -- requirements.lock | grep -E '^[-+][a-z0-9_.-]+==' || true
