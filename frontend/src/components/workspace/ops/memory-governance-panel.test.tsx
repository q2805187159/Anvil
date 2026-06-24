import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MemoryGovernancePanel } from "./memory-governance-panel";
import { opsCopy } from "./types";

const refetchOverview = vi.fn().mockResolvedValue({});
const refetchHealth = vi.fn().mockResolvedValue({});
const recallMutate = vi.fn().mockResolvedValue({});
const whyMutate = vi.fn().mockResolvedValue({});
const traceMutate = vi.fn().mockResolvedValue({});
const refetchHcmsMemories = vi.fn().mockResolvedValue({});
const deleteMemoryMutate = vi.fn().mockResolvedValue({ memory_id: "mem_deploy", status: "deleted", deleted: true, engine_notes: [] });
const governMemoryMutate = vi.fn().mockResolvedValue({ action: "archive", memory_id: "mem_deploy", status: "ok" });
let selectedMemoryLifecycleState = "active";
function defaultRecallItems(): any[] {
  return [
    {
      memory_id: "mem_deploy",
      score: 0.88,
      raw_scores: { bm25: 0.62, vector: 0.81, graph: 0.5, temporal: 0.92 },
      ranks: { bm25: 2, vector: 1, graph: 3, temporal: 1 },
      explanation: "temporal causal match",
      memory: {
        memory_id: "mem_deploy",
        version: 3,
        parent_id: "mem_deploy_v2",
        content: "Deployment failed because the canary database migration ran before schema validation.",
        summary: "Canary deployment failed after migration ordering drift.",
        category: "error_pattern",
        confidence: 0.93,
        salience: 0.82,
        state: "active",
        source_thread_id: "thread-deploy",
        source_type: "observation",
        tags: ["deploy", "canary"],
        entities: ["canary", "schema validation"],
        concepts: ["migration ordering"],
        metadata: { layer_id: "workspace", store_id: "hcms_workspace" },
        evidence: [
          {
            evidence_id: "ev_deploy",
            type: "observation",
            content: "Run log showed migration before validation.",
            weight: 0.91,
            timestamp: "2026-06-03T02:00:00Z",
            source_id: "thread-deploy",
            metadata: {},
          },
        ],
        created_at: "2026-06-03T02:00:00Z",
        updated_at: "2026-06-03T02:20:00Z",
        accessed_at: "2026-06-03T02:25:00Z",
      },
    },
  ];
}
function defaultWhyPaths(): any[] {
  return [
    {
      total_strength: 0.84,
      confidence: 0.88,
      nodes: [
        { memory_id: "mem_migration", event_type: "decision", timestamp: "2026-06-03T01:30:00Z", confidence: 0.85 },
        { memory_id: "mem_deploy", event_type: "error_pattern", timestamp: "2026-06-03T02:00:00Z", confidence: 0.93 },
      ],
      edges: [
        {
          edge_id: "edge_1",
          source_event: "mem_migration",
          target_event: "mem_deploy",
          causal_type: "direct_cause",
          strength: 0.84,
          evidence: ["ev_deploy"],
          timestamp: "2026-06-03T02:00:00Z",
          metadata: {},
        },
      ],
    },
  ];
}
let hcmsRecallItems: any[] = defaultRecallItems();
let hcmsWhyPaths: any[] = defaultWhyPaths();
let hcmsMemoryItems = [
  {
    memory_id: "mem_deploy",
    version: 3,
    parent_id: "mem_deploy_v2",
    content: "Deployment failed because the canary database migration ran before schema validation.",
    summary: "Canary deployment failed after migration ordering drift.",
    category: "error_pattern",
    confidence: 0.93,
    salience: 0.82,
    state: "active",
    source_thread_id: "thread-deploy",
    source_type: "observation",
    tags: ["deploy", "canary"],
    entities: ["canary", "schema validation"],
    concepts: ["migration ordering"],
    metadata: { layer_id: "workspace", store_id: "hcms_workspace" },
    evidence: [
      {
        evidence_id: "ev_deploy",
        type: "observation",
        content: "Run log showed migration before validation.",
        weight: 0.91,
        timestamp: "2026-06-03T02:00:00Z",
        source_id: "thread-deploy",
        metadata: {},
      },
    ],
    created_at: "2026-06-03T02:00:00Z",
    updated_at: "2026-06-03T02:20:00Z",
    accessed_at: "2026-06-03T02:25:00Z",
  },
  {
    memory_id: "mem_migration",
    version: 1,
    parent_id: null,
    content: "Schema validation was moved after the canary migration step.",
    summary: "Schema validation moved after canary migration.",
    category: "decision",
    confidence: 0.85,
    salience: 0.75,
    state: "active",
    source_thread_id: "thread-deploy",
    source_type: "observation",
    tags: ["deploy"],
    entities: ["schema validation"],
    concepts: ["migration ordering"],
    metadata: { layer_id: "workspace", store_id: "hcms_workspace" },
    evidence: [],
    created_at: "2026-06-03T01:30:00Z",
    updated_at: "2026-06-03T01:30:00Z",
    accessed_at: "2026-06-03T02:25:00Z",
  },
];

