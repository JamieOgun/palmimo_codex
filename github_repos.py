"""Your GitHub repos on the robot, without cloning by hand.

With a ``GITHUB_TOKEN``, a repo is named by voice ("work on test app jamie")
and found among the repos the token can see. It is cloned into the coding
folder the first time, and brought up to date before every task. Each task
runs on its own branch, which this module commits and pushes when Codex is
done, opening a pull request.

The token never reaches Codex or the folder it works in: only a separate bare
mirror (in ~/.cache/palmimo/git-mirrors) talks to GitHub, with the token
passed in git's environment for that one command. See the git section below.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("github_repos")

API = "https://api.github.com"
#: How long the list of repos on GitHub is reused before asking again.
LIST_TTL_SECS = 600.0
#: Pages of 100 repos to read; enough for most accounts.
MAX_PAGES = 5
#: Commits made here use this identity unless the robot's git config has one.
GIT_IDENTITY = ("-c", "user.name=Palmimo", "-c", "user.email=palmimo@users.noreply.github.com")
#: Set on every git command: no hooks, no repo-defined programs (fsmonitor,
#: pager, external diff), and only https and local transports.
SAFE_GIT_CONFIG = (
    ("core.hooksPath", "/dev/null"),
    ("core.fsmonitor", "false"),
    ("core.pager", "cat"),
    ("diff.external", ""),
    ("protocol.allow", "never"),
    ("protocol.https.allow", "always"),
    ("protocol.file.allow", "always"),
    ("credential.helper", ""),
)


class RepoError(Exception):
    """A reason the agent can read out."""


@dataclass
class Remote:
    name: str
    full_name: str
    default_branch: str


@dataclass
class Published:
    branch: str
    pr_url: str | None
    pr_new: bool


def normalize(name: str) -> str:
    """"Test App Jamie", "test-app-jamie" and "test_app_jamie" all become "testappjamie"."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def slug(text: str, limit: int = 40) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return words[:limit].rstrip("-") or "task"


