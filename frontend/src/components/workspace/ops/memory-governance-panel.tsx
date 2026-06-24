"use client";

import React from "react";
import {
  ActivityIcon,
  AlertTriangleIcon,
  ArchiveIcon,
  BrainCircuitIcon,
  DatabaseIcon,
  EyeOffIcon,
  HistoryIcon,
  Link2Icon,
  NetworkIcon,
  RefreshCwIcon,
  RotateCcwIcon,
  SearchIcon,
  ShieldCheckIcon,
  SparklesIcon,
  Trash2Icon,
} from "lucide-react";

import type {
  HCMSCausalEdgeView,
  HCMSCausalNodeView,
  HCMSEvidenceView,
  HCMSMemoryView,
  HCMSRecallItemView,
  HCMSRelationView,
  HCMSWhyPathView,
  MemoryStoreHealthView,
  MemoryTraceView,
} from "@/src/core/contracts";
import {
  useDeleteHCMSMemory,
  useHCMSMemory,
  useHCMSMemoryDiff,
  useHCMSMemoryHistory,
  useHCMSMemoryRelations,
  useHCMSMemories,
  useHCMSRecall,
  useHCMSWhy,
  useMemoryHealth,
  useMemoryOverview,
  useMemoryTrace,
  useGovernMemory,
} from "@/src/core/memory/hooks";
import { Badge, Button, ScrollArea } from "@/src/components/ui";
import { cn } from "@/src/lib/utils";

import { OpsEmptyState, OpsJsonBlock, OpsPanelCard } from "./shared";
import type { OpsCopy } from "./types";

type MemoryGovernancePanelProps = {
  copy: OpsCopy;
};

type HCMSSection = "atlas" | "recall" | "causal" | "relations" | "versions" | "evidence";
type HCMSLifecycleAction = "archive" | "forget" | "restore";
type HCMSAtlasSort = "updated" | "confidence" | "salience" | "evidence";
type DistributionBucket = {
  label: string;
  count: number;
  confidence: number;
  salience: number;
  evidenceCount: number;
};
type EntityBucket = {
  label: string;
  count: number;
  categories: number;
  evidenceCount: number;
  confidence: number;
  memoryIds: string[];
};
type SpectrumBucket = {
  label: string;
  count: number;
  evidenceCount: number;
  confidence: number;
  salience: number;
};
type AtlasNeighbor = {
  relation: HCMSRelationView;
  memory: HCMSMemoryView | null | undefined;
  memoryId: string;
  outbound: boolean;
};
type AtlasGraphMemory = {
  memoryId: string;
  summary: string;
  category: string;
  confidence: number | null | undefined;
  salience: number | null | undefined;
  state: string | null | undefined;
  evidenceCount: number;
};
type AtlasGraphNode = AtlasGraphMemory & {
  x: number;
  y: number;
  size: number;
  degree: number;
  categoryIndex: number;
};
type AtlasGraphCategory = {
  label: string;
  centerX: number;
  centerY: number;
  count: number;
  confidence: number;
  salience: number;
};
type HCMSPanelText = {
  runtime: string;
  memories: string;
  recallHits: string;
  causalPaths: string;
  engines: string;
  issues: string;
  stores: string;
  filter: string;
  state: string;
  category: string;
  sort: string;
  memoryAtlas: string;
  selectedMemory: string;
  lifecycle: string;
  archive: string;
  restore: string;
  forget: string;
  delete: string;
  rawScores: string;
  queryPlaceholder: string;
  queryResultTitle: string;
  recallEmptyTitle: string;
  recallEmptyBody: string;
  recallResultBody: string;
  engineNotesLabel: string;
  atlasEmptyTitle: string;
  atlasEmptyBody: string;
  recallAction: string;
  atlasSection: string;
  recallSection: string;
  causalSection: string;
  relationsSection: string;
  versionsSection: string;
  evidenceSection: string;
  visibleNodes: string;
  categories: string;
  graphLinks: string;
  avgConfidence: string;
  activeCount: string;
  filteredSet: string;
  selectedNode: string;
  attachedRecords: string;
  evidenceLabel: string;
  atlasFilterPlaceholder: string;
  fourStreamRecall: string;
  causalChains: string;
  relationGraph: string;
  versionHistory: string;
  traceEvidence: string;
  latestDiff: string;
  graphAtlas: string;
  graphAtlasHint: string;
  graphFocus: string;
  categoryConstellation: string;
  categoryConstellationHint: string;
  selectedCluster: string;
  selectedClusterHint: string;
  allMemory: string;
  nodesLabel: string;
  edgesLabel: string;
  linksLabel: string;
  evidenceSpectrum: string;
  evidenceSpectrumHint: string;
  bandsLabel: string;
  entityLens: string;
  entityLensHint: string;
  entitiesLabel: string;
  graphNeighborhood: string;
  density: string;
  direction: string;
  relationTypes: string;
  out: string;
  in: string;
  top: string;
  quality: string;
  retention: string;
  entries: string;
  evidenceGaps: string;
  stale: string;
  updated: string;
  selectedNodeFallback: string;
  categoryAll: string;
  memoryFallback: string;
  confidenceLabel: string;
  salienceLabel: string;
  scoreLabel: string;
  strengthLabel: string;
  weightLabel: string;
  bm25Label: string;
  vectorLabel: string;
  graphLabel: string;
  temporalLabel: string;
  outboundLabel: string;
  inboundLabel: string;
  linkedLabel: string;
  relationVersionLabel: string;
  unknownLabel: string;
  activeLabel: string;
  notAvailableLabel: string;
  categoryDistribution: string;
  lifecycleDistribution: string;
  showAllCategoriesAria: string;
  selectCategoryAria: string;
  categoryTitleSuffix: string;
  graphCategoryAria: string;
  graphMemoryAria: string;
  evidenceSpectrumAria: string;
  entityMemoryAria: string;
  relatedMemoryAria: string;
  memoryCountLabel: string;
  stateLabels: Record<string, string>;
  sortLabels: Record<HCMSAtlasSort, string>;
  shownLabel: string;
};

const HCMS_SECTIONS: HCMSSection[] = ["atlas", "recall", "causal", "relations", "versions", "evidence"];
const HCMS_MEMORY_STATES = ["all", "active", "archived", "forgotten"];
const HCMS_ATLAS_SORTS: HCMSAtlasSort[] = ["updated", "confidence", "salience", "evidence"];
const ATLAS_GRAPH_CENTERS = [
  { x: 20, y: 30 },
  { x: 50, y: 20 },
  { x: 78, y: 34 },
  { x: 30, y: 70 },
  { x: 63, y: 72 },
  { x: 86, y: 66 },
  { x: 14, y: 76 },
];

function isChineseCopy(copy: OpsCopy) {
  return /[\u4e00-\u9fff]/.test(copy.memory.title) || /[\u4e00-\u9fff]/.test(copy.memory.description);
}

function panelText(copy: OpsCopy): HCMSPanelText {
  if (isChineseCopy(copy)) {
    return {
      runtime: "运行模式",
      memories: "记忆数",
      recallHits: "召回结果",
      causalPaths: "因果路径",
      engines: "引擎",
      issues: "问题",
      stores: "存储层",
      filter: "筛选",
      state: "状态",
      category: "分类",
      sort: "排序",
      memoryAtlas: "记忆总览",
      selectedMemory: "当前记忆",
      lifecycle: "生命周期操作",
      archive: "归档",
      restore: "恢复",
      forget: "遗忘",
      delete: "删除",
      rawScores: "原始分数",
      queryPlaceholder: "例如：为什么最近这条记忆会形成？",
      queryResultTitle: "召回查询结果",
      recallEmptyTitle: "没有找到匹配的 HCMS 记忆",
      recallEmptyBody: "四流召回和因果推理已运行，但当前存储层没有返回可展示的记忆或路径。请先沉淀记忆、换一个更具体的实体/原因查询，或执行刷新/Flush 后重试。",
      recallResultBody: "四流召回返回了可展示的记忆。点击结果可查看证据、版本和关系邻域。",
      engineNotesLabel: "运行状态",
      atlasEmptyTitle: "当前还没有可展示的 HCMS 记忆",
      atlasEmptyBody: "请先沉淀记忆、刷新列表，或切换到 Recall 查询。图谱、分类和证据面板会在有记忆后自动展开。",
      recallAction: "HCMS 召回",
      atlasSection: "总览",
      recallSection: "召回",
      causalSection: "因果",
      relationsSection: "关系",
      versionsSection: "版本",
      evidenceSection: "证据",
      visibleNodes: "可见节点",
      categories: "分类数",
      graphLinks: "图谱连线",
      avgConfidence: "平均置信度",
      activeCount: "活跃",
      filteredSet: "筛选结果",
      selectedNode: "当前节点",
      attachedRecords: "关联证据",
      evidenceLabel: "证据",
      atlasFilterPlaceholder: "实体、分类、证据",
      fourStreamRecall: "四流召回",
      causalChains: "因果链",
      relationGraph: "关系图谱",
      versionHistory: "版本历史",
      traceEvidence: "追踪证据",
      latestDiff: "最新 diff",
      graphAtlas: "HCMS 图谱总览",
      graphAtlasHint: "按分类聚类，并用关系强度组织记忆节点",
      graphFocus: "图谱焦点",
      categoryConstellation: "分类星图",
      categoryConstellationHint: "按节点数、置信度、价值和证据强度综合加权",
      selectedCluster: "当前分类簇",
      selectedClusterHint: "点击分类簇即可直接驱动 Atlas 分类筛选。",
      allMemory: "全部记忆",
      nodesLabel: "节点",
      edgesLabel: "边",
      linksLabel: "连线",
      evidenceSpectrum: "证据光谱",
      evidenceSpectrumHint: "按分类观察证据密度，并叠加记忆质量",
      bandsLabel: "频带",
      entityLens: "实体透镜",
      entityLensHint: "查看跨记忆共享的实体、人、系统和工件",
      entitiesLabel: "实体",
      graphNeighborhood: "关系邻域",
      density: "密度",
      direction: "方向",
      relationTypes: "关系类型",
      out: "出",
      in: "入",
      top: "最高",
      quality: "质量",
      retention: "保留度",
      entries: "条目",
      evidenceGaps: "证据缺口",
      stale: "陈旧",
      updated: "更新时间",
      selectedNodeFallback: "未选中",
      categoryAll: "全部",
      memoryFallback: "记忆",
      confidenceLabel: "置信度",
      salienceLabel: "价值",
      scoreLabel: "分数",
      strengthLabel: "强度",
      weightLabel: "权重",
      bm25Label: "词法",
      vectorLabel: "向量",
      graphLabel: "图谱",
      temporalLabel: "时序",
      outboundLabel: "出向",
      inboundLabel: "入向",
      linkedLabel: "关联",
      relationVersionLabel: "版本",
      unknownLabel: "未知",
      activeLabel: "活跃",
      notAvailableLabel: "暂无",
      categoryDistribution: "分类分布",
      lifecycleDistribution: "生命周期状态",
      showAllCategoriesAria: "显示全部分类",
      selectCategoryAria: "选择分类",
      categoryTitleSuffix: "条记忆",
      graphCategoryAria: "筛选图谱分类",
      graphMemoryAria: "选择图谱记忆",
      evidenceSpectrumAria: "选择证据光谱",
      entityMemoryAria: "跳转到实体记忆",
      relatedMemoryAria: "选择关联记忆",
      memoryCountLabel: "记忆",
      stateLabels: {
        all: "全部",
        active: "活跃",
        archived: "已归档",
        forgotten: "已遗忘",
        healthy: "健康",
        ok: "正常",
        ready: "就绪",
        warning: "警告",
        review: "待复核",
        failed: "失败",
        error: "错误",
      },
      sortLabels: {
        updated: "更新时间",
        confidence: "置信度",
        salience: "价值",
        evidence: "证据数",
      },
      shownLabel: "已显示",
    };
  }
  return {
    runtime: "runtime",
    memories: "memories",
    recallHits: "recall hits",
    causalPaths: "causal paths",
    engines: "engines",
    issues: "issues",
    stores: "stores",
    filter: "Filter",
    state: "State",
    category: "Category",
    sort: "Sort",
    memoryAtlas: "Memory Atlas",
    selectedMemory: "Selected memory",
    lifecycle: "Lifecycle actions",
    archive: "Archive",
    restore: "Restore",
    forget: "Forget",
    delete: "Delete",
    rawScores: "Raw scores",
    queryPlaceholder: "why did the latest memory decision happen?",
    queryResultTitle: "Recall query result",
    recallEmptyTitle: "No matching HCMS memory found",
    recallEmptyBody: "Four-stream recall and causal reasoning ran, but the current stores returned no displayable memories or paths. Capture memory first, try a more specific entity or why query, or refresh/flush and retry.",
    recallResultBody: "Four-stream recall returned displayable memories. Select a result to inspect evidence, versions, and relation neighborhood.",
    engineNotesLabel: "Engine state",
    atlasEmptyTitle: "No HCMS memories are available yet",
    atlasEmptyBody: "Flush or capture memory first, refresh the list, or run recall. Graph, category, and evidence views will appear once memories exist.",
    recallAction: "HCMS Recall",
    atlasSection: "Atlas",
    recallSection: "Recall",
    causalSection: "Causal",
    relationsSection: "Relations",
    versionsSection: "Versions",
    evidenceSection: "Evidence",
    visibleNodes: "visible nodes",
    categories: "categories",
    graphLinks: "graph links",
    avgConfidence: "avg confidence",
    activeCount: "active",
    filteredSet: "filtered set",
    selectedNode: "selected node",
    attachedRecords: "attached records",
    evidenceLabel: "evidence",
    atlasFilterPlaceholder: "entity, category, evidence",
    fourStreamRecall: "Four-stream recall",
    causalChains: "Causal chains",
    relationGraph: "Relation graph",
    versionHistory: "Version history",
    traceEvidence: "Trace evidence",
    latestDiff: "Latest diff",
    graphAtlas: "HCMS Graph Atlas",
    graphAtlasHint: "Clustered by category with relation-weighted memory nodes",
    graphFocus: "Graph focus",
    categoryConstellation: "Category constellation",
    categoryConstellationHint: "Weighted by nodes, confidence, salience, and evidence",
    selectedCluster: "Selected cluster",
    selectedClusterHint: "Click a cluster to drive the Atlas category filter without leaving the graph.",
    allMemory: "All memory",
    nodesLabel: "nodes",
    edgesLabel: "edges",
    linksLabel: "links",
    evidenceSpectrum: "Evidence spectrum",
    evidenceSpectrumHint: "Category bands sized by attached evidence and weighted by memory quality",
    bandsLabel: "bands",
    entityLens: "Entity lens",
    entityLensHint: "Cross-memory entity clusters for drilling into shared people, systems, and artifacts",
    entitiesLabel: "entities",
    graphNeighborhood: "Graph neighborhood",
    density: "density",
    direction: "direction",
    relationTypes: "relation types",
    out: "out",
    in: "in",
    top: "top",
    quality: "quality",
    retention: "retention",
    entries: "entries",
    evidenceGaps: "evidence gaps",
    stale: "stale",
    updated: "updated",
    selectedNodeFallback: "none",
    categoryAll: "all",
    memoryFallback: "memory",
    confidenceLabel: "confidence",
    salienceLabel: "salience",
    scoreLabel: "score",
    strengthLabel: "strength",
    weightLabel: "weight",
    bm25Label: "bm25",
    vectorLabel: "vector",
    graphLabel: "graph",
    temporalLabel: "temporal",
    outboundLabel: "outbound",
    inboundLabel: "inbound",
    linkedLabel: "linked",
    relationVersionLabel: "version",
    unknownLabel: "unknown",
    activeLabel: "active",
    notAvailableLabel: "n/a",
    categoryDistribution: "Categories",
    lifecycleDistribution: "Lifecycle states",
    showAllCategoriesAria: "Show all categories",
    selectCategoryAria: "Select category",
    categoryTitleSuffix: "memories",
    graphCategoryAria: "Filter graph category",
    graphMemoryAria: "Select graph memory",
    evidenceSpectrumAria: "Select evidence spectrum",
    entityMemoryAria: "Jump to entity memory",
    relatedMemoryAria: "Select related memory",
    memoryCountLabel: "memories",
    stateLabels: {
      all: "all",
      active: "active",
      archived: "archived",
      forgotten: "forgotten",
      healthy: "healthy",
      ok: "ok",
      ready: "ready",
      warning: "warning",
      review: "review",
      failed: "failed",
      error: "error",
    },
    sortLabels: {
      updated: "updated",
      confidence: "confidence",
      salience: "salience",
      evidence: "evidence",
    },
    shownLabel: "shown",
  };
}

