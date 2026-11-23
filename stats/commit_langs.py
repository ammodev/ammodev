#!/usr/bin/env python3
"""Fetch every commit you authored (via a GitHub token) and render a language-stats SVG card.

Usage:
    GITHUB_TOKEN=ghp_... python commit_langs.py --out languages.svg

Only the Python standard library is used. Results per commit are cached in a JSON file,
so later runs only fetch commits that are new.
"""

import argparse
import base64
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from languages import color_of, detect_language, is_excluded, kind_of  # noqa: E402

API = "https://api.github.com"
CACHE_VERSION = 1


QUIET = False  # --quiet: no per-repo lines (Actions logs of public repos are public)


def log(msg: str) -> None:
    if QUIET and msg.startswith("  ") and "rate limit" not in msg:
        return
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- GitHub API


class GitHub:
    def __init__(self, token: str):
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "commit-langs-stats",
        }
        self.requests = 0

    def get(self, url: str, params=None):
        """GET a URL; returns (json, headers). Waits out rate limits, retries server errors."""
        if not url.startswith("http"):
            url = API + url
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        for attempt in range(6):
            req = urllib.request.Request(url, headers=self.headers)
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    self.requests += 1
                    return json.load(resp), resp.headers
            except urllib.error.HTTPError as e:
                if e.code in (403, 429) and self._wait_for_rate_limit(e.headers):
                    continue
                if e.code == 409:  # empty repository
                    return [], e.headers
                if e.code >= 500 and attempt < 5:
                    time.sleep(2 ** attempt)
                    continue
                body = e.read().decode("utf-8", "replace")[:300]
                raise RuntimeError(f"GitHub API {e.code} for {url}: {body}") from None
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < 5:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"Network error for {url}: {e}") from None
        raise RuntimeError(f"Giving up on {url}")

    @staticmethod
    def _wait_for_rate_limit(headers) -> bool:
        retry_after = headers.get("Retry-After")
        if retry_after:
            wait = int(retry_after) + 1
        elif headers.get("X-RateLimit-Remaining") == "0":
            wait = max(int(headers.get("X-RateLimit-Reset", "0")) - time.time(), 0) + 5
        else:
            return False
        log(f"  rate limit hit, waiting {int(wait)}s ...")
        time.sleep(wait)
        return True

    @staticmethod
    def next_link(headers):
        m = re.search(r'<([^>]+)>;\s*rel="next"', headers.get("Link", "") or "")
        return m.group(1) if m else None

    def paginate(self, path: str, params=None):
        url, params = path, dict(params or {}, per_page=100)
        while url:
            data, headers = self.get(url, params)
            params = None  # the "next" link already contains them
            yield from data
            url = self.next_link(headers)


# --------------------------------------------------------------------------- collection


def load_cache(path: Path) -> dict:
    if path.exists():
        cache = json.loads(path.read_text(encoding="utf-8"))
        if cache.get("version") == CACHE_VERSION:
            return cache
        log("Cache format changed, starting fresh.")
    return {"version": CACHE_VERSION, "repos": {}, "commits": {}}


def save_cache(path: Path, cache: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cache, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def list_repos(gh: GitHub, args) -> list:
    repos = {}
    for r in gh.paginate("/user/repos", {"affiliation": "owner,collaborator,organization_member"}):
        repos[r["full_name"]] = r
    for name in args.extra_repo:
        data, _ = gh.get(f"/repos/{name}")
        repos[data["full_name"]] = data
    result = []
    for name, r in sorted(repos.items()):
        if not args.include_forks and r.get("fork"):
            continue
        if any(re.fullmatch(p, name, re.I) for p in args.exclude_repo):
            continue
        result.append(r)
    return result


def list_my_commits(gh: GitHub, repo: str, login: str, all_branches: bool) -> list:
    """Return [(sha, date, is_merge)] for commits authored by `login`."""
    refs = [None]
    if all_branches:
        refs = [b["name"] for b in gh.paginate(f"/repos/{repo}/branches")] or [None]
    seen = {}
    for ref in refs:
        params = {"author": login}
        if ref:
            params["sha"] = ref
        for c in gh.paginate(f"/repos/{repo}/commits", params):
            if c["sha"] not in seen:
                date = (c["commit"].get("author") or {}).get("date", "")
                seen[c["sha"]] = (c["sha"], date, len(c.get("parents", [])) > 1)
    return list(seen.values())


def fetch_commit_files(gh: GitHub, repo: str, sha: str) -> tuple:
    """Return (author_date, [[filename, additions, deletions], ...]) for one commit."""
    files, url, date = [], f"/repos/{repo}/commits/{sha}", ""
    while url:
        data, headers = gh.get(url)
        date = date or ((data.get("commit") or {}).get("author") or {}).get("date", "")
        files += [[f["filename"], f.get("additions", 0), f.get("deletions", 0)] for f in data.get("files", [])]
        url = gh.next_link(headers)
    return date, files


# --------------------------------------------------------------------------- clone mode


def _git_env(token: str) -> dict:
    """Pass the token as an HTTP header via env, so it shows up neither in argv nor in .git/config."""
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_LFS_SKIP_SMUDGE="1")
    env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.extraHeader",
               GIT_CONFIG_VALUE_0=f"Authorization: Basic {basic}")
    return env


