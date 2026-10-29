"""Exercise release guards without publishing tags, images, or releases."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
IMAGE_WORKFLOW = ROOT / ".github/workflows/build-push-images.yml"


def run_image_guard(
    tmp_path: Path,
    *,
    event: str = "push",
    ref_type: str = "tag",
    ref: str,
    tag: str = "",
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    workflow = yaml.safe_load(IMAGE_WORKFLOW.read_text())
    script = workflow["jobs"]["validate"]["steps"][0]["run"]
    output = tmp_path / "output"
    output.write_text("")
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        env={
            **os.environ,
            "EVENT_NAME": event,
            "REF_TYPE": ref_type,
            "REF_NAME": ref,
            "INPUT_TAG": tag,
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return result, dict(line.split("=", 1) for line in output.read_text().splitlines())


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
@pytest.mark.parametrize(
    ("tag", "latest", "release"),
    [
        ("1.2.0", "true", "true"),
        ("1.2.1", "true", "true"),
        ("0.0.0", "true", "true"),
        ("1.3.0-alpha.1", "false", "true"),
        ("1.3.0-rc.1", "false", "true"),
        ("1.0.0-beta.1-rc.2.post1", "false", "true"),
        ("nightly-20260901", "false", "false"),
    ],
)
def test_only_stable_tags_promote_latest(
    tmp_path: Path, event: str, tag: str, latest: str, release: str
) -> None:
    result, output = run_image_guard(tmp_path, event=event, ref=tag, tag=tag)
    assert result.returncode == 0, result.stderr
    assert output == {"tag": tag, "latest": latest, "release": release}


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
@pytest.mark.parametrize(
    "tag",
    [
        "latest",
        "v1.2.3",
        "01.2.3",
        "1.2.3+build.1",
        "1.2.3-",
        "1.2.3;echo bad",
        "1.2.3\nlatest=true",
        "1.2.3-" + "a" * 128,
    ],
)
def test_rejects_aliases_and_malformed_tags(
    tmp_path: Path, event: str, tag: str
) -> None:
    result, output = run_image_guard(tmp_path, event=event, ref=tag, tag=tag)
    assert result.returncode != 0
    assert output == {}


@pytest.mark.parametrize(
    ("ref_type", "ref", "tag"),
    [
        ("branch", "main", "1.2.3"),
        ("branch", "1.2.3", "1.2.3"),
        ("tag", "1.3.0-alpha.1", "1.3.0"),
        ("tag", "1.2.3", "latest"),
        ("tag", "1.2.3", ""),
    ],
)
def test_manual_rebuild_requires_matching_tag_ref(
    tmp_path: Path, ref_type: str, ref: str, tag: str
) -> None:
    result, output = run_image_guard(
        tmp_path, event="workflow_dispatch", ref_type=ref_type, ref=ref, tag=tag
    )
    assert result.returncode != 0
    assert output == {}


@pytest.mark.parametrize(
    ("event", "branch"),
    [("push", "staging"), ("push", "preview"), ("schedule", "main")],
)
def test_branch_and_scheduled_builds_do_not_promote_latest(
    tmp_path: Path, event: str, branch: str
) -> None:
    result, output = run_image_guard(
        tmp_path, event=event, ref_type="branch", ref=branch
    )
    assert result.returncode == 0, result.stderr
    assert output == {"tag": "", "latest": "false", "release": "false"}


def test_non_release_api_builds_stamp_the_commit_time_on_the_registry_version(
    tmp_path: Path,
) -> None:
    """Release builds keep the file; others get the same .post<commit time>."""
    workflow = yaml.safe_load(IMAGE_WORKFLOW.read_text())
    step = next(
        step
        for step in workflow["jobs"]["build-and-push-api"]["steps"]
        if step.get("name") == "Stamp registry version on non-release builds"
    )
    assert step["if"] == "needs.validate.outputs.release == 'false'"

    init = tmp_path / "packages/tracecat-registry/tracecat_registry/__init__.py"
    init.parent.mkdir(parents=True)
    original = (
        ROOT / "packages/tracecat-registry/tracecat_registry/__init__.py"
    ).read_text()
    init.write_text(original)
    commit_time = "1761696000"
    git_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "GIT_COMMITTER_DATE": f"@{commit_time} +0000",
    }
    for args in (
        ["init", "-q"],
        ["add", "."],
        ["-c", "commit.gpgsign=false", "commit", "-q", "-m", "base"],
    ):
        subprocess.run(["git", *args], cwd=tmp_path, env=git_env, check=True)

    subprocess.run(
        ["bash", "-euo", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )

    stamped = init.read_text()
    for name in ("__version__", "__pep440_version__"):
        base = next(
            line.split('"')[1]
            for line in original.splitlines()
            if line.startswith(f"{name} = ")
        )
        assert f'{name} = "{base}.post{commit_time}"' in stamped.splitlines()
    unstamped = [
        line
        for line in stamped.splitlines()
        if not line.startswith(("__version__ = ", "__pep440_version__ = "))
    ]
    assert unstamped == [
        line
        for line in original.splitlines()
        if not line.startswith(("__version__ = ", "__pep440_version__ = "))
    ]


def test_only_serialized_promotion_can_publish_latest() -> None:
    workflow = yaml.safe_load(IMAGE_WORKFLOW.read_text())
    promotion = workflow["jobs"]["promote-latest"]
    assert set(promotion["needs"]) == {
        "validate",
        "merge-api",
        "build-and-push-ui",
        "merge-tunnel-gateway",
    }
    # The implicit success() gate must require both tag-publishing jobs to succeed.
    assert promotion["if"] == "needs.validate.outputs.latest == 'true'"
    assert promotion["concurrency"] == {
        "group": "publish-images-latest",
        "queue": "max",
        "cancel-in-progress": False,
    }
    for name in ("build-and-push-api", "build-and-push-ui", "merge-api"):
        job = workflow["jobs"][name]
        assert "validate" in job["needs"]
        metadata = next(step for step in job["steps"] if step.get("id") == "meta")
        assert metadata["with"]["flavor"] == "latest=false"
        assert "latest" not in metadata["with"]["tags"]
        for step in job["steps"]:
            # Manifest creation must not append tags outside metadata-action.
            assert ":latest" not in step.get("run", "")


@pytest.mark.parametrize("version", ["1.2.0", "1.3.0-alpha.1"])
def test_manifest_publishes_exactly_the_validated_tags(
    tmp_path: Path, version: str
) -> None:
    result, _ = run_image_guard(tmp_path, ref=version)
    assert result.returncode == 0
    repository = "ghcr.io/tracecathq/tracecat"
    tags = [f"{repository}:{version}"]

    workflow = yaml.safe_load(IMAGE_WORKFLOW.read_text())
    job = workflow["jobs"]["merge-api"]
    script = next(
        step["run"]
        for step in job["steps"]
        if step.get("name") == "Create manifest list and push"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$CAPTURE_ARGS"\n')
    docker.chmod(0o755)
    digests = tmp_path / "digests"
    digests.mkdir()
    for digest in ("a" * 64, "b" * 64):
        (digests / digest).touch()
    args_file = tmp_path / "args"
    subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        cwd=digests,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CAPTURE_ARGS": str(args_file),
            "DOCKER_METADATA_OUTPUT_JSON": json.dumps({"tags": tags}),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert args_file.read_text().splitlines() == [
        "buildx",
        "imagetools",
        "create",
        *(argument for tag in tags for argument in ("-t", tag)),
        f"{repository}@sha256:{'a' * 64}",
        f"{repository}@sha256:{'b' * 64}",
    ]


def test_ui_build_pushes_only_metadata_tags() -> None:
    workflow = yaml.safe_load(IMAGE_WORKFLOW.read_text())
    build = next(
        step
        for step in workflow["jobs"]["build-and-push-ui"]["steps"]
        if step.get("name") == "Build and push"
    )
    # The multi-platform UI build tags the image itself, so metadata-action
    # must be the only tag source.
    assert build["with"]["tags"] == "${{ steps.meta.outputs.tags }}"
    assert "outputs" not in build["with"]


def _run_promotion(
    tmp_path: Path, version: str, latest: str, *, fail_api: bool = False
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run the promote step with a Docker stub.

    `inspect` of latest prints `latest` as its version label. `-` means the
    latest tag doesn't exist yet, `!` means the registry call fails, and `?`
    means the tag exists but its child content is missing.
    """
    workflow = yaml.safe_load(IMAGE_WORKFLOW.read_text())
    step = workflow["jobs"]["promote-latest"]["steps"][-1]
    assert step["env"]["VERSION"] == "${{ needs.validate.outputs.tag }}"
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/bash\n"
        'if [[ "$3" == inspect ]]; then\n'
        '  case "$LATEST" in\n'
        '    -) echo "ERROR: ${@: -1}: not found" >&2; exit 1 ;;\n'
        '    !) echo "ERROR: unexpected status 503" >&2; exit 1 ;;\n'
        "  esac\n"
        '  if [[ "$4" == --raw ]]; then\n'
        "    echo '{}'\n"
        '  elif [[ "$LATEST" == "?" ]]; then\n'
        '    echo "ERROR: sha256:abc: not found" >&2; exit 1\n'
        "  else\n"
        '    echo "$LATEST"\n'
        "  fi\n"
        "  exit 0\n"
        "fi\n"
        'printf "%s\\n" "$@" >> "$CAPTURE_ARGS"\n'
        'if [[ "$FAIL_API" == true && "$*" == *"/tracecat:latest"* ]]; then\n'
        "  exit 1\n"
        "fi\n"
    )
    docker.chmod(0o755)
    args_file = tmp_path / "args"
    args_file.touch()
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", step["run"]],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "CAPTURE_ARGS": str(args_file),
            "VERSION": version,
            "LATEST": latest,
            "FAIL_API": str(fail_api).lower(),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return result, args_file.read_text().splitlines()


