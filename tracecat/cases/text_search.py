"""Case-specific indexed matching, ranking, and bounded native excerpts.

Shared lexical parsing defines required PostgreSQL simple words/phrases and
escaped literal mode. Case adapters add exact prefixed IDs/UUIDs and at most
two ASCII word typo expansions. Callers compose workspace/structured filters
before counting or pagination, and own rollback around the execution context.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Literal, TypedDict, cast

import orjson
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, REGCONFIG, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from tracecat import config
from tracecat.cases.exact_search import MAX_CASE_NUMBER
from tracecat.cases.text_search_indexes import (
    CASE_TEXT_DOCUMENT_BYTES,
    CASE_TEXT_MATCH_FUNCTION,
    ensure_case_text_indexes,
)
from tracecat.db.models import Case, CaseSearchWord
from tracecat.query.errors import TracecatQueryTimeoutError
from tracecat.query.execution import query_execution_context
from tracecat.query.lexical import (
    literal_search_pattern,
    parse_full_text_query,
    resolve_search_columns,
)
from tracecat.query.lexical_types import (
    MAX_MATCH_EXCERPT_LENGTH,
    SearchMode,
)

MAX_FUZZY_TERMS = 2
MAX_EXCERPT_LENGTH = MAX_MATCH_EXCERPT_LENGTH
CASE_TEXT_FIELDS = ("summary", "description")
type CaseTextField = Literal["summary", "description"]
FUZZY_THRESHOLD = 0.65
_SHORT_ID = re.compile(r"CASE-(\d+)", re.IGNORECASE)
_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_FUZZY_WORD = re.compile(r"[a-zA-Z]{5,32}")
_HASH = re.compile(
    r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{40}|[0-9a-fA-F]{64}|[0-9a-fA-F]{96}|[0-9a-fA-F]{128})"
)
_CONFIG = sa.literal_column("'pg_catalog.simple'::regconfig", type_=REGCONFIG())
_EMPTY = sa.literal_column("''", type_=sa.Text())


@dataclass(frozen=True, slots=True)
class SearchClause:
    """One AND clause, evaluated independently in each source field."""

    value: str
    kind: Literal["word", "phrase", "literal"]
    fuzzy: bool = False


@dataclass(frozen=True, slots=True)
class CaseTextQuery:
    """Validated query semantics shared by filtering and result attribution."""

    text: str
    clauses: tuple[SearchClause, ...]
    mode: SearchMode = SearchMode.FULL_TEXT
    columns: tuple[CaseTextField, ...] = CASE_TEXT_FIELDS
    case_number: int | None = None
    case_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class _SearchField:
    """A searchable source column with its stored safe vector and word list.

    The vector is trigger-maintained (revision 5ca140c06ec2), so search reads it
    instead of recomputing the safe vector for every candidate row. NULL marks
    an over-budget or unsafe source that needs a complete-source recheck.

    The word list holds the source's distinct lowercase words (revision
    e4fd31674ae9). NULL marks an over-budget source. It omits words that cannot
    reach FUZZY_THRESHOLD with a fuzzy term of at most 32 letters; see that
    revision before changing either bound.
    """

    name: CaseTextField
    source: sa.SQLColumnExpression[str]
    vector: ColumnElement[Any]
    words: ColumnElement[Sequence[str]]


_SEARCH_FIELDS = (
    _SearchField("summary", Case.summary, Case.summary_tsv, Case.summary_words),
    _SearchField(
        "description", Case.description, Case.description_tsv, Case.description_words
    ),
)


@dataclass(frozen=True, slots=True)
class CaseTextExpressions:
    """Compositional expressions; the caller owns filters and pagination."""

    predicate: ColumnElement[bool]
    score: ColumnElement[int]
    summary_match: ColumnElement[bool]
    description_match: ColumnElement[bool]
    identifier_match: ColumnElement[bool]


@dataclass(frozen=True, slots=True)
class CaseSearchExcerpt:
    """A bounded plain-text excerpt; render ``text`` as text, never as HTML."""

    field: Literal["summary", "description"]
    text: str


def parse_case_text_query(
    value: str,
    *,
    mode: SearchMode = SearchMode.FULL_TEXT,
    columns: Sequence[str] | None = None,
) -> CaseTextQuery:
    """Consume shared lexical units, adding case identity and fuzzy policies.

    Bare numbers, IPs, hashes, and punctuation keep PostgreSQL tokenization in
    full-text mode; they never become fuzzy words. Literal mode preserves the
    complete input, including spaces and characters resembling operators.
    """
    selected = cast(
        tuple[CaseTextField, ...],
        resolve_search_columns(
            "all_columns" if columns is None else tuple(columns), CASE_TEXT_FIELDS
        ),
    )
    if mode == SearchMode.LITERAL:
        literal_search_pattern(value)
        return CaseTextQuery(value, (SearchClause(value, "literal"),), mode, selected)

    units = parse_full_text_query(value)
    text = value.strip()
    case_number: int | None = None
    case_id: uuid.UUID | None = None
    if match := _SHORT_ID.fullmatch(text):
        digits = match.group(1)
        if len(digits) > 10 or int(digits) > MAX_CASE_NUMBER:
            raise ValueError("Short ID number cannot exceed 2147483647")
        case_number = int(digits)
    if _UUID.fullmatch(text):
        case_id = uuid.UUID(text)

    clauses: list[SearchClause] = []
    fuzzy_count = 0
    for unit in units:
        fuzzy = (
            not unit.is_phrase
            and _FUZZY_WORD.fullmatch(unit.text) is not None
            and _HASH.fullmatch(unit.text) is None
            and fuzzy_count < MAX_FUZZY_TERMS
        )
        fuzzy_count += int(fuzzy)
        clauses.append(
            SearchClause(unit.text, "phrase" if unit.is_phrase else "word", fuzzy)
        )
    return CaseTextQuery(text, tuple(clauses), mode, selected, case_number, case_id)


async def validate_case_text_lexemes(
    session: AsyncSession, query: CaseTextQuery
) -> None:
    """Let PostgreSQL reject every word/phrase that produces no query nodes."""
    for clause in query.clauses:
        if clause.kind == "literal":
            continue
        invalid = await session.scalar(
            sa.text("""
            SELECT EXISTS (
                SELECT 1 FROM pg_catalog.ts_parse('pg_catalog.default', :term) p
                JOIN pg_catalog.pg_ts_config_map m
                    ON m.mapcfg = 'pg_catalog.simple'::regconfig AND m.maptokentype=p.tokid
                CROSS JOIN LATERAL (SELECT pg_catalog.ts_lexize(m.mapdict::regdictionary,p.token) AS lexemes) l
                WHERE octet_length(p.token) >= 2047
                    OR EXISTS (SELECT 1 FROM unnest(l.lexemes) lexeme WHERE octet_length(lexeme) >= 2047)
            )
        """),
            {"term": clause.value},
        )
        if invalid:
            raise ValueError(
                "A full-text token exceeds the native byte limit. Use literal search for this value."
            )
    checks = [
        sa.func.numnode(
            (
                sa.func.phraseto_tsquery
                if clause.kind == "phrase"
                else sa.func.plainto_tsquery
            )(_CONFIG, clause.value)
        )
        > 0
        for clause in query.clauses
        if clause.kind != "literal"
    ]
    if checks and not await session.scalar(sa.select(sa.and_(*checks))):
        raise ValueError(
            "Each full-text term must produce searchable PostgreSQL lexemes"
        )


def _source(field: sa.SQLColumnExpression[str]) -> ColumnElement[str]:
    return sa.func.coalesce(sa.cast(field, sa.Text), _EMPTY)


def _within_budget(field: sa.SQLColumnExpression[str]) -> ColumnElement[bool]:
    return sa.func.octet_length(_source(field)) <= sa.literal_column(
        str(CASE_TEXT_DOCUMENT_BYTES), type_=sa.Integer()
    )


def _guarded_source(field: sa.SQLColumnExpression[str]) -> ColumnElement[str]:
    return sa.case((_within_budget(field), _source(field)), else_=_EMPTY)


def _normalized(field: sa.SQLColumnExpression[str]) -> ColumnElement[str]:
    return sa.func.lower(_guarded_source(field))


def _full_normalized(field: sa.SQLColumnExpression[str]) -> ColumnElement[str]:
    return sa.func.lower(_source(field))


def _native_match(
    field: sa.SQLColumnExpression[str], clause: SearchClause
) -> ColumnElement[bool]:
    return sa.func.public.tracecat_case_text_matches_v1(
        _source(field), clause.value, clause.kind == "phrase", type_=sa.Boolean()
    )


def _literal_match(field: _SearchField, value: str) -> ColumnElement[bool]:
    pattern = sa.func.lower(_literal_pattern(value))
    indexed = _normalized(field.source).like(pattern, escape="\\")
    fallback = _full_normalized(field.source).like(pattern, escape="\\")
    fallback_document = field.vector.is_(None)
    return sa.or_(
        indexed,
        sa.and_(
            fallback_document, sa.case((fallback_document, fallback), else_=sa.false())
        ),
    )


def _literal_pattern(value: str) -> str:
    # Ranking boosts also use this helper for short full-text units. Shared
    # literal validation applies to the explicit mode, not these boost strings.
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _field_clause(
    field: _SearchField, clause: SearchClause, workspace_id: uuid.UUID
) -> tuple[ColumnElement[bool], ColumnElement[bool], ColumnElement[float]]:
    if clause.kind == "literal":
        exact = _literal_match(field, clause.value)
        return exact, exact, sa.cast(sa.case((exact, 1), else_=0), sa.Float)
    tsquery = (
        sa.func.phraseto_tsquery if clause.kind == "phrase" else sa.func.plainto_tsquery
    )(_CONFIG, clause.value)
    source = field.source
    vector = field.vector
    words = sa.func.plainto_tsquery(_CONFIG, clause.value)
    indexed_words = vector.bool_op("@@")(words)
    native_exact = vector.bool_op("@@")(tsquery)
    if clause.kind == "phrase":
        # Below the byte/position budget a positive phrase result is exact.
        # Negative phrases with all words present may have lost positions after
        # the256-occurrence cap; recover them from the complete native stream.
        indexed_exact = sa.and_(
            indexed_words,
            sa.case((native_exact, sa.true()), else_=_native_match(source, clause)),
        )
    else:
        indexed_exact = indexed_words
    exact = sa.or_(
        indexed_exact,
        sa.and_(
            vector.is_(None),
            sa.case(
                (vector.is_(None), _native_match(source, clause)), else_=sa.false()
            ),
        ),
    )
    native_rank = sa.func.ts_rank_cd(vector, tsquery, 32, type_=sa.Float())
    rank = sa.case((exact, sa.case((native_exact, native_rank), else_=0.5)), else_=0.0)
    if not clause.fuzzy:
        return exact, exact, rank
    term = clause.value.lower()
    # Vocabulary words within typo distance of the term, found once per query
    # through the vocabulary's trigram index. The explicit similarity check
    # keeps the result independent of the session's pg_trgm threshold.
    typo_words = sa.func.array(
        sa.select(CaseSearchWord.word)
        .where(
            CaseSearchWord.workspace_id == workspace_id,
            CaseSearchWord.word.bool_op("%>>")(term),
            sa.func.strict_word_similarity(term, CaseSearchWord.word)
            >= FUZZY_THRESHOLD,
        )
        .scalar_subquery(),
        type_=ARRAY(sa.Text()),
    )
    # A case matches when one of its complete words is a typo word. Its stored
    # list holds every such word, and the vocabulary holds every listed word.
    indexed_fuzzy = field.words.bool_op("&&")(typo_words)
    split_words = (
        sa.func.regexp_split_to_table(_full_normalized(source), "[^[:alnum:]]+")
        .table_valued(sa.column("word", sa.Text()))
        .render_derived(name="case_search_words")
    )
    complete_word = sa.exists(
        sa.select(1)
        .select_from(split_words)
        .where(
            sa.func.strict_word_similarity(term, split_words.c.word) >= FUZZY_THRESHOLD
        )
        .correlate(Case)
    )
    # Over-budget sources have no stored list: split their complete text per
    # row. The full-text operator rejects most rows before the split, and CASE
    # keeps the planner from evaluating either for rows that have a list.
    full_candidates = _full_normalized(source).bool_op("%>>")(term)
    fallback_words = field.words.is_(None)
    fuzzy = sa.or_(
        indexed_fuzzy,
        sa.and_(
            fallback_words,
            sa.case(
                (
                    fallback_words,
                    sa.and_(
                        full_candidates,
                        sa.case((full_candidates, complete_word), else_=sa.false()),
                    ),
                ),
                else_=sa.false(),
            ),
        ),
    )
    similarity = sa.func.strict_word_similarity(term, _full_normalized(source))
    return (
        exact,
        sa.or_(exact, fuzzy),
        sa.case((exact, rank), (fuzzy, similarity), else_=0.0),
    )


def build_case_text_expressions(
    query: CaseTextQuery, workspace_id: uuid.UUID
) -> CaseTextExpressions:
    """Build indexed matching and a deterministic integer relevance score.

    ``workspace_id`` selects the typo vocabulary; callers still scope the cases
    to that workspace themselves.

    Rank tiers prefer exact identifier, whole summary, contiguous text, all
    exact clauses, then fuzzy clauses. Native per-clause cover-density ranks
    carry 70% summary / 30% description weight. The bounded subscore cannot
    cross a tier boundary, and its integer representation survives cursor
    serialization without floating-point seek errors.
    """
    identifier: ColumnElement[bool] = sa.false()
    if query.case_number is not None:
        identifier = Case.case_number == query.case_number
    elif query.case_id is not None:
        identifier = Case.id == query.case_id
    if not query.clauses:
        raise ValueError("Search must contain at least one clause")
    exact_clauses: list[ColumnElement[bool]] = []
    clauses: list[ColumnElement[bool]] = []
    summaries: list[ColumnElement[bool]] = []
    descriptions: list[ColumnElement[bool]] = []
    ranks: list[ColumnElement[float]] = []
    for clause in query.clauses:
        field_matches = {
            field.name: _field_clause(field, clause, workspace_id)
            for field in _SEARCH_FIELDS
            if field.name in query.columns
        }
        summary_exact, summary_match, summary_rank = field_matches.get(
            "summary", (sa.false(), sa.false(), sa.literal(0.0))
        )
        description_exact, description_match, description_rank = field_matches.get(
            "description", (sa.false(), sa.false(), sa.literal(0.0))
        )
        exact_clauses.append(sa.or_(summary_exact, description_exact))
        clauses.append(sa.or_(summary_match, description_match))
        summaries.append(summary_match)
        descriptions.append(description_match)
        ranks.append(summary_rank * 0.7 + description_rank * 0.3)

    exact_match = sa.and_(*exact_clauses)
    # Phrase syntax is syntax, not source text. Removing its quotes allows an
    # exact literal boost for a pure phrase without crossing source boundaries.
    contiguous = " ".join(clause.value for clause in query.clauses)
    tier = sa.case(
        (identifier, 5),
        (
            sa.and_(
                sa.literal("summary" in query.columns),
                _full_normalized(Case.summary) == sa.func.lower(contiguous),
            ),
            4,
        ),
        (
            sa.or_(
                *(
                    _literal_match(field, contiguous)
                    for field in _SEARCH_FIELDS
                    if field.name in query.columns
                ),
            ),
            3,
        ),
        (exact_match, 2),
        else_=1,
    )
    subscore = sa.cast(
        sa.func.least(999_999, sa.func.floor(sum(ranks) / len(ranks) * 999_999)),
        sa.BigInteger,
    )
    return CaseTextExpressions(
        predicate=sa.or_(identifier, sa.and_(*clauses)),
        score=sa.cast(tier * 1_000_000 + subscore, sa.BigInteger),
        summary_match=sa.or_(*summaries),
        description_match=sa.or_(*descriptions),
        identifier_match=identifier,
    )


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        self.parts.append(" ")


def case_search_excerpt(
    field: Literal["summary", "description"],
    native_context: str,
    *,
    start_marker: str,
    stop_marker: str,
) -> CaseSearchExcerpt:
    """Format native match context as bounded text, without fuzzy heuristics.

    Markers are private per-request strings placed by PostgreSQL around a real
    matching token. They are removed before transport. If markup hides a match
    in an attribute, preserve the source window as literal text rather than
    silently substituting an unrelated beginning-of-field snippet.
    """
    extractor = _TextExtractor()
    extractor.feed(native_context)
    extractor.close()
    text = " ".join("".join(extractor.parts).split())
    if start_marker not in text:
        text = " ".join(native_context.split())
    anchor = text.find(start_marker)
    text = text.replace(start_marker, "").replace(stop_marker, "")
    start = max(0, anchor - 60)
    prefix = "…" if start else ""
    available = MAX_EXCERPT_LENGTH - len(prefix)
    suffix = "…" if len(text) > start + available else ""
    fragment = text[start : start + available - len(suffix)]
    return CaseSearchExcerpt(field, prefix + fragment + suffix)


class _ExcerptClause(TypedDict):
    value: str
    kind: Literal["word", "phrase", "literal"]
    fuzzy: bool
    pattern: str
    position: int


_EXCERPT_SQL = f"""
WITH page_sources AS MATERIALIZED (
    SELECT c.id AS case_id, f.field, f.source, f.vector
    FROM public."case" c
    CROSS JOIN LATERAL (VALUES ('summary', c.summary, c.summary_tsv),
        ('description', c.description, c.description_tsv)) f(field, source, vector)
    WHERE c.workspace_id = :workspace_id AND c.id = ANY(:case_ids) AND f.field = ANY(:columns)
), clauses AS (
    SELECT * FROM jsonb_to_recordset(CAST(:clauses AS jsonb))
        AS q(value text, kind text, fuzzy boolean, pattern text, position integer)
), matched AS MATERIALIZED (
    SELECT s.*, q.*, CASE q.kind
        WHEN 'literal' THEN lower(s.source) LIKE lower(q.pattern) ESCAPE '\\'
        WHEN 'phrase' THEN CASE WHEN s.vector IS NULL THEN
            public.{CASE_TEXT_MATCH_FUNCTION}(s.source, q.value, true)
            ELSE (s.vector @@ phraseto_tsquery('pg_catalog.simple', q.value))
                OR ((s.vector @@ plainto_tsquery('pg_catalog.simple', q.value))
                    AND public.{CASE_TEXT_MATCH_FUNCTION}(s.source, q.value, true)) END
        ELSE CASE WHEN s.vector IS NULL THEN
            public.{CASE_TEXT_MATCH_FUNCTION}(s.source, q.value, false)
            ELSE s.vector @@ plainto_tsquery('pg_catalog.simple', q.value) END
    END AS exact
    FROM page_sources s CROSS JOIN clauses q
), exact_context AS (
    SELECT case_id, field, position, 0 AS priority, 0 AS anchor,
        CASE kind WHEN 'literal' THEN
            overlay(substring(source FROM greatest(1, strpos(lower(source), lower(value)) - 60) FOR 480)
                PLACING :start_marker FROM least(61, strpos(lower(source), lower(value))) FOR 0)
        ELSE ts_headline('pg_catalog.simple', source,
            CASE WHEN kind = 'phrase' AND (vector @@ phraseto_tsquery('pg_catalog.simple', value))
                THEN phraseto_tsquery('pg_catalog.simple', value)
                ELSE plainto_tsquery('pg_catalog.simple', value) END,
            :headline_options)
        END AS context
    FROM matched WHERE exact
), tokens AS (
    SELECT m.*, t.parts[1] AS token,
        (sum(length(t.parts[1])) OVER (PARTITION BY m.case_id, m.field, m.position ORDER BY t.ordinality)
            - length(t.parts[1]) + 1)::integer AS token_start
    FROM matched m
    CROSS JOIN LATERAL regexp_matches(m.source, '[[:alnum:]]+|[^[:alnum:]]+', 'g')
        WITH ORDINALITY AS t(parts, ordinality)
    WHERE NOT m.exact AND m.fuzzy AND lower(m.source) %>> lower(m.value)
), fuzzy_context AS (
    SELECT case_id, field, position, 1 AS priority, token_start AS anchor,
        overlay(substring(source FROM greatest(1, token_start - 60) FOR 480)
            PLACING :start_marker FROM least(61, token_start) FOR 0) AS context
    FROM tokens WHERE token ~ '^[[:alnum:]]+$'
        AND strict_word_similarity(lower(value), lower(token)) >= :threshold
), contexts AS (
    SELECT * FROM exact_context UNION ALL SELECT * FROM fuzzy_context
), chosen AS (
    SELECT DISTINCT ON (case_id, field) case_id, field, context
    FROM contexts ORDER BY case_id, field, priority, position, anchor
), cleaned AS (
    SELECT case_id, field,
        overlay(replace(replace(context, :start_marker, ''), :stop_marker, '')
            PLACING :start_marker FROM greatest(1, strpos(context, :start_marker)) FOR 0) AS context
    FROM chosen
)
SELECT case_id, field, substring(context FROM greatest(1, strpos(context, :start_marker) - 60) FOR 640) AS context
FROM cleaned
"""


async def get_case_search_excerpts(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    case_ids: Sequence[uuid.UUID],
    query: CaseTextQuery,
) -> dict[uuid.UUID, tuple[CaseSearchExcerpt, ...]]:
    """Extract native match context in one batch for returned page IDs only.

    Owns a transaction/savepoint and reads at most the API page maximum, scoped
    to a workspace and explicit IDs.
    Full source text is evaluated inside PostgreSQL, including late matches;
    only bounded source windows leave the database. The caller supplies the
    IDs after pagination, excluding lookahead rows. Reference queries can also
    match textual mentions. Fields that no longer match have no excerpt.
    """
    if len(case_ids) > config.TRACECAT__LIMIT_CURSOR_MAX:
        raise ValueError("Excerpt IDs must fit one returned case page")
    if not case_ids or not query.clauses:
        return {}
    marker = uuid.uuid4().hex
    start_marker, stop_marker = f"TCSTART_{marker}", f"TCSTOP_{marker}"
    clauses: list[_ExcerptClause] = []
    for position, clause in enumerate(query.clauses):
        clauses.append(
            {
                "value": clause.value,
                "kind": clause.kind,
                "fuzzy": clause.fuzzy,
                "pattern": _literal_pattern(clause.value),
                "position": position,
            }
        )
    statement = sa.text(_EXCERPT_SQL).bindparams(
        sa.bindparam("workspace_id", type_=UUID()),
        sa.bindparam("case_ids", type_=ARRAY(UUID())),
        sa.bindparam("columns", type_=ARRAY(sa.Text())),
    )
    transaction = (
        session.begin_nested() if session.in_transaction() else session.begin()
    )
    async with (
        transaction,
        case_search_execution_context(
            session, use_trigram_threshold=any(clause.fuzzy for clause in query.clauses)
        ),
    ):
        rows = (
            (
                await session.execute(
                    statement,
                    {
                        "workspace_id": workspace_id,
                        "case_ids": list(dict.fromkeys(case_ids)),
                        "columns": list(query.columns),
                        "clauses": orjson.dumps(clauses).decode(),
                        "threshold": FUZZY_THRESHOLD,
                        "start_marker": start_marker,
                        "stop_marker": stop_marker,
                        "headline_options": f"MaxFragments=1, MaxWords=16, MinWords=3, ShortWord=0, StartSel={start_marker}, StopSel={stop_marker}",
                    },
                )
            )
            .tuples()
            .all()
        )
    excerpts: dict[uuid.UUID, list[CaseSearchExcerpt]] = {}
    for case_id, field, context in rows:
        excerpts.setdefault(case_id, []).append(
            case_search_excerpt(
                field,
                context,
                start_marker=start_marker,
                stop_marker=stop_marker,
            )
        )
    return {case_id: tuple(values) for case_id, values in excerpts.items()}


@asynccontextmanager
async def case_search_execution_context(
    session: AsyncSession,
    *,
    check_readiness: bool = False,
    use_trigram_threshold: bool = True,
) -> AsyncIterator[None]:
    """Apply search limits; the caller owns transaction/savepoint rollback.

    Callers with no fuzzy clauses can skip trigram settings. Standalone callers
    retain the fuzzy threshold by default, including extension loading before
    reading its connection-local setting.
    """
    try:
        async with query_execution_context(
            session,
            statement_timeout_ms=config.TRACECAT__CASE_SEARCH_STATEMENT_TIMEOUT_MS,
        ):
            if check_readiness:
                await ensure_case_text_indexes(session)
            if not use_trigram_threshold:
                yield
                return
            # Extension GUCs are registered only after this connection loads
            # pg_trgm. A fresh pooled connection cannot SHOW them before that.
            await session.execute(sa.text("SELECT strict_word_similarity('', '')"))
            previous_threshold = await session.scalar(
                sa.text("SHOW pg_trgm.strict_word_similarity_threshold")
            )
            threshold_changed = float(previous_threshold) != FUZZY_THRESHOLD
            if threshold_changed:
                await session.execute(
                    sa.text(
                        "SELECT set_config('pg_trgm.strict_word_similarity_threshold', :threshold, true)"
                    ),
                    {"threshold": str(FUZZY_THRESHOLD)},
                )
            yield
            if threshold_changed:
                await session.execute(
                    sa.text(
                        "SELECT set_config('pg_trgm.strict_word_similarity_threshold', :threshold, true)"
                    ),
                    {"threshold": previous_threshold},
                )
    except TracecatQueryTimeoutError as exc:
        raise TracecatQueryTimeoutError(
            "Case search timed out. Narrow the query or filters."
        ) from exc
