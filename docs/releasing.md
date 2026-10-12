# Releasing

Development happens in a private repository. The public repository only receives tested releases.
Why it's set up this way: D32 in [decisions.md](decisions.md).

| Lane | Repository | Local remote | Holds |
|---|---|---|---|
| Developer (private) | `cmdiego/ableton_live_mcp-dev` | `origin` | `dev`, feature branches, `release/X.Y` branches, all tags including release candidates |
| Public | `cmdiego/ableton_live_mcp` | `public` | `main` (one commit per release), version tags, GitHub Releases, issues, the CLA bot |

```
private  feature/x ──PR──▶ dev ──cut──▶ release/0.2 ──fixes, rc tags──▶ v0.2.0
                                ▲                                         │
                                └────────── merge fixes back ─────────────┤
                                                                          ▼  scripts/release.sh
public                                   main:  … ── Release v0.1.0 ── Release v0.2.0
```

## Rules

- **Everything merged into `dev` becomes public at the next release**, including `TODO.md` and
  `docs/decisions.md`. Anything that must stay private (paid features, a private roadmap) lives in
  the separate add-on repository (D31), never in `dev` behind a filter.
- Never push to `public` by hand. Only `scripts/release.sh` writes to it.
- `dev` takes feature branches through pull requests in the private repo. On GitHub Free, private
  repos have no branch protection, so check that CI passes before merging.
- A feature that isn't ready when a release branch is cut is reverted on that branch, or stays
  unmerged. A release doesn't ship half a feature.

## Cutting a release

1. **Cut the branch** from `dev`:
   ```bash
   git switch -c release/0.2 origin/dev
   ```
   Then push it to origin:
   ```bash
   git push -u origin release/0.2
   ```
   From now on, only fixes go on it.
2. **Stabilize.** Optionally tag release candidates (`v0.2.0-rc.1`) to test a build; they stay
   private.
3. **Prepare the release commit** on the release branch:
   - `version` in `pyproject.toml`, and `VERSION` in `remote_script/AbletonMCP/surface.py` if the
     surface changed;
   - a `## [0.2.0] - YYYY-MM-DD` section in `CHANGELOG.md`;
   - tool counts in the docs, if the tool set changed.
4. **Check it:**
   - CI passes on the pushed release branch;
   - `uv run python scripts/smoke.py` passes against Live Suite. It plays audio through the
     speakers and adds and removes `__mcp_smoke…` tracks, so use a scratch set. Record the Live
     version in the changelog section: `Smoke-tested on Live 12.4.6 Suite (macOS).`
5. **Tag** the commit and push the tag:
   ```bash
   git tag -a v0.2.0 -m "v0.2.0"
   ```
   ```bash
   git push origin v0.2.0
   ```
6. **Publish.** First do a dry run, which checks everything and prints the diff that would go public:
   ```bash
   scripts/release.sh v0.2.0
   ```
   Then publish:
   ```bash
   scripts/release.sh v0.2.0 --publish
   ```
   The script refuses if:
   - the tag isn't on `origin/release/0.2`;
   - the version or the changelog section doesn't match the tag;
   - no smoke test is recorded;
   - CI didn't pass on the tagged commit;
   - files under `agent/` would be published.
7. **Merge the release branch back** into `dev`, so the version bump, changelog and fixes aren't
   lost.

## Hotfixes

Fix it on the existing `release/X.Y` branch, then bump to `X.Y.1`, update the changelog, smoke-test,
tag, publish, and merge back into `dev`, the same as steps 3 to 7.

## Outside contributions

Pull requests are opened on the public repo, where the CLA bot runs (D31). Public `main` only takes
release commits, so an accepted pull request is brought into the private lane:
1. Fetch the pull request's commits:
   ```bash
   git fetch public pull/<n>/head:pr-<n>
   ```
2. Cherry-pick them onto a branch from `dev`. Cherry-picking keeps the contributor as author.
3. Merge that branch into `dev` through a private pull request.
4. Close the public pull request with a comment saying it's in `dev` and naming the release that
   will include it. Add the release link once it ships.
