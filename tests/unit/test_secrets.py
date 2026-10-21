import os

import pytest
from cryptography.fernet import Fernet, InvalidToken

from tracecat.secrets import common as secrets_common
from tracecat.secrets.common import apply_masks, apply_masks_object
from tracecat.secrets.constants import MASK_VALUE
from tracecat.secrets.encryption import (
    decrypt_bytes,
    decrypt_value,
    encrypt_bytes,
    encrypt_value,
)


@pytest.fixture
def test_encryption_key() -> str:
    """Generate a valid Fernet encryption key for testing.

    Returns:
        str: A base64-encoded 32-byte key suitable for Fernet encryption
    """
    return Fernet.generate_key().decode()


class TestEncryption:
    """Test suite for encryption and decryption functionality."""

    def test_encrypt_decrypt_object(self, env_sandbox):
        """Test encryption and decryption of complex objects."""
        key = os.getenv("TRACECAT__DB_ENCRYPTION_KEY")
        obj = {
            "client_id": "TEST_CLIENT_ID",
            "client_secret": "TEST_CLIENT_SECRET",
            "metadata": {"value": 1},
        }
        assert key is not None

        encrypted_obj = encrypt_bytes(obj, key=key)
        decrypted_obj = decrypt_bytes(encrypted_obj, key=key)
        assert decrypted_obj == obj

    def test_encrypt_decrypt_value(self, test_encryption_key):
        """Test successful encryption and decryption of a value."""
        original_value = b"test secret value"

        # Test encryption
        encrypted_value = encrypt_value(original_value, key=test_encryption_key)
        assert isinstance(encrypted_value, bytes)
        assert encrypted_value != original_value

        # Test decryption
        decrypted_value = decrypt_value(encrypted_value, key=test_encryption_key)
        assert decrypted_value == original_value

    def test_decrypt_value_invalid_key(self, test_encryption_key):
        """Test decryption with invalid key raises ValueError."""
        encrypted_value = encrypt_value(b"test value", key=test_encryption_key)

        with pytest.raises(ValueError):
            decrypt_value(encrypted_value, key="invalid_key")

    def test_decrypt_value_corrupted_token(self, test_encryption_key):
        """Test decryption with corrupted token raises InvalidToken."""
        corrupted_value = b"corrupted_token"

        with pytest.raises(InvalidToken):
            decrypt_value(corrupted_value, key=test_encryption_key)


MASK_CASES = [
    pytest.param("This is a test string with no masks.", [], None, id="no_masks"),
    pytest.param(
        "This is a test string with a secret.",
        ["secret"],
        "This is a test string with a ***.",
        id="single_mask",
    ),
    pytest.param(
        "This is a test string with multiple secrets: secret1 and secret2.",
        ["secret1", "secret2"],
        "This is a test string with multiple secrets: *** and ***.",
        id="multiple_masks",
    ),
    pytest.param(
        "This is a test string with a partialsecret.",
        ["partial"],
        "This is a test string with a ***secret.",
        id="partial_match",
    ),
    pytest.param(
        "This is a test string with no matching secrets.",
        ["nomatch"],
        None,
        id="no_match",
    ),
    pytest.param(
        "This is a secret message",
        ["", "secret", ""],
        "This is a *** message",
        id="empty_masks_ignored",
    ),
    pytest.param(
        "This is a secret message",
        [" ", "\t", "secret", "\n"],
        "This is a *** message",
        id="whitespace_masks_ignored",
    ),
    pytest.param(
        "Login with user@domain.com and secret.key then use pass[word] for api_key+123",
        [
            "secret.key",
            "user@domain.com",
            "pass[word]",
            "api_key+123",
            "token*",
            "hash^123",
        ],
        "Login with *** and *** then use *** for ***",
        id="regex_special_characters",
    ),
    pytest.param(
        f"Here is a short secret and {'x' * 1000} is very long",
        ["x" * 1000, "short"],
        "Here is a *** secret and *** is very long",
        id="very_long_secret",
    ),
    pytest.param(
        "This contains secret_50 and secret_99 but not secret_200",
        [f"secret_{i}" for i in range(100)],
        # Longest-first: secret_20 matches inside secret_200.
        "This contains *** and *** but not ***0",
        id="many_masks",
    ),
    pytest.param(
        "The Secret is secret and PASSWORD is password",
        ["Secret", "PASSWORD"],
        "The *** is secret and *** is password",
        id="case_sensitive",
    ),
    pytest.param(
        "The secretkey contains secret and key",
        ["secret", "secretkey", "key"],
        "The *** contains *** and ***",
        id="overlapping_longest_first",
    ),
    pytest.param(
        "start of text has middle content and end",
        ["start", "end", "middle"],
        "*** of text has *** content and ***",
        id="boundaries",
    ),
    pytest.param("start", ["start", "end"], "***", id="whole_string"),
    pytest.param(
        "First secret123 and second secret123 and third secret123",
        ["secret123"],
        "First *** and second *** and third ***",
        id="repeated",
    ),
    pytest.param(
        "admin user with password 123 is not admin_password_123",
        ["admin_password_123"],
        "admin user with password 123 is not ***",
        id="partial_secret_not_masked",
    ),
    pytest.param(
        f"The secret is hidden as {MASK_VALUE} but real secret should be masked",
        [MASK_VALUE, "secret"],
        f"The *** is hidden as {MASK_VALUE} but real *** should be masked",
        id="mask_value_as_secret",
    ),
    pytest.param(
        "The real_secret should be masked but 42 and True and 3.14 are okay",
        ["42", "True", "3.14", "real_secret", "  "],
        "The *** should be masked but *** and *** and *** are okay",
        id="stringified_non_string_secrets",
    ),
]


