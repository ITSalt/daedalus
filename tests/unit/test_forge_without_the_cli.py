"""The proposal path without the GitHub CLI: same answers, one API, no new dependency.

`gh` is driven in `selfdev.gh` for six operations — list, create, view, edit, merge, close. Each test
below names the operation, the request it must produce and the shape it must answer in, because the
point of the fallback is that the code *above* it cannot tell which one ran.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from daedalus.extensions.selfdev import SelfDevelopment
from daedalus.host.forge import RestForge, parse_gh_args, remote_slug
from daedalus.host.gitrun import GitError

SLUG = ("anchor-inference", "daedalus")
API = "https://api.github.test"
TOKEN = "test-token-not-a-credential"


def forge(handler) -> RestForge:
    return RestForge(TOKEN, api=API, transport=httpx.MockTransport(handler))


def seen(request: httpx.Request) -> tuple[str, str, dict]:
    body = json.loads(request.content) if request.content else {}
    return request.method, request.url.path, {"query": dict(request.url.params), "json": body}


def test_remote_slug_reads_the_three_shapes_git_writes() -> None:
    assert remote_slug("https://github.com/anchor-inference/daedalus.git\n") == SLUG
    assert remote_slug("git@github.com:anchor-inference/daedalus") == SLUG
    assert remote_slug("https://github.com/anchor-inference/daedalus/") == SLUG


def test_remote_slug_refuses_a_remote_it_cannot_name() -> None:
    with pytest.raises(GitError):
        remote_slug("https://gitlab.example.invalid/team/repo.git")


def test_the_cli_arguments_the_proposal_path_uses_are_understood() -> None:
    sub, opts, positional = parse_gh_args(("pr", "list", "--head", "b", "--json", "number,url"))
    assert (sub, opts["head"], opts["json"], positional) == ("list", "b", "number,url", [])
    sub, opts, positional = parse_gh_args(("pr", "edit", "12", "--title", "t", "--body", "b"))
    assert (sub, positional, opts["title"]) == ("edit", ["12"], "t")


def test_an_option_the_fallback_does_not_implement_is_refused_not_ignored() -> None:
    with pytest.raises(GitError, match="unsupported option --draft"):
        parse_gh_args(("pr", "create", "--head", "b", "--draft"))


@pytest.mark.asyncio
async def test_list_answers_the_json_the_caller_parses() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        method, path, extra = seen(request)
        assert (method, path) == ("GET", "/repos/anchor-inference/daedalus/pulls")
        assert extra["query"]["head"] == "anchor-inference:agent/x"
        return httpx.Response(200, json=[{"number": 7, "html_url": "https://github.com/o/r/pull/7", "state": "open"}])

    out = await forge(handler).run(("pr", "list", "--head", "agent/x", "--json", "number,url"), SLUG)
    assert json.loads(out) == [{"number": 7, "url": "https://github.com/o/r/pull/7"}]


@pytest.mark.asyncio
async def test_list_keeps_only_the_fields_asked_for() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"number": 7, "html_url": "u", "state": "open"}])

    out = await forge(handler).run(("pr", "list", "--head", "b", "--json", "number,state"), SLUG)
    assert json.loads(out) == [{"number": 7, "state": "open"}]


@pytest.mark.asyncio
async def test_a_field_the_fallback_cannot_answer_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"number": 7, "html_url": "u"}])

    with pytest.raises(GitError, match="not a field this REST path answers"):
        await forge(handler).run(("pr", "list", "--head", "b", "--json", "number,mergedAt"), SLUG)


@pytest.mark.asyncio
async def test_create_posts_the_branch_and_the_public_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        method, path, extra = seen(request)
        assert (method, path) == ("POST", "/repos/anchor-inference/daedalus/pulls")
        assert extra["json"] == {"title": "T", "body": "B", "head": "agent/x", "base": "main"}
        return httpx.Response(201, json={"number": 8, "html_url": "https://github.com/o/r/pull/8"})

    out = await forge(handler).run(
        ("pr", "create", "--base", "main", "--head", "agent/x", "--title", "T", "--body", "B"), SLUG
    )
    assert out.strip() == "https://github.com/o/r/pull/8"


@pytest.mark.asyncio
async def test_view_answers_one_object_and_refuses_to_invent_one() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"number": 8, "html_url": "https://github.com/o/r/pull/8"}])

    out = await forge(handler).run(("pr", "view", "agent/x", "--json", "number,url"), SLUG)
    assert json.loads(out) == {"number": 8, "url": "https://github.com/o/r/pull/8"}

    empty = forge(lambda request: httpx.Response(200, json=[]))
    with pytest.raises(GitError, match="no pull request found"):
        await empty.run(("pr", "view", "agent/x", "--json", "number,url"), SLUG)


@pytest.mark.asyncio
async def test_edit_patches_only_what_was_given() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        method, path, extra = seen(request)
        assert (method, path) == ("PATCH", "/repos/anchor-inference/daedalus/pulls/12")
        assert extra["json"] == {"title": "T2", "body": "B2"}
        return httpx.Response(200, json={"html_url": "u"})

    await forge(handler).run(("pr", "edit", "12", "--title", "T2", "--body", "B2"), SLUG)


@pytest.mark.asyncio
async def test_merge_and_close_do_what_the_approval_path_asks_for() -> None:
    calls: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(seen(request))
        return httpx.Response(200, json={"message": "Pull Request successfully merged"})

    await forge(handler).run(("pr", "merge", "8", "--squash"), SLUG)
    assert calls[-1][0:2] == ("PUT", "/repos/anchor-inference/daedalus/pulls/8/merge")
    assert calls[-1][2]["json"] == {"merge_method": "squash"}

    await forge(handler).run(("pr", "close", "8", "--comment", "Rejected."), SLUG)
    assert calls[-2][0:2] == ("PATCH", "/repos/anchor-inference/daedalus/pulls/8")
    assert calls[-2][2]["json"] == {"state": "closed"}
    assert calls[-1][0:2] == ("POST", "/repos/anchor-inference/daedalus/issues/8/comments")
    assert calls[-1][2]["json"] == {"body": "Rejected."}


@pytest.mark.asyncio
async def test_a_refused_call_names_the_permission_the_api_asked_for() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"message": "Resource not accessible by personal access token"},
            headers={"x-accepted-github-permissions": "pull_requests=write"},
        )

    with pytest.raises(GitError) as err:
        await forge(handler).run(("pr", "create", "--head", "agent/x", "--title", "T", "--body", "B"), SLUG)
    message = str(err.value)
    assert "403" in message
    assert "pull_requests: write" in message
    assert "pull_requests=write" in message


@pytest.mark.asyncio
async def test_the_token_never_reaches_the_error_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    with pytest.raises(GitError) as err:
        await forge(handler).run(("pr", "view", "agent/x", "--json", "number,url"), SLUG)
    assert TOKEN not in str(err.value)


class _Recorder(SelfDevelopment):
    """`SelfDevelopment.gh` with the subprocess and the network taken out, keeping its routing."""

    def __init__(self, *, token: str) -> None:  # noqa: D107
        self.app = type("App", (), {"settings": type("S", (), {"github_token": token})()})()
        self.forge_calls: list[tuple[str, ...]] = []

    def _git_env(self) -> dict[str, str]:
        return {}

    async def forge(self, *args: str, cwd: Path) -> str:
        self.forge_calls.append(args)
        return "[]"


@pytest.mark.asyncio
async def test_gh_is_used_when_it_is_installed(monkeypatch) -> None:
    monkeypatch.setattr("daedalus.extensions.selfdev.shutil.which", lambda name: "/usr/bin/gh")
    calls: list[list[str]] = []

    async def fake_run_command(cmd, **kwargs):  # noqa: ANN001, ANN003
        calls.append(list(cmd))
        return "[]"

    monkeypatch.setattr("daedalus.extensions.selfdev.run_command", fake_run_command)
    dev = _Recorder(token="t")
    await SelfDevelopment.gh(dev, "pr", "list", "--head", "b", "--json", "number,url", cwd=Path("/tmp"))
    assert calls == [["gh", "pr", "list", "--head", "b", "--json", "number,url"]]
    assert dev.forge_calls == []


@pytest.mark.asyncio
async def test_the_fallback_is_used_when_it_is_not(monkeypatch) -> None:
    monkeypatch.setattr("daedalus.extensions.selfdev.shutil.which", lambda name: None)
    dev = _Recorder(token="t")
    await SelfDevelopment.gh(dev, "pr", "list", "--head", "b", "--json", "number,url", cwd=Path("/tmp"))
    assert dev.forge_calls == [("pr", "list", "--head", "b", "--json", "number,url")]


@pytest.mark.asyncio
async def test_without_a_token_the_fallback_says_so_rather_than_failing_obscurely() -> None:
    dev = _Recorder(token="")
    with pytest.raises(GitError, match="no GitHub token is configured"):
        await SelfDevelopment.forge(dev, "pr", "list", "--head", "b", "--json", "number,url", cwd=Path("/tmp"))
