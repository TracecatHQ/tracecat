from pathlib import Path

import pytest

import tests.shared as shared

DATA_PATH = Path(__file__).parent.parent.parent.joinpath("playbooks/alert_management")
TEST_WF_ID = "wf-00000000000000000000000000000000"


# Fixture to load workflow DSLs from YAML files
@pytest.fixture
def filename(request: pytest.FixtureRequest) -> Path:
    path = request.param
    return Path(path)


@pytest.mark.parametrize(
    "filename", [DATA_PATH / "aws-guardduty-to-cases.yml"], indirect=True
)
@pytest.mark.asyncio
async def test_workflow_commit(filename, auth_sandbox):
    print(filename)
    title = f"Test workflow: {filename}"
    workflow_result = await shared.create_workflow(title)
    await shared.commit_workflow(filename, workflow_result["id"])
