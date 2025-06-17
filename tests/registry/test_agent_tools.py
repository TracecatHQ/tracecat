"""Tests for agent tools implementations."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from tracecat_registry.integrations.agents.tools import (
    apply_python_lambda,
    create_file,
    find_and_replace,
    list_directory,
    read_file,
    search_files,
)


class TestReadFile:
    def test_read_file_success(self):
        """Test reading a file successfully."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as tmp:
            content = "Line 1\nLine 2\nLine 3"
            tmp.write(content)
            tmp.flush()

            try:
                result = read_file(tmp.name)
                assert result == content
            finally:
                os.unlink(tmp.name)

    def test_read_file_limit_250_lines(self):
        """Test that read_file limits to 250 lines."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as tmp:
            # Write 300 lines
            lines = [f"Line {i}" for i in range(300)]
            tmp.write("\n".join(lines))
            tmp.flush()

            try:
                result = read_file(tmp.name)
                result_lines = result.split("\n")
                assert len(result_lines) == 250
                assert result_lines[0] == "Line 0"
                assert result_lines[249] == "Line 249"
            finally:
                os.unlink(tmp.name)

    def test_read_file_nonexistent(self):
        """Test reading a non-existent file."""
        with pytest.raises(ValueError, match="File does not exist"):
            read_file("/nonexistent/file.txt")

    def test_read_file_directory(self):
        """Test reading a directory instead of file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(ValueError, match="Path is not a file"):
                read_file(tmpdir)

    @patch(
        "tracecat_registry.integrations.agents.tools.TRACECAT__MAX_FILE_SIZE_BYTES", 10
    )
    def test_read_file_too_large(self):
        """Test reading a file that's too large."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as tmp:
            tmp.write("This content is longer than 10 bytes")
            tmp.flush()

            try:
                with pytest.raises(ValueError, match="File too large"):
                    read_file(tmp.name)
            finally:
                os.unlink(tmp.name)


class TestCreateFile:
    def test_create_file_success(self):
        """Test creating a file successfully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "test_file.txt"
            content = "Test content"

            result = create_file(str(file_path), content)

            assert "File created successfully" in result
            assert file_path.exists()
            assert file_path.read_text() == content

    def test_create_file_empty_content(self):
        """Test creating a file with empty content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "empty_file.txt"

            result = create_file(str(file_path))

            assert "File created successfully" in result
            assert file_path.exists()
            assert file_path.read_text() == ""

    def test_create_file_with_parent_dirs(self):
        """Test creating a file with parent directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "subdir" / "test_file.txt"
            content = "Test content"

            result = create_file(str(file_path), content)

            assert "File created successfully" in result
            assert file_path.exists()
            assert file_path.read_text() == content

    def test_create_file_already_exists(self):
        """Test creating a file that already exists."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
            try:
                with pytest.raises(ValueError, match="File already exists"):
                    create_file(tmp.name, "content")
            finally:
                os.unlink(tmp.name)

    @patch(
        "tracecat_registry.integrations.agents.tools.TRACECAT__MAX_FILE_SIZE_BYTES", 10
    )
    def test_create_file_content_too_large(self):
        """Test creating a file with content that's too large."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "test_file.txt"
            content = "This content is longer than 10 bytes"

            with pytest.raises(ValueError, match="Content too large"):
                create_file(str(file_path), content)


class TestSearchFiles:
    def test_search_files_exact_match(self):
        """Test searching for files with exact match."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            # Create test files
            (tmpdir_path / "test_file.txt").touch()
            (tmpdir_path / "another_file.py").touch()
            (tmpdir_path / "test_script.py").touch()

            results = search_files("test_file.txt", tmpdir)

            assert len(results) >= 1
            assert any("test_file.txt" in result for result in results)

    def test_search_files_fuzzy_match(self):
        """Test searching for files with fuzzy matching."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            # Create test files
            (tmpdir_path / "test_file.txt").touch()
            (tmpdir_path / "testing_script.py").touch()
            (tmpdir_path / "unrelated.md").touch()

            results = search_files("test", tmpdir)

            assert len(results) >= 1
            # Should find files with "test" in the name
            found_files = [Path(result).name for result in results]
            assert any("test" in filename.lower() for filename in found_files)

    def test_search_files_max_results(self):
        """Test that search_files respects max_results limit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            # Create many files with similar names
            for i in range(20):
                (tmpdir_path / f"test_file_{i}.txt").touch()

            results = search_files("test", tmpdir, max_results=5)

            assert len(results) <= 5

    def test_search_files_nonexistent_directory(self):
        """Test searching in a non-existent directory."""
        with pytest.raises(ValueError, match="Directory does not exist"):
            search_files("test", "/nonexistent/directory")

    def test_search_files_empty_query(self):
        """Test searching with empty query."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(ValueError, match="Search query cannot be empty"):
                search_files("", tmpdir)

    def test_search_files_query_too_long(self):
        """Test searching with query that's too long."""
        with tempfile.TemporaryDirectory() as tmpdir:
            long_query = "a" * 101
            with pytest.raises(ValueError, match="Search query too long"):
                search_files(long_query, tmpdir)