def _create_args(version: str, images: list[str]) -> list[str]:
    return [
        arg
        for image in images
        for arg in (
            "buildx",
            "imagetools",
            "create",
            "--tag",
            f"ghcr.io/tracecathq/{image}:latest",
            f"ghcr.io/tracecathq/{image}:{version}",
        )
    ]


ALL_IMAGES = ["tracecat", "tracecat-ui", "tracecat-tunnel-gateway"]


@pytest.mark.parametrize("version", ["1.2.1", "1.3.0"])
@pytest.mark.parametrize("fail_api", [False, True])
def test_promotion_updates_all_images_from_the_same_stable_tag(
    tmp_path: Path, version: str, fail_api: bool
) -> None:
    result, created = _run_promotion(tmp_path, version, "1.2.0", fail_api=fail_api)
    assert (result.returncode != 0) == fail_api, result.stderr
    images = ["tracecat"] if fail_api else ALL_IMAGES
    assert created == _create_args(version, images)


@pytest.mark.parametrize(
    ("version", "latest", "promotes"),
    [
        # An older train's patch never moves latest back.
        ("1.1.4", "1.2.3", False),
        ("1.2.3", "1.3.0", False),
        # Same version re-promotes (rebuild), and patches compare numerically.
        ("1.2.3", "1.2.3", True),
        ("1.2.10", "1.2.9", True),
        ("2.0.0", "1.9.9", True),
        # No latest manifest yet: the first stable release bootstraps it.
        ("1.0.0", "-", True),
    ],
)
def test_promotion_only_moves_latest_forward(
    tmp_path: Path, version: str, latest: str, promotes: bool
) -> None:
    result, created = _run_promotion(tmp_path, version, latest)
    assert result.returncode == 0, result.stderr
    assert created == (_create_args(version, ALL_IMAGES) if promotes else [])


@pytest.mark.parametrize("latest", ["!", "?", ""])
def test_promotion_stops_when_latest_version_is_unreadable(
    tmp_path: Path, latest: str
) -> None:
    # A registry error, missing child content or a missing label must not be
    # read as "no latest".
    result, created = _run_promotion(tmp_path, "1.2.4", latest)
    assert result.returncode != 0
    assert created == []