vi.mock("@/src/core/memory/hooks", () => ({
  useMemoryOverview: () => ({
    isFetching: false,
    refetch: refetchOverview,
    data: {
      active_engine_id: "hcms",
      runtime_mode: "hcms",
      store_count: 2,
      archive_turn_count: 3,
      reflection_job_count: 1,
      migration_status: {},
      stores: [],
      layers: [],
    },
  }),
  useMemoryHealth: () => ({
    isFetching: false,
    refetch: refetchHealth,
    data: {
      status: "healthy",
      quality_score: 0.91,
      archive_turn_count: 3,
      observation_queue_count: 0,
      conflict_count: 0,
      stale_count: 0,
      engine_count: 1,
      engine_health: { hcms: "healthy" },
      recommendations: [],
      generated_at: "2026-06-03T02:30:00Z",
      issues: [],
      stores: [
        {
          store_id: "hcms_workspace",
          layer_id: "workspace",
          status: "healthy",
          entry_count: 2,
          active_count: 2,
          inactive_count: 0,
          low_confidence_count: 0,
          low_salience_count: 0,
          missing_evidence_count: 0,
          duplicate_cluster_count: 0,
          conflict_count: 0,
          stale_count: 0,
          accessed_count: 1,
          hot_count: 1,
          warm_count: 1,
          cold_count: 0,
          retention_average: 0.78,
          injection_token_pressure: 0.22,
          quality_score: 0.94,
          issues: [],
        },
      ],
    },
  }),
  useHCMSRecall: () => ({
    isPending: false,
    mutateAsync: recallMutate,
    data: {
      query: "why did deployment fail",
      engine_notes: ["HCMS four-stream recall active"],
      metrics: { recall_count: 1, last_latency_ms: 24.5, recall_hit_rate: 0.9 },
      items: hcmsRecallItems,
    },
  }),
  useHCMSWhy: () => ({
    isPending: false,
    mutateAsync: whyMutate,
    data: {
      query: "why did deployment fail",
      engine_notes: ["HCMS causal reasoning active"],
      paths: hcmsWhyPaths,
    },
  }),
  useHCMSMemories: () => ({
    isFetching: false,
    refetch: refetchHcmsMemories,
    data: {
      items: hcmsMemoryItems,
      total: hcmsMemoryItems.length,
      limit: 50,
      offset: 0,
      query: null,
      state: "all",
      category: null,
      layer_id: "all",
      engine_notes: ["HCMS memory list"],
    },
  }),
  useMemoryTrace: () => ({
    isPending: false,
    mutateAsync: traceMutate,
    data: {
      items: [
        {
          trace_id: "trace_1",
          thread_id: "thread-deploy",
          query: "why did deployment fail",
          trace_kind: "hcms_recall",
          target_id: "mem_deploy",
          engine_notes: ["HCMS four-stream recall active"],
          evidence: [],
          created_at: "2026-06-03T02:31:00Z",
        },
      ],
    },
  }),
  useDeleteHCMSMemory: () => ({
    isPending: false,
    mutateAsync: deleteMemoryMutate,
    data: null,
  }),
  useGovernMemory: () => ({
    isPending: false,
    mutateAsync: governMemoryMutate,
  }),
  useHCMSMemoryHistory: () => ({
    isFetching: false,
    data: {
      memory_id: "mem_deploy",
      engine_notes: [],
      versions: [
        {
          version_id: "ver_3",
          memory_id: "mem_deploy",
          version: 3,
          parent_id: "ver_2",
          content: "Deployment failed because the canary database migration ran before schema validation.",
          summary: "Canary deployment failed after migration ordering drift.",
          diff: "@@ -1 +1 @@",
          reason: "manual_update",
          created_at: "2026-06-03T02:20:00Z",
        },
      ],
    },
  }),
  useHCMSMemory: () => ({
    isFetching: false,
    data: {
      memory: {
        memory_id: "mem_deploy",
        version: 3,
        parent_id: "mem_deploy_v2",
        content: "Deployment failed because the canary database migration ran before schema validation.",
        summary: "Canary deployment failed after migration ordering drift.",
        category: "error_pattern",
        confidence: 0.93,
        salience: 0.82,
        state: selectedMemoryLifecycleState,
        source_thread_id: "thread-deploy",
        source_type: "observation",
        tags: ["deploy", "canary"],
        entities: ["canary", "schema validation"],
        concepts: ["migration ordering"],
        metadata: { layer_id: "workspace", store_id: "hcms_workspace" },
        evidence: [
          {
            evidence_id: "ev_deploy",
            type: "observation",
            content: "Run log showed migration before validation.",
            weight: 0.91,
            timestamp: "2026-06-03T02:00:00Z",
            source_id: "thread-deploy",
            metadata: {},
          },
        ],
        created_at: "2026-06-03T02:00:00Z",
        updated_at: "2026-06-03T02:20:00Z",
        accessed_at: "2026-06-03T02:25:00Z",
      },
      engine_notes: ["HCMS memory detail active"],
    },
  }),
  useHCMSMemoryRelations: () => ({
    isFetching: false,
    data: {
      memory_id: "mem_deploy",
      engine_notes: ["HCMS relation graph active"],
      relations: [
        {
          relation_id: "rel_schema",
          source_memory_id: "mem_migration",
          target_memory_id: "mem_deploy",
          relation_type: "causes",
          weight: 0.84,
          confidence: 0.9,
          bidirectional: false,
          metadata: {},
          created_at: "2026-06-03T02:00:00Z",
          updated_at: "2026-06-03T02:10:00Z",
          source_memory: {
            memory_id: "mem_migration",
            version: 1,
            parent_id: null,
            content: "Schema validation was moved after the canary migration step.",
            summary: "Schema validation moved after canary migration.",
            category: "decision",
            confidence: 0.85,
            salience: 0.75,
            state: "active",
            source_thread_id: "thread-deploy",
            source_type: "observation",
            tags: ["deploy"],
            entities: ["schema validation"],
            concepts: ["migration ordering"],
            metadata: {},
            evidence: [],
            created_at: "2026-06-03T01:30:00Z",
            updated_at: "2026-06-03T01:30:00Z",
            accessed_at: "2026-06-03T02:25:00Z",
          },
          target_memory: null,
        },
      ],
    },
  }),
  useHCMSMemoryDiff: () => ({
    isFetching: false,
    data: {
      memory_id: "mem_deploy",
      diff: "@@ -1 +1 @@\n- old deploy note\n+ migration ordering drift",
      engine_notes: [],
    },
  }),
}));