@pytest.mark.parametrize(("text", "masks", "expected"), MASK_CASES)
def test_apply_masks(text: str, masks: list[str], expected: str | None) -> None:
    assert apply_masks(text, masks) == (text if expected is None else expected)


class TestObjectMasking:
    """Test suite for complex object masking functionality."""

    def test_apply_masks_object_with_string(self):
        """Test masking of simple string objects."""
        masks = ["secret", "password"]
        assert (
            apply_masks_object("This is a secret message", masks)
            == "This is a *** message"
        )
        assert (
            apply_masks_object("No sensitive data here", masks)
            == "No sensitive data here"
        )

    def test_apply_masks_object_with_tuple(self):
        """Test apply_masks_object with a tuple."""
        masks = ["secret", "password"]
        input_tuple = ("This is a secret message", "No sensitive data here")
        expected_tuple = ("This is a *** message", "No sensitive data here")
        assert apply_masks_object(input_tuple, masks) == expected_tuple

    def test_apply_masks_object_with_dict(self):
        """Test masking of dictionary objects."""
        masks = ["secret", "password"]
        input_dict = {
            "key1": "This is a secret message",
            "key2": "No sensitive data here",
        }
        expected_dict = {
            "key1": "This is a *** message",
            "key2": "No sensitive data here",
        }
        assert apply_masks_object(input_dict, masks) == expected_dict

    def test_apply_masks_object_with_mixed_types(self):
        """Test apply_masks_object with mixed types."""
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

    def test_apply_masks_object_with_no_masks(self):
        """Test object masking with no masks provided."""
        input_data = {
            "key1": "This is a secret message",
            "key2": ["No sensitive data here", "Another secret"],
            "key3": {"subkey1": "password123", "subkey2": "No secret"},
        }
        assert apply_masks_object(input_data, []) == input_data

    def test_apply_masks_object_compiles_pattern_once(self, mocker):
        masks = ["secret", "password"]
        compile_pattern = mocker.spy(secrets_common, "_compile_mask_pattern")

        result = apply_masks_object(
            {
                "key1": "This is a secret message",
                "key2": ["password", "Another secret"],
            },
            masks,
        )

        assert result == {
            "key1": "This is a *** message",
            "key2": ["***", "Another ***"],
        }
        compile_pattern.assert_called_once_with(masks, include_short=False)

    def test_apply_masks_object_with_mask_generator(self):
        masks = (mask for mask in ["secret", "password"])

        result = apply_masks_object(
            ["secret", {"nested": "password"}],
            masks,
        )

        assert result == ["***", {"nested": "***"}]


