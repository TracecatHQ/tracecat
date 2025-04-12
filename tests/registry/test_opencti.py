from tracecat_registry.integrations.opencti import (
    list_entity_methods,
    list_entity_types,
)


def test_list_entity_types():
    """Test that the list_entity_types function returns a list of entity types."""
    important_entities = [
        "attack_pattern",
        "campaign",
        "location",
        "malware",
        "observed_data",
        "vulnerability",
    ]
    check = all(entity in list_entity_types() for entity in important_entities)
    assert check, list_entity_types()


def test_list_entity_methods():
    """Test that the list_entity_methods function returns a list of entity methods."""
    important_methods = ["create", "delete", "import_from_stix2", "list", "read"]
    check = all(
        method in list_entity_methods("attack_pattern") for method in important_methods
    )
    assert check, list_entity_methods("attack_pattern")
