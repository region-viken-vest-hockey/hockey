#!/usr/bin/env bash
# Resolve the requested ref to exactly one commit, check it out detached and
# record its identity. Refuses an ambiguous ref (branch and tag of the same
# name), an unknown ref and a dirty tree. Used by the software-release workflow.
#
# Usage: REQUESTED_REF=<ref> RELEASE_DIR=<dir> scripts/software-release-source.sh
set -euo pipefail

fail() {
  echo "::error::$*" >&2
  exit 1
}

requested="${REQUESTED_REF:-}"
release_dir="${RELEASE_DIR:?RELEASE_DIR is required}"
[[ -n "$requested" ]] || fail "ref is empty"

if [[ "$requested" =~ ^[0-9a-f]{40}$ ]]; then
  kind="commit"
  sha="$(git rev-parse --verify --quiet "${requested}^{commit}")" || fail "commit $requested is not in the repository"
elif [[ "$requested" =~ ^[A-Za-z0-9._/-]+$ && "$requested" != *..* ]]; then
  has_branch=0
  has_tag=0
  git show-ref --verify --quiet "refs/remotes/origin/$requested" && has_branch=1 || true
  git show-ref --verify --quiet "refs/tags/$requested" && has_tag=1 || true
  if (( has_branch + has_tag != 1 )); then
    fail "ref '$requested' must name exactly one branch or tag (branch=$has_branch tag=$has_tag)"
  fi
  if (( has_branch )); then
    kind="branch"
    sha="$(git rev-parse --verify "refs/remotes/origin/$requested^{commit}")"
  else
    kind="tag"
    sha="$(git rev-parse --verify "refs/tags/$requested^{commit}")"
  fi
else
  fail "ref must be a full commit SHA, a branch name or a tag name"
fi

git checkout --detach --quiet "$sha"
[[ "$(git rev-parse HEAD)" == "$sha" ]] || fail "checkout did not reach $sha"
if [[ -n "$(git status --porcelain --untracked-files=all)" ]]; then
  fail "source tree is dirty at $sha; refusing to build"
fi

mkdir -p "$release_dir"
# Every value here is already validated (hex SHA, fixed kind, safe ref charset), so printf is exact.
printf '{\n  "clean": true,\n  "commit": "%s",\n  "requested_ref": "%s",\n  "resolved_kind": "%s"\n}\n' \
  "$sha" "$requested" "$kind" > "$release_dir/source-identity.json"
echo "Source identity: $kind $sha"
