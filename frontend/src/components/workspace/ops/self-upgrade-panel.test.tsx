import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SelfUpgradePanel } from "./self-upgrade-panel";
import { opsCopy } from "./types";

const refetch = vi.fn().mockResolvedValue({});

vi.mock("@/src/core/self-upgrade/hooks", () => ({
  useSelfUpgradeHealth: () => ({
    isLoading: false,
    isFetching: false,
    refetch,
    data: {
      mode: "self_upgrade_health",
      status: "watch",
      score: 0.84,
      fingerprint: "health-test",
      generated_at: "2026-05-18T06:00:00Z",
      recommendations: ["Review pending memory queue"],
      domains: [
        {
          domain_id: "memory",
          label: "Memory Platform",
          status: "watch",
          score: 0.82,
          enabled: true,
          metrics: {
            update_queue_pending: 2,
            candidate_audit_skip_count: 1,
          },
          issues: ["low_confidence"],
          recommendations: ["Inspect candidate blockers"],
        },
        {
          domain_id: "trajectory",
          label: "Trajectory Quality",
          status: "watch",
          score: 0.7,
          enabled: true,
          metrics: {
            thread_count: 2,
            quality_failed_count: 1,
            quality_filtered_count: 1,
          },
          issues: ["missing_assistant_turn"],
          recommendations: ["Inspect failed trajectory quality issues"],
        },
      ],
      backlog: [
        {
          item_id: "memory:update_queue_pending",
          domain: "memory",
          severity: "watch",
          title: "Memory update queue has pending turns",
          summary: "Queued low-signal turns are waiting for a drain boundary.",
          metric: "update_queue_pending",
          count: 2,
          recommendation: "Let scheduled maintenance drain the queue.",
          metadata: { pending: 2 },
        },
        {
          item_id: "trajectory:quality_failed",
          domain: "trajectory",
          severity: "warning",
          title: "Trajectory quality failures detected",
          summary: "One or more durable thread trajectories fail the export quality gates.",
          metric: "quality_failed_count",
          count: 1,
          recommendation: "Inspect failed trajectory quality issues before using these runs for procedure learning or evaluation.",
          metadata: { thread_ids: ["thread-bad"] },
        },
      ],
    },
  }),
}));

describe("SelfUpgradePanel", () => {
  it("renders self-upgrade domains and backlog from the typed health report", () => {
    render(<SelfUpgradePanel copy={opsCopy("en-US")} />);

    expect(screen.getByText("Self-upgrade Health")).toBeInTheDocument();
    expect(screen.getByText("Memory Platform")).toBeInTheDocument();
    expect(screen.getByText("Trajectory Quality")).toBeInTheDocument();
    expect(screen.getByText("memory:update_queue_pending")).toBeInTheDocument();
    expect(screen.getByText("trajectory:quality_failed")).toBeInTheDocument();
    expect(screen.getByText("Review pending memory queue")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));
    expect(refetch).toHaveBeenCalled();
  });
});
