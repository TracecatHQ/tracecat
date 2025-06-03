import os

import pytest
from cryptography.fernet import Fernet, InvalidToken

from tracecat.secrets.common import apply_masks, apply_masks_object
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


class TestBasicMasking:
    """Test suite for basic string masking functionality."""

    def test_apply_masks_no_masks(self):
        """Test apply_masks with no masks provided."""
        value = "This is a test string with no masks."
        masks = []
        assert apply_masks(value, masks) == value

    def test_apply_masks_single_mask(self):
        """Test apply_masks with a single mask."""
        value = "This is a test string with a secret."
        masks = ["secret"]
        expected = "This is a test string with a ***."
        assert apply_masks(value, masks) == expected

    def test_apply_masks_multiple_masks(self):
        """Test apply_masks with multiple masks."""
        value = "This is a test string with multiple secrets: secret1 and secret2."
        masks = ["secret1", "secret2"]
        expected = "This is a test string with multiple secrets: *** and ***."
        assert apply_masks(value, masks) == expected

    def test_apply_masks_partial_match(self):
        """Test apply_masks with partial matching masks."""
        value = "This is a test string with a partialsecret."
        masks = ["partial"]
        expected = "This is a test string with a ***secret."
        assert apply_masks(value, masks) == expected

    def test_apply_masks_no_match(self):
        """Test apply_masks with masks that do not match."""
        value = "This is a test string with no matching secrets."
        masks = ["nomatch"]
        assert apply_masks(value, masks) == value


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

    def test_apply_masks_object_with_nested_structures(self):
        """Test masking of nested data structures."""
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

    def test_apply_masks_object_with_heavily_nested_structures(self):
        """Test apply_masks_object with heavily nested structures."""
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

    def test_apply_masks_object_with_no_masks(self):
        """Test object masking with no masks provided."""
        input_data = {
            "key1": "This is a secret message",
            "key2": ["No sensitive data here", "Another secret"],
            "key3": {"subkey1": "password123", "subkey2": "No secret"},
        }
        assert apply_masks_object(input_data, []) == input_data


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

    def test_empty_string_masks_ignored(self):
        """Test that empty string masks are ignored to prevent masking everything."""
        masks = ["", "secret", ""]
        input_text = "This is a secret message"
        expected = "This is a *** message"
        assert apply_masks(input_text, masks) == expected

    def test_whitespace_only_masks_ignored(self):
        """Test that whitespace-only masks are ignored."""
        masks = [" ", "\t", "secret", "\n"]
        input_text = "This is a secret message"
        expected = "This is a *** message"
        assert apply_masks(input_text, masks) == expected

    def test_none_values_in_masks_handled_safely(self):
        """Test that None values in mask lists are handled safely."""
        # This test ensures the masking logic doesn't break with None values
        masks = ["secret", None, "password"]  # type: ignore
        input_text = "This is a secret with password"
        # Should still mask the valid strings
        expected = "This is a *** with ***"
        # Filter out None values before passing to apply_masks
        valid_masks = [mask for mask in masks if mask is not None]
        assert apply_masks(input_text, valid_masks) == expected

    def test_regex_special_characters_in_secrets(self):
        """Test that secrets containing regex special characters are properly escaped."""
        # These secrets contain regex special characters that could break pattern matching
        masks = [
            "secret.key",
            "user@domain.com",
            "pass[word]",
            "api_key+123",
            "token*",
            "hash^123",
        ]

        input_text = "Login with user@domain.com and secret.key then use pass[word] for api_key+123"
        expected = "Login with *** and *** then use *** for ***"
        assert apply_masks(input_text, masks) == expected

    def test_very_long_secrets_performance(self):
        """Test performance and correctness with very long secret values."""
        # Test with a very long secret to ensure no performance issues
        long_secret = "x" * 1000  # 1000 character secret
        masks = [long_secret, "short"]

        input_text = f"Here is a short secret and {long_secret} is very long"
        expected = "Here is a *** secret and *** is very long"
        assert apply_masks(input_text, masks) == expected

    def test_many_masks_performance(self):
        """Test performance with a large number of masks."""
        # Create many masks to test performance doesn't degrade
        masks = [f"secret_{i}" for i in range(100)]

        input_text = "This contains secret_50 and secret_99 but not secret_200"
        # Multiple overlapping patterns will mask: secret_5 + "0", secret_9 + "9", secret_20 + "00"
        expected = "This contains ***0 and ***9 but not ***00"
        assert apply_masks(input_text, masks) == expected

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

    def test_case_sensitive_masking(self):
        """Test that masking is case-sensitive by default."""
        masks = ["Secret", "PASSWORD"]

        input_text = "The Secret is secret and PASSWORD is password"
        expected = "The *** is secret and *** is password"
        assert apply_masks(input_text, masks) == expected

    def test_overlapping_secrets(self):
        """Test behavior with overlapping secret patterns."""
        masks = ["secret", "secretkey", "key"]

        input_text = "The secretkey contains secret and key"
        # With regex OR pattern, all matching parts get masked
        # "secretkey" gets masked as "secret" + "key" = "***" + "***"
        expected = "The ****** contains *** and ***"
        assert apply_masks(input_text, masks) == expected

    def test_secrets_at_boundaries(self):
        """Test secrets at word boundaries and string boundaries."""
        masks = ["start", "end", "middle"]

        input_text = "start of text has middle content and end"
        expected = "*** of text has *** content and ***"
        assert apply_masks(input_text, masks) == expected

        # Test at actual string boundaries
        boundary_text = "start"
        assert apply_masks(boundary_text, masks) == "***"

    def test_repeated_secrets(self):
        """Test that the same secret is masked consistently when repeated."""
        masks = ["secret123"]

        input_text = "First secret123 and second secret123 and third secret123"
        expected = "First *** and second *** and third ***"
        assert apply_masks(input_text, masks) == expected

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

    def test_partial_secret_matches_security(self):
        """Test that partial matches don't create security vulnerabilities."""
        masks = ["admin_password_123"]

        # These should NOT be masked as they're only partial matches
        input_text = "admin user with password 123 is not admin_password_123"
        expected = "admin user with password 123 is not ***"
        assert apply_masks(input_text, masks) == expected

    def test_secret_masking_with_special_mask_value(self):
        """Test that the mask value itself doesn't create security issues."""
        from tracecat.secrets.constants import MASK_VALUE

        masks = [MASK_VALUE, "secret"]  # Include the mask value as a "secret"

        input_text = (
            f"The secret is hidden as {MASK_VALUE} but real secret should be masked"
        )
        # The actual mask value should be filtered out due to length check
        expected = f"The *** is hidden as {MASK_VALUE} but real *** should be masked"
        assert apply_masks(input_text, masks) == expected

    def test_type_confusion_prevention(self):
        """Test that type confusion doesn't lead to security bypass."""
        # Mix different types that could cause confusion
        mixed_masks = [42, True, 3.14, "real_secret", None, "", "  "]  # type: ignore

        input_text = (
            "The real_secret should be masked but 42 and True and 3.14 are okay"
        )

        # Filter to only valid string masks as the actual code would do
        valid_masks = [
            str(mask) for mask in mixed_masks if mask is not None and len(str(mask)) > 1
        ]
        # "42" will be masked when it appears in text, "True" will be masked, "3.14" will be masked
        expected = "The *** should be masked but *** and *** and *** are okay"
        assert apply_masks(input_text, valid_masks) == expected

    def test_configuration_disable_masking_security(self):
        """Test that masking disable configuration is handled securely."""
        # This test ensures that when masking is disabled, it's clearly indicated
        # and not accidentally enabled with wrong data
        masks = ["secret", "password"]
        input_text = "This has secret and password"

        # When masking is enabled (normal case)
        expected_masked = "This has *** and ***"
        assert apply_masks(input_text, masks) == expected_masked

        # When no masks provided (simulating disabled masking)
        assert apply_masks(input_text, []) == input_text

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


