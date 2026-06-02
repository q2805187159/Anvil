"""Cache key generator for consistent hashing."""

from __future__ import annotations

import hashlib
import json
from typing import Any


class CacheKeyGenerator:
    """Generates consistent cache keys for various inputs.

    Handles:
    - Prompt normalization
    - Parameter ordering
    - Hash generation
    """

    @staticmethod
    def normalize_prompt(prompt: str) -> str:
        """Normalize prompt for consistent hashing.

        Args:
            prompt: Raw prompt text

        Returns:
            Normalized prompt
        """
        # Remove extra whitespace
        normalized = " ".join(prompt.split())

        # Convert to lowercase for case-insensitive matching
        normalized = normalized.lower()

        return normalized

    @staticmethod
    def generate_response_key(
        prompt: str,
        model: str = "",
        temperature: float = 0.0,
        system_prompt: str = "",
        **kwargs: Any
    ) -> str:
        """Generate cache key for LLM response.

        Args:
            prompt: User prompt
            model: Model name
            temperature: Temperature setting
            system_prompt: System prompt
            **kwargs: Additional parameters

        Returns:
            Cache key (hex hash)
        """
        # Normalize prompt
        normalized_prompt = CacheKeyGenerator.normalize_prompt(prompt)

        # Create key components
        components = {
            "prompt": normalized_prompt,
            "model": model,
            "temperature": temperature,
            "system_prompt": system_prompt,
            **kwargs
        }

        # Sort keys for consistent ordering
        key_str = json.dumps(components, sort_keys=True)

        # Generate hash
        return hashlib.sha256(key_str.encode()).hexdigest()

    @staticmethod
    def generate_tool_key(
        tool_name: str,
        args: dict[str, Any],
        **kwargs: Any
    ) -> str:
        """Generate cache key for tool result.

        Args:
            tool_name: Name of tool
            args: Tool arguments
            **kwargs: Additional parameters

        Returns:
            Cache key (hex hash)
        """
        # Create key components
        components = {
            "tool": tool_name,
            "args": args,
            **kwargs
        }

        # Sort keys for consistent ordering
        key_str = json.dumps(components, sort_keys=True, default=str)

        # Generate hash
        return hashlib.sha256(key_str.encode()).hexdigest()

    @staticmethod
    def generate_file_key(file_path: str, mtime: float | None = None) -> str:
        """Generate cache key for file content.

        Args:
            file_path: Path to file
            mtime: File modification time (optional)

        Returns:
            Cache key (hex hash)
        """
        components = {
            "file_path": file_path,
            "mtime": mtime
        }

        key_str = json.dumps(components, sort_keys=True)
        return hashlib.sha256(key_str.encode()).hexdigest()

    @staticmethod
    def generate_semantic_key(embedding: list[float]) -> str:
        """Generate cache key for semantic embedding.

        Args:
            embedding: Vector embedding

        Returns:
            Cache key (hex hash)
        """
        # Convert embedding to string representation
        embedding_str = json.dumps(embedding)

        # Generate hash
        return hashlib.sha256(embedding_str.encode()).hexdigest()