class TestSecurityFeatures:
    """Test suite for security-related masking features and vulnerability prevention."""

    def test_prevent_individual_character_masking(self):
        """
        Test that individual characters from secret values don't get masked.
        This tests the fix for the bug where secret values containing common characters
        would cause those characters to be individually masked in output.
        """
        # If a secret contains "hello", individual characters should not be masked
        masks = ["hello", "world", "secret123"]

        # Text containing the same characters as secrets but not the full secret values
        input_text = "h e l l o w o r l d random text"

        # Should not mask individual characters, only complete secret values
        expected = "h e l l o w o r l d random text"
        assert apply_masks(input_text, masks) == expected

        # But should mask complete secret values
        input_text_with_secrets = "Say hello to the world and secret123 is here"
        expected_with_secrets = "Say *** to the *** and *** is here"
        assert apply_masks(input_text_with_secrets, masks) == expected_with_secrets

    def test_single_character_secrets_not_masked(self):
        """
        Test that single character secrets are not used for masking to avoid
        over-aggressive masking of common characters.
        """
        # Single character "secrets" should not be masked to avoid masking common chars
        single_char_masks = ["a", "e", "i", "o", "u"]

        input_data = {
            "message": "This is a test message with vowels",
            "status": "active",
        }

        # Should not mask any single characters
        expected_data = {
            "message": "This is a test message with vowels",
            "status": "active",
        }
        assert apply_masks_object(input_data, single_char_masks) == expected_data

    def test_non_string_secret_values_handling(self):
        """
        Test that non-string secret values are also masked when converted to strings.
        This ensures numeric, boolean, and other types are properly handled.
        """
        # Test with numeric and boolean values that would be converted to strings
        masks = ["12345", "True", "3.14159"]

        input_data = {
            "message": "The code is 12345 and status True with pi 3.14159",
            "numbers": [12345, 67890],
            "flags": [True, False],
            "floats": [3.14159, 2.71828],
        }

        expected_data = {
            "message": "The code is *** and status *** with pi ***",
            "numbers": [
                12345,
                67890,
            ],  # Numbers themselves don't get masked, only their string representations
            "flags": [
                True,
                False,
            ],  # Booleans themselves don't get masked, only their string representations
            "floats": [
                3.14159,
                2.71828,
            ],  # Floats themselves don't get masked, only their string representations
        }
        assert apply_masks_object(input_data, masks) == expected_data

    def test_unicode_secrets_handling(self):
        """Test that unicode characters in secrets are handled correctly."""
        masks = ["café", "naïve", "🔐secret", "пароль", "密码"]

        input_data = {
            "message": "The café password is naïve and 🔐secret with пароль and 密码",
            "unicode_list": ["café", "naïve", "🔐secret"],
        }

        expected_data = {
            "message": "The *** password is *** and *** with *** and ***",
            "unicode_list": ["***", "***", "***"],
        }
        assert apply_masks_object(input_data, masks) == expected_data

    def test_nested_object_secret_leakage_prevention(self):
        """Test that secrets don't leak through deeply nested object structures."""
        masks = ["deeply_hidden_secret"]

        complex_nested = {
            "level1": {
                "level2": {
                    "level3": {
                        "level4": {
                            "level5": [
                                {"secret_field": "deeply_hidden_secret"},
                                ("tuple_with", "deeply_hidden_secret"),
                                ["list", "with", "deeply_hidden_secret"],
                            ]
                        }
                    }
                }
            }
        }

        result = apply_masks_object(complex_nested, masks)

        # Verify the secret is masked at all levels
        assert (
            result["level1"]["level2"]["level3"]["level4"]["level5"][0]["secret_field"]
            == "***"
        )
        assert result["level1"]["level2"]["level3"]["level4"]["level5"][1][1] == "***"
        assert result["level1"]["level2"]["level3"]["level4"]["level5"][2][2] == "***"

    def test_memory_safety_large_objects(self):
        """Test that large objects don't cause memory issues during masking."""
        masks = ["secret_data"]

        # Create a large nested structure
        large_data = {
            f"key_{i}": {
                f"nested_{j}": f"value_{i}_{j}_secret_data"
                if j % 10 == 0
                else f"value_{i}_{j}"
                for j in range(50)
            }
            for i in range(20)
        }

        result = apply_masks_object(large_data, masks)

        # Verify some secret values were masked
        assert "***" in str(result)
        # Verify structure is preserved
        assert len(result) == 20
        assert len(result["key_0"]) == 50