def _rmtree(path: Path) -> None:
    def onerror(func, p, _exc):  # pack files are read-only on Windows
        os.chmod(p, stat.S_IWRITE)
        func(p)
    if path.exists():
        shutil.rmtree(path, onerror=onerror)


def _rename_target(path: str) -> str:
    """'src/{old => new}/a.py' or 'old.py => new.py' -> the new path (like the API reports it)."""
    m = re.fullmatch(r"(.*)\{(.*) => (.*)\}(.*)", path)
    if m:
        return re.sub("/+", "/", m.group(1) + m.group(3) + m.group(4))
    return path.split(" => ", 1)[1] if " => " in path else path


LOG_FORMAT = "--format=@%H%x1f%at%x1f%ae%x1f%an"


def _parse_numstat(output: str) -> dict:
    """Parse `git log LOG_FORMAT --numstat` output into {sha: (date, files, email, name)}."""
    result, current = {}, None
    for line in output.splitlines():
        if line.startswith("@") and "\x1f" in line:
            sha, ts, email, name = (line[1:].split("\x1f") + ["", "", ""])[:4]
            date = datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts.isdigit() else ""
            current = []
            result[sha.strip()] = (date, current, email.strip().lower(), name.strip())
        elif line and current is not None:
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            add, dele, path = parts
            if path.startswith('"') and path.endswith('"'):
                path = path[1:-1]
            path = _rename_target(path)
            current.append([path, int(add) if add.isdigit() else 0, int(dele) if dele.isdigit() else 0])
    return result


class Identity:
    """Which commits count as yours beyond what GitHub links to your account (old/unlinked e-mails)."""

    def __init__(self, emails=(), names=()):
        self.emails = {e.strip().lower() for e in emails if e.strip()}
        self.names = [re.compile(n, re.I) for n in names if n.strip()]

    def __bool__(self):
        return bool(self.emails or self.names)

    def matches(self, email: str, name: str) -> bool:
        return email in self.emails or any(p.fullmatch(name) for p in self.names)

    def fingerprint(self) -> str:
        return "|".join(sorted(self.emails)) + "#" + "|".join(p.pattern for p in self.names)


