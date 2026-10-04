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
    ("tag", "latest"),
    [
        ("1.2.0", "true"),
        ("1.2.1", "true"),
        ("0.0.0", "true"),
        ("1.3.0-alpha.1", "false"),
        ("1.3.0-rc.1", "false"),
        ("1.0.0-beta.1-rc.2.post1", "false"),
        ("nightly-20260901", "false"),
    ],
)
def test_only_stable_tags_promote_latest(
    tmp_path: Path, event: str, tag: str, latest: str
) -> None:
    result, output = run_image_guard(tmp_path, event=event, ref=tag, tag=tag)
    assert result.returncode == 0, result.stderr
    assert output == {"tag": tag, "latest": latest}


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
    assert output == {"tag": "", "latest": "false"}


def test_only_serialized_promotion_can_publish_latest() -> None:
    workflow = yaml.safe_load(IMAGE_WORKFLOW.read_text())
    promotion = workflow["jobs"]["promote-latest"]
    assert set(promotion["needs"]) == {"validate", "merge-api", "merge-ui"}
    # The implicit success() gate must require both manifest jobs to succeed.
    assert promotion["if"] == "needs.validate.outputs.latest == 'true'"
    assert promotion["concurrency"] == {
        "group": "publish-images-latest",
        "queue": "max",
        "cancel-in-progress": False,
    }
    for name in ("build-and-push-api", "build-and-push-ui", "merge-api", "merge-ui"):
        job = workflow["jobs"][name]
        assert "validate" in job["needs"]
        metadata = next(step for step in job["steps"] if step.get("id") == "meta")
        assert metadata["with"]["flavor"] == "latest=false"
        assert "latest" not in metadata["with"]["tags"]
        for step in job["steps"]:
            # Manifest creation must not append tags outside metadata-action.
            assert ":latest" not in step.get("run", "")


@pytest.mark.parametrize("image", ["tracecat", "tracecat-ui"])
@pytest.mark.parametrize("version", ["1.2.0", "1.3.0-alpha.1"])
def test_manifest_publishes_exactly_the_validated_tags(
    tmp_path: Path, image: str, version: str
) -> None:
    result, _ = run_image_guard(tmp_path, ref=version)
    assert result.returncode == 0
    repository = f"ghcr.io/tracecathq/{image}"
    tags = [f"{repository}:{version}"]

    workflow = yaml.safe_load(IMAGE_WORKFLOW.read_text())
    job = workflow["jobs"]["merge-api" if image == "tracecat" else "merge-ui"]
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


@pytest.mark.parametrize("version", ["1.2.1", "1.3.0"])
@pytest.mark.parametrize("fail_api", [False, True])
def test_promotion_updates_both_images_from_the_same_stable_tag(
    tmp_path: Path, version: str, fail_api: bool
) -> None:
    workflow = yaml.safe_load(IMAGE_WORKFLOW.read_text())
    step = workflow["jobs"]["promote-latest"]["steps"][-1]
    assert step["env"]["VERSION"] == "${{ needs.validate.outputs.tag }}"
    docker = tmp_path / "docker"
    docker.write_text(
        '#!/bin/bash\nprintf "%s\\n" "$@" >> "$CAPTURE_ARGS"\n'
        'if [[ "$FAIL_API" == true && "$*" == *"/tracecat:latest"* ]]; then\n'
        "  exit 1\n"
        "fi\n"
    )
    docker.chmod(0o755)
    args_file = tmp_path / "args"
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", step["run"]],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "CAPTURE_ARGS": str(args_file),
            "VERSION": version,
            "FAIL_API": str(fail_api).lower(),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode != 0) == fail_api, result.stderr
    images = ["tracecat"] if fail_api else ["tracecat", "tracecat-ui"]
    assert args_file.read_text().splitlines() == [
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
