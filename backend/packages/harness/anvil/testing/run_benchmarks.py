"""Benchmark Test Runner

Executes all benchmark tests and generates comprehensive reports.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from anvil.testing.benchmark_framework import BenchmarkSuite, TestCategory
from anvil.testing.benchmark_reports import BenchmarkReportGenerator
from anvil.testing.tests.context_management_tests import (
    LargeContextHandlingTest,
    CompactionTriggerTest,
    FactPreservationTest,
)


def create_benchmark_suite() -> BenchmarkSuite:
    """Create comprehensive benchmark test suite."""
    suite = BenchmarkSuite(
        name="Anvil Comprehensive Benchmark",
        description="Complete validation of all optimization improvements",
    )

    # Context Management Tests (3 implemented, 7 more needed)
    suite.add_test(LargeContextHandlingTest())
    suite.add_test(CompactionTriggerTest())
    suite.add_test(FactPreservationTest())

    # TODO: Add remaining 77 tests across all categories
    # - Context Management: 7 more tests
    # - Memory Evolution: 10 tests
    # - Caching: 10 tests
    # - Learning: 10 tests
    # - Prompt Optimization: 10 tests
    # - Token Optimization: 10 tests
    # - Integration: 10 tests
    # - Real-World: 20 tests

    return suite


def run_benchmarks(output_dir: str = "benchmark_reports") -> dict:
    """Run all benchmark tests and generate reports."""
    print("=" * 80)
    print("Anvil Benchmark Testing")
    print("=" * 80)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # Create test suite
    print("Creating test suite...")
    suite = create_benchmark_suite()
    print(f"Total tests: {len(suite.tests)}")
    print()

    # Run all tests
    print("Running tests...")
    print("-" * 80)
    results = suite.run_all(verbose=True)
    print("-" * 80)
    print()

    # Generate reports
    print("Generating reports...")
    report_gen = BenchmarkReportGenerator(output_dir)
    saved_files = report_gen.save_all_reports(suite)
    print(f"Reports saved to: {output_dir}/")
    print()

    # Get summary
    summary = suite.get_summary()

    # Print summary
    print("=" * 80)
    print("BENCHMARK SUMMARY")
    print("=" * 80)
    print(f"Total Tests: {summary['total_tests']}")
    print(f"Passed: {summary['passed']} ({summary['pass_rate']:.1f}%)")
    print(f"Failed: {summary['failed']}")
    print(f"Partial: {summary['partial']}")
    print(f"Average Quality: {summary['average_quality']:.1f}/10")
    print()
    print(f"Token Savings: {summary['metrics']['tokens']['savings_pct']:.1f}%")
    print(f"Cache Hit Rate: {summary['metrics']['performance']['cache_hit_rate']:.1f}%")
    print(f"Avg Overhead: {summary['metrics']['performance']['avg_overhead_ms']:.1f}ms")
    print()

    # Category breakdown
    print("Results by Category:")
    for category, stats in summary['by_category'].items():
        print(f"  {category.replace('_', ' ').title()}: {stats['passed']}/{stats['total']} ({stats['pass_rate']:.1f}%) - Avg Quality: {stats['avg_quality']:.1f}/10")
    print()

    print("=" * 80)
    print(f"Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    return summary


def main():
    """Main entry point."""
    # Run benchmarks
    summary = run_benchmarks()

    # Save summary as JSON
    output_path = Path("benchmark_reports") / "summary.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(summary, f, indent=2, default=str)

    print(f"\nSummary JSON saved to: {output_path}")

    # Exit with appropriate code
    if summary['pass_rate'] >= 90:
        print("\n✅ SUCCESS: 90%+ pass rate achieved!")
        sys.exit(0)
    elif summary['pass_rate'] >= 70:
        print(f"\n⚠️  PARTIAL: {summary['pass_rate']:.1f}% pass rate (target: 90%+)")
        sys.exit(1)
    else:
        print(f"\n❌ FAILURE: {summary['pass_rate']:.1f}% pass rate (target: 90%+)")
        sys.exit(2)


if __name__ == "__main__":
    main()