class TestListDirectory:
    def test_list_directory_success(self):
        """Test listing directory contents successfully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            # Create test files and directories
            (tmpdir_path / "file1.txt").touch()
            (tmpdir_path / "file2.py").touch()
            (tmpdir_path / "subdir").mkdir()

            results = list_directory(tmpdir)

            assert len(results) == 3
            file_entries = [entry for entry in results if entry.startswith("[FILE]")]
            dir_entries = [entry for entry in results if entry.startswith("[DIR]")]

            assert len(file_entries) == 2
            assert len(dir_entries) == 1
            assert any("file1.txt" in entry for entry in file_entries)
            assert any("file2.py" in entry for entry in file_entries)
            assert any("subdir" in entry for entry in dir_entries)

    def test_list_directory_empty(self):
        """Test listing an empty directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            results = list_directory(tmpdir)
            assert results == []

    def test_list_directory_nonexistent(self):
        """Test listing a non-existent directory."""
        with pytest.raises(ValueError, match="Directory does not exist"):
            list_directory("/nonexistent/directory")

    def test_list_directory_file_instead_of_dir(self):
        """Test listing a file instead of directory."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
            try:
                with pytest.raises(ValueError, match="Path is not a directory"):
                    list_directory(tmp.name)
            finally:
                os.unlink(tmp.name)


class TestFindAndReplace:
    def test_find_and_replace_success(self):
        """Test find and replace successfully."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as tmp:
            original_content = "Hello world\nHello universe\nGoodbye world"
            tmp.write(original_content)
            tmp.flush()

            try:
                result = find_and_replace(tmp.name, r"Hello", "Hi")

                expected = "Hi world\nHi universe\nGoodbye world"
                assert result == expected

                # Verify file was actually modified
                with open(tmp.name) as f:
                    assert f.read() == expected
            finally:
                os.unlink(tmp.name)

    def test_find_and_replace_regex_pattern(self):
        """Test find and replace with regex pattern."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as tmp:
            original_content = "Phone: 123-456-7890\nPhone: 987-654-3210"
            tmp.write(original_content)
            tmp.flush()

            try:
                result = find_and_replace(
                    tmp.name, r"Phone: (\d{3}-\d{3}-\d{4})", r"Tel: \1"
                )

                expected = "Tel: 123-456-7890\nTel: 987-654-3210"
                assert result == expected
            finally:
                os.unlink(tmp.name)

    def test_find_and_replace_no_matches(self):
        """Test find and replace with no matches."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as tmp:
            original_content = "Hello world"
            tmp.write(original_content)
            tmp.flush()

            try:
                result = find_and_replace(tmp.name, r"xyz", "abc")

                # Content should remain unchanged
                assert result == original_content
            finally:
                os.unlink(tmp.name)

    def test_find_and_replace_nonexistent_file(self):
        """Test find and replace on non-existent file."""
        with pytest.raises(ValueError, match="File does not exist"):
            find_and_replace("/nonexistent/file.txt", "pattern", "replacement")

    def test_find_and_replace_empty_pattern(self):
        """Test find and replace with empty pattern."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as tmp:
            tmp.write("content")
            tmp.flush()

            try:
                with pytest.raises(ValueError, match="Pattern cannot be empty"):
                    find_and_replace(tmp.name, "", "replacement")
            finally:
                os.unlink(tmp.name)

    def test_find_and_replace_invalid_regex(self):
        """Test find and replace with invalid regex pattern."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as tmp:
            tmp.write("content")
            tmp.flush()

            try:
                with pytest.raises(ValueError, match="Invalid regex pattern"):
                    find_and_replace(tmp.name, "[", "replacement")
            finally:
                os.unlink(tmp.name)

    def test_find_and_replace_pattern_too_long(self):
        """Test find and replace with pattern that's too long."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as tmp:
            tmp.write("content")
            tmp.flush()

            try:
                long_pattern = "a" * 1001
                with pytest.raises(ValueError, match="Pattern too long"):
                    find_and_replace(tmp.name, long_pattern, "replacement")
            finally:
                os.unlink(tmp.name)


class TestApplyPythonLambda:
    def test_apply_python_lambda_success(self):
        """Test applying a Python lambda successfully."""
        result = apply_python_lambda("5", "lambda x: int(x) * 2")
        assert result == 10

    def test_apply_python_lambda_string_operation(self):
        """Test applying a Python lambda on string."""
        result = apply_python_lambda("hello", "lambda x: x.upper()")
        assert result == "HELLO"

    def test_apply_python_lambda_complex_operation(self):
        """Test applying a complex Python lambda."""
        result = apply_python_lambda(
            "hello world", "lambda x: ' '.join(word.capitalize() for word in x.split())"
        )
        assert result == "Hello World"