def clone_and_numstat(repo: str, shas: list, token: str, clone_root: Path, keep: bool, identity=None) -> tuple:
    """Bare-clone a repo and read per-file stats locally.

    Returns (files_by_sha, missing_shas, identity_shas): `files_by_sha` covers the given SHAs plus,
    if `identity` is set, every non-merge commit on any branch whose author matches it.
    """
    env = _git_env(token)
    target = clone_root / repo.replace("/", "__")
    run = lambda *cmd, **kw: subprocess.run(  # noqa: E731
        ["git", *cmd], env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)
    try:
        if (target / "HEAD").exists():
            res = run("-C", str(target), "fetch", "--quiet", "--prune", "origin", "+refs/heads/*:refs/heads/*")
        else:
            _rmtree(target)
            res = run("clone", "--bare", "--quiet", f"https://github.com/{repo}.git", str(target))
        if res.returncode != 0:
            raise RuntimeError(f"git failed: {res.stderr.strip()[:300]}")

        if identity:
            # one pass over every branch: wanted = GitHub-attributed SHAs + identity matches
            res = run("-C", str(target), "-c", "core.quotePath=false", "log", "--all", "--no-merges",
                      "-M", "--numstat", LOG_FORMAT)
            if res.returncode != 0 and "does not have any commits" not in res.stderr:
                raise RuntimeError(f"git log failed: {res.stderr.strip()[:300]}")
            parsed = _parse_numstat(res.stdout)
            wanted = set(shas)
            ident = [s for s, (_, _, email, name) in parsed.items() if s not in wanted and identity.matches(email, name)]
            keep_shas = wanted | set(ident)
            files = {s: v for s, v in parsed.items() if s in keep_shas}
            missing = [s for s in shas if s not in files]
            return files, missing, ident

        # keep only SHAs that exist locally; `git log --stdin` aborts on unknown objects
        check = run("-C", str(target), "cat-file", "--batch-check", input="\n".join(shas) + "\n")
        present = [p[0] for p in (line.split() for line in check.stdout.splitlines()) if len(p) > 1 and p[1] == "commit"]
        missing = [s for s in shas if s not in set(present)]
        files = {}
        if present:
            res = run("-C", str(target), "-c", "core.quotePath=false", "log", "--no-walk=unsorted", "--stdin",
                      "-M", "--numstat", LOG_FORMAT, input="\n".join(present) + "\n")
            if res.returncode != 0:
                raise RuntimeError(f"git log failed: {res.stderr.strip()[:300]}")
            files = _parse_numstat(res.stdout)
            missing += [s for s in present if s not in files]
        return files, missing, []
    finally:
        if not keep:
            _rmtree(target)


def fetch_via_clones(gh: GitHub, todo: list, cache: dict, cache_path: Path, args, scan_repos=(), identity=None) -> list:
    """Process `todo` (+ identity scan of `scan_repos`) by cloning. Returns commits that still need the API."""
    clone_root = Path(args.clone_dir).resolve()
    clone_root.mkdir(parents=True, exist_ok=True)
    by_repo = defaultdict(list)
    for repo in scan_repos:
        by_repo[repo] = []
    for repo, sha, date in todo:
        by_repo[repo].append((sha, date))
    scan = set(scan_repos)  # these need all GitHub-attributed SHAs + an identity pass
    log(f"Cloning {len(by_repo)} repositories into {clone_root} ...")
    leftover, lock, done = [], threading.Lock(), 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                clone_and_numstat, repo,
                cache["repos"][repo]["shas"] if repo in scan else [s for s, _ in items],
                args.token, clone_root, args.keep_clones, identity if repo in scan else None,
            ): repo
            for repo, items in by_repo.items()
        }
        for fut in as_completed(futures):
            repo = futures[fut]
            dates = dict(by_repo[repo])
            with lock:
                done += 1
                try:
                    files, missing, ident = fut.result()
                except Exception as e:  # noqa: BLE001 - fall back to the API for this repo
                    log(f"  [{done}/{len(by_repo)}] {repo}: clone failed, using API ({e})")
                    leftover += [(repo, s, d) for s, d in by_repo[repo]]
                    continue
                for sha, (date, f, _email, _name) in files.items():
                    if sha not in cache["commits"]:
                        cache["commits"][sha] = {"repo": repo, "date": dates.get(sha) or date, "files": f}
                leftover += [(repo, s, dates.get(s, "")) for s in missing if s not in cache["commits"]]
                entry = cache["repos"][repo]
                if repo in scan:
                    entry["identity_shas"] = ident
                    entry["identity_scan"] = entry.get("pushed_at", "") + "#" + identity.fingerprint()
                extra = f", {len(ident)} more via your e-mails/names" if ident else ""
                extra += f", {len(missing)} via API" if missing else ""
                log(f"  [{done}/{len(by_repo)}] {repo}: {len(files)} commits{extra}")
                if done % 10 == 0:
                    save_cache(cache_path, cache)
    save_cache(cache_path, cache)
    if not args.keep_clones:
        try:
            clone_root.rmdir()
        except OSError:
            pass
    return leftover


# --------------------------------------------------------------------------- collection (cont.)


