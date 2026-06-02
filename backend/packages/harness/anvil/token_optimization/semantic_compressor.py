"""Semantic compressor for token reduction while preserving meaning."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .contracts import CompressionLevel, CompressionResult, TokenOptimizationConfig


class SemanticCompressor:
    """Compresses content while preserving semantic meaning.

    Uses fact extraction and deduplication to reduce tokens
    without losing critical information.
    """

    def __init__(self, config: TokenOptimizationConfig):
        """Initialize compressor.

        Args:
            config: Token optimization configuration
        """
        self.config = config

    def compress(self, content: str, target_ratio: float | None = None) -> CompressionResult:
        """Compress content to target ratio.

        Args:
            content: Content to compress
            target_ratio: Target compression ratio (0.0-1.0), defaults to config

        Returns:
            Compression result
        """
        if not self.config.enable_semantic_compression:
            tokens = self._count_tokens(content)
            return CompressionResult(
                original=content,
                compressed=content,
                original_tokens=tokens,
                compressed_tokens=tokens,
                token_savings=0,
                compression_ratio=1.0,
            )

        start_time = datetime.now()
        target_ratio = target_ratio or self.config.compression_ratio

        # Extract facts
        facts = self._extract_facts(content)

        # Deduplicate facts
        unique_facts = self._deduplicate_facts(facts)

        # Reconstruct with target ratio
        compressed = self._reconstruct(unique_facts, target_ratio)

        # Calculate metrics
        original_tokens = self._count_tokens(content)
        compressed_tokens = self._count_tokens(compressed)
        token_savings = original_tokens - compressed_tokens
        actual_ratio = compressed_tokens / original_tokens if original_tokens > 0 else 1.0

        compression_time = (datetime.now() - start_time).total_seconds() * 1000

        return CompressionResult(
            original=content,
            compressed=compressed,
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            token_savings=token_savings,
            compression_ratio=actual_ratio,
            facts_preserved=len(unique_facts),
            compression_time_ms=compression_time,
        )

    def _extract_facts(self, content: str) -> list[str]:
        """Extract key facts from content.

        Args:
            content: Content to extract from

        Returns:
            List of facts
        """
        # Split into sentences
        sentences = re.split(r'[.!?]+', content)
        facts = []

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            # Skip very short sentences (likely noise)
            if len(sentence.split()) < 3:
                continue

            # Skip common filler phrases
            if self._is_filler(sentence):
                continue

            facts.append(sentence)

        return facts

    def _is_filler(self, sentence: str) -> bool:
        """Check if sentence is filler content.

        Args:
            sentence: Sentence to check

        Returns:
            True if filler
        """
        filler_patterns = [
            r'^(however|moreover|furthermore|additionally|in addition)',
            r'^(as mentioned|as stated|as noted)',
            r'^(it is important|it should be noted)',
            r'^(please note|keep in mind)',
        ]

        sentence_lower = sentence.lower()
        for pattern in filler_patterns:
            if re.match(pattern, sentence_lower):
                return True

        return False

    def _deduplicate_facts(self, facts: list[str]) -> list[str]:
        """Remove duplicate or very similar facts.

        Args:
            facts: List of facts

        Returns:
            Deduplicated facts
        """
        if not facts:
            return []

        unique_facts = []
        seen_hashes = set()

        for fact in facts:
            # Create a simple hash based on key words
            fact_hash = self._fact_hash(fact)

            if fact_hash not in seen_hashes:
                seen_hashes.add(fact_hash)
                unique_facts.append(fact)

        return unique_facts

    def _fact_hash(self, fact: str) -> int:
        """Create hash for fact deduplication.

        Args:
            fact: Fact to hash

        Returns:
            Hash value
        """
        # Extract key words (nouns, verbs, important terms)
        words = fact.lower().split()

        # Remove common stop words
        stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by'}
        key_words = [w for w in words if w not in stop_words]

        # Create hash from key words
        return hash(' '.join(sorted(key_words[:5])))  # Use first 5 key words

    def _reconstruct(self, facts: list[str], target_ratio: float) -> str:
        """Reconstruct content from facts with target ratio.

        Args:
            facts: List of facts
            target_ratio: Target compression ratio

        Returns:
            Reconstructed content
        """
        if not facts:
            return ""

        # Calculate how many facts to include
        target_count = max(1, int(len(facts) * target_ratio))

        # Take most important facts (first ones are usually more important)
        selected_facts = facts[:target_count]

        # Join with periods
        reconstructed = '. '.join(selected_facts)
        if reconstructed and not reconstructed.endswith('.'):
            reconstructed += '.'

        return reconstructed

    def _count_tokens(self, text: str) -> int:
        """Estimate token count.

        Args:
            text: Text to count

        Returns:
            Estimated token count
        """
        if not text:
            return 0
        words = len(text.split())
        return int(words / 0.75)  # Approximate: 1 token ≈ 0.75 words

    def compress_batch(self, contents: list[str], target_ratio: float | None = None) -> list[CompressionResult]:
        """Compress multiple contents.

        Args:
            contents: List of contents to compress
            target_ratio: Target compression ratio

        Returns:
            List of compression results
        """
        return [self.compress(content, target_ratio) for content in contents]
