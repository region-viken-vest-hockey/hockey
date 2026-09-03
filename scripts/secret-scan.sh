#!/usr/bin/env sh
set -eu

# CI/operator verification protects the current repository snapshot. Historical
# findings cannot be removed without rewriting published Git history, and make
# every future run permanently red. GitHub/remote history can be audited
# separately when intentionally investigating old commits.
if command -v gitleaks >/dev/null 2>&1; then
  exec gitleaks detect --source . --no-git --config .gitleaks.toml --redact --verbose
fi

if command -v docker >/dev/null 2>&1; then
  exec docker run --rm -v "$(pwd):/repo" ghcr.io/gitleaks/gitleaks:latest detect \
    --source=/repo \
    --no-git \
    --config=/repo/.gitleaks.toml \
    --redact \
    --verbose \
    --no-banner
fi

echo "Install gitleaks or Docker to run the secret scan." >&2
exit 1
