import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
EXA_ROOT = (
    ROOT
    / "packages"
    / "tracecat-registry"
    / "tracecat_registry"
    / "templates"
    / "tools"
    / "exa"
)
EXPECTED_SECRET = [{"name": "exa", "keys": ["EXA_API_KEY"]}]
EXA_SEARCH_TYPE = (
    'enum["instant", "fast", "auto", "deep-lite", "deep", "deep-reasoning"] | None'
)
EXA_COMPLIANCE = 'enum["hipaa"] | None'


def load_template(filename: str) -> dict:
    with (EXA_ROOT / filename).open() as handle:
        return yaml.safe_load(handle)


def request_step(template: dict) -> dict:
    return next(
        step
        for step in template["definition"]["steps"]
        if step["action"] == "core.http_request"
    )


def test_exa_template_surface_matches_current_core_tools():
    templates = {path.name: load_template(path.name) for path in EXA_ROOT.glob("*.yml")}

    assert set(templates) == {
        "search.yml",
        "search_news.yml",
        "search_people.yml",
        "search_companies.yml",
        "get_contents.yml",
        "answer.yml",
        "deep_research.yml",
    }

    for template in templates.values():
        definition = template["definition"]
        assert definition["namespace"] == "tools.exa"
        assert definition["display_group"] == "Exa"
        assert definition["secrets"] == EXPECTED_SECRET
        assert definition["returns"].endswith(".result.data }}")
        assert [step["action"] for step in definition["steps"]] == ["core.http_request"]


def test_exa_templates_use_current_endpoints_and_auth():
    expected = {
        "search.yml": ("search", "https://api.exa.ai/search"),
        "search_news.yml": ("search_news", "https://api.exa.ai/search"),
        "search_people.yml": ("search_people", "https://api.exa.ai/search"),
        "search_companies.yml": ("search_companies", "https://api.exa.ai/search"),
        "get_contents.yml": ("get_contents", "https://api.exa.ai/contents"),
        "answer.yml": ("answer", "https://api.exa.ai/answer"),
        "deep_research.yml": ("deep_research", "https://api.exa.ai/search"),
    }

    for filename, (name, url) in expected.items():
        template = load_template(filename)
        definition = template["definition"]
        request = request_step(template)

        assert definition["name"] == name
        assert request["args"]["url"] == url
        assert request["args"]["method"] == "POST"
        assert (
            request["args"]["headers"]["x-api-key"] == "${{ SECRETS.exa.EXA_API_KEY }}"
        )


def test_exa_search_exposes_advanced_current_parameters():
    search = load_template("search.yml")
    expects = search["definition"]["expects"]
    payload = request_step(search)["args"]["payload"]

    for field in [
        "type",
        "numResults",
        "category",
        "includeDomains",
        "excludeDomains",
        "startPublishedDate",
        "endPublishedDate",
        "moderation",
        "contents",
        "additionalQueries",
        "systemPrompt",
        "outputSchema",
        "userLocation",
        "compliance",
    ]:
        assert field in expects
        assert payload[field] == f"${{{{ inputs.{field} }}}}"
    assert expects["type"]["type"] == EXA_SEARCH_TYPE
    assert expects["category"]["type"] == "str | None"
    assert expects["compliance"]["type"] == EXA_COMPLIANCE


def test_exa_vertical_search_wrappers_set_supported_categories():
    expected = {
        "search_people.yml": "people",
        "search_companies.yml": "company",
    }

    for filename, category in expected.items():
        template = load_template(filename)
        expects = template["definition"]["expects"]
        payload = request_step(template)["args"]["payload"]

        assert payload["category"] == category
        for supported in ["query", "type", "numResults", "contents", "outputSchema"]:
            assert supported in expects
        for unsupported in [
            "includeDomains",
            "excludeDomains",
            "startPublishedDate",
            "endPublishedDate",
        ]:
            assert unsupported not in expects
            assert unsupported not in payload
        assert expects["type"]["type"] == EXA_SEARCH_TYPE


def test_exa_news_search_wrapper_sets_news_category_and_filters():
    template = load_template("search_news.yml")
    expects = template["definition"]["expects"]
    payload = request_step(template)["args"]["payload"]

    assert payload["category"] == "news"
    for supported in [
        "query",
        "type",
        "numResults",
        "includeDomains",
        "excludeDomains",
        "startPublishedDate",
        "endPublishedDate",
        "contents",
        "outputSchema",
        "compliance",
    ]:
        assert supported in expects
    assert "category" not in expects
    assert expects["type"]["type"] == EXA_SEARCH_TYPE
    assert expects["compliance"]["type"] == EXA_COMPLIANCE


def test_exa_contents_uses_top_level_content_options():
    get_contents = load_template("get_contents.yml")
    expects = get_contents["definition"]["expects"]
    payload = request_step(get_contents)["args"]["payload"]

    assert expects["text"]["default"] is True
    assert payload["urls"] == "${{ inputs.urls }}"
    for field in [
        "text",
        "highlights",
        "summary",
        "maxAgeHours",
        "livecrawlTimeout",
        "subpages",
        "subpageTarget",
        "extras",
        "compliance",
    ]:
        assert payload[field] == f"${{{{ inputs.{field} }}}}"
    assert "contents" not in payload
    assert expects["compliance"]["type"] == EXA_COMPLIANCE


def test_exa_deep_research_replaces_deprecated_research_api():
    combined = "\n".join(path.read_text() for path in EXA_ROOT.glob("*.yml"))
    deep_research = load_template("deep_research.yml")
    payload = request_step(deep_research)["args"]["payload"]

    assert "/research/v1" not in combined
    assert payload["type"] == "deep-reasoning"
    assert (
        deep_research["definition"]["expects"]["compliance"]["type"] == EXA_COMPLIANCE
    )
    assert request_step(deep_research)["args"]["url"] == "https://api.exa.ai/search"
