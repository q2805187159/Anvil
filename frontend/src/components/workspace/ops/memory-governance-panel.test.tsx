import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { opsCopy } from "./types";
import { MemoryGovernancePanel } from "./memory-governance-panel";

const approveReviewMock = vi.fn().mockResolvedValue({});
const rejectReviewMock = vi.fn().mockResolvedValue({});
const batchReviewMock = vi.fn().mockResolvedValue({ approved: ["review-1"], rejected: [] });
const resolveConflictMock = vi.fn().mockResolvedValue({});
const runBenchmarkMock = vi.fn().mockResolvedValue({});
const flushMemoryMock = vi.fn().mockResolvedValue({});
const governMemoryMock = vi.fn().mockResolvedValue({});
const batchGovernMemoryMock = vi.fn().mockResolvedValue({});
const runMaintenanceMock = vi.fn().mockResolvedValue({});
const runMaintenanceAutomationMock = vi.fn().mockResolvedValue({});
const runBenchmarkSuiteMock = vi.fn().mockResolvedValue({});
const governProfileFacetMock = vi.fn().mockResolvedValue({});
const rebuildProfileFacetsMock = vi.fn().mockResolvedValue({});

const refetch = vi.fn().mockResolvedValue({});
const hookOptions = {
  useMemoryOverview: vi.fn(),
  useMemoryHealth: vi.fn(),
  useMemoryProviders: vi.fn(),
  useMemoryAdminAudit: vi.fn(),
  useMemoryLayerEntries: vi.fn(),
  useProfileFacets: vi.fn(),
  useProfileFacetAudit: vi.fn(),
  useMemoryReview: vi.fn(),
  useMemoryConflicts: vi.fn(),
  useMemoryStaleness: vi.fn(),
  useMemoryMaintenanceAutomation: vi.fn(),
  useMemoryBenchmarkSuites: vi.fn(),
  useMemoryBenchmarkRuns: vi.fn(),
};