describe("MemoryGovernancePanel", () => {
  beforeEach(() => {
    refetchOverview.mockClear();
    refetchHealth.mockClear();
    recallMutate.mockClear();
    whyMutate.mockClear();
    traceMutate.mockClear();
    refetchHcmsMemories.mockClear();
    deleteMemoryMutate.mockClear();
    governMemoryMutate.mockClear();
    selectedMemoryLifecycleState = "active";
    hcmsRecallItems = defaultRecallItems();
    hcmsWhyPaths = defaultWhyPaths();
    hcmsMemoryItems = [
      {
        memory_id: "mem_deploy",
        version: 3,
        parent_id: "mem_deploy_v2",
        content: "Deployment failed because the canary database migration ran before schema validation.",
        summary: "Canary deployment failed after migration ordering drift.",
        category: "error_pattern",
        confidence: 0.93,
        salience: 0.82,
        state: "active",
        source_thread_id: "thread-deploy",
        source_type: "observation",
        tags: ["deploy", "canary"],
        entities: ["canary", "schema validation"],
        concepts: ["migration ordering"],
        metadata: { layer_id: "workspace", store_id: "hcms_workspace" },
        evidence: [
          {
            evidence_id: "ev_deploy",
            type: "observation",
            content: "Run log showed migration before validation.",
            weight: 0.91,
            timestamp: "2026-06-03T02:00:00Z",
            source_id: "thread-deploy",
            metadata: {},
          },
        ],
        created_at: "2026-06-03T02:00:00Z",
        updated_at: "2026-06-03T02:20:00Z",
        accessed_at: "2026-06-03T02:25:00Z",
      },
      {
        memory_id: "mem_migration",
        version: 1,
        parent_id: null,
        content: "Schema validation was moved after the canary migration step.",
        summary: "Schema validation moved after canary migration.",
        category: "decision",
        confidence: 0.85,
        salience: 0.75,
        state: "active",
        source_thread_id: "thread-deploy",
        source_type: "observation",
        tags: ["deploy"],
        entities: ["schema validation"],
        concepts: ["migration ordering"],
        metadata: { layer_id: "workspace", store_id: "hcms_workspace" },
        evidence: [],
        created_at: "2026-06-03T01:30:00Z",
        updated_at: "2026-06-03T01:30:00Z",
        accessed_at: "2026-06-03T02:25:00Z",
      },
    ];
  });

  it("renders the HCMS-native management console", () => {
    render(<MemoryGovernancePanel copy={opsCopy("en-US")} />);

    expect(screen.getByText("HCMS Console")).toBeInTheDocument();
    expect(screen.getByText("hcms_workspace")).toBeInTheDocument();
    expect(screen.getByText("Memory Atlas")).toBeInTheDocument();
    expect(screen.getByText("HCMS Graph Atlas")).toBeInTheDocument();
    expect(screen.getByText("Graph focus")).toBeInTheDocument();
    expect(screen.getByText("Category constellation")).toBeInTheDocument();
    expect(screen.getByText("Selected cluster")).toBeInTheDocument();
    expect(screen.getByText("Categories")).toBeInTheDocument();
    expect(screen.getByText("Lifecycle states")).toBeInTheDocument();
    expect(screen.getByText("Graph neighborhood")).toBeInTheDocument();
    expect(screen.getByText("visible nodes")).toBeInTheDocument();
    expect(screen.getAllByText("graph links").length).toBeGreaterThan(0);
    expect(screen.getByText("avg confidence")).toBeInTheDocument();
    expect(screen.getByText("Sort")).toBeInTheDocument();
    expect(screen.getAllByText("confidence 89%").length).toBeGreaterThan(0);
    expect(screen.getAllByText("salience 78%").length).toBeGreaterThan(0);
    expect(screen.getAllByText("evidence 1").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Filter graph category error_pattern" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Select graph memory mem_migration" })).toBeInTheDocument();
    expect(screen.getByText("0 out")).toBeInTheDocument();
    expect(screen.getByText("1 in")).toBeInTheDocument();
    expect(screen.getAllByText("error_pattern").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Canary deployment failed after migration ordering drift.").length).toBeGreaterThan(0);
    expect(screen.getByText("Run log showed migration before validation.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Review$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Retention tiers$/i })).not.toBeInTheDocument();
  });

  it("uses Atlas distribution buckets and sorting controls as visual filters", () => {
    render(<MemoryGovernancePanel copy={opsCopy("en-US")} />);

    fireEvent.change(screen.getByLabelText("Sort"), { target: { value: "confidence" } });
    fireEvent.click(screen.getByRole("button", { name: "Select category error_pattern" }));
    expect(screen.getByLabelText("Category")).toHaveValue("error_pattern");
    fireEvent.click(screen.getByRole("button", { name: "Show all categories" }));
    expect(screen.getByLabelText("Category")).toHaveValue("all");
    fireEvent.click(screen.getByRole("button", { name: "Filter graph category decision" }));
    expect(screen.getByLabelText("Category")).toHaveValue("decision");
    fireEvent.click(screen.getByRole("button", { name: "Filter graph category decision" }));
    expect(screen.getByLabelText("Category")).toHaveValue("all");
    fireEvent.click(screen.getByRole("button", { name: "Select graph memory mem_migration" }));
    fireEvent.click(screen.getAllByRole("button", { name: /decision 1 confidence 85% salience 75%/i })[0]!);
    fireEvent.click(screen.getByRole("button", { name: /active 2 confidence 89% salience 78% evidence 1/i }));

    expect(screen.getByLabelText("Sort")).toHaveValue("confidence");
    expect(screen.getByLabelText("Category")).toHaveValue("decision");
    expect(screen.getByLabelText("State")).toHaveValue("active");
  });

  it("runs recall, causal reasoning, and trace refresh from the HCMS query box", async () => {
    render(<MemoryGovernancePanel copy={opsCopy("en-US")} />);

    fireEvent.change(screen.getByPlaceholderText("why did the latest memory decision happen?"), {
      target: { value: "why did deployment fail" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^HCMS Recall$/i }));

    await waitFor(() => expect(recallMutate).toHaveBeenCalledWith({ query: "why did deployment fail", limit: 10 }));
    expect(whyMutate).toHaveBeenCalledWith({ query: "why did deployment fail", limit: 4 });
    expect(traceMutate).toHaveBeenCalledWith({ targetId: "mem_deploy", limit: 12 });
  });

  it("explains empty HCMS recall results instead of only showing engine notes", () => {
    hcmsRecallItems = [];
    hcmsWhyPaths = [];
    render(<MemoryGovernancePanel copy={opsCopy("zh-CN")} />);

    expect(screen.getByText("召回查询结果")).toBeInTheDocument();
    expect(screen.getByText("没有找到匹配的 HCMS 记忆")).toBeInTheDocument();
    expect(screen.getByText("四流召回和因果推理已运行，但当前存储层没有返回可展示的记忆或路径。请先沉淀记忆、换一个更具体的实体/原因查询，或执行刷新/Flush 后重试。")).toBeInTheDocument();
  });

  it("shows causal chains, relation graph, version history, diff, and trace evidence", () => {
    render(<MemoryGovernancePanel copy={opsCopy("en-US")} />);

    fireEvent.click(screen.getByRole("button", { name: /^Causal$/i }));
    expect(screen.getByText("Causal chains")).toBeInTheDocument();
    expect(screen.getByText("direct_cause")).toBeInTheDocument();
    expect(screen.getAllByText(/mem_migration/).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: /^Relations$/i }));
    expect(screen.getByText("Relation graph")).toBeInTheDocument();
    expect(screen.getByText("causes")).toBeInTheDocument();
    expect(screen.getByText("Schema validation moved after canary migration.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^Versions$/i }));
    expect(screen.getByText("Version history")).toBeInTheDocument();
    expect(screen.getByText("Latest diff")).toBeInTheDocument();
    expect(screen.getAllByText(/migration ordering drift/).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: /^Evidence$/i }));
    expect(screen.getByText("Trace evidence")).toBeInTheDocument();
    expect(screen.getByText("hcms_recall")).toBeInTheDocument();
  });

  it("refreshes HCMS overview and health without old governance calls", async () => {
    render(<MemoryGovernancePanel copy={opsCopy("en-US")} />);

    fireEvent.click(screen.getByRole("button", { name: /^Refresh$/i }));

    await waitFor(() => expect(refetchOverview).toHaveBeenCalled());
    expect(refetchHealth).toHaveBeenCalled();
    expect(refetchHcmsMemories).toHaveBeenCalled();
    expect(traceMutate).toHaveBeenCalledWith({ targetId: "mem_deploy", limit: 12 });
  });

  it("deletes the selected HCMS memory through the lifecycle action", async () => {
    render(<MemoryGovernancePanel copy={opsCopy("en-US")} />);

    fireEvent.click(screen.getByRole("button", { name: /^Delete$/i }));

    await waitFor(() => expect(deleteMemoryMutate).toHaveBeenCalledWith("mem_deploy"));
    expect(refetchOverview).toHaveBeenCalled();
    expect(refetchHealth).toHaveBeenCalled();
  });

  it("governs the selected HCMS memory through soft lifecycle actions", async () => {
    const { unmount } = render(<MemoryGovernancePanel copy={opsCopy("en-US")} />);

    fireEvent.click(screen.getByRole("button", { name: /^Archive$/i }));
    await waitFor(() =>
      expect(governMemoryMutate).toHaveBeenCalledWith({
        memoryId: "mem_deploy",
        action: "archive",
        reason: "HCMS lifecycle archive from Memory Governance panel",
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: /^Forget$/i }));
    await waitFor(() =>
      expect(governMemoryMutate).toHaveBeenCalledWith({
        memoryId: "mem_deploy",
        action: "forget",
        reason: "HCMS lifecycle forget from Memory Governance panel",
      }),
    );

    unmount();
    selectedMemoryLifecycleState = "archived";
    render(<MemoryGovernancePanel copy={opsCopy("en-US")} />);

    fireEvent.click(screen.getByRole("button", { name: /^Restore$/i }));
    await waitFor(() =>
      expect(governMemoryMutate).toHaveBeenCalledWith({
        memoryId: "mem_deploy",
        action: "restore",
        reason: "HCMS lifecycle restore from Memory Governance panel",
      }),
    );
    expect(refetchOverview).toHaveBeenCalled();
    expect(refetchHealth).toHaveBeenCalled();
  });

  it("switches primary atlas labels with zh-CN copy", () => {
    render(<MemoryGovernancePanel copy={opsCopy("zh-CN")} />);

    expect(screen.getByText("HCMS 控制台")).toBeInTheDocument();
    expect(screen.getByText("记忆总览")).toBeInTheDocument();
    expect(screen.getByText("当前记忆")).toBeInTheDocument();
    expect(screen.getByText("生命周期操作")).toBeInTheDocument();
    expect(screen.getByText("分类分布")).toBeInTheDocument();
    expect(screen.getByText("生命周期状态")).toBeInTheDocument();
    expect(screen.getByText("证据光谱")).toBeInTheDocument();
    expect(screen.getByText("实体透镜")).toBeInTheDocument();
    expect(screen.getByText("关系邻域")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "归档" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "恢复" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "遗忘" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "删除" })).toBeInTheDocument();
    expect(screen.getByPlaceholderText("例如：为什么最近这条记忆会形成？")).toBeInTheDocument();
  });

  it("shows one atlas-level empty state instead of repeated empty placeholders", () => {
    hcmsMemoryItems = [];
    render(<MemoryGovernancePanel copy={opsCopy("zh-CN")} />);

    expect(screen.getByText("当前还没有可展示的 HCMS 记忆")).toBeInTheDocument();
    expect(screen.getByText("请先沉淀记忆、刷新列表，或切换到 Recall 查询。图谱、分类和证据面板会在有记忆后自动展开。")).toBeInTheDocument();
    expect(screen.queryAllByText("无").length).toBeLessThanOrEqual(1);
  });
});
