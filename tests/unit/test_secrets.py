import os

from tracecat.secrets.common import apply_masks, apply_masks_object
from tracecat.secrets.encryption import decrypt_bytes, encrypt_bytes


def test_encrypt_decrypt_object(env_sandbox):
    key = os.getenv("TRACECAT__DB_ENCRYPTION_KEY")
    obj = {
        "client_id": "TEST_CLIENT_ID",
        "client_secret": "TEST_CLIENT_SECRET",
        "metadata": {"value": 1},
    }
    encrypted_obj = encrypt_bytes(obj, key=key)
    decrypted_obj = decrypt_bytes(encrypted_obj, key=key)
    assert decrypted_obj == obj


def test_apply_masks_no_masks():
    """
    Test apply_masks with no masks provided.
    """
    value = "This is a test string with no masks."
    masks = []
    assert apply_masks(value, masks) == value


def test_apply_masks_single_mask():
    """
    Test apply_masks with a single mask.
    """
    value = "This is a test string with a secret."
    masks = ["secret"]
    expected = "This is a test string with a ***."
    assert apply_masks(value, masks) == expected


def test_apply_masks_multiple_masks():
    """
    Test apply_masks with multiple masks.
    """
    value = "This is a test string with multiple secrets: secret1 and secret2."
    masks = ["secret1", "secret2"]
    expected = "This is a test string with multiple secrets: *** and ***."
    assert apply_masks(value, masks) == expected


def test_apply_masks_partial_match():
    """
    Test apply_masks with partial matching masks.
    """
    value = "This is a test string with a partialsecret."
    masks = ["partial"]
    expected = "This is a test string with a ***secret."
    assert apply_masks(value, masks) == expected


def test_apply_masks_no_match():
    """
    Test apply_masks with masks that do not match.
    """
    value = "This is a test string with no matching secrets."
    masks = ["nomatch"]
    assert apply_masks(value, masks) == value


def test_apply_masks_object_with_string():
    masks = ["secret", "password"]
    assert (
        apply_masks_object("This is a secret message", masks) == "This is a *** message"
    )
    assert (
        apply_masks_object("No sensitive data here", masks) == "No sensitive data here"
    )


def test_apply_masks_object_with_tuple():
    """
    Test apply_masks_object with a tuple.
    """
    masks = ["secret", "password"]
    input_tuple = ("This is a secret message", "No sensitive data here")
    expected_tuple = ("This is a *** message", "No sensitive data here")
    assert apply_masks_object(input_tuple, masks) == expected_tuple


def test_apply_masks_object_with_mixed_types():
    """
    Test apply_masks_object with mixed types.
    """
    masks = ["secret", "password"]
    input_data = [
        "This is a secret message",
        {"key": "password123"},
        ("No sensitive data here", "Another secret"),
        ["No sensitive data here", "Another secret in a list"],
    ]
    expected_data = [
        "This is a *** message",
        {"key": "***123"},
        ("No sensitive data here", "Another ***"),
        ["No sensitive data here", "Another *** in a list"],
    ]
    assert apply_masks_object(input_data, masks) == expected_data


def test_apply_masks_object_with_dict():
    masks = ["secret", "password"]
    input_dict = {"key1": "This is a secret message", "key2": "No sensitive data here"}
    expected_dict = {
        "key1": "This is a *** message",
        "key2": "No sensitive data here",
    }
    assert apply_masks_object(input_dict, masks) == expected_dict


def test_apply_masks_object_with_nested_structures():
    masks = ["secret", "password"]
    input_data = {
        "key1": "This is a secret message",
        "key2": ["No sensitive data here", "Another secret"],
        "key3": {
            "subkey1": "password123",
            "subkey2": "No secret",
        },
    }
    expected_data = {
        "key1": "This is a *** message",
        "key2": ["No sensitive data here", "Another ***"],
        "key3": {
            "subkey1": "***123",
            "subkey2": "No ***",
        },
    }
    assert apply_masks_object(input_data, masks) == expected_data


def test_apply_masks_object_with_no_masks():
    input_data = {
        "key1": "This is a secret message",
        "key2": ["No sensitive data here", "Another secret"],
        "key3": {"subkey1": "password123", "subkey2": "No secret"},
    }
    assert apply_masks_object(input_data, []) == input_data


def test_apply_masks_object_with_heavily_nested_structures():
    """
    Test apply_masks_object with heavily nested structures.
    """
    masks = ["secret", "password"]
    input_data = {
        "level1": {
            "level2": {
                "level3": {
                    "level4": {
                        "level5": {
                            "key1": "This is a secret message",
                            "key2": ["No sensitive data here", "Another secret"],
                            "key3": {
                                "subkey1": "password123",
                                "subkey2": "No secret",
                            },
                        }
                    }
                }
            }
        }
    }
    expected_data = {
        "level1": {
            "level2": {
                "level3": {
                    "level4": {
                        "level5": {
                            "key1": "This is a *** message",
                            "key2": ["No sensitive data here", "Another ***"],
                            "key3": {
                                "subkey1": "***123",
                                "subkey2": "No ***",
                            },
                        }
                    }
                }
            }
        }
    }
    assert apply_masks_object(input_data, masks) == expected_data
