"""脱敏函数测试：日志与 DB 不得出现明文。"""

from __future__ import annotations

from llm_platform.utils.masking import hash_value, mask_dict, mask_secret


class TestMaskSecret:
    def test_long_key(self):
        result = mask_secret("sk-abcdef1234567890abcdef1234567890")
        assert "sk-abcde" in result
        assert "***" in result
        assert "(len=" in result
        assert "abcdef1234567890abcdef" not in result

    def test_short_key(self):
        result = mask_secret("sk")
        assert "len=2" in result

    def test_empty_key(self):
        result = mask_secret("")
        assert "len=0" in result


class TestHashValue:
    def test_sha256_format(self):
        result = hash_value("hello")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self):
        assert hash_value("hello") == hash_value("hello")
        assert hash_value("hello") != hash_value("world")


class TestMaskDict:
    def test_mask_sensitive_keys(self):
        data = {
            "api_key": "sk-secret-123456",
            "name": "test",
            "prompt": "hello world this is a long prompt",
        }
        result = mask_dict(data)
        assert "***" in result["api_key"]
        assert "sk-secret" not in result["api_key"]
        assert result["name"] == "test"
        assert "***" in result["prompt"]

    def test_nested_masking(self):
        data = {
            "outer": {"key": "sk-nested-secret-12345", "safe": "value"},
            "list": [{"token": "bearer-secret-12345"}],
        }
        result = mask_dict(data)
        assert "***" in result["outer"]["key"]
        assert result["outer"]["safe"] == "value"
        assert "***" in result["list"][0]["token"]

    def test_extra_keys(self):
        data = {"custom_secret": "sensitive-value-12345"}
        result = mask_dict(data, extra_keys={"custom_secret"})
        assert "***" in result["custom_secret"]
