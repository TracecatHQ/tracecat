import threading
from _thread import LockType
from collections import OrderedDict
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from types import ModuleType

import jsonpath_ng.jsonpath as jsonpath_nodes
import pytest
from tracecat_registry._internal import jsonpath as registry_jsonpath

from tracecat.expressions import common as core_jsonpath


@dataclass(frozen=True, slots=True)
class _JsonPathCacheHarness:
    name: str
    module: ModuleType
    parse: Callable[[str], jsonpath_nodes.JSONPath]
    cache: OrderedDict[str, jsonpath_nodes.JSONPath]
    cache_lock: LockType
    parser_lock: LockType
    get_cached: Callable[[str], jsonpath_nodes.JSONPath | None]
    max_entries: int
    max_expr_length: int


_HARNESSES = (
    _JsonPathCacheHarness(
        name="core",
        module=core_jsonpath,
        parse=core_jsonpath.parse_jsonpath,
        cache=core_jsonpath._JSONPATH_CACHE,
        cache_lock=core_jsonpath._JSONPATH_CACHE_LOCK,
        parser_lock=core_jsonpath._JSONPATH_PARSER_LOCK,
        get_cached=core_jsonpath._get_cached_jsonpath,
        max_entries=core_jsonpath._JSONPATH_CACHE_MAXSIZE,
        max_expr_length=core_jsonpath._JSONPATH_CACHE_MAX_EXPR_LENGTH,
    ),
    _JsonPathCacheHarness(
        name="registry",
        module=registry_jsonpath,
        parse=registry_jsonpath._parse_jsonpath,
        cache=registry_jsonpath._JSONPATH_CACHE,
        cache_lock=registry_jsonpath._JSONPATH_CACHE_LOCK,
        parser_lock=registry_jsonpath._JSONPATH_PARSER_LOCK,
        get_cached=registry_jsonpath._get_cached_jsonpath,
        max_entries=registry_jsonpath._JSONPATH_CACHE_MAXSIZE,
        max_expr_length=registry_jsonpath._JSONPATH_CACHE_MAX_EXPR_LENGTH,
    ),
)


class _CountingParser:
    def __init__(self) -> None:
        self.calls = 0
        self._lock = threading.Lock()

    def parse(self, expr: str) -> jsonpath_nodes.JSONPath:
        with self._lock:
            self.calls += 1
        return jsonpath_nodes.Root()


@pytest.fixture(params=_HARNESSES, ids=[harness.name for harness in _HARNESSES])
def jsonpath_cache(
    request: pytest.FixtureRequest,
) -> Iterator[_JsonPathCacheHarness]:
    harness: _JsonPathCacheHarness = request.param
    with harness.cache_lock:
        harness.cache.clear()
    yield harness
    with harness.cache_lock:
        harness.cache.clear()


def test_cache_admission_uses_expression_length(
    jsonpath_cache: _JsonPathCacheHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = _CountingParser()
    monkeypatch.setattr(jsonpath_cache.module, "_JSONPATH_PARSER", parser)
    cached_expr = "$." + "a" * (jsonpath_cache.max_expr_length - 2)
    uncached_expr = cached_expr + "a"

    cached_first = jsonpath_cache.parse(cached_expr)
    cached_second = jsonpath_cache.parse(cached_expr)
    uncached_first = jsonpath_cache.parse(uncached_expr)
    uncached_second = jsonpath_cache.parse(uncached_expr)

    assert cached_first is cached_second
    assert uncached_first is not uncached_second
    assert parser.calls == 3
    assert list(jsonpath_cache.cache) == [cached_expr]


def test_cache_evicts_the_least_recently_used_expression(
    jsonpath_cache: _JsonPathCacheHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = _CountingParser()
    monkeypatch.setattr(jsonpath_cache.module, "_JSONPATH_PARSER", parser)
    expressions = [f"$.field_{index}" for index in range(jsonpath_cache.max_entries)]

    for expr in expressions:
        jsonpath_cache.parse(expr)
    jsonpath_cache.parse(expressions[0])
    jsonpath_cache.parse("$.overflow")

    assert len(jsonpath_cache.cache) == jsonpath_cache.max_entries
    assert expressions[0] in jsonpath_cache.cache
    assert expressions[1] not in jsonpath_cache.cache
    assert "$.overflow" in jsonpath_cache.cache
    assert parser.calls == jsonpath_cache.max_entries + 1


def test_concurrent_cacheable_misses_parse_once(
    jsonpath_cache: _JsonPathCacheHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = _CountingParser()
    monkeypatch.setattr(jsonpath_cache.module, "_JSONPATH_PARSER", parser)
    worker_count = 8
    initial_lookups = 0
    initial_lookups_lock = threading.Lock()
    all_initial_lookups_complete = threading.Event()

    def counted_get_cached(expr: str) -> jsonpath_nodes.JSONPath | None:
        nonlocal initial_lookups
        parsed = jsonpath_cache.get_cached(expr)
        with initial_lookups_lock:
            initial_lookups += 1
            if initial_lookups == worker_count:
                all_initial_lookups_complete.set()
        return parsed

    monkeypatch.setattr(
        jsonpath_cache.module, "_get_cached_jsonpath", counted_get_cached
    )

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        jsonpath_cache.parser_lock.acquire()
        try:
            futures = [
                executor.submit(jsonpath_cache.parse, "$.shared")
                for _ in range(worker_count)
            ]
            all_workers_reached_parser_lock = all_initial_lookups_complete.wait(5)
        finally:
            jsonpath_cache.parser_lock.release()

        results = [future.result() for future in futures]

    assert all_workers_reached_parser_lock
    assert parser.calls == 1
    assert all(parsed is results[0] for parsed in results)


def test_cache_hit_does_not_wait_for_parser_lock(
    jsonpath_cache: _JsonPathCacheHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = _CountingParser()
    monkeypatch.setattr(jsonpath_cache.module, "_JSONPATH_PARSER", parser)
    cached = jsonpath_cache.parse("$.cached")

    with ThreadPoolExecutor(max_workers=1) as executor:
        jsonpath_cache.parser_lock.acquire()
        try:
            future = executor.submit(jsonpath_cache.parse, "$.cached")
            parsed = future.result(timeout=1)
        finally:
            jsonpath_cache.parser_lock.release()

    assert parsed is cached
    assert parser.calls == 1


def test_oversized_valid_expression_still_parses(
    jsonpath_cache: _JsonPathCacheHarness,
) -> None:
    field = "a" * jsonpath_cache.max_expr_length
    expr = f"$.{field}"

    parsed = jsonpath_cache.parse(expr)

    assert [match.value for match in parsed.find({field: 42})] == [42]
    assert expr not in jsonpath_cache.cache
