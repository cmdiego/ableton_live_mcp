#!/usr/bin/env bash
# Publishes a tested release from the private dev repo (remote `origin`) to the public repo (remote
# `public`) as one snapshot commit: the tag's file tree, with the previous public release as its only
# parent, so private history never reaches the public repo. Process and checklist: docs/releasing.md (D32).
#
#   scripts/release.sh v0.2.0            # dry run: runs every check and shows what would be published
#   scripts/release.sh v0.2.0 --publish  # pushes public main and the tag, creates the GitHub Release
set -euo pipefail

PRIVATE_REPO=cmdiego/ableton_live_mcp-dev
PUBLIC_REPO=cmdiego/ableton_live_mcp
PUBLIC_REMOTE=public

fail() { echo "release: $*" >&2; exit 1; }

tag=${1:-}
publish=${2:-}
[[ $tag =~ ^v([0-9]+)\.([0-9]+)\.[0-9]+$ ]] \
  || fail "usage: scripts/release.sh vX.Y.Z [--publish] (release candidates stay private)"
[[ -z $publish || $publish == --publish ]] || fail "unknown option '$publish'"
version=${tag#v}
release_branch="release/${BASH_REMATCH[1]}.${BASH_REMATCH[2]}"

git fetch -q origin --tags
git fetch -q "$PUBLIC_REMOTE"

# The tag: exists here and on origin (same object), on its release branch.
local_tag=$(git rev-parse -q --verify "refs/tags/$tag") || fail "tag $tag doesn't exist; tag the release commit first"
remote_tag=$(git ls-remote origin "refs/tags/$tag" | cut -f1)
[[ $remote_tag == "$local_tag" ]] || fail "push $tag to origin first (git push origin $tag)"
sha=$(git rev-parse "$tag^{commit}")
git merge-base --is-ancestor "$sha" "origin/$release_branch" 2>/dev/null \
  || fail "$tag isn't on origin/$release_branch; releases are cut from a release branch"

# Not released yet.
[[ -z $(git ls-remote "$PUBLIC_REMOTE" "refs/tags/$tag") ]] || fail "$tag is already on the public repo"

# Version and changelog match the tag; the smoke test against Live is recorded.
git show "$sha:pyproject.toml" | grep -qx "version = \"$version\"" \
  || fail "pyproject.toml at $tag doesn't say version = \"$version\""
notes=$(git show "$sha:CHANGELOG.md" | awk -v h="## [$version]" '
  index($0, h) == 1 { on = 1; next }
  on && /^## \[/ { exit }
  on { print }')
[[ -n ${notes//[$'\n ']/} ]] || fail "CHANGELOG.md at $tag has no '## [$version]' section"
grep -q "Smoke-tested on Live" <<<"$notes" \
  || fail "the [$version] changelog section must record 'Smoke-tested on Live <version>' (scripts/smoke.py)"

# CI passed on the tagged commit in the private repo.
passed=$(gh run list -R "$PRIVATE_REPO" --commit "$sha" --workflow ci.yml --json conclusion \
  --jq 'map(select(.conclusion == "success")) | length')
[[ $passed -gt 0 ]] || fail "no successful CI run for $sha in $PRIVATE_REPO; push the release branch and wait for CI"

# No user audio or analyses in the tree (agent/ only keeps its layout).
leaked=$(git ls-tree -r --name-only "$sha" -- agent | grep -vE '^agent/(README\.md|[a-z]+/\.gitkeep)$' \
  | grep -v '^agent/presets/' || true)
[[ -z $leaked ]] || fail "files under agent/ would be published: $leaked"

# The snapshot commit.
msg=$(mktemp)
trap 'rm -f "$msg"' EXIT
printf 'Release %s\n%s\n' "$tag" "$notes" >"$msg"
snapshot=$(git commit-tree "$sha^{tree}" -p "$PUBLIC_REMOTE/main" -F "$msg")

echo "Release $tag: private $sha -> public main $(git rev-parse --short "$PUBLIC_REMOTE/main")..$(git rev-parse --short "$snapshot")"
git diff --stat "$PUBLIC_REMOTE/main" "$snapshot" | tail -n 25

if [[ $publish != --publish ]]; then
  echo "Dry run; nothing was pushed. Re-run with --publish to release."
  exit 0
fi

git push "$PUBLIC_REMOTE" "$snapshot:refs/heads/main"
git push "$PUBLIC_REMOTE" "$snapshot:refs/tags/$tag"
gh release create "$tag" -R "$PUBLIC_REPO" --verify-tag --title "$tag" --notes "$notes"
git fetch -q "$PUBLIC_REMOTE"
echo "Published $tag: https://github.com/$PUBLIC_REPO/releases/tag/$tag"