function pct(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "--";
  }
  return `${Math.round(value * 100)}%`;
}

function score(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "--";
  }
  return value.toFixed(3);
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function toneForScore(value: number | null | undefined): "success" | "warning" | "danger" | "neutral" {
  const numeric = value ?? 0;
  if (numeric >= 0.75) {
    return "success";
  }
  if (numeric >= 0.45) {
    return "warning";
  }
  if (numeric > 0) {
    return "danger";
  }
  return "neutral";
}

function statusTone(status: string | null | undefined): "success" | "warning" | "danger" | "neutral" {
  const normalized = (status ?? "").toLowerCase();
  if (["active", "healthy", "ok", "ready"].includes(normalized)) {
    return "success";
  }
  if (["archived", "watch", "warning", "review"].includes(normalized)) {
    return "warning";
  }
  if (["forgotten", "critical", "failed", "error"].includes(normalized)) {
    return "danger";
  }
  return "neutral";
}

function formatDate(value: string | null | undefined, emptyLabel: string) {
  if (!value) {
    return emptyLabel;
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function oneLine(value: string | null | undefined, fallback: string) {
  const normalized = (value ?? "").replace(/\s+/g, " ").trim();
  return normalized || fallback;
}

function averageScore(values: Array<number | null | undefined>) {
  const numeric = values.filter((value): value is number => typeof value === "number" && !Number.isNaN(value));
  if (!numeric.length) {
    return null;
  }
  return numeric.reduce((sum, value) => sum + value, 0) / numeric.length;
}

function memoryEvidenceCount(memory: HCMSMemoryView) {
  return memory.evidence?.length ?? 0;
}

function memoryUpdatedAt(memory: HCMSMemoryView) {
  const time = new Date(memory.updated_at || memory.created_at).getTime();
  return Number.isNaN(time) ? 0 : time;
}

function shortMemoryLabel(memoryId: string) {
  return memoryId.replace(/^mem[_-]?/, "").replace(/[_-]+/g, " ").trim().slice(0, 5) || memoryId.slice(0, 5);
}

function sortAtlasMemories(items: HCMSMemoryView[], sort: HCMSAtlasSort) {
  return [...items].sort((left, right) => {
    if (sort === "confidence") {
      return (right.confidence ?? 0) - (left.confidence ?? 0) || memoryUpdatedAt(right) - memoryUpdatedAt(left);
    }
    if (sort === "salience") {
      return (right.salience ?? 0) - (left.salience ?? 0) || memoryUpdatedAt(right) - memoryUpdatedAt(left);
    }
    if (sort === "evidence") {
      return memoryEvidenceCount(right) - memoryEvidenceCount(left) || memoryUpdatedAt(right) - memoryUpdatedAt(left);
    }
    return memoryUpdatedAt(right) - memoryUpdatedAt(left);
  });
}

function selectedMemoryFromResult(item: HCMSRecallItemView | null): HCMSMemoryView | null {
  return item?.memory ?? null;
}

function CompactMetric({ label, value, sub }: { label: string; value: string; sub?: string | null }) {
  return (
    <div className="min-w-0 rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-3">
      <div className="truncate text-[11px] font-semibold uppercase tracking-[0.06em] text-[var(--muted)]">{label}</div>
      <div className="mt-2 truncate font-mono text-xl font-semibold leading-none text-[var(--ink)]">{value}</div>
      {sub ? <div className="mt-1 truncate text-xs text-[var(--muted)]">{sub}</div> : null}
    </div>
  );
}

function ScoreBar({ label, value }: { label: string; value: number | null | undefined }) {
  const percent = Math.min(100, Math.max(0, Math.round((value ?? 0) * 100)));
  const fill =
    percent >= 75
      ? "bg-[var(--success)]"
      : percent >= 45
        ? "bg-[var(--warning)]"
        : percent > 0
          ? "bg-[var(--danger)]"
          : "bg-[var(--line)]";
  return (
    <div className="min-w-0">
      <div className="mb-1 flex items-center justify-between gap-2 text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">
        <span className="truncate">{label}</span>
        <span className="font-mono">{percent}%</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-[var(--panel-muted)]">
        <div className={cn("h-full rounded-full", fill)} style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}

function StoreHealthStrip({ stores, text }: { stores: MemoryStoreHealthView[]; text: HCMSPanelText }) {
  if (!stores.length) {
    return null;
  }
  return (
    <div className="grid gap-2 lg:grid-cols-2">
      {stores.map((store) => (
        <div key={store.store_id} className="rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-3">
          <div className="flex min-w-0 items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-[var(--ink)]">{store.store_id}</div>
              <div className="mt-1 truncate text-xs text-[var(--muted)]">{store.layer_id ?? "hcms"}</div>
            </div>
            <Badge tone={statusTone(store.status)}>{store.status ? (text.stateLabels[store.status] ?? store.status) : text.unknownLabel}</Badge>
          </div>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <ScoreBar label={text.quality} value={store.quality_score} />
            <ScoreBar label={text.retention} value={store.retention_average} />
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <Badge tone="neutral">{text.entries} {store.entry_count ?? 0}</Badge>
            <Badge tone={(store.missing_evidence_count ?? 0) > 0 ? "warning" : "neutral"}>{text.evidenceGaps} {store.missing_evidence_count ?? 0}</Badge>
            <Badge tone={(store.stale_count ?? 0) > 0 ? "warning" : "neutral"}>{text.stale} {store.stale_count ?? 0}</Badge>
          </div>
        </div>
      ))}
    </div>
  );
}

function EvidenceRows({ evidence, emptyLabel, text }: { evidence: HCMSEvidenceView[]; emptyLabel: string; text: HCMSPanelText }) {
  if (!evidence.length) {
    return <OpsEmptyState text={emptyLabel} />;
  }
  return (
    <div className="space-y-2">
      {evidence.map((item) => (
        <div key={item.evidence_id} className="rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-3">
          <div className="flex min-w-0 items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-[var(--ink)]">{item.type || text.evidenceLabel}</div>
              <div className="mt-1 truncate text-xs text-[var(--muted)]">{item.source_id}</div>
            </div>
            <Badge tone={toneForScore(item.weight)}>{pct(item.weight)}</Badge>
          </div>
          <div className="mt-2 text-sm leading-6 text-[var(--muted)] [overflow-wrap:anywhere]">{item.content}</div>
        </div>
      ))}
    </div>
  );
}

function RecallCard({
  item,
  active,
  onSelect,
  text,
}: {
  item: HCMSRecallItemView;
  active: boolean;
  onSelect(): void;
  text: HCMSPanelText;
}) {
  const memory = item.memory;
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "block w-full min-w-0 rounded-lg border px-3 py-3 text-left transition-[background,border-color,box-shadow,transform] active:translate-y-px",
        active
          ? "border-[color-mix(in_srgb,var(--primary)_44%,var(--line))] bg-[var(--accent-soft)] shadow-[var(--shadow-card)]"
          : "border-[var(--line)] bg-[var(--panel-muted)] hover:border-[color-mix(in_srgb,var(--primary)_28%,var(--line))]",
      )}
    >
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-[var(--ink)]">{memory?.summary || item.memory_id}</div>
          <div className="mt-1 truncate text-xs text-[var(--muted)]">{memory?.category ?? "hcms"} · {item.memory_id}</div>
        </div>
        <Badge tone={toneForScore(item.score)}>{score(item.score)}</Badge>
      </div>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <ScoreBar label={text.confidenceLabel} value={memory?.confidence} />
        <ScoreBar label={text.salienceLabel} value={memory?.salience} />
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Badge tone={statusTone(memory?.state)}>{memory?.state ? (text.stateLabels[memory.state] ?? memory.state) : text.unknownLabel}</Badge>
        <Badge tone="neutral">{text.bm25Label} {score(item.raw_scores?.bm25)}</Badge>
        <Badge tone="neutral">{text.vectorLabel} {score(item.raw_scores?.vector)}</Badge>
        <Badge tone="neutral">{text.graphLabel} {score(item.raw_scores?.graph)}</Badge>
        <Badge tone="neutral">{text.temporalLabel} {score(item.raw_scores?.temporal)}</Badge>
      </div>
      {item.explanation ? <div className="mt-2 text-xs leading-5 text-[var(--muted)]">{item.explanation}</div> : null}
    </button>
  );
}