vi.mock("@/src/core/memory/hooks", () => ({
  useMemoryOverview: (options = {}) => {
    hookOptions.useMemoryOverview(options);
    return {
      isFetching: false,
      refetch,
      data: {
        store_count: 1,
        archive_turn_count: 7,
      },
    };
  },
  useMemoryHealth: (options = {}) => {
    hookOptions.useMemoryHealth(options);
    return {
      isFetching: false,
      refetch,
      data: {
        status: "warning",
        quality_score: 0.81,
        archive_turn_count: 7,
        pending_review_count: 1,
        conflict_count: 1,
        stale_count: 1,
        provider_count: 1,
        provider_health: { local: "ready" },
        generated_at: "2026-05-17T06:00:00Z",
        recommendations: ["Review duplicated workflow facts"],
        issues: [
          {
            issue_id: "issue-1",
            kind: "low_salience",
            severity: "warning",
            message: "Low salience memory should be reviewed",
            recommendation: "Archive or reinforce it",
            related_memory_ids: [],
          },
        ],
        stores: [
          {
            store_id: "user-profile",
            layer_id: "user",
            status: "warning",
            entry_count: 4,
            active_count: 3,
            inactive_count: 1,
            low_confidence_count: 1,
            low_salience_count: 1,
            missing_evidence_count: 0,
            duplicate_cluster_count: 1,
            conflict_count: 1,
            stale_count: 1,
            accessed_count: 2,
            hot_count: 1,
            warm_count: 1,
            cold_count: 2,
            retention_average: 0.46,
            injection_token_pressure: 0.72,
            quality_score: 0.81,
            issues: [],
          },
        ],
      },
    };
  },
  useMemoryProviders: (options = {}) => {
    hookOptions.useMemoryProviders(options);
    return { isFetching: false, refetch, data: [] };
  },
  useMemoryAdminAudit: (options = {}) => {
    hookOptions.useMemoryAdminAudit(options);
    return {
    isFetching: false,
    refetch,
    data: {
      candidate_audit: [
        {
          audit_id: "candidate-1",
          action: "skip",
          reason: "quality gate skipped candidate",
          layer_id: "workspace",
          store_id: "runtime_memory",
          category: "resolved_outcome",
          candidate_preview: "Temporary note without durable evidence.",
          quality_score: 0.31,
          quality_decision: "skip",
          blockers: ["missing_durable_outcome_signal"],
          confidence: 0.55,
          salience: 0.4,
          priority: 0.5,
          evidence_count: 1,
          evidence_refs: ["archive-1"],
          target_id: null,
          supersedes: [],
          conflicts_with: [],
          created_at: "2026-05-17T05:30:00Z",
        },
      ],
    },
    };
  },
  useMemoryLayerEntries: (layerId: string, options = {}) => {
    hookOptions.useMemoryLayerEntries(layerId, options);
    return {
    isFetching: false,
    refetch,
    data:
      layerId === "user"
        ? [
            {
              entry_id: "entry-1",
              memory_id: "mem-1",
              store_id: "user-profile",
              layer_id: "user",
              content: "User prefers concise implementation summaries.",
              category: "style",
              source_kind: "manual",
              priority: 0.7,
              confidence: 0.9,
              salience: 0.8,
              evidence_refs: ["thread-a"],
              status: "active",
              metadata: { profile_class: "style" },
              created_at: "2026-05-17T05:00:00Z",
              updated_at: "2026-05-17T05:00:00Z",
            },
          ]
        : [],
    };
  },
  useProfileFacets: (options = {}) => {
    hookOptions.useProfileFacets(options);
    return {
    isFetching: false,
    refetch,
    data: {
      policy: {
        active_threshold: 1.5,
        provisional_threshold: 0.7,
        candidate_threshold: 0.4,
        require_review_classes: ["identity", "veto"],
        class_budgets: { style: 4, tooling: 5 },
        default_class_budget: 5,
        max_facets: 80,
        pollution_requires_review: true,
      },
      items: [
        {
          facet_id: "facet-1",
          source_memory_id: "mem-1",
          entry_id: "entry-1",
          store_id: "user_profile",
          class_id: "style",
          key: "style:summary",
          value: "User prefers concise implementation summaries.",
          source_category: "preference",
          evidence_refs: ["thread-a"],
          confidence: 0.9,
          salience: 0.8,
          priority: 0.7,
          stability_score: 1.76,
          state: "active",
          user_state: "auto",
          prompt_visible: true,
          source_polluted: false,
          pollution_reasons: [],
          reason: "stability score reached active threshold",
          last_seen_at: "2026-05-17T05:00:00Z",
          created_at: "2026-05-17T05:00:00Z",
          updated_at: "2026-05-17T05:00:00Z",
        },
        {
          facet_id: "facet-2",
          source_memory_id: "mem-2",
          entry_id: "entry-2",
          store_id: "user_profile",
          class_id: "identity",
          key: "identity:teams",
          value: "User works with release engineering teams.",
          source_category: "identity",
          evidence_refs: ["thread-b"],
          confidence: 0.8,
          salience: 0.6,
          priority: 0.4,
          stability_score: 1.5,
          state: "provisional",
          user_state: "auto",
          prompt_visible: false,
          source_polluted: true,
          pollution_reasons: ["external information tool used"],
          reason: "source thread used external/web/MCP context; requires explicit review before active prompt injection",
          last_seen_at: "2026-05-17T05:00:00Z",
          created_at: "2026-05-17T05:00:00Z",
          updated_at: "2026-05-17T05:00:00Z",
        },
      ],
    },
    };
  },
  useProfileFacetAudit: (_limit = 20, options = {}) => {
    hookOptions.useProfileFacetAudit(options);
    return {
    isFetching: false,
    refetch,
    data: {
      items: [
        {
          audit_id: "facet-audit-1",
          action: "pin",
          facet_id: "facet-1",
          source_memory_id: "mem-1",
          before_state: "provisional",
          after_state: "active",
          before_user_state: "auto",
          after_user_state: "pinned",
          reason: "operator confirmed",
          source: "ops",
          created_at: "2026-05-17T05:00:00Z",
        },
      ],
    },
    };
  },
  useMemoryReview: (options = {}) => {
    hookOptions.useMemoryReview(options);
    return {
    isFetching: false,
    refetch,
    data: [
      {
        review_id: "review-1",
        layer_id: "user",
        store_id: "user-profile",
        action: "add",
        content: "User prefers durable memory drilldowns.",
        category: "workflow",
        priority: 0.6,
        confidence: 0.7,
        salience: 0.8,
        evidence_refs: ["thread-b"],
        supersedes: [],
        conflicts_with: [],
        rationale: "Repeated in two sessions",
        status: "pending",
        created_at: "2026-05-17T05:10:00Z",
        updated_at: "2026-05-17T05:10:00Z",
      },
    ],
    };
  },
  useMemoryConflicts: (options = {}) => {
    hookOptions.useMemoryConflicts(options);
    return {
    isFetching: false,
    refetch,
    data: [
      {
        conflict_id: "conflict-1",
        memory_id: "mem-left",
        conflicting_memory_id: "mem-right",
        reason: "conflicting memory content detected",
        created_at: "2026-05-17T05:20:00Z",
        resolved: false,
        recommended_action: "review",
        memory_content: "Use MiniMax for testing.",
        conflicting_content: "Use OpenAI for testing.",
      },
    ],
    };
  },
  useMemoryStaleness: (options = {}) => {
    hookOptions.useMemoryStaleness(options);
    return {
    isFetching: false,
    refetch,
    data: [
      {
        memory_id: "mem-cold",
        layer_id: "workspace",
        stale_score: 0.82,
        reason: "Not accessed recently",
        last_accessed_at: "2026-04-01T00:00:00Z",
        expires_at: null,
        retention_score: 0.18,
        tier: "cold",
        access_count: 0,
        reinforcement_boost: 0.05,
        temporal_decay: 0.73,
        salience: 0.2,
      },
    ],
    };
  },
  useFlushMemory: () => ({ isPending: false, mutateAsync: flushMemoryMock }),
  useBatchGovernMemory: () => ({
    isPending: false,
    mutateAsync: batchGovernMemoryMock,
    data: {
      policy: "balanced",
      layer_id: null,
      dry_run: true,
      candidate_count: 1,
      executed_count: 0,
      skipped_count: 1,
      errors: [],
      results: [],
      items: [
        {
          memory_id: "mem-cold",
          store_id: "runtime_memory",
          entry_id: "entry-cold",
          layer_id: "workspace",
          action: "review",
          reason: "balanced policy queued stale memory for review",
          tier: "cold",
          stale_score: 0.82,
          retention_score: 0.18,
          salience: 0.2,
          access_count: 0,
          last_accessed_at: "2026-04-01T00:00:00Z",
          expires_at: null,
        },
      ],
    },
  }),
  useRunMemoryMaintenance: () => ({
    isPending: false,
    mutateAsync: runMaintenanceMock,
    data: {
      run_id: "maintenance-1",
      status: "noop",
      dry_run: true,
      policy: "balanced",
      layer_id: null,
      source: "ops",
      update_queue_pending: 2,
      update_queue_drained: 2,
      reflection_jobs_due: 1,
      reflection_jobs_run: 1,
      reflection_entries_written: 1,
      governance: {
        policy: "balanced",
        layer_id: null,
        dry_run: true,
        candidate_count: 1,
        executed_count: 0,
        skipped_count: 1,
        errors: [],
        results: [],
        items: [],
      },
      health_before: { quality_score: 0.7 },
      health_after: { quality_score: 0.75 },
      actions_executed: {},
      skipped_actions: { review: 1 },
      errors: [],
      started_at: "2026-05-17T05:40:00Z",
      finished_at: "2026-05-17T05:40:01Z",
    },
  }),
  useMemoryMaintenanceAutomation: (options = {}) => {
    hookOptions.useMemoryMaintenanceAutomation(options);
    return {
    isFetching: false,
    refetch,
    data: {
      enabled: true,
      last_run_at: "2026-05-17T05:45:00Z",
      last_status: "completed",
      last_reason: "due",
      last_run_id: "automation-1",
      last_counts: {
        update_queue_drained: 2,
        reflection_jobs_run: 1,
        governance_executed: 1,
      },
      last_error_count: 0,
      last_errors: [],
      next_run_at: "2026-05-17T11:45:00Z",
      tick_seconds: 300,
      interval_seconds: 21600,
      min_idle_seconds: 0,
      dry_run: true,
      execute: false,
      policy: "balanced",
      layer_id: null,
      limit: 12,
      run_reflection_due_jobs: true,
    },
    };
  },
  useRunMemoryMaintenanceAutomation: () => ({
    isPending: false,
    mutateAsync: runMaintenanceAutomationMock,
    data: {
      ran: false,
      reason: "not_due",
      next_run_at: "2026-05-17T11:45:00Z",
      report: null,
    },
  }),
  useMemoryBenchmarkSuites: (options = {}) => {
    hookOptions.useMemoryBenchmarkSuites(options);
    return {
    isFetching: false,
    refetch,
    data: [
      {
        suite_id: "northstar-regression",
        name: "Northstar Regression",
        description: "Persistent recall regression suite.",
        cases: [
          {
            case_id: "northstar-canary",
            query: "Northstar canary pytest",
            thread_id: "ops-benchmark",
            expected_terms: ["canary deployment"],
            expected_memory_ids: ["mem-1"],
            expected_archive_thread_ids: [],
            forbidden_terms: [],
            forbidden_memory_ids: [],
            min_score: 0.6,
          },
        ],
        tags: ["memory"],
        enabled: true,
        source: "ops",
        created_at: "2026-05-17T05:00:00Z",
        updated_at: "2026-05-17T05:50:00Z",
        latest_run_id: "run-1",
        latest_score: 0.88,
        latest_passed: true,
        latest_run_at: "2026-05-17T05:50:00Z",
      },
    ],
    };
  },
  useMemoryBenchmarkRuns: (_suiteId = null, options = {}) => {
    hookOptions.useMemoryBenchmarkRuns(options);
    return {
    isFetching: false,
    refetch,
    data: [
      {
        run_id: "run-1",
        suite_id: "northstar-regression",
        suite_name: "Northstar Regression",
        source: "ops",
        created_at: "2026-05-17T05:50:00Z",
        report: {
          suite_id: "northstar-regression",
          passed: true,
          score: 0.88,
          case_count: 1,
          passed_count: 1,
          failed_count: 0,
          recall_hit_rate: 1,
          false_positive_rate: 0,
          average_evidence_count: 2,
          generated_at: "2026-05-17T05:50:00Z",
          recommendations: [],
          cases: [],
        },
      },
    ],
    };
  },
  useRunMemoryBenchmarkSuite: () => ({
    isPending: false,
    mutateAsync: runBenchmarkSuiteMock,
  }),
  useRunMemoryBenchmark: () => ({
    isPending: false,
    mutateAsync: runBenchmarkMock,
    data: {
      suite_id: "ops-memory-smoke",
      passed: false,
      score: 0.5,
      case_count: 1,
      passed_count: 0,
      failed_count: 1,
      recall_hit_rate: 0.5,
      false_positive_rate: 0.25,
      average_evidence_count: 2,
      generated_at: "2026-05-17T05:30:00Z",
      recommendations: ["Add stronger evidence"],
      cases: [
        {
          case_id: "case-1",
          query: "concise summaries",
          passed: false,
          score: 0.42,
          recall_hits: 1,
          expected_count: 2,
          false_positive_count: 1,
          evidence_count: 2,
          missing_expectations: ["implementation summaries"],
          false_positives: ["weather preference"],
          summary: "Expected memory was weakly recalled.",
          top_evidence: [
            {
              evidence_id: "ev-1",
              source_kind: "curated",
              source_id: "mem-1",
              score: 0.42,
              final_score: 0.42,
              reason: "term overlap",
              excerpt: "User prefers concise implementation summaries.",
            },
          ],
        },
      ],
    },
  }),
  useApproveMemoryReview: () => ({ isPending: false, mutateAsync: approveReviewMock }),
  useRejectMemoryReview: () => ({ isPending: false, mutateAsync: rejectReviewMock }),
  useBatchMemoryReview: () => ({ isPending: false, mutateAsync: batchReviewMock }),
  useGovernMemory: () => ({ isPending: false, mutateAsync: governMemoryMock }),
  useGovernProfileFacet: () => ({ isPending: false, mutateAsync: governProfileFacetMock }),
  useRebuildProfileFacets: () => ({ isPending: false, mutateAsync: rebuildProfileFacetsMock }),
  useResolveMemoryConflict: () => ({ isPending: false, mutateAsync: resolveConflictMock }),
}));

