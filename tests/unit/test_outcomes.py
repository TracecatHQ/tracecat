"""Unit tests for ActionOutcome types."""

from tracecat.dsl.outcomes import (
    ActionOutcomeAdapter,
    ActionOutcomeError,
    ActionOutcomeGather,
    ActionOutcomeScatter,
    ActionOutcomeSkipped,
    ActionOutcomeSuccess,
    ManifestRef,
    ResultRef,
    dict_to_outcome,
    error,
    from_task_result,
    gather,
    is_error,
    is_gather,
    is_scatter,
    is_skipped,
    is_success,
    outcome_to_dict,
    scatter,
    skipped,
    success,
    to_task_result_dict,
)


class TestActionOutcomeSuccess:
    """Tests for ActionOutcomeSuccess."""

    def test_create_success_outcome(self):
        outcome = ActionOutcomeSuccess(result={"foo": "bar"}, result_typename="dict")
        assert outcome.status == "success"
        assert outcome.result == {"foo": "bar"}
        assert outcome.result_typename == "dict"
        assert outcome.error is None
        assert outcome.error_typename is None

    def test_success_factory(self):
        outcome = success(result=[1, 2, 3])
        assert outcome.status == "success"
        assert outcome.result == [1, 2, 3]
        assert outcome.result_typename == "list"

    def test_success_with_interaction(self):
        outcome = ActionOutcomeSuccess(
            result="test",
            result_typename="str",
            interaction={"type": "confirm"},
            interaction_id="int-123",
            interaction_type="confirm",
        )
        assert outcome.interaction == {"type": "confirm"}
        assert outcome.interaction_id == "int-123"
        assert outcome.interaction_type == "confirm"


class TestActionOutcomeError:
    """Tests for ActionOutcomeError."""

    def test_create_error_outcome(self):
        outcome = ActionOutcomeError(
            error={"message": "Something failed"}, error_typename="ValueError"
        )
        assert outcome.status == "error"
        assert outcome.error == {"message": "Something failed"}
        assert outcome.error_typename == "ValueError"
        assert outcome.result is None
        assert outcome.result_typename == "NoneType"

    def test_error_factory(self):
        err = ValueError("test error")
        outcome = error(err=err, error_typename="ValueError")
        assert outcome.status == "error"
        assert outcome.error == err
        assert outcome.error_typename == "ValueError"


class TestActionOutcomeSkipped:
    """Tests for ActionOutcomeSkipped."""

    def test_create_skipped_outcome(self):
        outcome = ActionOutcomeSkipped(reason="run_if condition false")
        assert outcome.status == "skipped"
        assert outcome.reason == "run_if condition false"
        assert outcome.result is None
        assert outcome.result_typename == "NoneType"
        assert outcome.error is None
        assert outcome.error_typename is None

    def test_skipped_factory(self):
        outcome = skipped(reason="propagated from parent")
        assert outcome.status == "skipped"
        assert outcome.reason == "propagated from parent"


class TestActionOutcomeScatter:
    """Tests for ActionOutcomeScatter."""

    def test_create_scatter_outcome(self):
        outcome = ActionOutcomeScatter(count=10, result=10)
        assert outcome.status == "scatter"
        assert outcome.count == 10
        assert outcome.result == 10
        assert outcome.error is None

    def test_scatter_factory(self):
        outcome = scatter(count=5)
        assert outcome.count == 5
        assert outcome.result == 5  # Default to count

    def test_scatter_with_manifest_ref(self):
        ref = ManifestRef(backend="s3", key="manifest-123", size_bytes=1024)
        outcome = scatter(count=100, manifest_ref=ref)
        assert outcome.manifest_ref == ref


class TestActionOutcomeGather:
    """Tests for ActionOutcomeGather."""

    def test_create_gather_outcome(self):
        outcome = ActionOutcomeGather(result=[1, 2, 3], result_typename="list")
        assert outcome.status == "gather"
        assert outcome.result == [1, 2, 3]

    def test_gather_factory(self):
        outcome = gather(count=3)
        assert outcome.count == 3
        assert outcome.result == 3  # Backwards compat: result = count
        assert outcome.result_typename == "int"

    def test_gather_with_errors(self):
        errors = [{"ref": "action_1", "message": "Failed"}]
        outcome = gather(count=1, errors=errors)
        assert outcome.count == 1
        assert outcome.error == errors
        assert outcome.error_typename == "list"