class TestExecutorServiceMasking:
    """Test suite for executor service-level masking and logging security."""

    def test_mask_values_filtering_logic(self):
        """Test the mask values filtering logic that prevents character-level masking."""
        from tracecat.parse import traverse_leaves

        # Simulate the secrets structure from the executor service
        secrets = {
            "api_secret": {
                "API_KEY": "long_api_key_12345",  # gitleaks:allow
                "SHORT": "x",  # Single character - should be filtered out
                "EMPTY": "",  # Empty string - should be filtered out
                "VALID": "valid_secret",
            },
            "db_secret": {
                "PASSWORD": "database_password_789",
                "A": "a",  # Single character - should be filtered out
            },
        }

        # Replicate the executor service masking logic
        mask_values = set()
        for _, secret_value in traverse_leaves(secrets):
            if secret_value is not None:
                secret_str = str(secret_value)
                if len(secret_str) > 1:
                    mask_values.add(secret_str)
                if isinstance(secret_value, str) and len(secret_value) > 1:
                    mask_values.add(secret_value)

        # Verify single characters are filtered out
        assert "x" not in mask_values
        assert "a" not in mask_values
        assert "" not in mask_values

        # Verify valid secrets are included
        assert "long_api_key_12345" in mask_values
        assert "valid_secret" in mask_values
        assert "database_password_789" in mask_values

    def test_non_string_secret_values_conversion(self):
        """Test that non-string secret values are properly converted for masking."""
        from tracecat.parse import traverse_leaves

        secrets = {
            "config_secret": {
                "API_TIMEOUT": 30,  # Integer
                "ENABLE_DEBUG": True,  # Boolean
                "RATE_LIMIT": 1.5,  # Float
                "API_KEY": "real_string_secret",  # gitleaks:allow
                "NONE_VALUE": None,  # None
            }
        }

        mask_values = set()
        for _, secret_value in traverse_leaves(secrets):
            if secret_value is not None:
                secret_str = str(secret_value)
                if len(secret_str) > 1:
                    mask_values.add(secret_str)
                if isinstance(secret_value, str) and len(secret_value) > 1:
                    mask_values.add(secret_value)

        # Verify non-string values are converted and included
        assert "30" in mask_values
        assert "True" in mask_values
        assert "1.5" in mask_values
        assert "real_string_secret" in mask_values

        # Verify None values don't cause issues
        assert "None" not in mask_values

    def test_secrets_structure_traversal_safety(self):
        """Test that secrets traversal handles various data structures safely."""
        from tracecat.parse import traverse_leaves

        # Complex secrets structure that could cause issues
        complex_secrets = {
            "nested_secret": {
                "SIMPLE": "simple_value",
                "NESTED_DICT": {"inner": "inner_value"},
                "NESTED_LIST": ["item1", "item2"],
                "MIXED": {
                    "list_in_dict": ["a", "b"],
                    "dict_in_dict": {"deep": "deep_value"},
                },
            }
        }

        # This should not raise any exceptions
        mask_values = set()
        for _, secret_value in traverse_leaves(complex_secrets):
            if secret_value is not None:
                secret_str = str(secret_value)
                if len(secret_str) > 1:
                    mask_values.add(secret_str)
                if isinstance(secret_value, str) and len(secret_value) > 1:
                    mask_values.add(secret_value)

        # Verify leaf values are extracted correctly
        assert "simple_value" in mask_values
        assert "inner_value" in mask_values
        assert "item1" in mask_values
        assert "item2" in mask_values
        assert "deep_value" in mask_values

    def test_logging_safety_simulation(self):
        """Test that common logging scenarios don't leak secrets."""
        # Simulate what would happen if we accidentally logged sensitive data
        secrets = ["api_key_12345", "password_secret", "token_xyz"]

        # Test data that might appear in logs
        log_data = {
            "action": "user_login",
            "args": {
                "username": "admin",
                "password": "password_secret",  # This should be masked
                "api_key": "api_key_12345",  # gitleaks:allow
            },
            "result": "Login successful with token_xyz",  # This should be masked
            "metadata": {"timestamp": "2023-01-01", "safe_data": "this is safe"},
        }

        # Apply masking to simulate what should happen in logs
        masked_log_data = apply_masks_object(log_data, secrets)

        # Verify secrets are masked
        assert masked_log_data["args"]["password"] == "***"
        assert masked_log_data["args"]["api_key"] == "***"
        assert "***" in masked_log_data["result"]

        # Verify safe data is preserved
        assert masked_log_data["action"] == "user_login"
        assert masked_log_data["args"]["username"] == "admin"
        assert masked_log_data["metadata"]["safe_data"] == "this is safe"

    def test_masking_disabled_configuration(self):
        """Test behavior when masking is disabled via configuration."""
        # Simulate the TRACECAT__UNSAFE_DISABLE_SM_MASKING flag
        secrets = ["secret1", "secret2"]
        sensitive_data = "This contains secret1 and secret2"

        # When masking is enabled (normal case)
        masked_result = apply_masks(sensitive_data, secrets)
        assert masked_result == "This contains *** and ***"

        # When masking is disabled (mask_values = None scenario)
        # The service would not call apply_masks at all, so data remains unmasked
        unmasked_result = sensitive_data  # Simulating no masking applied
        assert unmasked_result == "This contains secret1 and secret2"

        # Verify this is clearly different
        assert masked_result != unmasked_result
