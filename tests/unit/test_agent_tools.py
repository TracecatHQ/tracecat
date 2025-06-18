"""Tests for agent tools implementations after secure refactor."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from tracecat_registry.integrations.agents.tools import create_secure_file_tools


@pytest.fixture()
def secure_tools():
    """Provision a fresh, isolated set of secure tool functions for each test."""

    with tempfile.TemporaryDirectory() as tmpdir:
        tools_dict = {t.name: t.function for t in create_secure_file_tools(tmpdir)}
        tmp_path = Path(tmpdir)
        yield tools_dict, tmp_path


class TestReadFile:
    def test_read_file_success(self, secure_tools):
        """Read a small text file successfully."""

        tools, tmp_path = secure_tools
        file_path = tmp_path / "sample.txt"
        content = "Line 1\nLine 2\nLine 3"
        file_path.write_text(content)

        result = tools["read_file"](str(file_path.relative_to(tmp_path)))

        assert result == content

    def test_read_file_limit_250_lines(self, secure_tools):
        """Ensure only the first 250 lines are returned."""

        tools, tmp_path = secure_tools
        file_path = tmp_path / "many_lines.txt"
        lines = [f"Line {i}" for i in range(300)]
        file_path.write_text("\n".join(lines))

        result = tools["read_file"](str(file_path.relative_to(tmp_path)))
        result_lines = result.split("\n")

        assert len(result_lines) == 250
        assert result_lines[0] == "Line 0"
        assert result_lines[249] == "Line 249"

    def test_read_file_nonexistent(self, secure_tools):
        """Reading a missing file raises an error."""

        tools, _ = secure_tools
        with pytest.raises(ValueError, match="Path does not exist"):
            tools["read_file"]("missing.txt")

    def test_read_file_directory(self, secure_tools):
        """Attempting to read a directory should fail."""

        tools, tmp_path = secure_tools
        dir_path = tmp_path / "somedir"
        dir_path.mkdir()

        with pytest.raises(ValueError, match="Path is not a file"):
            tools["read_file"](str(dir_path.relative_to(tmp_path)))

    @patch(
        "tracecat_registry.integrations.agents.tools.TRACECAT__MAX_FILE_SIZE_BYTES", 10
    )
    def test_read_file_too_large(self, secure_tools):
        """Reading a file larger than the configured limit should fail."""

        tools, tmp_path = secure_tools
        file_path = tmp_path / "large.txt"
        file_path.write_text("This content is longer than 10 bytes")

        with pytest.raises(ValueError, match="File too large"):
            tools["read_file"](str(file_path.relative_to(tmp_path)))


class TestCreateFile:
    def test_create_file_success(self, secure_tools):
        tools, tmp_path = secure_tools
        rel_path = "test_file.txt"
        content = "Test content"

        result = tools["create_file"](rel_path, content)

        assert "File created successfully" in result
        full_path = tmp_path / rel_path
        assert full_path.exists() and full_path.read_text() == content

    def test_create_file_empty_content(self, secure_tools):
        tools, tmp_path = secure_tools
        rel_path = "empty_file.txt"

        result = tools["create_file"](rel_path)

        assert "File created successfully" in result
        assert (tmp_path / rel_path).read_text() == ""

    def test_create_file_with_parent_dirs(self, secure_tools):
        tools, tmp_path = secure_tools
        rel_path = "subdir/test_file.txt"
        content = "Test content"

        result = tools["create_file"](rel_path, content)

        assert "File created successfully" in result
        full_path = tmp_path / rel_path
        assert full_path.exists() and full_path.read_text() == content

    def test_create_file_already_exists(self, secure_tools):
        tools, tmp_path = secure_tools
        rel_path = "exists.txt"

        # Pre-create the file
        (tmp_path / rel_path).write_text("original")

        with pytest.raises(ValueError, match="File already exists"):
            tools["create_file"](rel_path, "content")

    @patch(
        "tracecat_registry.integrations.agents.tools.TRACECAT__MAX_FILE_SIZE_BYTES", 10
    )
    def test_create_file_content_too_large(self, secure_tools):
        tools, _ = secure_tools
        rel_path = "too_large.txt"
        content = "This content is longer than 10 bytes"

        with pytest.raises(ValueError, match="Content too large"):
            tools["create_file"](rel_path, content)


class TestSearchFiles:
    def test_search_files_exact_match(self, secure_tools):
        tools, tmp_path = secure_tools

        (tmp_path / "test_file.txt").touch()
        (tmp_path / "another_file.py").touch()
        (tmp_path / "test_script.py").touch()

        results = tools["search_files"]("test_file.txt")

        assert any(result == "test_file.txt" for result in results)

    def test_search_files_fuzzy_match(self, secure_tools):
        tools, tmp_path = secure_tools

        (tmp_path / "test_file.txt").touch()
        (tmp_path / "testing_script.py").touch()
        (tmp_path / "unrelated.md").touch()

        results = tools["search_files"]("test")

        assert results
        assert any("test" in Path(r).name.lower() for r in results)

    def test_search_files_max_results(self, secure_tools):
        tools, tmp_path = secure_tools

        for i in range(20):
            (tmp_path / f"test_file_{i}.txt").touch()

        results = tools["search_files"]("test", max_results=5)

        assert len(results) <= 5

    def test_search_files_empty_query(self, secure_tools):
        tools, _ = secure_tools
        with pytest.raises(ValueError, match="Search query must be a non-empty string"):
            tools["search_files"]("")

    def test_search_files_query_too_long(self, secure_tools):
        long_query = "a" * 101
        tools, _ = secure_tools
        with pytest.raises(ValueError, match="Search query too long"):
            tools["search_files"](long_query)


class TestListDirectory:
    def test_list_directory_success(self, secure_tools):
        tools, tmp_path = secure_tools

        (tmp_path / "file1.txt").touch()
        (tmp_path / "file2.py").touch()
        (tmp_path / "subdir").mkdir()

        results = tools["list_directory"]()

        assert len(results) == 3
        file_entries = [e for e in results if e.startswith("[FILE]")]
        dir_entries = [e for e in results if e.startswith("[DIR]")]

        assert len(file_entries) == 2
        assert len(dir_entries) == 1
        assert any("file1.txt" in e for e in file_entries)
        assert any("file2.py" in e for e in file_entries)
        assert any("subdir" in e for e in dir_entries)

    def test_list_directory_empty(self, secure_tools):
        tools, _ = secure_tools
        results = tools["list_directory"]()
        assert results == []

    def test_list_directory_nonexistent(self, secure_tools):
        tools, _ = secure_tools
        with pytest.raises(ValueError, match="Path does not exist"):
            tools["list_directory"]("nonexistent_dir")

    def test_list_directory_file_instead_of_dir(self, secure_tools):
        tools, tmp_path = secure_tools
        file_path = tmp_path / "some_file.txt"
        file_path.write_text("data")

        with pytest.raises(ValueError, match="Path is not a directory"):
            tools["list_directory"]("some_file.txt")


class TestFindAndReplace:
    def test_find_and_replace_success(self, secure_tools):
        tools, tmp_path = secure_tools
        file_path = tmp_path / "greetings.txt"
        original = "Hello world\nHello universe\nGoodbye world"
        file_path.write_text(original)

        result = tools["find_and_replace"]("greetings.txt", r"Hello", "Hi")

        expected = "Hi world\nHi universe\nGoodbye world"
        assert result == expected
        assert file_path.read_text() == expected

    def test_find_and_replace_regex_pattern(self, secure_tools):
        tools, tmp_path = secure_tools
        file_path = tmp_path / "phones.txt"
        original = "Phone: 123-456-7890\nPhone: 987-654-3210"
        file_path.write_text(original)

        result = tools["find_and_replace"](
            "phones.txt", r"Phone: (\d{3}-\d{3}-\d{4})", r"Tel: \1"
        )

        expected = "Tel: 123-456-7890\nTel: 987-654-3210"
        assert result == expected

    def test_find_and_replace_no_matches(self, secure_tools):
        tools, tmp_path = secure_tools
        file_path = tmp_path / "no_match.txt"
        original = "Hello world"
        file_path.write_text(original)

        result = tools["find_and_replace"]("no_match.txt", r"xyz", "abc")

        assert result == original

    def test_find_and_replace_nonexistent_file(self, secure_tools):
        tools, _ = secure_tools
        with pytest.raises(ValueError, match="Path does not exist"):
            tools["find_and_replace"]("missing.txt", "pattern", "replacement")

    def test_find_and_replace_empty_pattern(self, secure_tools):
        tools, tmp_path = secure_tools
        (tmp_path / "file.txt").write_text("content")

        with pytest.raises(ValueError, match="Pattern must be a non-empty string"):
            tools["find_and_replace"]("file.txt", "", "replacement")

    def test_find_and_replace_invalid_regex(self, secure_tools):
        tools, tmp_path = secure_tools
        (tmp_path / "file.txt").write_text("content")

        with pytest.raises(ValueError, match="Invalid regex pattern"):
            tools["find_and_replace"]("file.txt", "[", "replacement")

    def test_find_and_replace_pattern_too_long(self, secure_tools):
        tools, tmp_path = secure_tools
        (tmp_path / "file.txt").write_text("content")

        long_pattern = "a" * 1001
        with pytest.raises(ValueError, match="Pattern too long"):
            tools["find_and_replace"]("file.txt", long_pattern, "replacement")


class TestApplyPythonLambda:
    def test_apply_python_lambda_success(self, secure_tools):
        tools, _ = secure_tools
        result = tools["apply_python_lambda"]("5", "lambda x: int(x) * 2")
        assert result == 10

    def test_apply_python_lambda_string_operation(self, secure_tools):
        tools, _ = secure_tools
        result = tools["apply_python_lambda"]("hello", "lambda x: x.upper()")
        assert result == "HELLO"

    def test_apply_python_lambda_complex_operation(self, secure_tools):
        tools, _ = secure_tools
        result = tools["apply_python_lambda"](
            "hello world",
            "lambda x: ' '.join(word.capitalize() for word in x.split())",
        )
        assert result == "Hello World"