def collect(gh: GitHub, login: str, cache: dict, cache_path: Path, args) -> None:
    repos = list_repos(gh, args)
    log(f"{len(repos)} repositories to scan.")
    todo = []  # (repo, sha, date)
    for i, r in enumerate(repos, 1):
        name, pushed = r["full_name"], r.get("pushed_at")
        known = cache["repos"].get(name)
        if known and known.get("pushed_at") == pushed and known.get("all_branches") == args.all_branches and not args.refresh:
            todo += [(name, sha, "") for sha in known["shas"] if sha not in cache["commits"]]  # resume
            continue
        try:
            commits = list_my_commits(gh, name, login, args.all_branches)
        except RuntimeError as e:
            log(f"  [{i}/{len(repos)}] {name}: skipped ({e})")
            continue
        new = [(name, sha, date) for sha, date, merge in commits if not merge and sha not in cache["commits"]]
        todo += new
        cache["repos"].setdefault(name, {}).update({
            "pushed_at": pushed,
            "all_branches": args.all_branches,
            "private": r.get("private", False),
            "shas": [sha for sha, _, merge in commits if not merge],
        })
        log(f"  [{i}/{len(repos)}] {name}: {len(commits)} commits by you, {len(new)} new")
    save_cache(cache_path, cache)

    identity = Identity(args.author_email, args.author_name)
    scan_repos = []
    if identity and args.clone_dir:
        fp = identity.fingerprint()
        scan_repos = [
            r["full_name"] for r in repos
            if (e := cache["repos"].get(r["full_name"])) is not None
            and e.get("identity_scan") != e.get("pushed_at", "") + "#" + fp
        ]
        log(f"Scanning {len(scan_repos)} repositories for commits by your e-mails/names ...")
    elif identity:
        log("--author-email/--author-name need --clone-dir; ignoring them.")
    if (todo or scan_repos) and args.clone_dir:
        todo = fetch_via_clones(gh, todo, cache, cache_path, args, scan_repos, identity)
    if not todo:
        log("No new commits to fetch via the API.")
        return
    log(f"Fetching file details for {len(todo)} commits via the API ...")
    lock, done = threading.Lock(), 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_commit_files, gh, repo, sha): (repo, sha, date) for repo, sha, date in todo}
        for fut in as_completed(futures):
            repo, sha, date = futures[fut]
            try:
                fetched_date, files = fut.result()
            except RuntimeError as e:
                log(f"  {repo}@{sha[:7]}: {e}")
                continue
            with lock:
                cache["commits"][sha] = {"repo": repo, "date": date or fetched_date, "files": files}
                done += 1
                if done % 100 == 0 or done == len(todo):
                    log(f"  {done}/{len(todo)} commits")
                    save_cache(cache_path, cache)
    save_cache(cache_path, cache)


# --------------------------------------------------------------------------- statistics


def _parse_date(value: str):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def commit_durations(commits: dict, gap_minutes: float, start_minutes: float) -> dict:
    """Estimate hours per commit from timestamps (like git-hours).

    All your commits are sorted by time. If the previous commit is at most `gap_minutes` earlier,
    the time in between counts as work on this commit; otherwise a new session starts and the
    commit gets `start_minutes` (the work before the first commit of a session).
    """
    timed = sorted((d, sha) for sha, c in commits.items() if (d := _parse_date(c.get("date"))))
    hours, prev = {}, None
    for d, sha in timed:
        gap = (d - prev).total_seconds() / 60 if prev else None
        hours[sha] = (gap if gap is not None and gap <= gap_minutes else start_minutes) / 60
        prev = d
    return hours


