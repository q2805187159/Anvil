"""Tests for semantic compressor."""

from __future__ import annotations

import pytest

from anvil.token_optimization.contracts import CompressionLevel, TokenOptimizationConfig
from anvil.token_optimization.semantic_compressor import SemanticCompressor


@pytest.fixture
def config():
    """Create test configuration."""
    return TokenOptimizationConfig()


@pytest.fixture
def compressor(config):
    """Create compressor instance."""
    return SemanticCompressor(config)


class TestSemanticCompressor:
    """Tests for SemanticCompressor."""

    def test_compress_basic(self, compressor):
        """Test basic compression."""
        content = "This is a test. This is another test. This is a third test."
        result = compressor.compress(content)

        assert result.compressed_tokens < result.original_tokens
        assert result.token_savings > 0
        assert result.compression_ratio < 1.0

    def test_compress_with_target_ratio(self, compressor):
        """Test compression with target ratio."""
        content = "First sentence. Second sentence. Third sentence. Fourth sentence."
        result = compressor.compress(content, target_ratio=0.5)

        assert result.compression_ratio <= 0.6  # Allow some variance

    def test_extract_facts(self, compressor):
        """Test fact extraction."""
        content = "The system processes data. It handles errors. The output is validated."
        facts = compressor._extract_facts(content)

        assert len(facts) > 0
        assert all(isinstance(fact, str) for fact in facts)

    def test_deduplicate_facts(self, compressor):
        """Test fact deduplication."""
        facts = [
            "The system processes data",
            "The system processes information",  # Similar
            "Error handling is important",
        ]
        unique = compressor._deduplicate_facts(facts)

        assert len(unique) <= len(facts)

    def test_compress_empty_content(self, compressor):
        """Test compressing empty content."""
        result = compressor.compress("")

        assert result.compressed == ""
        assert result.token_savings == 0

    def test_compress_short_content(self, compressor):
        """Test compressing short content."""
        content = "Short text."
        result = compressor.compress(content)

        assert result.compressed_tokens <= result.original_tokens

    def test_compression_disabled(self):
        """Test compression can be disabled."""
        config = TokenOptimizationConfig(enable_semantic_compression=False)
        compressor = SemanticCompressor(config)

        content = "This is a test with multiple sentences."
        result = compressor.compress(content)

        assert result.compressed == content
        assert result.token_savings == 0

    def test_compress_batch(self, compressor):
        """Test batch compression."""
        contents = [
            "First content with multiple sentences.",
            "Second content with different text.",
            "Third content for testing.",
        ]

        results = compressor.compress_batch(contents)

        assert len(results) == 3
        assert all(r.token_savings >= 0 for r in results)

    def test_is_filler(self, compressor):
        """Test filler detection."""
        assert compressor._is_filler("However, this is important") is True
        assert compressor._is_filler("As mentioned earlier") is True
        assert compressor._is_filler("The system works correctly") is False

    def test_fact_hash(self, compressor):
        """Test fact hashing."""
        fact1 = "The system processes data efficiently"
        fact2 = "The system processes data efficiently"
        fact3 = "The system handles errors properly"

        hash1 = compressor._fact_hash(fact1)
        hash2 = compressor._fact_hash(fact2)
        hash3 = compressor._fact_hash(fact3)

        assert hash1 == hash2
        assert hash1 != hash3

    def test_reconstruct(self, compressor):
        """Test content reconstruction."""
        facts = ["Fact one", "Fact two", "Fact three", "Fact four"]
        reconstructed = compressor._reconstruct(facts, target_ratio=0.5)

        assert len(reconstructed) > 0
        assert reconstructed.endswith('.')

    def test_token_counting(self, compressor):
        """Test token counting."""
        text = "This is a test with ten words in it."
        tokens = compressor._count_tokens(text)

        assert tokens > 0
        assert 10 <= tokens <= 15  # Approximate

    def test_compression_preserves_meaning(self, compressor):
        """Test that compression preserves key information."""
        content = "The user requested a file. The system read the file. The file was returned successfully."
        result = compressor.compress(content, target_ratio=0.7)

        # Key words should be preserved
        assert "file" in result.compressed.lower()

    def test_compression_time_tracking(self, compressor):
        """Test compression time is tracked."""
        content = "Test content for compression timing."
        result = compressor.compress(content)

        assert result.compression_time_ms >= 0
