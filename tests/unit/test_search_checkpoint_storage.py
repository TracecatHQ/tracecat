"""One canonical worker checkpoint with validated compatibility decoding."""

from uuid import uuid4

import pytest

from tracecat.search.chunking_types import ChunkCheckpoint, ChunkingIdentity
from tracecat.search.types import EnumerationCursor, decode_enumeration_cursor


@pytest.fixture
def checkpoint():
    return ChunkCheckpoint(
        identity=ChunkingIdentity(
            organization_id=uuid4(),
            workspace_id=uuid4(),
            collection_id=uuid4(),
            document_id=uuid4(),
            generation=1,
            revision=1,
            config_version=1,
        ),
        config_hash="a" * 64,
        column_index=1,
        character_offset=20,
        next_ordinal=2,
    )


def test_canonical_checkpoint_roundtrips_without_duplicate_positions(checkpoint):
    data = checkpoint.model_dump(mode="json")
    assert "chunker" not in data
    assert decode_enumeration_cursor(data) == checkpoint


def test_earlier_nested_checkpoint_decodes_to_canonical_form(checkpoint):
    data = {
        "column_index": checkpoint.column_index,
        "character_offset": checkpoint.character_offset,
        "next_ordinal": checkpoint.next_ordinal,
        "chunker": checkpoint.model_dump(mode="json"),
    }
    assert decode_enumeration_cursor(data) == checkpoint


@pytest.mark.parametrize(
    "position", ["column_index", "character_offset", "next_ordinal"]
)
def test_conflicting_legacy_positions_are_rejected(checkpoint, position):
    data = {
        "column_index": 1,
        "character_offset": 20,
        "next_ordinal": 2,
        "chunker": checkpoint.model_dump(mode="json"),
    }
    data[position] = 999
    with pytest.raises(ValueError, match="position mismatch"):
        decode_enumeration_cursor(data)


def test_generic_storage_cursor_remains_supported():
    assert decode_enumeration_cursor(None) == EnumerationCursor()
    assert decode_enumeration_cursor(
        {"character_offset": 10, "next_ordinal": 1}
    ) == EnumerationCursor(character_offset=10, next_ordinal=1)
