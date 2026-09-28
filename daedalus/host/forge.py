"""Opening a pull request when the GitHub CLI is not installed.

The proposal path in :mod:`daedalus.extensions.selfdev` drives ``gh`` for four things: list the pull
requests already open on a head branch, create one, read it back, and edit its title and body. Those
four are HTTP calls to one API with the token that already authenticates the push, so ``gh`` is a
convenience rather than a dependency — and when it is absent, the proposal used to die *after* the
branch had been pushed, leaving a branch whose whole purpose was a pull request that does not exist.

This module is the same four operations over the REST API. It answers in the shape ``gh`` answers in
(``--json number,url`` is a JSON array of objects with those keys; ``pr create`` prints the URL), so
the caller does not branch on which one ran: the boundary is here, and everything above it is
unchanged.

Two properties matter more than the calls themselves:

* it is used **only** when ``gh`` cannot be found, so a working installation behaves exactly as before;
* anything it does not understand is refused with the command in the message, rather than guessed at —
  a wrong pull request is worse than a missing one.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from daedalus.host.gitrun import GitError, mask_credentials

API = "https://api.github.com"

_REMOTE_RE = re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/\s]+?)(?:\.git)?/?$")
"""``https://github.com/o/r.git``, ``git@github.com:o/r`` and the same with a trailing slash."""

_VALUE_FLAGS = {
    "--json": "json",
    "--base": "base",
    "--head": "head",
    "--title": "title",
    "--body": "body",
    "--comment": "comment",
}

_BOOL_FLAGS = {"--squash": "squash", "--merge": "merge", "--rebase": "rebase"}
"""Flags that carry no value: ``pr merge <n> --squash`` names the merge method by being present."""


def remote_slug(url: str) -> tuple[str, str]:
    """The ``(owner, repo)`` a git remote URL names; refuses anything that is not GitHub."""
    match = _REMOTE_RE.search(url.strip())
    if not match:
        raise GitError(f"not a GitHub remote: {mask_credentials(url.strip())[:200]}")
    return match.group("owner"), match.group("repo")


def parse_gh_args(args: tuple[str, ...]) -> tuple[str, dict[str, Any], list[str]]:
    """Split a ``gh`` argument list into ``(subcommand, options, positionals)``.

    Only the shapes the proposal path uses are understood. ``pr list --head x --json number,url`` and
    ``pr view x --json number,url`` carry a positional (the head branch); ``pr create`` and ``pr edit``
    carry flags, and ``edit``'s positional is the pull-request number.
    """
    if len(args) < 2 or args[0] != "pr":
        raise GitError(f"the GitHub CLI is not installed, and this command has no REST equivalent here: gh {' '.join(args)}")
    sub, rest = args[1], list(args[2:])
    opts: dict[str, Any] = {}
    positional: list[str] = []
    while rest:
        item = rest.pop(0)
        if item in _BOOL_FLAGS:
            opts[_BOOL_FLAGS[item]] = True
            continue
        key = _VALUE_FLAGS.get(item)
        if key:
            if not rest:
                raise GitError(f"gh {' '.join(args)}: {item} needs a value")
            opts[key] = rest.pop(0)
        elif item.startswith("--"):
            raise GitError(f"gh {' '.join(args)}: unsupported option {item}")
        else:
            positional.append(item)
    if sub not in {"list", "create", "view", "edit", "merge", "close"}:
        raise GitError(f"gh {' '.join(args)}: unsupported subcommand {sub!r}")
    return sub, opts, positional


_FIELD_OF = {"number": "number", "url": "html_url", "state": "state", "title": "title", "headRefName": "head.ref"}


def _fields(opts: dict[str, Any]) -> list[str]:
    return [name.strip() for name in str(opts.get("json", "number,url")).split(",") if name.strip()]


def _project(row: dict[str, Any], fields: list[str], asked: str) -> dict[str, Any]:
    """``gh --json`` semantics: the keys asked for, and nothing else.

    A field this module cannot answer is refused rather than dropped: a caller reading a key that is
    silently absent would take it for a pull request without that property.
    """
    out: dict[str, Any] = {}
    for name in fields:
        path = _FIELD_OF.get(name)
        if path is None:
            raise GitError(f"gh --json {asked}: {name} is not a field this REST path answers")
        value: Any = row
        for part in path.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        out[name] = value
    return out