class GitHubRepos:
    def __init__(self, token: str, workdir: Path) -> None:
        self._token = token
        self._workdir = workdir
        self._remotes: dict[str, Remote] = {}
        self._listed_at = 0.0
        self._basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        #: Bare mirrors that talk to GitHub; outside the coding folder Codex works in.
        self._mirrors = Path.home() / ".cache" / "palmimo" / "git-mirrors"

    # ---- GitHub API ----

    def _api(self, method: str, path: str, body: dict | None = None) -> object:
        request = urllib.request.Request(
            API + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:200]
            raise RepoError(f"GitHub said HTTP {exc.code}: {detail}") from exc
        except OSError as exc:
            raise RepoError(f"I can't reach GitHub ({exc}).") from exc

    async def remotes(self) -> dict[str, Remote]:
        """Repos the token can see, keyed by normalized name."""
        if self._remotes and time.monotonic() - self._listed_at < LIST_TTL_SECS:
            return self._remotes
        found: dict[str, Remote] = {}
        for page in range(1, MAX_PAGES + 1):
            batch = await asyncio.to_thread(self._api, "GET", f"/user/repos?per_page=100&sort=pushed&page={page}")
            assert isinstance(batch, list)
            for repo in batch:
                remote = Remote(repo["name"], repo["full_name"], repo.get("default_branch") or "main")
                found.setdefault(normalize(remote.name), remote)
            if len(batch) < 100:
                break
        self._remotes, self._listed_at = found, time.monotonic()
        return found

    async def names(self) -> list[str]:
        """Repo names on GitHub, most recently pushed first; [] if GitHub can't be reached."""
        try:
            return [remote.name for remote in (await self.remotes()).values()]
        except RepoError as exc:
            logger.warning("could not list GitHub repos: %s", exc)
            return []

    # ---- git ----
    #
    # Codex can write anything in its working tree, including .git/config and
    # .git/hooks, so git never runs there with the token. Each repo also has a
    # bare mirror outside the coding folder that only this module writes to:
    # the mirror talks to GitHub (with the token), the working tree only talks
    # to the mirror (without it). Every git command runs with hooks and
    # repo-defined helpers off and a minimal environment, and uses explicit
    # URLs and paths instead of remotes from a config file.

    def _env(self, auth: bool) -> dict[str, str]:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/tmp"),
            "LANG": "C.UTF-8",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
        }
        settings = list(SAFE_GIT_CONFIG)
        if auth:
            # In the environment, not argv, so other processes' `ps` can't see it.
            settings.append(("http.https://github.com/.extraheader", f"Authorization: Basic {self._basic}"))
        env["GIT_CONFIG_COUNT"] = str(len(settings))
        for i, (key, value) in enumerate(settings):
            env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"] = key, value
        return env

    async def _git(self, cwd: Path, *args: str, auth: bool = False, check: bool = True) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=cwd,
            env=self._env(auth),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        text = out.decode(errors="replace").strip()
        if check and proc.returncode != 0:
            raise RepoError(f"git {args[0]} failed: {text[-300:]}")
        return text

    def _url(self, remote: Remote) -> str:
        return f"https://github.com/{remote.full_name}.git"

    def _mirror(self, remote: Remote) -> Path:
        return self._mirrors / f"{remote.full_name.replace('/', '__')}.git"

    async def _sync_mirror(self, remote: Remote) -> Path:
        """The trusted bare mirror of *remote*, fetched from GitHub just now."""
        mirror = self._mirror(remote)
        url = self._url(remote)
        if not mirror.is_dir():
            self._mirrors.mkdir(parents=True, exist_ok=True)
            logger.info("cloning %s", remote.full_name)
            await self._git(self._mirrors, "clone", "-q", "--bare", url, mirror.name, auth=True)
        else:
            await self._git(mirror, "fetch", "-q", "--prune", url, "+refs/heads/*:refs/heads/*", auth=True)
        return mirror

    async def prepare(self, spoken: str | None, task: str) -> tuple[str, Path, str]:
        """Find the repo, bring it up to date, and check out a new task branch. Returns (name, path, branch)."""
        remote = await self.find(spoken)
        mirror = await self._sync_mirror(remote)
        path = self._workdir / remote.name
        base = remote.default_branch
        if not (path / ".git").is_dir():
            await self._git(self._workdir, "clone", "-q", "--branch", base, str(mirror), remote.name)
        else:
            if await self._git(path, "status", "--porcelain"):
                await self._git(path, *GIT_IDENTITY, "stash", "push", "-u", "-m", "palmimo: left over from a task")
                logger.info("stashed leftover changes in %s", remote.name)
            await self._git(path, "fetch", "-q", str(mirror), f"+refs/heads/{base}:refs/remotes/origin/{base}")
            await self._git(path, "checkout", "-q", "-B", base, f"refs/remotes/origin/{base}")
        branch = f"palmimo/{time.strftime('%m%d-%H%M%S')}-{slug(task)}"
        await self._git(path, "checkout", "-q", "-b", branch)
        return remote.name, path, branch

    async def find(self, spoken: str | None) -> Remote:
        remotes = await self.remotes()
        if not spoken:
            raise RepoError("Which repo should I work on?")
        key = normalize(spoken)
        if key in remotes:
            return remotes[key]
        close = [r for k, r in remotes.items() if key and (key in k or k in key)]
        if len(close) == 1:
            return close[0]
        if close:
            raise RepoError("That matches several repos: " + ", ".join(r.name for r in close[:5]) + ".")
        raise RepoError(f"I can't find a repo called {spoken} on your GitHub.")

    async def publish(self, name: str, path: Path, branch: str, title: str, summary: str) -> Published | None:
        """Commit what Codex left, push *branch*, and open a pull request. None if nothing changed."""
        remote = (await self.remotes())[normalize(name)]
        if await self._git(path, "status", "--porcelain"):
            await self._git(path, "add", "-A")
            await self._git(path, *GIT_IDENTITY, "commit", "-q", "--no-verify", "-m", title[:72], "-m", summary)
        ahead = await self._git(path, "rev-list", "--count", f"refs/remotes/origin/{remote.default_branch}..HEAD")
        if ahead == "0":
            return None
        # Bring the commits into the trusted mirror, then push from there.
        mirror = self._mirror(remote)
        await self._git(mirror, "fetch", "-q", str(path), f"+HEAD:refs/heads/{branch}")
        url = self._url(remote)
        await self._git(mirror, "push", "-q", "--no-verify", url, f"refs/heads/{branch}:refs/heads/{branch}", auth=True)
        owner = remote.full_name.split("/")[0]
        existing = await asyncio.to_thread(
            self._api, "GET", f"/repos/{remote.full_name}/pulls?head={owner}:{branch}&state=open"
        )
        if isinstance(existing, list) and existing:
            return Published(branch, existing[0]["html_url"], pr_new=False)
        try:
            pr = await asyncio.to_thread(
                self._api,
                "POST",
                f"/repos/{remote.full_name}/pulls",
                {"title": title[:120], "head": branch, "base": remote.default_branch, "body": summary},
            )
        except RepoError as exc:
            logger.warning("pushed %s but could not open a pull request: %s", branch, exc)
            return Published(branch, None, pr_new=False)
        assert isinstance(pr, dict)
        return Published(branch, pr["html_url"], pr_new=True)