describe("MemoryGovernancePanel", () => {
  beforeEach(() => {
    approveReviewMock.mockClear();
    rejectReviewMock.mockClear();
    batchReviewMock.mockClear();
    resolveConflictMock.mockClear();
    runBenchmarkMock.mockClear();
    flushMemoryMock.mockClear();
    governMemoryMock.mockClear();
    batchGovernMemoryMock.mockClear();
    runMaintenanceMock.mockClear();
    runMaintenanceAutomationMock.mockClear();
    governProfileFacetMock.mockClear();
    rebuildProfileFacetsMock.mockClear();
    refetch.mockClear();
    Object.values(hookOptions).forEach((mock) => mock.mockClear());
  });

  it("renders retention, review, conflict, and benchmark drilldowns", () => {
    render(<MemoryGovernancePanel copy={opsCopy("en-US")} />);

    expect(screen.getAllByText("Hot: 1").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Cold: 2").length).toBeGreaterThan(0);
    expect(screen.queryByText("User prefers durable memory drilldowns.")).not.toBeInTheDocument();
    expect(screen.queryByText("Use MiniMax for testing.")).not.toBeInTheDocument();
    expect(screen.queryByText("mem-cold")).not.toBeInTheDocument();
    expect(screen.queryByText("Expected memory was weakly recalled.")).not.toBeInTheDocument();
    expect(screen.queryByText("Temporary note without durable evidence.")).not.toBeInTheDocument();
    expect(screen.queryByText("Profile policy")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^Candidate audit$/i }));
    expect(screen.getByText("Temporary note without durable evidence.")).toBeInTheDocument();
    expect(screen.getByText(/missing_durable_outcome_signal/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^Profile facets$/i }));
    expect(screen.getByText("Profile policy")).toBeInTheDocument();
    expect(screen.getByText("identity:teams")).toBeInTheDocument();
    expect(screen.getByText("Facet audit")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^Review$/i }));
    expect(screen.getByText("User prefers durable memory drilldowns.")).toBeInTheDocument();
    expect(screen.getByText("Use MiniMax for testing.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^Retention tiers$/i }));
    expect(screen.getAllByText("mem-cold").length).toBeGreaterThan(0);
    expect(screen.getByText("maintenance-1")).toBeInTheDocument();
    expect(screen.getByText("Background automation")).toBeInTheDocument();
    expect(screen.getByText("automation-1")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^Recall benchmark$/i }));
    expect(screen.getByText("Expected memory was weakly recalled.")).toBeInTheDocument();
    expect(screen.getByText("implementation summaries")).toBeInTheDocument();
    expect(screen.getAllByText("User prefers concise implementation summaries.").length).toBeGreaterThan(0);
  });

  it("enables only the visible memory governance section queries", () => {
    render(<MemoryGovernancePanel copy={opsCopy("en-US")} />);

    expect(lastEnabled(hookOptions.useMemoryOverview)).toBe(true);
    expect(lastEnabled(hookOptions.useMemoryHealth)).toBe(true);
    expect(lastEnabled(hookOptions.useMemoryProviders)).toBe(false);
    expect(lastEnabled(hookOptions.useMemoryAdminAudit)).toBe(false);
    expect(lastEnabled(hookOptions.useProfileFacets)).toBe(false);
    expect(lastEnabled(hookOptions.useProfileFacetAudit)).toBe(false);
    expect(lastEnabled(hookOptions.useMemoryReview)).toBe(false);
    expect(lastEnabled(hookOptions.useMemoryConflicts)).toBe(false);
    expect(lastEnabled(hookOptions.useMemoryStaleness)).toBe(false);
    expect(lastEnabled(hookOptions.useMemoryBenchmarkSuites)).toBe(false);
    expect(lastEnabled(hookOptions.useMemoryBenchmarkRuns)).toBe(false);
    expect(lastEnabled(hookOptions.useMemoryMaintenanceAutomation)).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: /^Providers$/i }));
    expect(lastEnabled(hookOptions.useMemoryHealth)).toBe(false);
    expect(lastEnabled(hookOptions.useMemoryProviders)).toBe(true);
    expect(lastEnabled(hookOptions.useMemoryAdminAudit)).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: /^Candidate audit$/i }));
    expect(lastEnabled(hookOptions.useMemoryHealth)).toBe(false);
    expect(lastEnabled(hookOptions.useMemoryProviders)).toBe(false);
    expect(lastEnabled(hookOptions.useMemoryAdminAudit)).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: /^Review$/i }));
    expect(lastEnabled(hookOptions.useMemoryOverview)).toBe(false);
    expect(lastEnabled(hookOptions.useMemoryReview)).toBe(true);
    expect(lastEnabled(hookOptions.useMemoryConflicts)).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: /^Recall benchmark$/i }));
    expect(lastEnabled(hookOptions.useMemoryReview)).toBe(false);
    expect(lastEnabled(hookOptions.useMemoryBenchmarkSuites)).toBe(true);
    expect(lastEnabled(hookOptions.useMemoryBenchmarkRuns)).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: /^Retention tiers$/i }));
    expect(lastEnabled(hookOptions.useMemoryBenchmarkSuites)).toBe(false);
    expect(lastEnabled(hookOptions.useMemoryStaleness)).toBe(true);
    expect(lastEnabled(hookOptions.useMemoryMaintenanceAutomation)).toBe(true);
  });

  it("wires review and conflict actions to memory governance hooks", async () => {
    render(<MemoryGovernancePanel copy={opsCopy("en-US")} />);

    fireEvent.click(screen.getByRole("button", { name: /^Review$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^Approve$/i }));
    await waitFor(() => expect(approveReviewMock).toHaveBeenCalledWith("review-1"));

    fireEvent.click(screen.getByRole("button", { name: /^Reject$/i }));
    await waitFor(() => expect(rejectReviewMock).toHaveBeenCalledWith("review-1"));

    fireEvent.click(screen.getByRole("button", { name: /^Approve all$/i }));
    await waitFor(() => expect(batchReviewMock).toHaveBeenCalledWith({ approve: ["review-1"], reject: [] }));

    fireEvent.click(screen.getByRole("button", { name: /^Keep left$/i }));
    await waitFor(() => expect(resolveConflictMock).toHaveBeenCalledWith({ conflictId: "conflict-1", action: "keep_memory" }));

    fireEvent.click(screen.getByRole("button", { name: /^Retention tiers$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^Reinforce$/i }));
    await waitFor(() => expect(governMemoryMock).toHaveBeenCalledWith({ memoryId: "mem-cold", action: "reinforce" }));

    fireEvent.click(screen.getByRole("button", { name: /^Archive$/i }));
    await waitFor(() => expect(governMemoryMock).toHaveBeenCalledWith({ memoryId: "mem-cold", action: "archive" }));

    fireEvent.click(screen.getByRole("button", { name: /^Plan$/i }));
    await waitFor(() => expect(batchGovernMemoryMock).toHaveBeenCalledWith(expect.objectContaining({ dry_run: true, policy: "balanced" })));

    fireEvent.click(screen.getByRole("button", { name: /^Plan maintenance$/i }));
    await waitFor(() => expect(runMaintenanceMock).toHaveBeenCalledWith(expect.objectContaining({ dry_run: true, policy: "balanced" })));

    fireEvent.click(screen.getByRole("button", { name: /^Run due check$/i }));
    await waitFor(() => expect(runMaintenanceAutomationMock).toHaveBeenCalledWith({ force_run: true }));

    fireEvent.click(screen.getByRole("button", { name: /^Recall benchmark$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^Run suite$/i }));
    await waitFor(() => expect(runBenchmarkSuiteMock).toHaveBeenCalledWith({ suiteId: "northstar-regression", evidenceLimit: 4 }));

    fireEvent.click(screen.getByRole("button", { name: /^Profile facets$/i }));
    fireEvent.click(screen.getAllByRole("button", { name: /^Pin$/i })[0]);
    await waitFor(() => expect(governProfileFacetMock).toHaveBeenCalledWith({ facetId: "facet-1", action: "pin", reason: "ops profile facet pin" }));

    fireEvent.click(screen.getByRole("button", { name: /^Rebuild facets$/i }));
    await waitFor(() => expect(rebuildProfileFacetsMock).toHaveBeenCalled());
  });

  it("runs the recall benchmark from generated memory cases", async () => {
    render(<MemoryGovernancePanel copy={opsCopy("en-US")} />);

    fireEvent.click(screen.getByRole("button", { name: /^Recall benchmark$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^Run benchmark$/i }));

    await waitFor(() =>
      expect(runBenchmarkMock).toHaveBeenCalledWith({
        suite_id: "ops-memory-smoke",
        cases: [
          expect.objectContaining({
            query: "User prefers concise implementation summaries",
            expected_memory_ids: ["mem-1"],
          }),
        ],
        evidence_limit: 4,
      }),
    );
  });
});

function lastEnabled(mock: ReturnType<typeof vi.fn>): boolean | undefined {
  const call = mock.mock.calls.at(-1);
  const options = call?.at(-1);
  if (!options || typeof options !== "object" || !("enabled" in options)) {
    return true;
  }
  return Boolean((options as { enabled?: boolean }).enabled);
}
