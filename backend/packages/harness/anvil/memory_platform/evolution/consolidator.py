"""Consolidator for detecting patterns across observations."""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import TYPE_CHECKING

from .contracts import ConsolidatedPattern, MemoryEvolutionConfig

if TYPE_CHECKING:
    from ..stores.base import MemoryStore

logger = logging.getLogger(__name__)


class Consolidator:
    """Consolidates observations into long-term patterns.

    Based on agentmemory consolidation pattern:
    - Analyzes recent observations
    - Detects recurring patterns
    - Extracts user preferences
    - Identifies architectural patterns
    - Consolidates similar memories
    """

    def __init__(self, config: MemoryEvolutionConfig):
        """Initialize consolidator.

        Args:
            config: Memory evolution configuration
        """
        self.config = config

    def should_consolidate(self, observation_count: int, turns_since_last: int) -> bool:
        """Check if consolidation should run.

        Args:
            observation_count: Number of observations available
            turns_since_last: Turns since last consolidation

        Returns:
            True if should consolidate
        """
        if not self.config.consolidation_enabled:
            return False

        # Need minimum observations
        if observation_count < self.config.consolidation_min_observations:
            return False

        # Check interval
        if turns_since_last < self.config.consolidation_interval_turns:
            return False

        return True

    def consolidate(
        self,
        observations: list[dict],
        existing_patterns: list[ConsolidatedPattern] | None = None
    ) -> list[ConsolidatedPattern]:
        """Consolidate observations into patterns.

        Args:
            observations: Recent observations to analyze
            existing_patterns: Existing patterns to update

        Returns:
            List of consolidated patterns
        """
        patterns = []

        # Detect preferences
        preferences = self._detect_preferences(observations)
        patterns.extend(preferences)

        # Detect architectural patterns
        architecture = self._detect_architecture_patterns(observations)
        patterns.extend(architecture)

        # Detect workflow patterns
        workflows = self._detect_workflow_patterns(observations)
        patterns.extend(workflows)

        # Detect common bugs/issues
        bugs = self._detect_bug_patterns(observations)
        patterns.extend(bugs)

        # Update existing patterns with new evidence
        if existing_patterns:
            patterns = self._merge_patterns(patterns, existing_patterns)

        logger.info(f"Consolidated {len(observations)} observations into {len(patterns)} patterns")
        return patterns

    def _detect_preferences(self, observations: list[dict]) -> list[ConsolidatedPattern]:
        """Detect user preferences from observations.

        Args:
            observations: Observations to analyze

        Returns:
            List of preference patterns
        """
        patterns = []

        # Track tool usage preferences
        tool_usage = Counter()
        for obs in observations:
            if obs.get("type") == "tool_call":
                tool_name = obs.get("tool_name", "")
                if tool_name:
                    tool_usage[tool_name] += 1

        # Create patterns for frequently used tools
        total_tools = sum(tool_usage.values())
        if total_tools > 0:
            for tool, count in tool_usage.most_common(5):
                frequency = count / total_tools
                if frequency > 0.2:  # Used in >20% of cases
                    patterns.append(ConsolidatedPattern(
                        pattern_id=f"pref_tool_{tool}",
                        pattern_type="preference",
                        description=f"Prefers using {tool} tool (used {count} times)",
                        evidence=[f"Tool usage: {count}/{total_tools} ({frequency:.1%})"],
                        confidence=min(frequency * 2, 1.0)
                    ))

        # Track coding style preferences
        style_indicators = defaultdict(int)
        for obs in observations:
            content = obs.get("content", "")

            # Check for style indicators
            if "async def" in content or "await " in content:
                style_indicators["async_preferred"] += 1
            if "type:" in content or "-> " in content:
                style_indicators["type_hints_preferred"] += 1
            if '"""' in content or "'''" in content:
                style_indicators["docstrings_preferred"] += 1

        # Create style preference patterns
        for style, count in style_indicators.items():
            if count >= 3:  # Seen at least 3 times
                patterns.append(ConsolidatedPattern(
                    pattern_id=f"pref_style_{style}",
                    pattern_type="preference",
                    description=f"Code style preference: {style.replace('_', ' ')}",
                    evidence=[f"Observed {count} times in recent code"],
                    confidence=min(count / 10, 0.9)
                ))

        return patterns

    def _detect_architecture_patterns(self, observations: list[dict]) -> list[ConsolidatedPattern]:
        """Detect architectural patterns from observations.

        Args:
            observations: Observations to analyze

        Returns:
            List of architecture patterns
        """
        patterns = []

        # Track file organization patterns
        file_paths = []
        for obs in observations:
            if "file_path" in obs:
                file_paths.append(obs["file_path"])

        if file_paths:
            # Detect common directory structures
            directories = ["/".join(path.split("/")[:-1]) for path in file_paths]
            dir_counter = Counter(directories)

            for directory, count in dir_counter.most_common(3):
                if count >= 2:
                    patterns.append(ConsolidatedPattern(
                        pattern_id=f"arch_dir_{directory.replace('/', '_')}",
                        pattern_type="architecture",
                        description=f"Frequently works in directory: {directory}",
                        evidence=[f"Modified {count} files in this directory"],
                        confidence=min(count / 5, 0.8)
                    ))

        # Detect module patterns
        module_patterns = defaultdict(list)
        for obs in observations:
            content = obs.get("content", "")

            # Check for common patterns
            if "middleware" in content.lower():
                module_patterns["middleware_architecture"].append(obs)
            if "service" in content.lower() and "class" in content:
                module_patterns["service_layer"].append(obs)
            if "repository" in content.lower() or "store" in content.lower():
                module_patterns["data_layer"].append(obs)

        for pattern_name, evidence_obs in module_patterns.items():
            if len(evidence_obs) >= 2:
                patterns.append(ConsolidatedPattern(
                    pattern_id=f"arch_{pattern_name}",
                    pattern_type="architecture",
                    description=f"Uses {pattern_name.replace('_', ' ')} pattern",
                    evidence=[f"Observed in {len(evidence_obs)} recent changes"],
                    confidence=min(len(evidence_obs) / 5, 0.85)
                ))

        return patterns

    def _detect_workflow_patterns(self, observations: list[dict]) -> list[ConsolidatedPattern]:
        """Detect workflow patterns from observations.

        Args:
            observations: Observations to analyze

        Returns:
            List of workflow patterns
        """
        patterns = []

        # Track action sequences
        sequences = []
        current_sequence = []

        for obs in observations:
            action_type = obs.get("action_type", "")
            if action_type:
                current_sequence.append(action_type)
                if action_type == "result":
                    sequences.append(tuple(current_sequence))
                    current_sequence = []

        # Find common sequences
        if sequences:
            sequence_counter = Counter(sequences)
            for sequence, count in sequence_counter.most_common(3):
                if count >= 2:
                    patterns.append(ConsolidatedPattern(
                        pattern_id=f"workflow_{'_'.join(sequence)}",
                        pattern_type="workflow",
                        description=f"Common workflow: {' → '.join(sequence)}",
                        evidence=[f"Repeated {count} times"],
                        confidence=min(count / 3, 0.8)
                    ))

        # Detect test-driven patterns
        test_first_count = 0
        for i in range(len(observations) - 1):
            curr = observations[i]
            next_obs = observations[i + 1]

            if "test" in curr.get("file_path", "").lower():
                if "test" not in next_obs.get("file_path", "").lower():
                    test_first_count += 1

        if test_first_count >= 2:
            patterns.append(ConsolidatedPattern(
                pattern_id="workflow_tdd",
                pattern_type="workflow",
                description="Follows test-driven development workflow",
                evidence=[f"Wrote tests before implementation {test_first_count} times"],
                confidence=min(test_first_count / 5, 0.9)
            ))

        return patterns

    def _detect_bug_patterns(self, observations: list[dict]) -> list[ConsolidatedPattern]:
        """Detect common bug patterns from observations.

        Args:
            observations: Observations to analyze

        Returns:
            List of bug patterns
        """
        patterns = []

        # Track error patterns
        error_types = defaultdict(list)
        for obs in observations:
            content = obs.get("content", "")

            # Check for common errors
            if "ImportError" in content or "ModuleNotFoundError" in content:
                error_types["import_errors"].append(obs)
            if "TypeError" in content or "AttributeError" in content:
                error_types["type_errors"].append(obs)
            if "KeyError" in content or "IndexError" in content:
                error_types["access_errors"].append(obs)

        for error_type, occurrences in error_types.items():
            if len(occurrences) >= 2:
                patterns.append(ConsolidatedPattern(
                    pattern_id=f"bug_{error_type}",
                    pattern_type="bug",
                    description=f"Recurring issue: {error_type.replace('_', ' ')}",
                    evidence=[f"Encountered {len(occurrences)} times"],
                    confidence=min(len(occurrences) / 3, 0.7)
                ))

        return patterns

    def _merge_patterns(
        self,
        new_patterns: list[ConsolidatedPattern],
        existing_patterns: list[ConsolidatedPattern]
    ) -> list[ConsolidatedPattern]:
        """Merge new patterns with existing ones.

        Args:
            new_patterns: Newly detected patterns
            existing_patterns: Existing patterns

        Returns:
            Merged pattern list
        """
        # Create lookup by pattern_id
        existing_by_id = {p.pattern_id: p for p in existing_patterns}
        merged = []

        for new_pattern in new_patterns:
            if new_pattern.pattern_id in existing_by_id:
                # Update existing pattern
                existing = existing_by_id[new_pattern.pattern_id]
                existing.evidence.extend(new_pattern.evidence)
                existing.confidence = min(
                    (existing.confidence + new_pattern.confidence) / 2 + 0.1,
                    1.0
                )
                merged.append(existing)
                del existing_by_id[new_pattern.pattern_id]
            else:
                # Add new pattern
                merged.append(new_pattern)

        # Add remaining existing patterns
        merged.extend(existing_by_id.values())

        return merged
