"""Check rebuild preflight without dispatching any GitHub Actions runs."""

import subprocess
from unittest.mock import patch

import pytest
from rebuild_release_images import ROOT, WORKFLOW, main, rebuild

PUBLISHER = (ROOT / WORKFLOW).read_text()


def result(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout)


@pytest.mark.parametrize("tag", ["1.2.0", "1.2.1", "1.3.0-alpha.1", "nightly-20260918"])
def test_dispatch_requires_matching_remote_tag_workflow(tag: str) -> None:
    with patch(
        "rebuild_release_images.subprocess.run",
        side_effect=[
            result(PUBLISHER),
            result("example/project\n"),
            result(PUBLISHER),
            result(),
        ],
    ) as command:
        rebuild(tag)

    calls = [call.args[0] for call in command.call_args_list]
    assert calls == [
        ("git", "show", f"HEAD:{WORKFLOW}"),
        ("gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"),
        (
            "gh",
            "api",
            f"repos/example/project/contents/{WORKFLOW}?ref=refs%2Ftags%2F{tag}",
            "-H",
            "Accept: application/vnd.github.raw+json",
        ),
        (
            "gh",
            "workflow",
            "run",
            "build-push-images.yml",
            "--repo",
            "example/project",
            "--ref",
            tag,
            "--field",
            f"tag={tag}",
        ),
    ]
    assert all(call.kwargs["check"] for call in command.call_args_list)


@pytest.mark.parametrize(
    "tagged",
    [
        "",
        PUBLISHER.replace("flavor: latest=false", "flavor: latest=true"),
        PUBLISHER.replace(
            "if: needs.validate.outputs.latest == 'true'", "if: always()"
        ),
        PUBLISHER + "\n# A different workflow revision also requires review.\n",
    ],
)
def test_different_publishers_never_dispatch(tagged: str) -> None:
    with patch(
        "rebuild_release_images.subprocess.run",
        side_effect=[result(PUBLISHER), result("example/project\n"), result(tagged)],
    ) as command:
        with pytest.raises(ValueError, match="Rebuild refused"):
            rebuild("1.3.0-alpha.1")
    assert command.call_count == 3


@pytest.mark.parametrize(
    "tag",
    [
        "main",
        "latest",
        "refs/pull/1/head",
        "abc1234",
        "1.2.3\n",
        "01.2.3",
        "1.2.3-" + "a" * 128,
    ],
)
def test_invalid_tags_are_rejected_before_any_commands(tag: str) -> None:
    with patch("rebuild_release_images.subprocess.run") as command:
        with pytest.raises(ValueError, match="Expected an existing"):
            rebuild(tag)
    command.assert_not_called()


@pytest.mark.parametrize("failure_at", [0, 1, 2, 3])
def test_command_failure_stops_without_retry(
    failure_at: int, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    responses = [result(PUBLISHER), result("example/project\n"), result(PUBLISHER)]
    monkeypatch.setattr("sys.argv", ["rebuild_release_images.py", "1.2.0"])
    with patch(
        "rebuild_release_images.subprocess.run",
        side_effect=[
            *responses[:failure_at],
            subprocess.CalledProcessError(1, ["test-command"]),
        ],
    ) as command:
        assert main() == 1
    assert command.call_count == failure_at + 1
    assert "Rebuild failed" in capsys.readouterr().err