function MemoryDetail({
  memory,
  emptyLabel,
  text,
}: {
  memory: HCMSMemoryView | null;
  emptyLabel: string;
  text: HCMSPanelText;
}) {
  if (!memory) {
    return <OpsEmptyState text={emptyLabel} />;
  }
  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-3">
        <div className="flex min-w-0 items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-[var(--ink)]">{memory.memory_id}</div>
            <div className="mt-1 truncate text-xs text-[var(--muted)]">{memory.category} · {text.relationVersionLabel} {memory.version ?? 1}</div>
          </div>
          <Badge tone={statusTone(memory.state)}>{memory.state ? (text.stateLabels[memory.state] ?? memory.state) : text.activeLabel}</Badge>
        </div>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <ScoreBar label={text.confidenceLabel} value={memory.confidence} />
          <ScoreBar label={text.salienceLabel} value={memory.salience} />
        </div>
        <div className="mt-3 max-h-[190px] overflow-auto rounded-lg border border-[var(--line)] bg-[var(--panel)] p-3 text-sm leading-6 text-[var(--ink)] [overflow-wrap:anywhere]">
          {memory.content}
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {(memory.tags ?? []).slice(0, 8).map((tag) => (
            <Badge key={tag} tone="neutral">{tag}</Badge>
          ))}
          {(memory.entities ?? []).slice(0, 8).map((entity) => (
            <Badge key={entity} tone="neutral">{entity}</Badge>
          ))}
        </div>
      </div>
      <EvidenceRows evidence={memory.evidence ?? []} emptyLabel={emptyLabel} text={text} />
    </div>
  );
}