def compute_stats(cache: dict, args) -> dict:
    exclude_langs = {l.lower() for l in args.exclude_lang}
    kinds = set(args.kinds.split(","))
    since = args.since or ""
    valid_shas = {sha for r in cache["repos"].values() for sha in (*r.get("shas", []), *r.get("identity_shas", []))}
    selected, seen = {}, set()
    for sha, c in sorted(cache["commits"].items(), key=lambda kv: kv[1]["repo"]):
        if sha not in valid_shas or (since and c["date"] < since):
            continue
        if any(re.fullmatch(p, c["repo"], re.I) for p in args.exclude_repo):
            continue
        # a commit copied between repos/hosts (GitHub <-> GitLab, re-imports) gets a new SHA but
        # keeps its author date and diff: count it once
        fingerprint = (c["date"], tuple(sorted(map(tuple, c["files"]))))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        selected[sha] = c
    # session timing uses every commit (also ones without counted files), so gaps stay realistic
    durations = commit_durations(selected, args.session_gap, args.session_start)

    per_lang = defaultdict(lambda: {"lines": 0, "additions": 0, "deletions": 0, "commits": set(), "files": 0,
                                    "hours": 0.0})
    repos, commits = set(), 0
    per_year = defaultdict(lambda: {"commits": 0, "hours": 0.0})
    for sha, c in selected.items():
        lines_by_lang = defaultdict(int)
        for filename, add, dele in c["files"]:
            if add + dele == 0 or add + dele > args.max_lines_per_file:
                continue
            if is_excluded(filename, args.exclude_path):
                continue
            lang = detect_language(filename)
            if not lang or lang.lower() in exclude_langs or kind_of(lang) not in kinds:
                continue
            s = per_lang[lang]
            s["additions"] += add
            s["deletions"] += dele
            s["lines"] += add + dele
            s["files"] += 1
            s["commits"].add(sha)
            lines_by_lang[lang] += add + dele
        if lines_by_lang:
            commits += 1
            repos.add(c["repo"])
            year = per_year[c["date"][:4]]
            year["commits"] += 1
            year["hours"] += durations.get(sha, 0)
            # split the commit's time across its languages by changed lines
            total_lines = sum(lines_by_lang.values())
            for lang, n in lines_by_lang.items():
                per_lang[lang]["hours"] += durations.get(sha, 0) * n / total_lines

    metric = args.metric
    value = {k: (len(v["commits"]) if metric == "commits" else v[metric]) for k, v in per_lang.items()}
    total = sum(value.values()) or 1
    languages = sorted(
        (
            {
                "name": k,
                "color": color_of(k),
                "percent": 100 * value[k] / total,
                "hours": round(v["hours"], 1),
                "lines": v["lines"],
                "additions": v["additions"],
                "deletions": v["deletions"],
                "commits": len(v["commits"]),
            }
            for k, v in per_lang.items()
        ),
        key=lambda x: -x["percent"],
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "metric": metric,
        "commits": commits,
        "repos": len(repos),
        "hours": round(sum(v["hours"] for v in per_lang.values()), 1),
        "languages": languages,
        "years": {y: {"commits": v["commits"], "hours": round(v["hours"], 1)} for y, v in sorted(per_year.items())},
    }


# --------------------------------------------------------------------------- SVG


def top_n(languages: list, n: int) -> list:
    if len(languages) <= n:
        return languages
    head, rest = languages[: n - 1], languages[n - 1 :]
    return head + [{
        "name": "Other", "color": "#6b7280",
        "percent": sum(l["percent"] for l in rest), "hours": sum(l["hours"] for l in rest),
    }]


def fmt_hours(h: float) -> str:
    if h < 1:
        return f"{round(h * 60)} mins"
    return f"{h:,.0f} hrs" if h >= 10 else f"{h:.1f} hrs"


