"""Correctness and work bounds for incremental secret-mask matching."""

import random
import re
import tracemalloc
from itertools import product

import pytest

from tracecat.secrets import masking, matcher
from tracecat.secrets.masking import SecretMaskCollector
from tracecat.secrets.matcher import SecretSubstringIndex


def test_substring_index_matches_literal_search_incrementally() -> None:
    rng = random.Random(7)
    index = SecretSubstringIndex()
    patterns: set[str] = set()
    alphabet = "abc\\\n'é漢🙂*"
    for _ in range(150):
        candidate = "".join(rng.choices(alphabet, k=rng.randrange(2, 12)))
        if candidate not in patterns:
            patterns.add(candidate)
            index.add(candidate)
        for text in (
            "".join(rng.choices(alphabet, k=80)),
            "prefix" + candidate + "suffix",
            "unrelated public diagnostic",
            "",
        ):
            assert index.contains(text) == any(p in text for p in patterns)


@pytest.mark.parametrize(
    ("patterns", "text", "expected"),
    [
        (["he", "hers", "she", "his"], "ushers", True),
        (["abcd", "bc"], "abc", True),
        (["aab", "abx"], "aaabx", True),
        (["long-secret", "secret"], "secret", True),
        (["a.b", "[token]"], "axb", False),
    ],
)
def test_substring_suffixes_and_literal_metacharacters(
    patterns: list[str], text: str, expected: bool
) -> None:
    index = SecretSubstringIndex()
    for pattern in patterns:
        index.add(pattern)
    assert index.contains(text) is expected


def test_compact_transitions_preserve_unicode_and_prefix_branches() -> None:
    patterns = {"a", "ab", "ab🙂", "ab漢", "ac", "éx", "\x00end", "\ud800x"}
    automaton = matcher._Automaton(patterns)
    for text in ("", "public", "xab🙂", "xab漢", "zzéx", "\x00end", "\ud800x"):
        assert automaton.contains(text) == any(p in text for p in patterns)
    # Wide sibling sets exercise binary search beyond the common one-edge case.
    patterns = {"prefix" + chr(codepoint) + "suffix" for codepoint in range(300)}
    automaton = matcher._Automaton(patterns)
    for codepoint in range(350):
        text = "leading-prefix" + chr(codepoint) + "suffix-trailing"
        assert automaton.contains(text) is (codepoint < 300)


def test_compact_matcher_agrees_with_literal_search_exhaustively() -> None:
    rng = random.Random(12)
    candidates = [
        "".join(chars)
        for length in range(1, 4)
        for chars in product("ab🙂", repeat=length)
    ]
    texts = [
        "".join(chars)
        for length in range(5)
        for chars in product("ab🙂", repeat=length)
    ]
    for _ in range(40):
        patterns = set(rng.sample(candidates, 4))
        automaton = matcher._Automaton(patterns)
        for text in texts:
            assert automaton.contains(text) == any(p in text for p in patterns)


def test_long_pattern_bounds_build_memory_as_well_as_retained_memory() -> None:
    pattern = "synthetic-long-value-" + "🙂" * 40000
    tracemalloc.start()
    try:
        automaton = matcher._Automaton({pattern})
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # Budget includes construction, so building per-state dictionaries and
    # compacting afterward cannot silently reintroduce the original memory spike.
    assert peak < 64 * len(pattern)
    assert automaton.contains("prefix " + pattern + " suffix")
    assert not automaton.contains(pattern[:-1])


def test_incremental_queries_do_not_rebuild_all_masks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = matcher._Automaton
    compiled_sizes: list[int] = []

    def counted(patterns: set[str]) -> matcher._Automaton:
        compiled_sizes.append(len(patterns))
        return original(patterns)

    monkeypatch.setattr(matcher, "_Automaton", counted)
    index = SecretSubstringIndex()
    count = 1024
    # A large initial batch must not get repeatedly rebuilt by tiny additions.
    for i in range(count):
        index.add(f"synthetic-secret-{i}")
    assert not index.contains("public")
    for i in range(count, 2 * count):
        index.add(f"synthetic-secret-{i}")
        assert index.contains(f"prefix: synthetic-secret-{i}")
    assert sum(compiled_sizes) <= 2 * count * (2 * count).bit_length()
    builds = len(compiled_sizes)
    for _ in range(100):
        assert not index.contains("public")
    assert len(compiled_sizes) == builds


def test_exact_values_do_not_build_substring_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(patterns: set[str]) -> matcher._Automaton:
        pytest.fail("Exact membership must not build or scan a substring matcher")

    monkeypatch.setattr(matcher, "_Automaton", unexpected)
    collector = SecretMaskCollector()
    values = [f"synthetic-result-{i:08d}" for i in range(10000)]
    collector.observe(values)
    assert all(collector.contains(value) for value in values)


def test_duplicate_observations_skip_representation_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector = SecretMaskCollector()
    collector.observe("synthetic\nsecret")

    def unexpected(*args: object, **kwargs: object) -> str:
        pytest.fail("Previously observed strings must not be escaped again")

    monkeypatch.setattr(masking.json, "dumps", unexpected)
    collector.observe({"nested": ["synthetic\nsecret"]})


def test_observing_an_existing_representation_adds_its_own_escapes() -> None:
    collector = SecretMaskCollector()
    collector.observe("synthetic\nsecret")
    literal = "synthetic\\nsecret"
    assert literal in collector.values
    assert repr(literal)[1:-1] not in collector.values
    collector.observe(literal)
    assert repr(literal)[1:-1] in collector.values
    assert collector.redact(repr(literal)) == "'***'"


def test_redaction_pattern_is_lazy_reused_and_invalidated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = masking._compile_mask_pattern
    calls = 0

    def counted(values: set[str], *, include_short: bool) -> re.Pattern[str] | None:
        nonlocal calls
        calls += 1
        return original(values, include_short=include_short)

    monkeypatch.setattr(masking, "_compile_mask_pattern", counted)
    collector = SecretMaskCollector()
    collector.observe("synthetic-secret")
    assert collector.contains("prefix synthetic-secret suffix")
    assert calls == 0
    assert collector.redact("synthetic-secret") == "***"
    collector.observe("synthetic-secret")
    assert collector.redact({"synthetic-secret": ["synthetic-secret"]}) == {
        "***": ["***"]
    }
    assert calls == 1
    collector.observe("new-secret")
    assert collector.redact("synthetic-secret new-secret") == "*** ***"
    assert calls == 2


def test_new_masks_invalidate_prior_substring_misses() -> None:
    collector = SecretMaskCollector()
    values = ["old-secret"]
    collector.observe(values)
    assert not collector.contains("prefix new-secret suffix")
    values.append("new-secret")
    collector.observe(values)
    assert collector.contains("prefix new-secret suffix")
    assert collector.redact("prefix new-secret suffix") == "prefix *** suffix"


def test_cached_redaction_preserves_overlap_and_idempotence() -> None:
    collector = SecretMaskCollector()
    collector.observe(["*", "abc", "abcdef", "bc"])
    text = "abcdef abc bc * ***"
    assert collector.redact(text) == "*** *** *** *** ***"
    assert collector.redact(collector.redact(text)) == collector.redact(text)