class RestForge:
    """The four pull-request operations, over the REST API, with the token the push already uses."""

    def __init__(self, token: str, *, api: str = API, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._token = token
        self._api = api.rstrip("/")
        self._transport = transport

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(base_url=self._api, headers=headers, timeout=30, transport=self._transport) as client:
            response = await client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise GitError(self._refusal(method, path, response))
        return response.json() if response.content else {}

    def _refusal(self, method: str, path: str, response: httpx.Response) -> str:
        """A failure a reader can act on: the call, the status, what the API asked for."""
        body = response.text[:600]
        try:
            body = str(json.loads(response.text).get("message", body))
        except (json.JSONDecodeError, AttributeError):
            pass
        accepted = response.headers.get("x-accepted-github-permissions", "")
        hint = ""
        if response.status_code in (403, 404) and method != "GET":
            hint = (
                " The token needs `pull_requests: write` on this repository"
                + (f" (the API named: {accepted})" if accepted else "")
                + "."
            )
        return mask_credentials(f"{method} {path} -> {response.status_code}: {body}{hint}")

    async def pr_list(self, slug: tuple[str, str], head: str, fields: list[str], asked: str) -> list[dict[str, Any]]:
        owner, repo = slug
        # `--head` matches a branch name only within this repository's own forks; the qualified form is
        # what gh sends and what makes a head branch unambiguous.
        params = {"head": f"{owner}:{head}", "state": "all", "per_page": "10"}
        rows = await self._call("GET", f"/repos/{owner}/{repo}/pulls", params=params)
        return [_project(row, fields, asked) for row in rows]

    async def pr_create(self, slug: tuple[str, str], opts: dict[str, Any]) -> str:
        owner, repo = slug
        payload = {"title": opts.get("title", ""), "body": opts.get("body", ""), "head": opts.get("head", ""), "base": opts.get("base", "main")}
        data = await self._call("POST", f"/repos/{owner}/{repo}/pulls", json=payload)
        return str(data.get("html_url", ""))

    async def pr_edit(self, slug: tuple[str, str], number: str, opts: dict[str, Any]) -> str:
        owner, repo = slug
        payload = {k: v for k, v in (("title", opts.get("title")), ("body", opts.get("body"))) if v is not None}
        data = await self._call("PATCH", f"/repos/{owner}/{repo}/pulls/{number}", json=payload)
        return str(data.get("html_url", ""))

    async def pr_merge(self, slug: tuple[str, str], number: str, opts: dict[str, Any]) -> str:
        """``pr merge <n> --squash``: the same merge the CLI performs, with the same method."""
        owner, repo = slug
        method = next((m for m in ("squash", "merge", "rebase") if m in opts), "merge")
        data = await self._call("PUT", f"/repos/{owner}/{repo}/pulls/{number}/merge", json={"merge_method": method})
        return str(data.get("message", "merged"))

    async def pr_close(self, slug: tuple[str, str], number: str, opts: dict[str, Any]) -> str:
        """``pr close <n> --comment X``: the state change and the comment, in that order."""
        owner, repo = slug
        await self._call("PATCH", f"/repos/{owner}/{repo}/pulls/{number}", json={"state": "closed"})
        comment = opts.get("comment")
        if comment:
            await self._call("POST", f"/repos/{owner}/{repo}/issues/{number}/comments", json={"body": comment})
        return f"closed #{number}"

    async def run(self, args: tuple[str, ...], slug: tuple[str, str]) -> str:
        """Answer in the shape ``gh`` answers in, so the caller above does not branch."""
        sub, opts, positional = parse_gh_args(args)
        asked = str(opts.get("json", "number,url"))
        if sub in {"list", "view"}:
            head = str(opts.get("head") or (positional[0] if positional else ""))
            rows = await self.pr_list(slug, head, _fields(opts), asked)
            if sub == "view" and not rows:
                raise GitError(f"no pull request found for head {head}")
            return json.dumps(rows if sub == "list" else rows[0])
        if sub == "create":
            return await self.pr_create(slug, opts)
        if not positional:
            raise GitError(f"gh {' '.join(args)}: {sub} needs the pull-request number")
        if sub == "merge":
            return await self.pr_merge(slug, positional[0], opts)
        if sub == "close":
            return await self.pr_close(slug, positional[0], opts)
        return await self.pr_edit(slug, positional[0], opts)