def render_svg(stats: dict, args) -> str:
    langs = top_n(stats["languages"], args.langs_count)
    layout = "list" if args.show_hours and args.layout == "auto" else args.layout
    layout = "compact" if layout == "auto" else layout
    width, pad = args.width, 25
    bar_y, bar_w = 55, width - 2 * pad
    list_y = bar_y + 25
    row_h = 25 if layout == "compact" else 22
    rows = (len(langs) + 1) // 2 if layout == "compact" else len(langs)
    footer = not args.hide_footer
    height = list_y + rows * row_h + (22 if footer else 0) + 5
    # rough text widths (18px semibold title, 10px note) so the note never overlaps the title
    note_fits = bool(args.title_note) and len(args.title) * 10 + len(args.title_note) * 5.5 + 16 < width - 2 * pad
    font = "'Segoe UI', Ubuntu, 'Helvetica Neue', Sans-Serif"
    c = lambda v: "#" + v.lstrip("#")  # noqa: E731

    segments, x = [], 0.0
    for l in langs:
        w = bar_w * l["percent"] / 100
        if w > 0:
            segments.append(f'<rect x="{x:.2f}" y="0" width="{w + 0.5:.2f}" height="8" fill="{l["color"]}"/>')
        x += w

    items = []
    for i, l in enumerate(langs):
        delay = f'style="animation-delay:{150 + i * 80}ms"'
        dot = f'<circle cx="5" cy="6" r="5" fill="{l["color"]}"/>'
        if layout == "compact":
            ix, iy = pad + (i % 2) * bar_w / 2, list_y + (i // 2) * row_h
            label = f'{escape(l["name"])} {l["percent"]:.2f}%'
            if args.show_hours:
                label = f'{escape(l["name"])} {fmt_hours(l["hours"])}'
            items.append(f'<g transform="translate({ix:.1f},{iy})" class="item" {delay}>{dot}'
                         f'<text x="15" y="10" class="lang">{label}</text></g>')
        else:
            iy = list_y + i * row_h
            right = f'{l["percent"]:.1f}%'
            if args.show_hours:
                right = f'{fmt_hours(l["hours"])}<tspan class="dim">  ·  {l["percent"]:.1f}%</tspan>'
            items.append(f'<g transform="translate({pad},{iy})" class="item" {delay}>{dot}'
                         f'<text x="15" y="10" class="lang">{escape(l["name"])}</text>'
                         f'<text x="{bar_w}" y="10" class="lang" text-anchor="end">{right}</text></g>')

    footer_svg = ""
    if footer:
        since = f" since {args.since[:10]}" if args.since else ""
        hours = f' · ≈{stats["hours"]:,.0f} hrs' if args.show_hours else ""
        footer_svg = (
            f'<text x="{pad}" y="{height - 14}" class="foot">'
            f'{stats["commits"]:,} commits · {stats["repos"]} repos{hours}{since}</text>'
        )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" fill="none" role="img" aria-labelledby="title">
<title id="title">{escape(args.title)}</title>
<style>
  .header {{ font: 600 18px {font}; fill: {c(args.title_color)}; }}
  .lang {{ font: 400 11px {font}; fill: {c(args.text_color)}; }}
  .dim {{ opacity: .6; }}
  .foot {{ font: 400 10px {font}; fill: {c(args.text_color)}; opacity: .7; }}
  .item {{ animation: fade .3s ease-in-out backwards; }}
  @keyframes fade {{ from {{ opacity: 0; }} }}
  @media (prefers-reduced-motion: reduce) {{ .item {{ animation: none; }} }}
</style>
<rect x="0.5" y="0.5" rx="{args.border_radius}" width="{width - 1}" height="{height - 1}" fill="{c(args.bg_color)}" stroke="{c(args.border_color)}" stroke-opacity="{0 if args.hide_border else 1}"/>
<text x="{pad}" y="35" class="header">{escape(args.title)}</text>
{f'<text x="{width - pad}" y="34" class="foot" text-anchor="end">{escape(args.title_note)}</text>' if note_fits else ""}
<mask id="bar"><rect x="0" y="0" width="{bar_w}" height="8" rx="4" fill="white"/></mask>
<g transform="translate({pad},{bar_y})"><g mask="url(#bar)">{"".join(segments)}</g></g>
{"".join(items)}
{footer_svg}
</svg>
"""


def render_activity_svg(stats: dict, args) -> str:
    """Bar chart of estimated hours per year."""
    years = stats.get("years", {})
    if not years:
        return ""
    first, last = int(min(years)), max(int(max(years)), datetime.now(timezone.utc).year)
    labels = [str(y) for y in range(first, last + 1)]
    values = [years.get(y, {}).get("hours", 0) for y in labels]
    width, pad, height = args.activity_width, 25, args.activity_height
    chart_top, chart_bottom = 70, height - 42
    chart_h = chart_bottom - chart_top
    slot = (width - 2 * pad) / len(labels)
    bar_w = min(28, slot * 0.62)
    peak = max(values) or 1
    font = "'Segoe UI', Ubuntu, 'Helvetica Neue', Sans-Serif"
    c = lambda v: "#" + v.lstrip("#")  # noqa: E731
    accent = c(args.activity_color)

    bars = []
    for i, (label, v) in enumerate(zip(labels, values)):
        h = max(chart_h * v / peak, 2 if v else 0)
        x = pad + i * slot + (slot - bar_w) / 2
        y = chart_bottom - h
        value = f"{v:,.0f}" if v >= 1 else ("" if not v else "&lt;1")
        bars.append(
            f'<g class="item" style="animation-delay:{150 + i * 60}ms">'
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{h:.1f}" rx="3" fill="{accent}" '
            f'fill-opacity="{0.35 + 0.65 * v / peak:.2f}"/>'
            f'<text x="{x + bar_w / 2:.1f}" y="{y - 5:.1f}" class="val" text-anchor="middle">{value}</text>'
            f'<text x="{x + bar_w / 2:.1f}" y="{chart_bottom + 15}" class="lbl" text-anchor="middle">{label[2:] if len(labels) > 12 else label}</text>'
            f'</g>'
        )
    total = sum(values)
    note = f'<text x="{width - pad}" y="34" class="lbl" text-anchor="end">{escape(args.title_note)}</text>' if args.title_note else ""

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" fill="none" role="img" aria-labelledby="atitle">
<title id="atitle">{escape(args.activity_title)}</title>
<style>
  .header {{ font: 600 18px {font}; fill: {c(args.title_color)}; }}
  .val {{ font: 600 10px {font}; fill: {c(args.title_color)}; }}
  .lbl {{ font: 400 10px {font}; fill: {c(args.text_color)}; }}
  .foot {{ font: 400 10px {font}; fill: {c(args.text_color)}; opacity: .7; }}
  .item {{ animation: fade .3s ease-in-out backwards; }}
  @keyframes fade {{ from {{ opacity: 0; }} }}
  @media (prefers-reduced-motion: reduce) {{ .item {{ animation: none; }} }}
</style>
<rect x="0.5" y="0.5" rx="{args.border_radius}" width="{width - 1}" height="{height - 1}" fill="{c(args.bg_color)}" stroke="{c(args.border_color)}" stroke-opacity="{0 if args.hide_border else 1}"/>
<text x="{pad}" y="35" class="header">{escape(args.activity_title)}</text>
{note}
<line x1="{pad}" y1="{chart_bottom + 0.5}" x2="{width - pad}" y2="{chart_bottom + 0.5}" stroke="{c(args.border_color)}"/>
{"".join(bars)}
<text x="{pad}" y="{height - 12}" class="foot">≈{total:,.0f} hrs estimated from commit timestamps · {stats["commits"]:,} commits</text>
</svg>
"""



# --------------------------------------------------------------------------- CLI


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_argument_group("fetching")
    g.add_argument("--token", default=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"),
                   help="GitHub token (default: $GITHUB_TOKEN or $GH_TOKEN)")
    g.add_argument("--login", help="GitHub user whose commits count (default: owner of the token)")
    g.add_argument("--cache", default="commit-cache.json", help="cache file (default: %(default)s)")
    g.add_argument("--offline", action="store_true", help="don't call the API, only use the cache")
    g.add_argument("--refresh", action="store_true", help="re-list commits of every repo, even unchanged ones")
    g.add_argument("--all-branches", action="store_true", help="scan all branches, not only the default branch")
    g.add_argument("--include-forks", action="store_true", help="also scan your forks")
    g.add_argument("--extra-repo", action="append", default=[], metavar="OWNER/NAME",
                   help="scan an additional repo you are not a member of (repeatable)")
    g.add_argument("--workers", type=int, default=4, help="parallel requests / clones (default: %(default)s)")
    g.add_argument("--clone-dir", default=os.environ.get("COMMIT_LANGS_CLONE_DIR"),
                   help="clone mode: bare-clone repos into this folder and read file stats with git "
                        "instead of one API request per commit (much faster; default: $COMMIT_LANGS_CLONE_DIR)")
    g.add_argument("--author-email", action="append",
                   default=[e for e in os.environ.get("COMMIT_LANGS_AUTHOR_EMAILS", "").split(",") if e.strip()],
                   metavar="EMAIL", help="clone mode: also count commits with this author e-mail, e.g. old e-mails "
                                         "not linked to GitHub (repeatable; env COMMIT_LANGS_AUTHOR_EMAILS, comma-separated)")
    g.add_argument("--author-name", action="append", default=[], metavar="REGEX",
                   help="clone mode: also count commits whose author name fully matches this regex (repeatable)")
    g.add_argument("--keep-clones", action="store_true",
                   help="keep the clones for the next run (it then only fetches); default deletes them")

    s = p.add_argument_group("statistics")
    s.add_argument("--metric", choices=["lines", "additions", "commits", "files", "hours"], default="lines",
                   help="what the percentages are based on (default: %(default)s = additions+deletions)")
    s.add_argument("--kinds", default="code,markup",
                   help="language kinds to count, comma-separated (default: %(default)s)")
    s.add_argument("--exclude-lang", action="append", default=[], metavar="NAME", help="e.g. JSON (repeatable)")
    s.add_argument("--exclude-repo", action="append", default=[], metavar="REGEX",
                   help="full-name regex, e.g. 'me/dotfiles' (repeatable)")
    s.add_argument("--exclude-path", action="append", default=[], metavar="GLOB",
                   help="extra path glob, e.g. '*/generated/*' (repeatable)")
    s.add_argument("--max-lines-per-file", type=int, default=5000,
                   help="ignore a file change bigger than this in one commit (default: %(default)s)")
    s.add_argument("--session-gap", type=float, default=120, metavar="MIN",
                   help="hours estimate: commits closer than this belong to one session (default: %(default)s)")
    s.add_argument("--session-start", type=float, default=120, metavar="MIN",
                   help="hours estimate: time credited before the first commit of a session "
                        "(default: %(default)s, like git-hours)")
    s.add_argument("--since", help="only count commits on/after this date, e.g. 2024-01-01")

    o = p.add_argument_group("output")
    o.add_argument("--out", default="languages.svg", help="SVG output (default: %(default)s)")
    o.add_argument("--json", help="also write the statistics as JSON to this file")
    o.add_argument("--title", default="Most Used Languages")
    o.add_argument("--title-note", help="small text at the top right, e.g. 'coding since 2009'")
    o.add_argument("--activity-out", help="also write a 'hours per year' card to this SVG file")
    o.add_argument("--activity-title", default="Coding Hours per Year")
    o.add_argument("--activity-width", type=int, default=495)
    o.add_argument("--activity-height", type=int, default=0, help="default: same height as the languages card")
    o.add_argument("--activity-color", default="A78BFA")
    o.add_argument("--langs-count", type=int, default=8)
    o.add_argument("--show-hours", action="store_true", help="show estimated hours per language")
    o.add_argument("--layout", choices=["auto", "compact", "list"], default="auto",
                   help="auto = compact, or list when --show-hours is set")
    o.add_argument("--width", type=int, default=300)
    o.add_argument("--bg-color", default="09090B")
    o.add_argument("--title-color", default="FAFAFA")
    o.add_argument("--text-color", default="A1A1AA")
    o.add_argument("--border-color", default="27272A")
    o.add_argument("--border-radius", type=float, default=8)
    o.add_argument("--hide-border", action="store_true")
    o.add_argument("--hide-footer", action="store_true", help="hide the 'N commits · M repos' line")
    o.add_argument("--quiet", action="store_true",
                   help="don't log repo names (use in public CI logs, they would reveal private repos)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    global QUIET
    args = parse_args(argv)
    QUIET = args.quiet
    cache_path = Path(args.cache)
    cache = load_cache(cache_path)

    if not args.offline:
        if not args.token:
            log("No token. Set GITHUB_TOKEN or pass --token (see README).")
            return 2
        gh = GitHub(args.token)
        login = args.login or gh.get("/user")[0]["login"]
        if cache.get("login") not in (None, login):
            log(f"Cache belongs to {cache['login']}, starting fresh for {login}.")
            cache = {"version": CACHE_VERSION, "repos": {}, "commits": {}}
        cache["login"] = login
        log(f"Collecting commits authored by {login} ...")
        started = time.time()
        collect(gh, login, cache, cache_path, args)
        log(f"Done in {time.time() - started:.0f}s with {gh.requests} API requests.")

    stats = compute_stats(cache, args)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    card = render_svg(stats, args)
    Path(args.out).write_text(card, encoding="utf-8")
    if args.activity_out:
        if not args.activity_height:
            args.activity_height = int(re.search(r'height="(\d+)"', card).group(1))
        Path(args.activity_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.activity_out).write_text(render_activity_svg(stats, args), encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps(stats, indent=2), encoding="utf-8")

    log(f"\n{stats['commits']} commits in {stats['repos']} repos, ~{stats['hours']:,.0f} h, by {stats['metric']}:")
    for l in stats["languages"][:20]:
        log(f"  {l['name']:<20} {l['percent']:6.2f}%  {l['hours']:>8,.0f} h  {l['lines']:>10,} lines  {l['commits']:>6} commits")
    log(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