function CausalPathCard({ path, text }: { path: HCMSWhyPathView; text: HCMSPanelText }) {
  return (
    <div className="rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={toneForScore(path.confidence)}>{text.confidenceLabel} {pct(path.confidence)}</Badge>
        <Badge tone={toneForScore(path.total_strength)}>{text.strengthLabel} {score(path.total_strength)}</Badge>
        <Badge tone="neutral">{text.nodesLabel} {path.nodes.length}</Badge>
        <Badge tone="neutral">{text.edgesLabel} {path.edges.length}</Badge>
      </div>
      <div className="mt-3 flex min-w-0 flex-wrap items-center gap-2">
        {path.nodes.map((node, index) => (
          <React.Fragment key={`${node.memory_id}-${index}`}>
            {index > 0 ? <Link2Icon className="size-4 shrink-0 text-[var(--muted)]" /> : null}
            <CausalNodePill node={node} />
          </React.Fragment>
        ))}
      </div>
      {path.edges.length ? (
        <div className="mt-3 space-y-2">
          {path.edges.map((edge) => (
            <CausalEdgeRow key={edge.edge_id} edge={edge} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function CausalNodePill({ node }: { node: HCMSCausalNodeView }) {
  return (
    <span className="inline-flex max-w-full items-center gap-1 rounded-lg border border-[var(--line)] bg-[var(--panel)] px-2.5 py-1 text-xs text-[var(--ink)]">
      <span className="truncate">{node.memory_id}</span>
      <span className="font-mono text-[var(--muted)]">{pct(node.confidence)}</span>
    </span>
  );
}

function CausalEdgeRow({ edge }: { edge: HCMSCausalEdgeView }) {
  return (
    <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2 text-xs leading-5 text-[var(--muted)]">
      <div className="flex min-w-0 items-center justify-between gap-2">
        <span className="truncate text-[var(--ink)]">{edge.causal_type}</span>
        <span className="font-mono">{pct(edge.strength)}</span>
      </div>
      <div className="mt-1 truncate">
        {edge.source_event} {"->"} {edge.target_event}
      </div>
    </div>
  );
}

function RelationRows({
  relations,
  selectedMemoryId,
  emptyLabel,
  text,
}: {
  relations: HCMSRelationView[];
  selectedMemoryId: string | null;
  emptyLabel: string;
  text: HCMSPanelText;
}) {
  if (!relations.length) {
    return <OpsEmptyState text={emptyLabel} />;
  }
  return (
    <div className="space-y-2">
      {relations.map((relation) => (
        <RelationCard
          key={relation.relation_id}
          relation={relation}
          selectedMemoryId={selectedMemoryId}
          text={text}
        />
      ))}
    </div>
  );
}

function RelationCard({
  relation,
  selectedMemoryId,
  text,
}: {
  relation: HCMSRelationView;
  selectedMemoryId: string | null;
  text: HCMSPanelText;
}) {
  const connectedMemory =
    relation.source_memory_id === selectedMemoryId
      ? relation.target_memory ?? relation.source_memory
      : relation.target_memory_id === selectedMemoryId
        ? relation.source_memory ?? relation.target_memory
        : relation.source_memory ?? relation.target_memory;
  const directionLabel =
    relation.source_memory_id === selectedMemoryId
      ? text.outboundLabel
      : relation.target_memory_id === selectedMemoryId
        ? text.inboundLabel
        : text.linkedLabel;
  return (
    <div className="rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-3">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-[var(--ink)]">{relation.relation_type}</div>
          <div className="mt-1 truncate text-xs text-[var(--muted)]">
            {relation.source_memory_id} {"->"} {relation.target_memory_id}
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap justify-end gap-2">
          <Badge tone="neutral">{directionLabel}</Badge>
          <Badge tone={toneForScore(relation.confidence)}>{text.confidenceLabel} {pct(relation.confidence)}</Badge>
          <Badge tone={toneForScore(relation.weight)}>{text.weightLabel} {score(relation.weight)}</Badge>
        </div>
      </div>
      {connectedMemory ? (
        <div className="mt-3 rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2">
          <div className="truncate text-xs font-semibold text-[var(--ink)]">{connectedMemory.memory_id}</div>
          <div className="mt-1 line-clamp-2 text-sm leading-6 text-[var(--muted)]">
            {oneLine(connectedMemory.summary || connectedMemory.content, connectedMemory.memory_id)}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function VersionTimeline({
  versions,
  emptyLabel,
  text,
}: {
  versions: NonNullable<ReturnType<typeof useHCMSMemoryHistory>["data"]>["versions"];
  emptyLabel: string;
  text: HCMSPanelText;
}) {
  if (!versions.length) {
    return <OpsEmptyState text={emptyLabel} />;
  }
  return (
    <div className="space-y-2">
      {versions.map((version) => (
        <div key={version.version_id} className="rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-3">
          <div className="flex min-w-0 items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-[var(--ink)]">v{version.version}</div>
              <div className="mt-1 truncate text-xs text-[var(--muted)]">{formatDate(version.created_at, text.notAvailableLabel)}</div>
            </div>
            <Badge tone="neutral">{version.reason || text.relationVersionLabel}</Badge>
          </div>
          <div className="mt-2 line-clamp-2 text-sm leading-6 text-[var(--muted)]">{oneLine(version.summary || version.content, version.memory_id)}</div>
        </div>
      ))}
    </div>
  );
}

function TraceRows({ traces, emptyLabel, text }: { traces: MemoryTraceView[]; emptyLabel: string; text: HCMSPanelText }) {
  if (!traces.length) {
    return <OpsEmptyState text={emptyLabel} />;
  }
  return (
    <div className="space-y-2">
      {traces.map((trace) => (
        <div key={trace.trace_id} className="rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-3">
          <div className="flex min-w-0 items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-[var(--ink)]">{trace.trace_kind}</div>
              <div className="mt-1 truncate text-xs text-[var(--muted)]">{formatDate(trace.created_at, text.notAvailableLabel)}</div>
            </div>
            <Badge tone="neutral">{trace.evidence.length} {text.evidenceLabel}</Badge>
          </div>
          <div className="mt-2 line-clamp-2 text-sm leading-6 text-[var(--muted)]">{trace.query ?? trace.target_id ?? trace.trace_id}</div>
          {trace.engine_notes.length ? (
            <div className="mt-2 flex flex-wrap gap-2">
              {trace.engine_notes.map((note) => (
                <Badge key={note} tone="neutral">{note}</Badge>
              ))}
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function distribution(items: HCMSMemoryView[], key: "category" | "state"): DistributionBucket[] {
  const groups = new Map<string, HCMSMemoryView[]>();
  for (const item of items) {
    const value = item[key] || "unknown";
    const group = groups.get(value) ?? [];
    group.push(item);
    groups.set(value, group);
  }
  return Array.from(groups.entries())
    .map(([label, memories]) => ({
      label,
      count: memories.length,
      confidence: averageScore(memories.map((memory) => memory.confidence)) ?? 0,
      salience: averageScore(memories.map((memory) => memory.salience)) ?? 0,
      evidenceCount: memories.reduce((sum, memory) => sum + memoryEvidenceCount(memory), 0),
    }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

function stateDisplay(value: string | null | undefined, text: HCMSPanelText) {
  return value ? (text.stateLabels[value] ?? value) : text.unknownLabel;
}

function entityDistribution(memories: HCMSMemoryView[]): EntityBucket[] {
  const groups = new Map<
    string,
    {
      memories: HCMSMemoryView[];
      categories: Set<string>;
    }
  >();
  for (const memory of memories) {
    for (const entity of memory.entities ?? []) {
      const label = entity.trim();
      if (!label) {
        continue;
      }
      const group = groups.get(label) ?? { memories: [], categories: new Set<string>() };
      group.memories.push(memory);
      group.categories.add(memory.category || "unknown");
      groups.set(label, group);
    }
  }
  return Array.from(groups.entries())
    .map(([label, group]) => ({
      label,
      count: group.memories.length,
      categories: group.categories.size,
      evidenceCount: group.memories.reduce((sum, memory) => sum + memoryEvidenceCount(memory), 0),
      confidence: averageScore(group.memories.map((memory) => memory.confidence)) ?? 0,
      memoryIds: Array.from(new Set(group.memories.map((memory) => memory.memory_id))),
    }))
    .sort((left, right) => right.count - left.count || right.evidenceCount - left.evidenceCount || right.confidence - left.confidence || left.label.localeCompare(right.label));
}

function evidenceSpectrum(memories: HCMSMemoryView[]): SpectrumBucket[] {
  return distribution(memories, "category")
    .map((item) => ({
      label: item.label,
      count: item.count,
      evidenceCount: item.evidenceCount,
      confidence: item.confidence,
      salience: item.salience,
    }))
    .sort((left, right) => right.evidenceCount - left.evidenceCount || right.confidence - left.confidence || left.label.localeCompare(right.label));
}

function graphMemory(memory: HCMSMemoryView): AtlasGraphMemory {
  return {
    memoryId: memory.memory_id,
    summary: oneLine(memory.summary || memory.content, memory.memory_id),
    category: memory.category || "unknown",
    confidence: memory.confidence,
    salience: memory.salience,
    state: memory.state,
    evidenceCount: memoryEvidenceCount(memory),
  };
}

function graphPlaceholder(memoryId: string): AtlasGraphMemory {
  return {
    memoryId,
    summary: memoryId,
    category: "linked",
    confidence: null,
    salience: null,
    state: "linked",
    evidenceCount: 0,
  };
}

function mergeGraphMemories(
  memories: HCMSMemoryView[],
  selectedMemory: HCMSMemoryView | null,
  relations: HCMSRelationView[],
) {
  const byId = new Map<string, AtlasGraphMemory>();
  const addMemory = (memory: HCMSMemoryView | null | undefined) => {
    if (memory) {
      byId.set(memory.memory_id, graphMemory(memory));
    }
  };
  for (const memory of memories) {
    addMemory(memory);
  }
  addMemory(selectedMemory);
  for (const relation of relations) {
    addMemory(relation.source_memory);
    addMemory(relation.target_memory);
    if (!byId.has(relation.source_memory_id)) {
      byId.set(relation.source_memory_id, graphPlaceholder(relation.source_memory_id));
    }
    if (!byId.has(relation.target_memory_id)) {
      byId.set(relation.target_memory_id, graphPlaceholder(relation.target_memory_id));
    }
  }
  return Array.from(byId.values());
}

function atlasGraphLayout(
  memories: AtlasGraphMemory[],
  relations: HCMSRelationView[],
): { nodes: AtlasGraphNode[]; categories: AtlasGraphCategory[] } {
  const degreeById = new Map<string, number>();
  for (const relation of relations) {
    degreeById.set(relation.source_memory_id, (degreeById.get(relation.source_memory_id) ?? 0) + 1);
    degreeById.set(relation.target_memory_id, (degreeById.get(relation.target_memory_id) ?? 0) + 1);
  }
  const groups = new Map<string, AtlasGraphMemory[]>();
  for (const memory of memories) {
    const group = groups.get(memory.category) ?? [];
    group.push(memory);
    groups.set(memory.category, group);
  }
  const grouped = Array.from(groups.entries())
    .sort((left, right) => right[1].length - left[1].length || left[0].localeCompare(right[0]));
  const categories: AtlasGraphCategory[] = grouped.map(([label, group], index) => {
    const center = ATLAS_GRAPH_CENTERS[index % ATLAS_GRAPH_CENTERS.length] ?? ATLAS_GRAPH_CENTERS[0]!;
    return {
      label,
      centerX: center.x,
      centerY: center.y,
      count: group.length,
      confidence: averageScore(group.map((memory) => memory.confidence)) ?? 0,
      salience: averageScore(group.map((memory) => memory.salience)) ?? 0,
    };
  });
  const nodes = grouped.flatMap(([label, group], categoryIndex) => {
    const center = ATLAS_GRAPH_CENTERS[categoryIndex % ATLAS_GRAPH_CENTERS.length] ?? ATLAS_GRAPH_CENTERS[0]!;
    return [...group]
      .sort((left, right) => {
        const leftDegree = degreeById.get(left.memoryId) ?? 0;
        const rightDegree = degreeById.get(right.memoryId) ?? 0;
        return rightDegree - leftDegree || (right.confidence ?? 0) - (left.confidence ?? 0) || left.memoryId.localeCompare(right.memoryId);
      })
      .map((memory, index) => {
        const angle = group.length === 1 ? -Math.PI / 2 : (Math.PI * 2 * index) / group.length - Math.PI / 2;
        const radius = group.length === 1 ? 0 : clamp(7 + group.length * 1.2, 8, 15);
        const degree = degreeById.get(memory.memoryId) ?? 0;
        const quality = averageScore([memory.confidence, memory.salience]) ?? 0;
        return {
          ...memory,
          category: label,
          categoryIndex,
          degree,
          x: clamp(center.x + Math.cos(angle) * radius, 8, 92),
          y: clamp(center.y + Math.sin(angle) * radius, 10, 90),
          size: clamp(44 + degree * 8 + memory.evidenceCount * 3 + quality * 12, 44, 76),
        };
      });
  });
  return { nodes, categories };
}

function DistributionBars({
  title,
  items,
  emptyLabel,
  activeLabel,
  onSelect,
  text,
}: {
  title: string;
  items: DistributionBucket[];
  emptyLabel: string;
  activeLabel?: string;
  onSelect?(label: string): void;
  text: HCMSPanelText;
}) {
  const total = items.reduce((sum, item) => sum + item.count, 0) || 1;
  if (!items.length) {
    return <OpsEmptyState text={emptyLabel} />;
  }
  return (
    <div className="rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] p-3">
      <div className="flex min-w-0 items-center justify-between gap-3">
        <div className="truncate text-xs font-semibold uppercase tracking-[0.06em] text-[var(--muted)]">{title}</div>
        <span className="font-mono text-xs text-[var(--muted)]">{total}</span>
      </div>
      <div className="mt-3 space-y-2">
        {items.slice(0, 6).map((item) => {
          const width = Math.max(8, Math.round((item.count / total) * 100));
          const active = activeLabel === item.label;
          const content = (
            <>
              <div className="mb-1 flex items-center justify-between gap-3 text-xs">
                <span className="truncate font-medium text-[var(--ink)]">{title === text.lifecycleDistribution ? stateDisplay(item.label, text) : item.label}</span>
                <span className="font-mono text-[var(--muted)]">{item.count}</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-[var(--panel)]">
                <div
                  className={cn(
                    "h-full rounded-full transition-[width,background-color]",
                    active ? "bg-[var(--primary)]" : "bg-[color-mix(in_srgb,var(--primary)_54%,var(--muted))]",
                  )}
                  style={{ width: `${width}%` }}
                />
              </div>
              <div className="mt-1 flex min-w-0 items-center gap-2 text-[10px] uppercase tracking-[0.04em] text-[var(--muted)]">
                <span className="font-mono">{text.confidenceLabel} {pct(item.confidence)}</span>
                <span className="font-mono">{text.salienceLabel} {pct(item.salience)}</span>
                <span className="font-mono">{text.evidenceLabel} {item.evidenceCount}</span>
              </div>
            </>
          );
          return (
            <div key={item.label} className="min-w-0">
              {onSelect ? (
                <button
                  type="button"
                  onClick={() => onSelect(item.label)}
                  className={cn(
                    "block w-full min-w-0 rounded-lg px-2 py-2 text-left transition-[background,border-color,transform] active:translate-y-px",
                    active
                      ? "bg-[var(--accent-soft)]"
                      : "hover:bg-[color-mix(in_srgb,var(--panel)_60%,var(--panel-muted))]",
                  )}
                >
                  {content}
                </button>
              ) : (
                <div className="px-2 py-2">{content}</div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function CategoryConstellation({
  items,
  emptyLabel,
  activeLabel,
  onSelect,
  text,
}: {
  items: DistributionBucket[];
  emptyLabel: string;
  activeLabel?: string;
  onSelect(label: string): void;
  text: HCMSPanelText;
}) {
  const total = items.reduce((sum, item) => sum + item.count, 0) || 1;
  const visible = items.slice(0, 7);
  if (!visible.length) {
    return <OpsEmptyState text={emptyLabel} />;
  }

  return (
    <div className="overflow-hidden rounded-lg border border-[var(--line)] bg-[var(--panel-muted)]">
      <div className="flex min-w-0 items-center justify-between gap-3 border-b border-[var(--line)] bg-[var(--panel)] px-4 py-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-[var(--ink)]">{text.categoryConstellation}</div>
          <div className="mt-1 truncate text-xs text-[var(--muted)]">{text.categoryConstellationHint}</div>
        </div>
        <Badge tone="neutral">{visible.length} {text.categories}</Badge>
      </div>
      <div className="grid gap-4 p-4 xl:grid-cols-[minmax(0,0.92fr)_minmax(220px,0.68fr)]">
        <div className="relative min-h-[320px] overflow-hidden rounded-lg border border-[var(--line)] bg-[var(--panel)]">
          <svg className="pointer-events-none absolute inset-0 size-full" viewBox="0 0 320 320" aria-hidden="true">
            <circle cx="160" cy="160" r="52" fill="none" stroke="var(--line)" strokeDasharray="2 8" />
            <circle cx="160" cy="160" r="104" fill="none" stroke="var(--line)" strokeDasharray="4 10" />
            <circle cx="160" cy="160" r="142" fill="none" stroke="var(--line)" strokeDasharray="1 12" />
            {visible.map((item, index) => {
              const angle = (Math.PI * 2 * index) / Math.max(visible.length, 1) - Math.PI / 2;
              const radius = 104 + (index % 2) * 28;
              const x = 160 + Math.cos(angle) * radius;
              const y = 160 + Math.sin(angle) * radius;
              return (
                <line
                  key={`category-line-${item.label}`}
                  x1="160"
                  y1="160"
                  x2={x}
                  y2={y}
                  stroke="var(--primary)"
                  strokeOpacity={activeLabel === item.label ? 0.54 : 0.22}
                  strokeWidth={activeLabel === item.label ? 2.4 : 1.2}
                />
              );
            })}
          </svg>
          <button
            type="button"
            onClick={() => onSelect("all")}
            className={cn(
              "absolute left-1/2 top-1/2 flex size-28 -translate-x-1/2 -translate-y-1/2 flex-col items-center justify-center rounded-full border p-3 text-center shadow-[var(--shadow-card)] transition-[border-color,background,transform] active:translate-y-px",
              activeLabel ? "border-[var(--line)] bg-[var(--panel-muted)]" : "border-[color-mix(in_srgb,var(--primary)_42%,var(--line))] bg-[var(--accent-soft)]",
            )}
            aria-label={text.showAllCategoriesAria}
          >
            <span className="text-[10px] font-semibold uppercase tracking-[0.06em] text-[var(--muted)]">{text.allMemory}</span>
            <span className="mt-1 font-mono text-2xl font-semibold leading-none text-[var(--ink)]">{total}</span>
            <span className="mt-1 text-[10px] text-[var(--muted)]">{text.nodesLabel}</span>
          </button>
          {visible.map((item, index) => {
            const active = activeLabel === item.label;
            const angle = (Math.PI * 2 * index) / Math.max(visible.length, 1) - Math.PI / 2;
            const radius = 104 + (index % 2) * 28;
            const x = Math.cos(angle) * radius;
            const y = Math.sin(angle) * radius;
            const size = clamp(58 + (item.count / total) * 42 + item.evidenceCount * 2, 58, 94);
            return (
              <button
                key={item.label}
                type="button"
                onClick={() => onSelect(item.label)}
                className={cn(
                  "absolute left-1/2 top-1/2 flex flex-col items-center justify-center rounded-full border px-2 text-center shadow-[var(--shadow-card)] transition-[border-color,background] active:opacity-90",
                  active
                    ? "border-[color-mix(in_srgb,var(--primary)_52%,var(--line))] bg-[var(--accent-soft)]"
                    : "border-[var(--line)] bg-[var(--panel-muted)] hover:border-[color-mix(in_srgb,var(--primary)_32%,var(--line))] hover:bg-[var(--panel)]",
                )}
                style={{
                  width: `${size}px`,
                  height: `${size}px`,
                  transform: `translate(calc(-50% + ${x}px), calc(-50% + ${y}px))`,
                }}
                aria-label={`${text.selectCategoryAria} ${item.label}`}
                title={`${item.label}: ${item.count} ${text.categoryTitleSuffix}`}
              >
                <span className="max-w-full truncate text-[11px] font-semibold text-[var(--ink)]">{item.label}</span>
                <span className="mt-1 font-mono text-sm text-[var(--muted)]">{item.count}</span>
              </button>
            );
          })}
        </div>
        <div className="min-w-0 space-y-2">
          <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-3">
            <div className="text-xs font-semibold uppercase tracking-[0.06em] text-[var(--muted)]">{text.selectedCluster}</div>
            <div className="mt-2 truncate text-lg font-semibold text-[var(--ink)]">{activeLabel ?? text.categoryAll}</div>
            <div className="mt-1 text-xs leading-5 text-[var(--muted)]">{text.selectedClusterHint}</div>
          </div>
          {visible.slice(0, 5).map((item) => (
            <button
              key={`category-rank-${item.label}`}
              type="button"
              onClick={() => onSelect(item.label)}
              className={cn(
                "block w-full rounded-lg border px-3 py-2 text-left transition-[border-color,background,transform] active:translate-y-px",
                activeLabel === item.label
                  ? "border-[color-mix(in_srgb,var(--primary)_42%,var(--line))] bg-[var(--accent-soft)]"
                  : "border-[var(--line)] bg-[var(--panel)] hover:border-[color-mix(in_srgb,var(--primary)_28%,var(--line))]",
              )}
            >
              <div className="flex min-w-0 items-center justify-between gap-2">
                <span className="truncate text-sm font-semibold text-[var(--ink)]">{item.label}</span>
                <span className="font-mono text-xs text-[var(--muted)]">{item.count}</span>
              </div>
              <div className="mt-2 grid gap-2 sm:grid-cols-2 xl:grid-cols-1">
                <ScoreBar label={text.confidenceLabel} value={item.confidence} />
                <ScoreBar label={text.salienceLabel} value={item.salience} />
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function AtlasGraphView({
  memories,
  relations,
  selectedMemoryId,
  activeCategory,
  emptyLabel,
  onSelectMemory,
  onSelectCategory,
  text,
}: {
  memories: AtlasGraphMemory[];
  relations: HCMSRelationView[];
  selectedMemoryId: string | null;
  activeCategory?: string;
  emptyLabel: string;
  onSelectMemory(memoryId: string): void;
  onSelectCategory(category: string): void;
  text: HCMSPanelText;
}) {
  const { nodes, categories } = React.useMemo(() => atlasGraphLayout(memories, relations), [memories, relations]);
  const nodesById = React.useMemo(() => new Map(nodes.map((node) => [node.memoryId, node])), [nodes]);
  const visibleRelations = relations
    .map((relation) => ({
      relation,
      source: nodesById.get(relation.source_memory_id),
      target: nodesById.get(relation.target_memory_id),
    }))
    .filter((item): item is { relation: HCMSRelationView; source: AtlasGraphNode; target: AtlasGraphNode } => Boolean(item.source && item.target));
  const selectedNode = selectedMemoryId ? nodesById.get(selectedMemoryId) : null;

  if (!nodes.length) {
    return <OpsEmptyState text={emptyLabel} />;
  }

  return (
    <div className="overflow-hidden rounded-lg border border-[var(--line)] bg-[var(--panel-muted)]">
      <div className="flex flex-col gap-3 border-b border-[var(--line)] bg-[var(--panel)] px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-[var(--ink)]">{text.graphAtlas}</div>
          <div className="mt-1 truncate text-xs text-[var(--muted)]">{text.graphAtlasHint}</div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge tone="neutral">{nodes.length} {text.nodesLabel}</Badge>
          <Badge tone="neutral">{visibleRelations.length} {text.edgesLabel}</Badge>
          <Badge tone="neutral">{categories.length} {text.categories}</Badge>
          {selectedNode ? <Badge tone={toneForScore(selectedNode.confidence)}>{text.selectedNode} {pct(selectedNode.confidence)}</Badge> : null}
        </div>
      </div>
      <div className="grid gap-4 p-4 xl:grid-cols-[minmax(0,1fr)_240px]">
        <div className="relative min-h-[420px] overflow-hidden rounded-lg border border-[var(--line)] bg-[var(--panel)]">
          <svg className="pointer-events-none absolute inset-0 size-full" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
            <defs>
              <pattern id="hcms-graph-grid" width="8" height="8" patternUnits="userSpaceOnUse">
                <path d="M 8 0 L 0 0 0 8" fill="none" stroke="var(--line)" strokeWidth="0.18" opacity="0.5" />
              </pattern>
            </defs>
            <rect width="100" height="100" fill="url(#hcms-graph-grid)" opacity="0.36" />
            {categories.map((category) => {
              const active = activeCategory === category.label;
              return (
                <circle
                  key={`cluster-${category.label}`}
                  cx={category.centerX}
                  cy={category.centerY}
                  r={clamp(13 + category.count * 2.2, 15, 25)}
                  fill="var(--primary)"
                  fillOpacity={active ? 0.14 : 0.05}
                  stroke="var(--primary)"
                  strokeOpacity={active ? 0.42 : 0.18}
                  strokeWidth={active ? 0.7 : 0.38}
                />
              );
            })}
            {visibleRelations.map(({ relation, source, target }) => {
              const active = relation.source_memory_id === selectedMemoryId || relation.target_memory_id === selectedMemoryId;
              return (
                <line
                  key={relation.relation_id}
                  x1={source.x}
                  y1={source.y}
                  x2={target.x}
                  y2={target.y}
                  stroke="var(--primary)"
                  strokeOpacity={active ? 0.64 : 0.25}
                  strokeWidth={active ? clamp((relation.weight ?? 0.5) * 1.2, 0.45, 1.6) : clamp((relation.weight ?? 0.5) * 0.75, 0.28, 1)}
                  vectorEffect="non-scaling-stroke"
                />
              );
            })}
          </svg>
          {categories.map((category) => {
            const active = activeCategory === category.label;
            return (
              <button
                key={`category-anchor-${category.label}`}
                type="button"
                onClick={() => onSelectCategory(category.label)}
                className={cn(
                  "absolute max-w-[9.5rem] -translate-x-1/2 rounded-full border px-3 py-1 text-xs font-semibold shadow-[var(--shadow-card)] transition-[border-color,background,transform] active:translate-y-px",
                  active
                    ? "border-[color-mix(in_srgb,var(--primary)_46%,var(--line))] bg-[var(--accent-soft)] text-[var(--ink)]"
                    : "border-[var(--line)] bg-[var(--panel-muted)] text-[var(--muted)] hover:border-[color-mix(in_srgb,var(--primary)_28%,var(--line))] hover:text-[var(--ink)]",
                )}
                style={{
                  left: `${category.centerX}%`,
                  top: `${clamp(category.centerY - 20, 4, 86)}%`,
                }}
                aria-label={`${text.graphCategoryAria} ${category.label}`}
                title={`${category.label}: ${category.count} ${text.nodesLabel}`}
              >
                <span className="block truncate">{category.label}</span>
              </button>
            );
          })}
          {nodes.map((node) => {
            const active = node.memoryId === selectedMemoryId;
            const dimmed = Boolean(activeCategory && node.category !== activeCategory && !active);
            return (
              <button
                key={node.memoryId}
                type="button"
                onClick={() => onSelectMemory(node.memoryId)}
                className={cn(
                  "absolute flex -translate-x-1/2 -translate-y-1/2 flex-col items-center justify-center rounded-full border px-2 text-center shadow-[var(--shadow-card)] transition-[border-color,background,opacity,transform] hover:scale-[1.03] active:scale-[0.98]",
                  active
                    ? "border-[color-mix(in_srgb,var(--primary)_58%,var(--line))] bg-[var(--accent-soft)] text-[var(--ink)]"
                    : "border-[var(--line)] bg-[var(--panel-muted)] text-[var(--ink)] hover:border-[color-mix(in_srgb,var(--primary)_34%,var(--line))] hover:bg-[var(--panel)]",
                  dimmed ? "opacity-35" : "opacity-100",
                )}
                style={{
                  left: `${node.x}%`,
                  top: `${node.y}%`,
                  width: `${node.size}px`,
                  height: `${node.size}px`,
                }}
                aria-label={`${text.graphMemoryAria} ${node.memoryId}`}
                title={node.summary}
              >
                <span className="max-w-full truncate text-[10px] font-semibold leading-4">{shortMemoryLabel(node.memoryId)}</span>
                <span className="mt-0.5 font-mono text-[9px] text-[var(--muted)]">{node.degree} {text.linksLabel}</span>
              </button>
            );
          })}
        </div>
        <div className="min-w-0 space-y-3">
          <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-3">
            <div className="text-xs font-semibold uppercase tracking-[0.06em] text-[var(--muted)]">{text.graphFocus}</div>
            <div className="mt-2 truncate text-lg font-semibold text-[var(--ink)]">{selectedNode?.summary ?? selectedMemoryId ?? text.selectedNodeFallback}</div>
            <div className="mt-2 grid gap-2 sm:grid-cols-2 xl:grid-cols-1">
              <ScoreBar label={text.confidenceLabel} value={selectedNode?.confidence} />
              <ScoreBar label={text.salienceLabel} value={selectedNode?.salience} />
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <Badge tone={statusTone(selectedNode?.state)}>{selectedNode?.state ?? text.memoryFallback}</Badge>
              <Badge tone="neutral">{selectedNode?.category ?? text.category}</Badge>
              <Badge tone="neutral">{selectedNode?.evidenceCount ?? 0} {text.evidenceLabel}</Badge>
            </div>
          </div>
          <div className="space-y-2">
            {categories.slice(0, 6).map((category) => (
              <button
                key={`graph-category-row-${category.label}`}
                type="button"
                onClick={() => onSelectCategory(category.label)}
                className={cn(
                  "block w-full rounded-lg border px-3 py-2 text-left transition-[border-color,background,transform] active:translate-y-px",
                  activeCategory === category.label
                    ? "border-[color-mix(in_srgb,var(--primary)_42%,var(--line))] bg-[var(--accent-soft)]"
                    : "border-[var(--line)] bg-[var(--panel)] hover:border-[color-mix(in_srgb,var(--primary)_28%,var(--line))]",
                )}
              >
                <div className="flex min-w-0 items-center justify-between gap-2">
                  <span className="truncate text-sm font-semibold text-[var(--ink)]">{category.label}</span>
                  <span className="font-mono text-xs text-[var(--muted)]">{category.count}</span>
                </div>
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[var(--panel-muted)]">
                  <div
                    className="h-full rounded-full bg-[var(--primary)]"
                    style={{ width: `${clamp(Math.round(category.confidence * 100), 5, 100)}%` }}
                  />
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function AtlasTopologySummary({
  memories,
  relations,
  text,
}: {
  memories: HCMSMemoryView[];
  relations: HCMSRelationView[];
  text: HCMSPanelText;
}) {
  const activeCount = memories.filter((memory) => (memory.state ?? "").toLowerCase() === "active").length;
  const avgConfidence = averageScore(memories.map((memory) => memory.confidence));
  const avgSalience = averageScore(memories.map((memory) => memory.salience));
  const evidenceCount = memories.reduce((sum, memory) => sum + memoryEvidenceCount(memory), 0);
  const categories = new Set(memories.map((memory) => memory.category || text.unknownLabel)).size;

  return (
    <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
      <CompactMetric label={text.visibleNodes} value={String(memories.length)} sub={`${activeCount} ${text.activeCount}`} />
      <CompactMetric label={text.categories} value={String(categories)} sub={text.filteredSet} />
      <CompactMetric label={text.graphLinks} value={String(relations.length)} sub={text.selectedNode} />
      <CompactMetric label={text.avgConfidence} value={pct(avgConfidence)} sub={`${text.salienceLabel} ${pct(avgSalience)}`} />
      <CompactMetric label={text.evidenceLabel} value={String(evidenceCount)} sub={text.attachedRecords} />
    </div>
  );
}

function EvidenceSpectrum({
  items,
  activeLabel,
  emptyLabel,
  onSelect,
  text,
}: {
  items: SpectrumBucket[];
  activeLabel?: string;
  emptyLabel: string;
  onSelect(label: string): void;
  text: HCMSPanelText;
}) {
  if (!items.length) {
    return <OpsEmptyState text={emptyLabel} />;
  }
  const maxEvidence = Math.max(...items.map((item) => item.evidenceCount), 1);
  return (
    <div className="overflow-hidden rounded-lg border border-[var(--line)] bg-[var(--panel-muted)]">
      <div className="flex min-w-0 items-center justify-between gap-3 border-b border-[var(--line)] bg-[var(--panel)] px-4 py-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-[var(--ink)]">{text.evidenceSpectrum}</div>
          <div className="mt-1 truncate text-xs text-[var(--muted)]">{text.evidenceSpectrumHint}</div>
        </div>
        <Badge tone="neutral">{items.length} {text.bandsLabel}</Badge>
      </div>
      <div className="space-y-2 p-4">
        {items.slice(0, 6).map((item) => {
          const active = activeLabel === item.label;
          const evidenceWidth = clamp(Math.round((item.evidenceCount / maxEvidence) * 100), 8, 100);
          return (
            <button
              key={`spectrum-${item.label}`}
              type="button"
              onClick={() => onSelect(item.label)}
              className={cn(
                "block w-full rounded-lg border px-3 py-3 text-left transition-[border-color,background,transform] active:translate-y-px",
                active
                  ? "border-[color-mix(in_srgb,var(--primary)_42%,var(--line))] bg-[var(--accent-soft)]"
                  : "border-[var(--line)] bg-[var(--panel)] hover:border-[color-mix(in_srgb,var(--primary)_28%,var(--line))]",
              )}
              aria-label={`${text.evidenceSpectrumAria} ${item.label}`}
            >
              <div className="flex min-w-0 items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold text-[var(--ink)]">{item.label}</div>
                  <div className="mt-1 flex flex-wrap gap-2 text-[10px] uppercase tracking-[0.04em] text-[var(--muted)]">
                    <span className="font-mono">{item.count} {text.memoryCountLabel}</span>
                    <span className="font-mono">{item.evidenceCount} {text.evidenceLabel}</span>
                  </div>
                </div>
                <Badge tone={toneForScore(item.confidence)}>{pct(item.confidence)}</Badge>
              </div>
              <div className="mt-3 h-2 overflow-hidden rounded-full bg-[var(--panel-muted)]">
                <div className="h-full rounded-full bg-[var(--primary)] transition-[width]" style={{ width: `${evidenceWidth}%` }} />
              </div>
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                <ScoreBar label={text.confidenceLabel} value={item.confidence} />
                <ScoreBar label={text.salienceLabel} value={item.salience} />
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function EntityLens({
  items,
  emptyLabel,
  onSelectMemory,
  text,
}: {
  items: EntityBucket[];
  emptyLabel: string;
  onSelectMemory(memoryId: string): void;
  text: HCMSPanelText;
}) {
  if (!items.length) {
    return <OpsEmptyState text={emptyLabel} />;
  }
  return (
    <div className="overflow-hidden rounded-lg border border-[var(--line)] bg-[var(--panel-muted)]">
      <div className="flex min-w-0 items-center justify-between gap-3 border-b border-[var(--line)] bg-[var(--panel)] px-4 py-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-[var(--ink)]">{text.entityLens}</div>
          <div className="mt-1 truncate text-xs text-[var(--muted)]">{text.entityLensHint}</div>
        </div>
        <Badge tone="neutral">{items.length} {text.entitiesLabel}</Badge>
      </div>
      <div className="grid gap-3 p-4 xl:grid-cols-[minmax(0,0.92fr)_minmax(220px,0.68fr)]">
        <div className="grid gap-3 sm:grid-cols-2">
          {items.slice(0, 4).map((item) => (
            <div key={`entity-card-${item.label}`} className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-3">
              <div className="flex min-w-0 items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold text-[var(--ink)]">{item.label}</div>
                  <div className="mt-1 flex flex-wrap gap-2 text-[10px] uppercase tracking-[0.04em] text-[var(--muted)]">
                    <span className="font-mono">{item.count} {text.memories}</span>
                    <span className="font-mono">{item.categories} {text.categories}</span>
                  </div>
                </div>
                <Badge tone={toneForScore(item.confidence)}>{pct(item.confidence)}</Badge>
              </div>
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                <div className="rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-2.5 py-2">
                  <div className="text-[10px] uppercase tracking-[0.06em] text-[var(--muted)]">{text.evidenceLabel}</div>
                  <div className="mt-1 font-mono text-sm font-semibold text-[var(--ink)]">{item.evidenceCount}</div>
                </div>
                <div className="rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-2.5 py-2">
                  <div className="text-[10px] uppercase tracking-[0.06em] text-[var(--muted)]">{text.graphLinks}</div>
                  <div className="mt-1 font-mono text-sm font-semibold text-[var(--ink)]">{item.memoryIds.length}</div>
                </div>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                {item.memoryIds.slice(0, 3).map((memoryId) => (
                  <button
                    key={`${item.label}-${memoryId}`}
                    type="button"
                    onClick={() => onSelectMemory(memoryId)}
                    className="rounded-full border border-[var(--line)] bg-[var(--panel-muted)] px-2.5 py-1 text-xs text-[var(--ink)] transition-[border-color,background,transform] hover:border-[color-mix(in_srgb,var(--primary)_28%,var(--line))] hover:bg-[var(--panel)] active:translate-y-px"
                    aria-label={`${text.entityMemoryAria} ${memoryId}`}
                  >
                    {shortMemoryLabel(memoryId)}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
        <div className="space-y-2">
          {items.slice(0, 6).map((item) => (
            <div key={`entity-row-${item.label}`} className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-3">
              <div className="flex min-w-0 items-center justify-between gap-2">
                <span className="truncate text-sm font-semibold text-[var(--ink)]">{item.label}</span>
                <span className="font-mono text-xs text-[var(--muted)]">{item.memoryIds.length}</span>
              </div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[var(--panel-muted)]">
                <div
                  className="h-full rounded-full bg-[var(--primary)]"
                  style={{ width: `${clamp(Math.round(item.confidence * 100), 6, 100)}%` }}
                />
              </div>
              <div className="mt-2 flex flex-wrap gap-2">
                <Badge tone="neutral">{item.categories} {text.categories}</Badge>
                <Badge tone="neutral">{item.evidenceCount} {text.evidenceLabel}</Badge>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function MemoryAtlasCard({
  memory,
  active,
  relationCount,
  onSelect,
  text,
}: {
  memory: HCMSMemoryView;
  active: boolean;
  relationCount: number;
  onSelect(): void;
  text: HCMSPanelText;
}) {
  const evidenceCount = memoryEvidenceCount(memory);
  const quality = averageScore([memory.confidence, memory.salience]) ?? 0;
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "group block h-full w-full min-w-0 overflow-hidden rounded-lg border text-left shadow-[var(--shadow-card)] transition-[background,border-color,transform] hover:-translate-y-0.5 active:translate-y-px",
        active
          ? "border-[color-mix(in_srgb,var(--primary)_44%,var(--line))] bg-[var(--accent-soft)]"
          : "border-[var(--line)] bg-[var(--panel-muted)] hover:border-[color-mix(in_srgb,var(--primary)_28%,var(--line))]",
      )}
    >
      <div className="h-1 bg-[var(--line)]">
        <div
          className="h-full bg-[var(--primary)] transition-[width]"
          style={{ width: `${clamp(Math.round(quality * 100), 4, 100)}%` }}
        />
      </div>
      <div className="px-3 py-3">
        <div className="flex min-w-0 items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="line-clamp-2 text-sm font-semibold leading-5 text-[var(--ink)]">{memory.summary || memory.memory_id}</div>
            <div className="mt-1 truncate text-xs text-[var(--muted)]">{memory.category} · {memory.memory_id}</div>
          </div>
          <Badge tone={statusTone(memory.state)}>{memory.state ? (text.stateLabels[memory.state] ?? memory.state) : text.unknownLabel}</Badge>
        </div>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <ScoreBar label={text.confidenceLabel} value={memory.confidence} />
          <ScoreBar label={text.salienceLabel} value={memory.salience} />
        </div>
        <div className="mt-3 grid grid-cols-3 gap-2">
          <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-2.5 py-2">
            <div className="text-[10px] uppercase tracking-[0.06em] text-[var(--muted)]">{text.evidenceLabel}</div>
            <div className="mt-1 font-mono text-sm font-semibold text-[var(--ink)]">{evidenceCount}</div>
          </div>
          <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-2.5 py-2">
            <div className="text-[10px] uppercase tracking-[0.06em] text-[var(--muted)]">{text.linksLabel}</div>
            <div className="mt-1 font-mono text-sm font-semibold text-[var(--ink)]">{relationCount}</div>
          </div>
          <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-2.5 py-2">
            <div className="text-[10px] uppercase tracking-[0.06em] text-[var(--muted)]">{text.quality}</div>
            <div className="mt-1 font-mono text-sm font-semibold text-[var(--ink)]">{pct(quality)}</div>
          </div>
        </div>
        <div className="mt-3 rounded-lg border border-[var(--line)] bg-[var(--panel)] px-2.5 py-2">
          <div className="flex min-w-0 items-center justify-between gap-2">
            <span className="text-[10px] uppercase tracking-[0.06em] text-[var(--muted)]">{text.updated}</span>
            <span className="truncate font-mono text-xs text-[var(--ink)]">{formatDate(memory.updated_at, text.notAvailableLabel)}</span>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {(memory.entities ?? []).slice(0, 4).map((entity) => (
            <Badge key={entity} tone="neutral">{entity}</Badge>
          ))}
          {(memory.tags ?? []).slice(0, 4).map((tag) => (
            <Badge key={tag} tone="neutral">{tag}</Badge>
          ))}
        </div>
      </div>
    </button>
  );
}

function RelationGraph({
  selectedMemory,
  relations,
  onSelect,
  emptyLabel,
  text,
}: {
  selectedMemory: HCMSMemoryView | null;
  relations: HCMSRelationView[];
  onSelect(memoryId: string): void;
  emptyLabel: string;
  text: HCMSPanelText;
}) {
  if (!selectedMemory) {
    return <OpsEmptyState text={emptyLabel} />;
  }
  const neighbors: AtlasNeighbor[] = relations
    .map((relation) => {
      const outbound = relation.source_memory_id === selectedMemory.memory_id;
      const memory = outbound ? relation.target_memory : relation.source_memory;
      const memoryId = outbound ? relation.target_memory_id : relation.source_memory_id;
      return {
        relation,
        memory,
        memoryId,
        outbound,
      };
    })
    .filter((item) => item.memoryId !== selectedMemory.memory_id);
  const visibleNeighbors = neighbors.slice(0, 8);
  const strongest = neighbors.reduce<(typeof neighbors)[number] | null>(
    (current, item) => ((item.relation.weight ?? 0) > (current?.relation.weight ?? -1) ? item : current),
    null,
  );
  const outboundCount = neighbors.filter((item) => item.outbound).length;
  const inboundCount = neighbors.length - outboundCount;
  const relationTypes = Array.from(new Set(neighbors.map((item) => item.relation.relation_type))).slice(0, 4);

  return (
    <div className="overflow-hidden rounded-lg border border-[var(--line)] bg-[var(--panel-muted)]">
      <div className="flex flex-col gap-3 border-b border-[var(--line)] bg-[var(--panel)] px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-[var(--ink)]">{text.graphNeighborhood}</div>
          <div className="mt-1 truncate text-xs text-[var(--muted)]">{selectedMemory.memory_id}</div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge tone="neutral">{neighbors.length} {text.linksLabel}</Badge>
          <Badge tone="neutral">{outboundCount} {text.out}</Badge>
          <Badge tone="neutral">{inboundCount} {text.in}</Badge>
          {strongest ? <Badge tone={toneForScore(strongest.relation.weight)}>{text.top} {score(strongest.relation.weight)}</Badge> : null}
        </div>
      </div>
      <div className="grid min-h-[340px] gap-4 p-4 lg:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
        <div className="flex min-h-[280px] items-center justify-center">
          <div className="relative flex size-[300px] items-center justify-center rounded-full border border-[var(--line)] bg-[var(--panel)] shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]">
            <svg className="pointer-events-none absolute inset-0 size-full" viewBox="0 0 300 300" aria-hidden="true">
              <circle cx="150" cy="150" r="58" fill="none" stroke="var(--line)" strokeDasharray="2 8" />
              <circle cx="150" cy="150" r="104" fill="none" stroke="var(--line)" strokeDasharray="4 9" />
              <circle cx="150" cy="150" r="132" fill="none" stroke="var(--line)" strokeDasharray="1 11" />
              {visibleNeighbors.map((item, index) => {
                const angle = (Math.PI * 2 * index) / Math.max(visibleNeighbors.length, 1) - Math.PI / 2;
                const radius = 116 + (index % 2) * 12;
                const x = 150 + Math.cos(angle) * radius;
                const y = 150 + Math.sin(angle) * radius;
                return (
                  <line
                    key={`line-${item.relation.relation_id}-${item.memoryId}`}
                    x1="150"
                    y1="150"
                    x2={x}
                    y2={y}
                    stroke={item.outbound ? "var(--primary)" : "var(--muted)"}
                    strokeOpacity={item.outbound ? 0.68 : 0.42}
                    strokeWidth={clamp(Math.round((item.relation.weight ?? 0.4) * 4), 1, 4)}
                  />
                );
              })}
            </svg>
            <button
              type="button"
              onClick={() => onSelect(selectedMemory.memory_id)}
              className="z-[1] flex size-36 flex-col items-center justify-center rounded-full border border-[color-mix(in_srgb,var(--primary)_36%,var(--line))] bg-[var(--accent-soft)] p-3 text-center shadow-[var(--shadow-card)] transition-transform active:translate-y-px"
            >
              <span className="line-clamp-2 text-xs font-semibold text-[var(--ink)]">{selectedMemory.summary || selectedMemory.memory_id}</span>
              <span className="mt-1 font-mono text-[10px] text-[var(--muted)]">{pct(selectedMemory.confidence)}</span>
              <span className="mt-1 rounded-full border border-[var(--line)] px-2 py-0.5 text-[10px] text-[var(--muted)]">{selectedMemory.category}</span>
            </button>
            {visibleNeighbors.map((item, index) => {
              const angle = (Math.PI * 2 * index) / Math.max(visibleNeighbors.length, 1) - Math.PI / 2;
              const radius = 116 + (index % 2) * 12;
              const x = Math.cos(angle) * radius;
              const y = Math.sin(angle) * radius;
              const size = clamp(54 + (item.relation.weight ?? 0.4) * 24, 54, 76);
              return (
                <button
                  key={`${item.relation.relation_id}-${item.memoryId}`}
                  type="button"
                  onClick={() => onSelect(item.memoryId)}
                  className="absolute left-1/2 top-1/2 flex flex-col items-center justify-center rounded-full border border-[var(--line)] bg-[var(--panel-muted)] text-[10px] font-semibold text-[var(--ink)] shadow-[var(--shadow-card)] transition-[border-color,background] hover:border-[color-mix(in_srgb,var(--primary)_38%,var(--line))] hover:bg-[var(--panel)] active:opacity-90"
                  style={{ width: `${size}px`, height: `${size}px`, transform: `translate(calc(-50% + ${x}px), calc(-50% + ${y}px))` }}
                  title={item.memory?.summary || item.memoryId}
                  aria-label={`${text.relatedMemoryAria} ${item.memoryId}`}
                >
                  <span>{shortMemoryLabel(item.memoryId)}</span>
                  <span className="font-mono text-[9px] text-[var(--muted)]">{item.outbound ? text.out : text.in}</span>
                </button>
              );
            })}
          </div>
        </div>
        <div className="min-w-0 space-y-2">
          <div className="grid gap-2 sm:grid-cols-3">
            <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2">
              <div className="text-[10px] uppercase tracking-[0.06em] text-[var(--muted)]">{text.density}</div>
              <div className="mt-1 font-mono text-sm font-semibold text-[var(--ink)]">{score(neighbors.length / Math.max(1, visibleNeighbors.length || 1))}</div>
            </div>
            <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2">
              <div className="text-[10px] uppercase tracking-[0.06em] text-[var(--muted)]">{text.direction}</div>
              <div className="mt-1 font-mono text-sm font-semibold text-[var(--ink)]">{outboundCount}/{inboundCount}</div>
            </div>
            <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2">
              <div className="text-[10px] uppercase tracking-[0.06em] text-[var(--muted)]">{text.relationTypes}</div>
              <div className="mt-1 font-mono text-sm font-semibold text-[var(--ink)]">{relationTypes.length}</div>
            </div>
          </div>
          <div className="flex flex-wrap gap-2 pt-1">
            {relationTypes.map((type) => (
              <Badge key={type} tone="neutral">{type}</Badge>
            ))}
          </div>
          {!neighbors.length ? <OpsEmptyState text={emptyLabel} /> : null}
          {neighbors.slice(0, 6).map((item) => (
            <button
              key={item.relation.relation_id}
              type="button"
              onClick={() => onSelect(item.memoryId)}
              className="block w-full rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-3 text-left transition-[border-color,background,transform] hover:border-[color-mix(in_srgb,var(--primary)_28%,var(--line))] active:translate-y-px"
            >
              <div className="flex min-w-0 items-center justify-between gap-2">
                <span className="truncate text-xs font-semibold text-[var(--ink)]">{item.relation.relation_type}</span>
                <span className="font-mono text-xs text-[var(--muted)]">{item.outbound ? text.out : text.in} {score(item.relation.weight)}</span>
              </div>
              <div className="mt-1 line-clamp-2 text-sm leading-5 text-[var(--muted)]">
                {oneLine(item.memory?.summary || item.memory?.content, item.memoryId)}
              </div>
              <div className="mt-2 flex flex-wrap gap-2">
                <Badge tone={toneForScore(item.relation.confidence)}>{text.confidenceLabel} {pct(item.relation.confidence)}</Badge>
                <Badge tone="neutral">{item.memory?.category ?? text.memoryFallback}</Badge>
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function sectionLabel(section: HCMSSection, text: HCMSPanelText) {
  switch (section) {
    case "atlas":
      return text.atlasSection;
    case "causal":
      return text.causalSection;
    case "relations":
      return text.relationsSection;
    case "versions":
      return text.versionsSection;
    case "evidence":
      return text.evidenceSection;
    case "recall":
    default:
      return text.recallSection;
  }
}

export function MemoryGovernancePanel({ copy }: MemoryGovernancePanelProps) {
  const text = React.useMemo(() => panelText(copy), [copy]);
  const [query, setQuery] = React.useState("");
  const [atlasQuery, setAtlasQuery] = React.useState("");
  const [atlasState, setAtlasState] = React.useState("all");
  const [atlasCategory, setAtlasCategory] = React.useState("all");
  const [atlasSort, setAtlasSort] = React.useState<HCMSAtlasSort>("updated");
  const [activeSection, setActiveSection] = React.useState<HCMSSection>("atlas");
  const [selectedMemoryId, setSelectedMemoryId] = React.useState<string | null>(null);

  const overview = useMemoryOverview();
  const health = useMemoryHealth();
  const recall = useHCMSRecall();
  const why = useHCMSWhy();
  const trace = useMemoryTrace(null);
  const deleteMemory = useDeleteHCMSMemory();
  const governMemory = useGovernMemory();
  const hcmsMemories = useHCMSMemories({
    query: atlasQuery,
    state: atlasState,
    category: atlasCategory === "all" ? null : atlasCategory,
    layerId: "all",
    limit: 50,
    offset: 0,
  });
  const detail = useHCMSMemory(selectedMemoryId, { enabled: Boolean(selectedMemoryId) });
  const relations = useHCMSMemoryRelations(selectedMemoryId, { enabled: Boolean(selectedMemoryId) });
  const history = useHCMSMemoryHistory(selectedMemoryId, { enabled: Boolean(selectedMemoryId) });
  const diff = useHCMSMemoryDiff(selectedMemoryId, { enabled: Boolean(selectedMemoryId) });

  const items = React.useMemo(
    () => (recall.data?.items ?? []).filter((item) => item.memory_id !== deleteMemory.data?.memory_id),
    [deleteMemory.data?.memory_id, recall.data?.items],
  );
  const atlasMemories = React.useMemo(
    () => (hcmsMemories.data?.items ?? []).filter((memory) => memory.memory_id !== deleteMemory.data?.memory_id),
    [deleteMemory.data?.memory_id, hcmsMemories.data?.items],
  );
  const sortedAtlasMemories = React.useMemo(
    () => sortAtlasMemories(atlasMemories, atlasSort),
    [atlasMemories, atlasSort],
  );
  const categoryDistribution = React.useMemo(() => distribution(atlasMemories, "category"), [atlasMemories]);
  const stateDistribution = React.useMemo(() => distribution(atlasMemories, "state"), [atlasMemories]);
  const entityBuckets = React.useMemo(() => entityDistribution(atlasMemories), [atlasMemories]);
  const evidenceBands = React.useMemo(() => evidenceSpectrum(atlasMemories), [atlasMemories]);
  const atlasCategories = React.useMemo(() => {
    const labels = new Set(categoryDistribution.map((item) => item.label));
    if (atlasCategory !== "all") {
      labels.add(atlasCategory);
    }
    return ["all", ...Array.from(labels).sort((a, b) => a.localeCompare(b))];
  }, [atlasCategory, categoryDistribution]);
  const selectedItem = React.useMemo(
    () => items.find((item) => item.memory_id === selectedMemoryId) ?? items[0] ?? null,
    [items, selectedMemoryId],
  );
  const selectedAtlasMemory = React.useMemo(
    () => sortedAtlasMemories.find((memory) => memory.memory_id === selectedMemoryId) ?? sortedAtlasMemories[0] ?? null,
    [selectedMemoryId, sortedAtlasMemories],
  );
  const selectedMemory = detail.data?.memory ?? selectedMemoryFromResult(selectedItem) ?? selectedAtlasMemory;
  const selectedMemoryState = (selectedMemory?.state ?? "").toLowerCase();
  const lifecyclePending = deleteMemory.isPending || governMemory.isPending;
  const canArchiveSelected = Boolean(selectedMemoryId) && !["archived", "forgotten"].includes(selectedMemoryState);
  const canForgetSelected = Boolean(selectedMemoryId) && selectedMemoryState !== "forgotten";
  const canRestoreSelected = Boolean(selectedMemoryId) && ["archived", "forgotten"].includes(selectedMemoryState);
  const stores = health.data?.stores ?? [];
  const engineHealth = Object.entries(health.data?.engine_health ?? {});
  const relationItems = relations.data?.relations ?? [];
  const graphMemories = React.useMemo(
    () => mergeGraphMemories(atlasMemories, selectedMemory ?? null, relationItems),
    [atlasMemories, relationItems, selectedMemory],
  );
  const relationCountByMemoryId = React.useMemo(() => {
    const counts = new Map<string, number>();
    for (const relation of relationItems) {
      counts.set(relation.source_memory_id, (counts.get(relation.source_memory_id) ?? 0) + 1);
      counts.set(relation.target_memory_id, (counts.get(relation.target_memory_id) ?? 0) + 1);
    }
    return counts;
  }, [relationItems]);
  const notes = [
    ...(recall.data?.engine_notes ?? []),
    ...(why.data?.engine_notes ?? []),
    ...(detail.data?.engine_notes ?? []),
    ...(relations.data?.engine_notes ?? []),
  ];
  const causalPathCount = why.data?.paths?.length ?? 0;
  const hasQueryResponse = Boolean(recall.data || why.data);
  const hasQueryResults = items.length > 0 || causalPathCount > 0;

  React.useEffect(() => {
    const firstMemoryId = items[0]?.memory_id ?? sortedAtlasMemories[0]?.memory_id;
    if (!selectedMemoryId && firstMemoryId) {
      setSelectedMemoryId(firstMemoryId);
    }
  }, [items, selectedMemoryId, sortedAtlasMemories]);

  function runRecall() {
    const normalized = query.trim();
    if (!normalized) {
      return;
    }
    void recall.mutateAsync({ query: normalized, limit: 10 });
    void why.mutateAsync({ query: normalized, limit: 4 });
    void trace.mutateAsync({ targetId: selectedMemoryId, limit: 12 });
  }

  function refreshAll() {
    void overview.refetch();
    void health.refetch();
    void hcmsMemories.refetch();
    void trace.mutateAsync({ targetId: selectedMemoryId, limit: 12 });
    if (query.trim()) {
      runRecall();
    }
  }

  async function deleteSelectedMemory() {
    if (!selectedMemoryId || lifecyclePending) {
      return;
    }
    const deletedId = selectedMemoryId;
    await deleteMemory.mutateAsync(deletedId);
    setSelectedMemoryId((current) => (current === deletedId ? null : current));
    void overview.refetch();
    void health.refetch();
    void hcmsMemories.refetch();
  }

  async function governSelectedMemory(action: HCMSLifecycleAction) {
    if (!selectedMemoryId || lifecyclePending) {
      return;
    }
    await governMemory.mutateAsync({
      memoryId: selectedMemoryId,
      action,
      reason: `HCMS lifecycle ${action} from Memory Governance panel`,
    });
    void overview.refetch();
    void health.refetch();
    void hcmsMemories.refetch();
  }

  return (
    <ScrollArea className="h-full">
      <div className="space-y-4 p-4">
        <section className="rounded-xl border border-[var(--line)] bg-[var(--panel)] p-4 shadow-[var(--shadow-card)]">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div className="min-w-0">
              <div className="flex min-w-0 items-center gap-2">
                <BrainCircuitIcon className="size-5 shrink-0 text-[var(--primary)]" />
                <h2 className="truncate text-base font-semibold text-[var(--ink)]">{copy.memory.title}</h2>
              </div>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--muted)]">{copy.memory.description}</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button type="button" size="sm" variant="secondary" onClick={refreshAll} disabled={overview.isFetching || health.isFetching || recall.isPending || why.isPending}>
                <RefreshCwIcon className={cn("size-4", overview.isFetching || health.isFetching ? "animate-spin" : "")} />
                {copy.memory.refresh}
              </Button>
            </div>
          </div>

          <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
            <CompactMetric label={text.runtime} value={overview.data?.runtime_mode ?? "hcms"} sub={overview.data?.active_engine_id ?? "hcms"} />
            <CompactMetric label={copy.memory.health} value={health.data?.status ?? copy.common.loading} />
            <CompactMetric label={copy.memory.qualityScore} value={pct(health.data?.quality_score)} />
            <CompactMetric label={text.memories} value={String(hcmsMemories.data?.total ?? atlasMemories.length)} sub={`${atlasMemories.length} ${text.shownLabel}`} />
            <CompactMetric label={text.recallHits} value={String(items.length)} sub={recall.data?.metrics ? `${recall.data.metrics.last_latency_ms ?? 0}ms` : null} />
            <CompactMetric label={text.causalPaths} value={String(why.data?.paths?.length ?? 0)} />
          </div>

          <div className="mt-4 flex flex-col gap-2 lg:flex-row">
            <label className="min-w-0 flex-1">
              <span className="sr-only">HCMS query</span>
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    runRecall();
                  }
                }}
                className="h-10 w-full rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 text-sm text-[var(--ink)] outline-none transition focus:border-[var(--primary)] focus:ring-2 focus:ring-[var(--ring)]"
                placeholder={text.queryPlaceholder}
              />
            </label>
            <Button type="button" variant="primary" disabled={!query.trim() || recall.isPending || why.isPending} onClick={runRecall}>
              <SearchIcon className={cn("size-4", recall.isPending || why.isPending ? "animate-spin" : "")} />
              {text.recallAction}
            </Button>
          </div>

          {hasQueryResponse ? (
            <div className={cn(
              "mt-3 rounded-lg border px-3 py-3",
              hasQueryResults
                ? "border-[var(--success)]/25 bg-[color-mix(in_srgb,var(--success)_10%,transparent)]"
                : "border-[var(--warning)]/25 bg-[var(--warning-soft)]",
            )}>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <div className="text-xs font-semibold uppercase tracking-[0.06em] text-[var(--muted)]">{text.queryResultTitle}</div>
                  <div className="mt-1 text-sm font-semibold text-[var(--ink)]">
                    {hasQueryResults
                      ? `${items.length} ${text.recallHits} · ${causalPathCount} ${text.causalPaths}`
                      : text.recallEmptyTitle}
                  </div>
                </div>
                <Badge tone={hasQueryResults ? "success" : "warning"}>
                  {recall.data?.metrics ? `${recall.data.metrics.last_latency_ms ?? 0}ms` : text.runtime}
                </Badge>
              </div>
              <p className="mt-2 text-sm leading-6 text-[var(--muted)]">
                {hasQueryResults ? text.recallResultBody : text.recallEmptyBody}
              </p>
              {notes.length ? (
                <div className="mt-3 flex flex-wrap gap-2">
                  <Badge tone="neutral">
                    <SparklesIcon className="size-3" />
                    {text.engineNotesLabel}
                  </Badge>
                  {notes.slice(0, 3).map((note) => (
                    <Badge key={note} tone="neutral">{note}</Badge>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}

          <div className="mt-4 flex flex-wrap gap-2">
            <Badge tone={engineHealth.length ? "success" : "neutral"}>
              <ShieldCheckIcon className="size-3" />
              {text.engines} {engineHealth.length || 1}
            </Badge>
            <Badge tone={(health.data?.issues?.length ?? 0) > 0 ? "warning" : "success"}>
              <AlertTriangleIcon className="size-3" />
              {text.issues} {health.data?.issues?.length ?? 0}
            </Badge>
            <Badge tone="neutral">
              <DatabaseIcon className="size-3" />
              {text.stores} {overview.data?.store_count ?? stores.length}
            </Badge>
          </div>

          <div className="mt-4 flex flex-wrap gap-2">
            {HCMS_SECTIONS.map((section) => (
              <Button
                key={section}
                type="button"
                size="sm"
                variant={activeSection === section ? "primary" : "secondary"}
                onClick={() => setActiveSection(section)}
              >
                {section === "atlas" ? <NetworkIcon className="size-4" /> : section === "causal" ? <BrainCircuitIcon className="size-4" /> : section === "relations" ? <Link2Icon className="size-4" /> : section === "versions" ? <HistoryIcon className="size-4" /> : section === "evidence" ? <ActivityIcon className="size-4" /> : <SearchIcon className="size-4" />}
                {sectionLabel(section, text)}
              </Button>
            ))}
          </div>
        </section>

        <StoreHealthStrip stores={stores} text={text} />

        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.25fr)_minmax(320px,0.75fr)]">
          <div className="space-y-4">
            {activeSection === "atlas" ? (
              <OpsPanelCard title={text.memoryAtlas}>
                <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_150px_180px_160px]">
                  <label className="min-w-0">
                    <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.06em] text-[var(--muted)]">{text.filter}</span>
                    <input
                      value={atlasQuery}
                      onChange={(event) => setAtlasQuery(event.target.value)}
                      className="h-10 w-full rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 text-sm text-[var(--ink)] outline-none transition focus:border-[var(--primary)] focus:ring-2 focus:ring-[var(--ring)]"
                      placeholder={text.atlasFilterPlaceholder}
                    />
                  </label>
                  <label className="min-w-0">
                    <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.06em] text-[var(--muted)]">{text.state}</span>
                    <select
                      value={atlasState}
                      onChange={(event) => setAtlasState(event.target.value)}
                      className="h-10 w-full rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 text-sm text-[var(--ink)] outline-none transition focus:border-[var(--primary)] focus:ring-2 focus:ring-[var(--ring)]"
                    >
                      {HCMS_MEMORY_STATES.map((state) => (
                        <option key={state} value={state}>{text.stateLabels[state] ?? state}</option>
                      ))}
                    </select>
                  </label>
                  <label className="min-w-0">
                    <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.06em] text-[var(--muted)]">{text.category}</span>
                    <select
                      value={atlasCategory}
                      onChange={(event) => setAtlasCategory(event.target.value)}
                      className="h-10 w-full rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 text-sm text-[var(--ink)] outline-none transition focus:border-[var(--primary)] focus:ring-2 focus:ring-[var(--ring)]"
                    >
                      {atlasCategories.map((category) => (
                        <option key={category} value={category}>{category === "all" ? text.categoryAll : category}</option>
                      ))}
                    </select>
                  </label>
                  <label className="min-w-0">
                    <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.06em] text-[var(--muted)]">{text.sort}</span>
                    <select
                      value={atlasSort}
                      onChange={(event) => setAtlasSort(event.target.value as HCMSAtlasSort)}
                      className="h-10 w-full rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 text-sm text-[var(--ink)] outline-none transition focus:border-[var(--primary)] focus:ring-2 focus:ring-[var(--ring)]"
                    >
                      {HCMS_ATLAS_SORTS.map((sort) => (
                        <option key={sort} value={sort}>{text.sortLabels[sort]}</option>
                      ))}
                    </select>
                  </label>
                </div>

                {!atlasMemories.length ? (
                  <div className="mt-4 rounded-xl border border-dashed border-[var(--line)] bg-[var(--panel-muted)] px-4 py-5">
                    <div className="text-sm font-semibold text-[var(--ink)]">{text.atlasEmptyTitle}</div>
                    <div className="mt-2 text-sm leading-6 text-[var(--muted)]">{text.atlasEmptyBody}</div>
                  </div>
                ) : (
                  <>
                    <div className="mt-4">
                      <AtlasTopologySummary memories={atlasMemories} relations={relationItems} text={text} />
                    </div>

                    <div className="mt-4">
                      <AtlasGraphView
                        memories={graphMemories}
                        relations={relationItems}
                        selectedMemoryId={selectedMemoryId}
                        activeCategory={atlasCategory === "all" ? undefined : atlasCategory}
                        emptyLabel={copy.common.none}
                        onSelectMemory={setSelectedMemoryId}
                        onSelectCategory={(category) => setAtlasCategory((current) => (current === category ? "all" : category))}
                        text={text}
                      />
                    </div>

                    <div className="mt-4">
                      <CategoryConstellation
                        items={categoryDistribution}
                        emptyLabel={copy.common.none}
                        activeLabel={atlasCategory === "all" ? undefined : atlasCategory}
                        onSelect={(label) => setAtlasCategory((current) => (label === "all" || current === label ? "all" : label))}
                        text={text}
                      />
                    </div>

                    <div className="mt-4 grid gap-3 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
                      <DistributionBars
                        title={text.categoryDistribution}
                        items={categoryDistribution}
                        emptyLabel={copy.common.none}
                        activeLabel={atlasCategory === "all" ? undefined : atlasCategory}
                        onSelect={(label) => setAtlasCategory((current) => (current === label ? "all" : label))}
                        text={text}
                      />
                      <DistributionBars
                        title={text.lifecycleDistribution}
                        items={stateDistribution}
                        emptyLabel={copy.common.none}
                        activeLabel={atlasState === "all" ? undefined : atlasState}
                        onSelect={(label) => setAtlasState((current) => (current === label ? "all" : label))}
                        text={text}
                      />
                    </div>

                    <div className="mt-4">
                      <EvidenceSpectrum
                        items={evidenceBands}
                        activeLabel={atlasCategory === "all" ? undefined : atlasCategory}
                        emptyLabel={copy.common.none}
                        onSelect={(label) => setAtlasCategory((current) => (current === label ? "all" : label))}
                        text={text}
                      />
                    </div>

                    <div className="mt-4">
                      <EntityLens
                        items={entityBuckets}
                        emptyLabel={copy.common.none}
                        onSelectMemory={setSelectedMemoryId}
                        text={text}
                      />
                    </div>

                    <div className="mt-4">
                      <RelationGraph
                        selectedMemory={selectedMemory}
                        relations={relationItems}
                        onSelect={setSelectedMemoryId}
                        emptyLabel={copy.common.none}
                        text={text}
                      />
                    </div>
                  </>
                )}

                <div className="mt-4 space-y-2">
                  {hcmsMemories.isFetching ? (
                    <div className="grid gap-2">
                      <div className="h-20 rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] opacity-70" />
                      <div className="h-20 rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] opacity-50" />
                    </div>
                  ) : null}
                  {!sortedAtlasMemories.length && atlasMemories.length > 0 && !hcmsMemories.isFetching ? <OpsEmptyState text={copy.common.none} /> : null}
                  <div className="grid gap-3 lg:grid-cols-2">
                    {sortedAtlasMemories.map((memory) => (
                      <MemoryAtlasCard
                        key={memory.memory_id}
                        memory={memory}
                        active={memory.memory_id === selectedMemoryId}
                        relationCount={relationCountByMemoryId.get(memory.memory_id) ?? 0}
                        onSelect={() => setSelectedMemoryId(memory.memory_id)}
                        text={text}
                      />
                    ))}
                  </div>
                </div>
              </OpsPanelCard>
            ) : null}

            {activeSection === "recall" ? (
              <OpsPanelCard title={text.fourStreamRecall}>
                {!items.length ? <OpsEmptyState text={text.recallEmptyTitle} /> : null}
                <div className="space-y-2">
                  {items.map((item) => (
                    <RecallCard
                      key={item.memory_id}
                      item={item}
                      active={item.memory_id === selectedMemoryId}
                      onSelect={() => setSelectedMemoryId(item.memory_id)}
                      text={text}
                    />
                  ))}
                </div>
              </OpsPanelCard>
            ) : null}

            {activeSection === "causal" ? (
              <OpsPanelCard title={text.causalChains}>
                {!(why.data?.paths ?? []).length ? <OpsEmptyState text={copy.common.none} /> : null}
                <div className="space-y-2">
                  {(why.data?.paths ?? []).map((path, index) => (
                    <CausalPathCard key={`${path.nodes.map((node) => node.memory_id).join("-")}-${index}`} path={path} text={text} />
                  ))}
                </div>
              </OpsPanelCard>
            ) : null}

            {activeSection === "relations" ? (
              <OpsPanelCard title={text.relationGraph}>
                <RelationRows
                  relations={relationItems}
                  selectedMemoryId={selectedMemoryId}
                  emptyLabel={copy.common.none}
                  text={text}
                />
              </OpsPanelCard>
            ) : null}

            {activeSection === "versions" ? (
              <OpsPanelCard title={text.versionHistory}>
                <VersionTimeline versions={history.data?.versions ?? []} emptyLabel={copy.common.none} text={text} />
              </OpsPanelCard>
            ) : null}

            {activeSection === "evidence" ? (
              <OpsPanelCard title={text.traceEvidence}>
                <TraceRows traces={trace.data?.items ?? []} emptyLabel={copy.common.none} text={text} />
              </OpsPanelCard>
            ) : null}
          </div>

          <div className="space-y-4">
            <OpsPanelCard title={text.selectedMemory}>
              <div className="mb-3 space-y-3">
                <div className="min-w-0 text-xs uppercase tracking-[0.06em] text-[var(--muted)]">{text.lifecycle}</div>
                <div className="grid grid-cols-2 gap-2 xl:grid-cols-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    className="min-w-0 overflow-hidden px-2 [&>svg]:shrink-0"
                    disabled={!canArchiveSelected || lifecyclePending}
                    onClick={() => void governSelectedMemory("archive")}
                  >
                    <ArchiveIcon className={cn("size-4", governMemory.isPending ? "animate-pulse" : "")} />
                    <span className="truncate">{text.archive}</span>
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    className="min-w-0 overflow-hidden px-2 [&>svg]:shrink-0"
                    disabled={!canRestoreSelected || lifecyclePending}
                    onClick={() => void governSelectedMemory("restore")}
                  >
                    <RotateCcwIcon className={cn("size-4", governMemory.isPending ? "animate-pulse" : "")} />
                    <span className="truncate">{text.restore}</span>
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    className="min-w-0 overflow-hidden px-2 [&>svg]:shrink-0"
                    disabled={!canForgetSelected || lifecyclePending}
                    onClick={() => void governSelectedMemory("forget")}
                  >
                    <EyeOffIcon className={cn("size-4", governMemory.isPending ? "animate-pulse" : "")} />
                    <span className="truncate">{text.forget}</span>
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="danger"
                    className="min-w-0 overflow-hidden px-2 [&>svg]:shrink-0"
                    disabled={!selectedMemoryId || lifecyclePending}
                    onClick={deleteSelectedMemory}
                  >
                    <Trash2Icon className={cn("size-4", deleteMemory.isPending ? "animate-pulse" : "")} />
                    <span className="truncate">{text.delete}</span>
                  </Button>
                </div>
              </div>
              <MemoryDetail memory={selectedMemory} emptyLabel={copy.common.none} text={text} />
            </OpsPanelCard>

            {activeSection === "versions" ? (
              <OpsPanelCard title={text.latestDiff}>
                <OpsJsonBlock value={diff.data?.diff ?? ""} emptyLabel={copy.common.none} />
              </OpsPanelCard>
            ) : null}

            {activeSection !== "versions" ? (
              <OpsPanelCard title={text.rawScores}>
                <OpsJsonBlock value={selectedItem?.raw_scores ?? null} emptyLabel={copy.common.none} />
              </OpsPanelCard>
            ) : null}
          </div>
        </div>
      </div>
    </ScrollArea>
  );
}
