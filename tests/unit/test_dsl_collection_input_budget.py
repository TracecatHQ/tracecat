from tracecat.dsl.scheduler import _partition_by_size
from tracecat.storage.object import InlineObject, StoredObject, StoredObjectValidator


def _item(size: int) -> InlineObject:
    return InlineObject(data="x" * size)


def _size(item: StoredObject) -> int:
    return len(StoredObjectValidator.dump_json(item))


def test_items_within_budget_are_not_partitioned() -> None:
    items = [_item(100) for _ in range(5)]
    assert _partition_by_size(items, sum(_size(i) for i in items)) is None


def test_items_over_budget_are_split_into_bounded_parts_in_order() -> None:
    items = [InlineObject(data=f"{i:04d}" + "x" * 1000) for i in range(50)]
    budget = 5 * _size(items[0])

    parts = _partition_by_size(items, budget)

    assert parts is not None
    assert [item for part in parts for item in part] == items
    assert all(sum(_size(item) for item in part) <= budget for part in parts)
    assert len(parts) == 10


def test_item_larger_than_budget_gets_its_own_part() -> None:
    small, big = _item(10), _item(10_000)
    parts = _partition_by_size([small, big, small], _size(big) - 1)
    assert parts == [[small], [big], [small]]
