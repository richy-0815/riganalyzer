#!/usr/bin/env bash
# Weitere Linter neben ruff/shellcheck/actionlint/biome, lokal und in der CI (ci.yml, Job test):
#   gitleaks  Secrets in der ganzen Historie (.gitleaks.toml)
#   hadolint  Dockerfile (.hadolint.yaml)
#   yamllint  alle YAML-Dateien (.yamllint.yml)
#   JSON      alle *.json im Repo gueltig
# Werkzeuge auf dem Mac: brew install gitleaks hadolint yamllint
set -u
cd "$(dirname "$0")/.." || exit 1
RC=0
for t in gitleaks hadolint yamllint; do
  command -v "$t" >/dev/null || { echo "::error::$t fehlt – brew install $t"; RC=1; }
done
[ "$RC" = 0 ] || exit 1

echo "== gitleaks (Historie)"
gitleaks git --no-banner --redact --no-color --log-level warn . || RC=1
echo "== hadolint"
hadolint Dockerfile || RC=1
echo "== yamllint"
git ls-files -z '*.yml' '*.yaml' | xargs -0 yamllint -f parsable || RC=1
echo "== JSON"
git ls-files -z '*.json' | xargs -0 python3 -c '
import json, sys
bad = 0
for p in sys.argv[1:]:
    try:
        with open(p, encoding="utf-8") as f:
            json.load(f)
    except ValueError as e:
        print(f"{p}: {e}"); bad = 1
sys.exit(bad)' || RC=1
[ "$RC" = 0 ] && echo "Alle weiteren Linter gruen."
exit $RC
