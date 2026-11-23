# Commit language stats

The cards on my profile come from this script. It looks at **every commit I authored**, in every
repo my token can see (personal, org and private), and works out which languages I actually wrote
and roughly how long I spent on each. It doesn't use repo sizes. No dependencies, just the Python
standard library.

## How it counts

1. **Which commits are mine**: the GitHub API lists the commits it attributes to my account
   (`/repos/{repo}/commits?author=<login>`). With `--author-email` the script also counts commits
   made under old e-mail addresses that were never linked to GitHub. This matters for repos that
   were moved between GitHub and GitLab.
2. **What changed**: each repo is bare-cloned into a temp folder, and `git log --numstat` returns
   the lines changed per file. Without `--clone-dir`, the script asks the API once per commit
   instead (slow: 5,000 requests/h).
3. **Language**: comes from the file extension (`languages.py`, Linguist colors). These don't count:
   vendored and generated files (`node_modules`, `dist`, lockfiles, `*.min.js`, Unity `Library/` …),
   file changes over 5,000 lines in a single commit, and data/prose formats (JSON, YAML, Markdown …;
   bring them back with `--kinds code,markup,data,prose`). Merge commits are skipped. A commit
   copied between repos with a new SHA counts once.
4. **Hours**: Git doesn't record working time, so it is estimated from commit timestamps the way
   [git-hours](https://github.com/kimmobrunfeldt/git-hours) does it. If two commits are less than
   2 h apart, the time between them counts. Otherwise a new session starts and is credited 2 h
   (`--session-gap`, `--session-start`). Each commit's time is split across its languages by
   changed lines.

Everything goes into `commit-cache.json`, so later runs only process new commits. The cache holds
private repo and file names, so it is git-ignored. In CI it lives in the Actions cache.

## Run it locally

```powershell
$env:GITHUB_TOKEN = "ghp_..."   # classic PAT with the `repo` scope
python stats/commit_langs.py --clone-dir S:\commit-langs-clones `
  --author-email old@example.com --show-hours --metric hours `
  --title-note "coding since 2009" `
  --out assets/languages.svg --activity-out assets/activity.svg
```

| Option | Purpose |
|---|---|
| `--clone-dir DIR` / `--keep-clones` | fast mode via temporary bare clones; keep them for the next run |
| `--author-email EMAIL` | also count commits made under this e-mail (repeatable, env `COMMIT_LANGS_AUTHOR_EMAILS`) |
| `--metric hours\|lines\|commits` | what the percentages are based on |
| `--show-hours` | hours per language (list layout) |
| `--activity-out FILE` | extra "hours per year" card |
| `--exclude-lang NAME`, `--exclude-repo REGEX`, `--since DATE` | filters |
| `--offline` | re-render from the cache only |
| `--quiet` | don't log repo names (use in public CI) |

## In CI

[`language-stats.yml`](../.github/workflows/language-stats.yml) runs every night and needs two
repository secrets:

- `STATS_TOKEN`: classic PAT with the `repo` scope (authorize it for SSO orgs if needed)
- `AUTHOR_EMAILS`: comma-separated list of extra commit e-mails