class TestSerialization:
    """Tests for serialization/deserialization."""

    def test_outcome_to_dict(self):
        outcome = success(result={"key": "value"})
        d = outcome_to_dict(outcome)
        assert d["status"] == "success"
        assert d["result"] == {"key": "value"}
        assert d["result_typename"] == "dict"

    def test_dict_to_outcome_success(self):
        d = {"status": "success", "result": 42, "result_typename": "int"}
        outcome = dict_to_outcome(d)
        assert isinstance(outcome, ActionOutcomeSuccess)
        assert outcome.result == 42

    def test_dict_to_outcome_error(self):
        d = {
            "status": "error",
            "error": "Something went wrong",
            "error_typename": "str",
        }
        outcome = dict_to_outcome(d)
        assert isinstance(outcome, ActionOutcomeError)
        assert outcome.error == "Something went wrong"

    def test_dict_to_outcome_skipped(self):
        d = {"status": "skipped", "reason": "condition not met"}
        outcome = dict_to_outcome(d)
        assert isinstance(outcome, ActionOutcomeSkipped)
        assert outcome.reason == "condition not met"

    def test_type_adapter_roundtrip(self):
        original = success(result={"nested": {"data": [1, 2, 3]}})
        json_str = ActionOutcomeAdapter.dump_json(original)
        restored = ActionOutcomeAdapter.validate_json(json_str)
        assert restored.result == original.result


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_is_success(self):
        assert is_success(success(result=1))
        assert is_success(scatter(count=1))
        assert is_success(gather(count=0))
        assert not is_success(error(err="oops"))
        assert not is_success(skipped())

    def test_is_error(self):
        assert is_error(error(err="oops"))
        assert not is_error(success(result=1))
        assert not is_error(skipped())

    def test_is_skipped(self):
        assert is_skipped(skipped())
        assert not is_skipped(success(result=1))
        assert not is_skipped(error(err="oops"))

    def test_is_scatter(self):
        assert is_scatter(scatter(count=5))
        assert not is_scatter(success(result=1))
        assert not is_scatter(gather(count=0))

    def test_is_gather(self):
        assert is_gather(gather(count=0))
        assert not is_gather(success(result=1))
        assert not is_gather(scatter(count=5))


class TestTaskResultCompatibility:
    """Tests for backwards compatibility with TaskResult."""

    def test_from_task_result_success(self):
        task_result = {
            "result": {"data": "value"},
            "result_typename": "dict",
        }
        outcome = from_task_result(task_result)
        assert isinstance(outcome, ActionOutcomeSuccess)
        assert outcome.result == {"data": "value"}

    def test_from_task_result_error(self):
        task_result = {
            "result": None,
            "result_typename": "NoneType",
            "error": {"message": "Failed"},
            "error_typename": "ActionErrorInfo",
        }
        outcome = from_task_result(task_result)
        assert isinstance(outcome, ActionOutcomeError)
        assert outcome.error == {"message": "Failed"}

    def test_from_task_result_with_interaction(self):
        task_result = {
            "result": "confirmed",
            "result_typename": "str",
            "interaction": {"type": "confirm"},
            "interaction_id": "int-456",
            "interaction_type": "confirm",
        }
        outcome = from_task_result(task_result)
        assert isinstance(outcome, ActionOutcomeSuccess)
        assert outcome.interaction == {"type": "confirm"}

    def test_from_task_result_already_outcome(self):
        # If dict already has status, treat as ActionOutcome
        d = {"status": "skipped", "reason": "test"}
        outcome = from_task_result(d)
        assert isinstance(outcome, ActionOutcomeSkipped)

    def test_to_task_result_dict_success(self):
        outcome = success(result=42)
        d = to_task_result_dict(outcome)
        # Only TaskResult fields, no status
        assert d == {"result": 42, "result_typename": "int"}
        assert "status" not in d  # Backwards compat: no new fields

    def test_to_task_result_dict_success_with_interaction(self):
        outcome = success(
            result="ok",
            interaction={"type": "confirm"},
            interaction_id="int-123",
            interaction_type="confirm",
        )
        d = to_task_result_dict(outcome)
        assert d["result"] == "ok"
        assert d["interaction"] == {"type": "confirm"}
        assert d["interaction_id"] == "int-123"
        assert d["interaction_type"] == "confirm"

    def test_to_task_result_dict_error(self):
        outcome = error(err="oops", error_typename="str")
        d = to_task_result_dict(outcome)
        assert d["error"] == "oops"
        assert d["error_typename"] == "str"
        # Backwards compat fields
        assert d["result"] is None
        assert d["result_typename"] == "NoneType"
        assert "status" not in d

    def test_to_task_result_dict_skipped(self):
        outcome = skipped(reason="test")
        d = to_task_result_dict(outcome)
        # Backwards compat fields only
        assert d["result"] is None
        assert d["result_typename"] == "NoneType"
        assert d["error"] is None
        assert d["error_typename"] is None
        assert "status" not in d
        assert "reason" not in d  # reason is not a TaskResult field


class TestResultRef:
    """Tests for ResultRef type."""

    def test_create_result_ref(self):
        ref = ResultRef(
            backend="s3",
            key="results/abc123",
            size_bytes=2048,
            sha256="deadbeef",
        )
        assert ref.backend == "s3"
        assert ref.key == "results/abc123"
        assert ref.size_bytes == 2048
        assert ref.sha256 == "deadbeef"

    def test_success_with_result_ref(self):
        ref = ResultRef(backend="db", key="result-001")
        outcome = success(result=None, result_ref=ref)
        assert outcome.result_ref == ref
        assert outcome.result is None


class TestManifestRef:
    """Tests for ManifestRef type."""

    def test_create_manifest_ref(self):
        ref = ManifestRef(backend="s3", key="manifests/scatter-456", size_bytes=512)
        assert ref.backend == "s3"
        assert ref.key == "manifests/scatter-456"

    def test_scatter_with_manifest_ref(self):
        ref = ManifestRef(backend="inline", key="inline-data")
        outcome = scatter(count=50, manifest_ref=ref)
        assert outcome.manifest_ref == ref
        assert outcome.count == 50
