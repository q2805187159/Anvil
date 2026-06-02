"use client";

import React, { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  AnvilIcon,
  ArrowUpIcon,
  BrainCircuitIcon,
  CheckIcon,
  CircleIcon,
  CopyIcon,
  CornerDownRightIcon,
  ChevronDownIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  ChevronsUpDownIcon,
  EllipsisIcon,
  ExternalLinkIcon,
  FileTextIcon,
  HardDriveUploadIcon,
  KeyboardIcon,
  Loader2Icon,
  MenuIcon,
  MessageSquarePlusIcon,
  MoonStarIcon,
  PanelLeftCloseIcon,
  PanelLeftOpenIcon,
  PaperclipIcon,
  PencilIcon,
  PlusIcon,
  PinIcon,
  RotateCcwIcon,
  SearchIcon,
  SettingsIcon,
  ShieldAlertIcon,
  SquareIcon,
  TerminalIcon,
  Trash2Icon,
  SunMediumIcon,
  WrenchIcon,
  XIcon,
} from "lucide-react";
import { useTheme } from "next-themes";

import type {
  ApprovalView,
  ArtifactRefView,
  CapabilityAssemblyDiagnosticsView,
  CompactionDiagnosticsView,
  ContextCacheDiagnosticsView,
  ContextWindowUsageView,
  ExecutionMode,
  MessageWindowView,
  MemoryConflictView,
  MemoryEntryView,
  MemoryInjectionDiagnosticsView,
  MemoryLayerId,
  MemoryReviewItemView,
  MemoryStalenessEntryView,
  MessageView,
  ProcessLogView,
  ProcessSessionView,
  PromptSectionTokenLedgerView,
  PromptCacheDiagnosticsView,
  RuntimePhaseTimingsView,
  RuntimeOperatorStatusView,
  TerminalBackendCapabilitiesView,
  TokenUsageSummaryView,
  RunStreamEvent,
  SessionMemoryView,
  SessionSearchResultView,
  SubagentToolEvidenceView,
  SubagentTaskView,
  ThreadView,
  ThreadStateView,
  ToolActivityView,
  ToolCallView,
  UploadItemView,
  QueuedFollowUpView,
  QueuedFollowUpCreateRequest,
  RunRequestBody,
  UserInteractionRequestView,
  UserInteractionResumeRequest,
} from "@/src/core/contracts";
import { resolveGatewayUrl } from "@/src/core/api/client";
import { useExtensions } from "@/src/core/extensions/hooks";
import { useI18n, type Locale } from "@/src/core/i18n";
import {
  useActivateMemoryProvider,
  useBatchMemoryReview,
  useCreateMemoryLayerEntry,
  useDeleteMemoryLayerEntry,
  useApproveMemoryReview,
  useExportMemoryAdmin,
  useFlushMemory,
  useImportMemoryAdmin,
  useMemoryAdminAudit,
  useMemoryConflicts,
  useMemoryLayers,
  useMemoryOverview,
  useMemoryProviders,
  useMemoryReview,
  useMemoryStaleness,
  useMemoryTrace,
  useMemoryLayerEntries,
  usePauseReflectionJob,
  useReflectionJobs,
  useRemoveReflectionJob,
  useResumeReflectionJob,
  useRejectMemoryReview,
  useReloadMemoryProviders,
  useResolveMemoryConflict,
  useRunReflectionJob,
  useSessionMemory,
  useSessionSearch,
  useTestMemoryProvider,
  useUpdateMemoryLayerEntry,
} from "@/src/core/memory/hooks";
import { useModels } from "@/src/core/models/hooks";
import { usePlugins } from "@/src/core/plugins/hooks";
import { useSkills } from "@/src/core/skills/hooks";
import { useGatewayHealth } from "@/src/core/system/hooks";
import { useMessageReducer, type StepTranscriptMessage } from "@/src/core/threads/message-reducer";
import { THREAD_DETAIL_MESSAGE_WINDOW_PAGE_SIZE, useCancelSubagentTask, useCancelThreadApproval, useCloseProcessStdin, useCreateThread, useDeleteThread, useDeleteThreadFollowup, useEnqueueThreadFollowup, useInterruptProcessSession, useKillProcessSession, usePopNextThreadFollowup, useProcessCapabilities, useProcessLog, useResizeProcessSession, useThreadDetail, useThreadMessageWindowLoader, useThreadRunStream, useThreadSettings, useThreads, useThreadState, useUpdateThreadFollowup, useUpdateThreadSettings, useWaitProcessSession, useWaitSubagentTask, useWriteProcessStdin } from "@/src/core/threads/hooks";
import { formatThreadActivityAge, sortThreadsByRecency, threadActivityAt } from "@/src/core/threads/recency";
import { useUploadFiles, useUploads } from "@/src/core/uploads/hooks";
import { cn } from "@/src/lib/utils";
import {
  Badge,
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  Input,
  NativeSelect,
  Textarea,
  SectionCard,
  InfoRow,
  EmptyPanelText,
  PanelHeader,
  DataCard,
} from "@/src/components/ui";

import { ArtifactPreviewPanel } from "./artifact-preview-panel";
import { ModelPicker } from "./model-picker";
import { ToolBlock } from "./transcript/tool-block";
import { ArtifactRefList } from "./transcript/common";
import { StepChainMessage, UserStepMessage } from "./transcript/step-chain-message";
import { buildTranscriptTurns, type TranscriptTurn } from "./transcript-turns";
import { WorkspaceRichContent } from "./workspace-rich-content";
import { OpsConsole } from "./ops/ops-console";
import type { OpsUrlState } from "./ops/types";
import { opsCopy } from "./ops/types";
import { DEFAULT_OPS_URL_STATE, applyOpsStateToSearch, mergeOpsUrlState, parseOpsUrlState } from "./ops/url-state";

type WorkspaceShellProps = {
  initialThreadId?: string | null;
};

type OptimisticTurn = {
  threadId: string;
  clientMessageId: string;
  message: string;
  artifactRefs: ArtifactRefView[];
};

type ComposerQueuedAttachment = {
  filename: string;
  artifactRef: ArtifactRefView;
};

type ComposerDraft = {
  message: string;
  selectedFiles: File[];
  queuedAttachments: ComposerQueuedAttachment[];
};

type UserInteractionSubmitDraft = {
  selectedOptionIds: string[];
  customResponse?: string | null;
  freeText?: string | null;
  fieldResponses?: Array<{
    fieldId: string;
    selectedOptionIds: string[];
    customResponse?: string | null;
    freeText?: string | null;
  }>;
};

type NormalizedInteractionField = {
  field_id: string;
  label: string;
  description?: string | null;
  selection_mode: "single" | "multiple" | "text";
  options: UserInteractionRequestView["options"];
  min_selections: number;
  max_selections?: number | null;
  allow_custom: boolean;
  custom_label?: string | null;
  placeholder?: string | null;
  required: boolean;
};

type UserInteractionFieldDraft = {
  selectedOptionIds: string[];
  customResponse: string;
  freeText: string;
};

type DrawerSection = "timeline" | "recent_tools" | "approvals" | "files" | "memory" | "skills" | "subagents" | "processes" | "settings" | "ops";

function HoverRevealText({
  value,
  className,
  tooltipClassName,
  testId,
  showTooltip = true,
}: {
  value: string;
  className?: string;
  tooltipClassName?: string;
  testId?: string;
  showTooltip?: boolean;
}) {
  const tooltipId = useId();
  return (
    <span className="group/hover-reveal relative block min-w-0 max-w-full">
      <span
        data-testid={testId}
        className={cn("block min-w-0 max-w-full truncate", className)}
        title={showTooltip ? value : undefined}
        aria-describedby={showTooltip ? tooltipId : undefined}
      >
        {value}
      </span>
      {showTooltip ? (
        <span
          id={tooltipId}
          role="tooltip"
          className={cn(
            "pointer-events-none invisible absolute bottom-[calc(100%+0.35rem)] left-0 z-50 block max-w-[min(30rem,86vw)] whitespace-normal break-words rounded-lg border border-[var(--line)] bg-[var(--panel-strong)] px-3 py-2 text-xs leading-5 text-[var(--ink)] opacity-0 shadow-[var(--panel-shadow)] transition-opacity group-hover/hover-reveal:visible group-hover/hover-reveal:opacity-100",
            tooltipClassName,
          )}
        >
          {value}
        </span>
      ) : null}
    </span>
  );
}

function useDismissablePopup<T extends HTMLElement>(
  open: boolean,
  containerRef: React.RefObject<T | null>,
  onDismiss: () => void,
) {
  const onDismissRef = useRef(onDismiss);

  useEffect(() => {
    onDismissRef.current = onDismiss;
  }, [onDismiss]);

  useEffect(() => {
    if (!open) {
      return;
    }
    function handlePointerDown(event: PointerEvent) {
      const target = event.target;
      if (target instanceof Node && containerRef.current?.contains(target)) {
        return;
      }
      onDismissRef.current();
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onDismissRef.current();
      }
    }
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [containerRef, open]);
}

const DRAWER_SECTIONS: DrawerSection[] = [
  "timeline",
  "recent_tools",
  "approvals",
  "files",
  "memory",
  "skills",
  "subagents",
  "processes",
  "settings",
  "ops",
];

const EXECUTION_MODES: Array<{ id: ExecutionMode; label: string; summary: string }> = [
  { id: "chat", label: "Chat", summary: "Safe chat mode" },
  { id: "agent", label: "Agent", summary: "Guided local action" },
  { id: "full_access", label: "Full Access", summary: "High-trust automation" },
];

const LEFT_COLLAPSED_KEY = "forge.chat.left-collapsed";
const DRAWER_PINNED_KEY = "forge.chat.drawer-pinned";
const PINNED_THREADS_KEY = "forge.chat.pinned-thread-ids";
const RENAMED_THREADS_KEY = "forge.chat.thread-title-overrides";
const DRAFT_SESSION_KEY = "__draft__";
const THREAD_DETAIL_WINDOW_LIMIT = THREAD_DETAIL_MESSAGE_WINDOW_PAGE_SIZE;
const FULL_THREAD_STATE_DRAWER_SECTIONS = new Set<DrawerSection>([
  "recent_tools",
  "approvals",
  "subagents",
  "processes",
  "files",
]);

type ThreadMessageWindowCache = {
  messages: MessageView[];
  window: MessageWindowView | null;
};

type ContextWindowUsageDisplay = ContextWindowUsageView & {
  cache_creation_tokens?: number | null;
  cache_read_tokens?: number | null;
  cache_write_tokens?: number | null;
  cache_hit_ratio?: number | null;
  cache_savings_tokens?: number | null;
  compaction_level?: number | null;
  compaction_level_label?: string | null;
  compaction_reason?: string | null;
  compaction_input_tokens?: number | null;
  compaction_summary_tokens?: number | null;
  compaction_savings_tokens?: number | null;
  compaction_keep_recent_turns?: number | null;
  last_input_tokens?: number | null;
  last_output_tokens?: number | null;
  last_total_tokens?: number | null;
  last_cache_read_tokens?: number | null;
  last_cache_write_tokens?: number | null;
};

type PromptCacheDiagnosticsDisplay = PromptCacheDiagnosticsView;
type PromptSectionTokenLedgerDisplay = PromptSectionTokenLedgerView;
type ContextCacheDiagnosticsDisplay = ContextCacheDiagnosticsView;
type CapabilityAssemblyDiagnosticsDisplay = CapabilityAssemblyDiagnosticsView;
type MemoryInjectionDiagnosticsDisplay = MemoryInjectionDiagnosticsView;
type CompactionDiagnosticsDisplay = CompactionDiagnosticsView;

type PromptSectionTokenLedgerSummary = {
  stableTotal: number | null;
  volatileTotal: number | null;
  stableSections: Array<{ name: string; tokens: number }>;
  volatileSections: Array<{ name: string; tokens: number }>;
  hasLedger: boolean;
};

type CapabilityAssemblyDiagnosticsSummary = {
  visibleToolCount: number | null;
  deferredToolCount: number | null;
  visibleSchemaTokens: number | null;
  deferredSchemaTokens: number | null;
  totalSchemaTokens: number | null;
  visibleSchemaTokenBudget: number | null;
  visibleSchemaBudgetRemainingTokens: number | null;
  schemaCompactedToolCount: number | null;
  schemaDeferredToolCount: number | null;
  actionPrefilterDeferredToolCount: number | null;
  sanitizerTruncatedToolCount: number | null;
  slowestAssemblyStage: string | null;
  slowestAssemblyStageDurationMs: number | null;
  topAssemblyStages: Array<{ name: string; count: number }>;
  skillsDiscoveryCacheHit: boolean | null;
  skillsDiscoveryWatchEnabled: boolean | null;
  skillsDiscoveryRootCount: number | null;
  skillsDiscoveryManifestCount: number | null;
  skillsDiscoveryEnabledCount: number | null;
  skillsDiscoveryPackageCount: number | null;
  slowestSkillsDiscoveryStage: string | null;
  slowestSkillsDiscoveryStageDurationMs: number | null;
  topSkillsDiscoveryStages: Array<{ name: string; count: number }>;
  topVisibleSources: Array<{ name: string; count: number }>;
  topDeferredSources: Array<{ name: string; count: number }>;
  topVisibleGroups: Array<{ name: string; count: number }>;
  topDeferredGroups: Array<{ name: string; count: number }>;
  hasDiagnostics: boolean;
};

type ContextCacheDiagnosticsSummary = {
  projectStatus: string | null;
  projectFingerprint: string | null;
  projectFileCount: number | null;
  projectTruncatedFileCount: number | null;
  projectTotalChars: number | null;
  projectDiscoveryScannedPathCount: number | null;
  projectDiscoveryMaxScannedPaths: number | null;
  projectDiscoveryScanTruncated: boolean;
  projectScopeCounts: Array<{ name: string; count: number }>;
  projectAppliesToCounts: Array<{ name: string; count: number }>;
  runtimeStatus: string | null;
  runtimeFingerprint: string | null;
  runtimeRootCount: number | null;
  runtimeHostBridgeCount: number | null;
  hasDiagnostics: boolean;
};

type MemoryInjectionDiagnosticsSummary = {
  source: string | null;
  status: string | null;
  snapshotId: string | null;
  queryTokens: number | null;
  curatedMatchCount: number | null;
  archiveHitCount: number | null;
  evidenceCount: number | null;
  providerNoteCount: number | null;
  renderedTokensBeforeTruncation: number | null;
  renderedTokens: number | null;
  tokenBudget: number | null;
  truncated: boolean;
  errorType: string | null;
  topStores: Array<{ name: string; count: number }>;
  topSourceKinds: Array<{ name: string; count: number }>;
  hasDiagnostics: boolean;
};

type CompactionDiagnosticsSummary = {
  summarySource: string | null;
  summaryModel: string | null;
  archivedMessageCount: number | null;
  toolCallCount: number | null;
  toolResultCount: number | null;
  imageBlockCount: number | null;
  truncatedMessageCount: number | null;
  prunedToolResultCount: number | null;
  serializedTokens: number | null;
  summaryPromptTokens: number | null;
  hasDiagnostics: boolean;
};

type ContextBreakdownItem = {
  key: string;
  label: string;
  tokens: number | null;
  percentage: number | null;
  color: string;
  group: "context" | "reserve";
};

type ContextUsageSummary = {
  contextTokens: number | null;
  thresholdTokens: number | null;
  windowTokens: number | null;
  providerTotalTokens: number | null;
  inputTokens: number | null;
  outputTokens: number | null;
  lastInputTokens: number | null;
  lastOutputTokens: number | null;
  lastTotalTokens: number | null;
  cacheReadTokens: number | null;
  cacheWriteTokens: number | null;
  lastCacheReadTokens: number | null;
  lastCacheWriteTokens: number | null;
  requestCount: number | null;
  dominantContextLabel: string | null;
  cacheHitRatio: number | null;
  cacheSavingsTokens: number | null;
  compactionLevel: number;
  compactionLevelLabel: string;
  compactionReason: string | null;
  compactionInputTokens: number | null;
  compactionSummaryTokens: number | null;
  compactionSavingsTokens: number | null;
  compactionKeepRecentTurns: number | null;
  costLabel: string | null;
  compactRatio: number | null;
  usageRatio: number | null;
  pressureRatio: number;
  usedPercentLabel: string | null;
  remainingPercentLabel: string | null;
  remainingTokens: number | null;
  contextSourceLabel: string | null;
  isEstimated: boolean;
  isCompacted: boolean;
  isApproaching: boolean;
  modelLabel: string;
  providerLabel: string | null;
  breakdownRows: ContextBreakdownItem[];
  claudeRows: ContextBreakdownItem[];
  hasBreakdown: boolean;
  hasProviderUsage: boolean;
  hasLastUsage: boolean;
};

function emptyComposerDraft(): ComposerDraft {
  return {
    message: "",
    selectedFiles: [],
    queuedAttachments: [],
  };
}

function workspaceCopy(locale: Locale) {
  if (locale === "zh-CN") {
    return {
      drawerSections: {
        timeline: "时间线",
        recent_tools: "最近工具",
        approvals: "审批",
        files: "文件",
        memory: "记忆",
        skills: "技能",
        subagents: "子代理",
        processes: "进程",
        settings: "设置",
        ops: "配置",
      },
      modeLabels: {
        chat: "聊天",
        agent: "代理",
        full_access: "完全访问",
      },
      modeSummaries: {
        chat: "安全聊天模式",
        agent: "引导式本地操作",
        full_access: "高信任自动化",
      },
      shell: {
        brand: "Anvil",
        newSessionTitle: "你在忙什么？",
        newSessionSubtitle: "输入一个目标，Anvil 会在当前工作区内执行、追踪并保留上下文。",
        createThread: "新会话",
        searchThreads: "搜索聊天",
        more: "更多",
        openConfigCenter: "配置中心",
        langsmith: "LangSmith",
        searchDialogTitle: "搜索聊天",
        statusOk: "正常",
        noActiveThread: "当前没有活动线程",
        openThreads: "打开线程列表",
        toggleTheme: "切换主题",
        openUtilities: "打开工具抽屉",
        closeUtilities: "关闭工具抽屉",
        mobileThreadsTitle: "线程",
        mobileThreadsDescription: "选择一个对话线程。",
        mobileUtilitiesDescription: "当前线程的操作面板。",
      },
      threadList: {
        approvals: "审批",
        tasks: "任务",
        actions: "线程操作",
        renameThread: "重命名",
        pinThread: "置顶",
        unpinThread: "取消置顶",
        pinned: "已置顶",
        delete: "删除",
        renamePrompt: "输入新的线程名称",
        noResults: "没有匹配的聊天",
        deleteThread: (label: string) => `删除 ${label}`,
      },
      transcript: {
        liveStream: "实时流",
        thinkingStreaming: "正在思考并流式输出…",
        userInteraction: "需要你的选择",
        userInteractionDetail: "Agent 已暂停，提交后会在同一线程继续执行。",
        contextUsage: "上下文",
        compactThreshold: "压缩阈值",
        toCompaction: "距自动压缩",
        contextWindow: "上下文窗口",
        contextTokens: "上下文",
        reportedUsage: "模型上报",
        contextEstimated: "估算",
        requestCount: "模型调用",
        estimatedCost: "估算费用",
        compacted: "上下文已自动压缩",
        compactedDetail: "较早消息已压缩为摘要，后续回复会继续使用保留上下文。",
        compactingSoon: "接近自动压缩阈值",
        compactingSoonDetail: "达到阈值后会自动压缩较早会话。",
      },
      composer: {
        attach: "附件",
        add: "添加",
        autoModel: "选择模型",
        executionMode: "执行模式",
        deepThinking: "深度思考",
        deepThinkingOn: "已开启",
        deepThinkingOff: "已关闭",
        imageAttachment: "图片附件",
        fileAttachment: "文件附件",
        previewImage: "预览图片",
        closePreview: "关闭预览",
        queuedFollowups: "后续变更",
        queuedDuringRun: "当前运行结束后自动发送",
        guide: "引导",
        guiding: "已设为引导",
        editQueued: "编辑",
        deleteQueued: "删除",
        moreQueuedActions: "更多队列操作",
        pastedFiles: "已粘贴附件",
        attachmentFromQueue: "队列附件",
        compactOptions: "选项",
        sending: "发送中…",
        stop: "停止",
        planMode: "计划模式",
        approveOnce: "是",
        approveSession: "是，且本命令不再询问",
        denyApproval: "否",
        manualApproval: "手动补充信息",
        submitInteraction: "确认并继续",
        interactionCustom: "其他",
        interactionFreeText: "补充说明",
        interactionRequired: "请选择或填写后继续",
      },
      drawer: {
        noThread: "没有线程",
        togglePinnedDrawer: "固定抽屉",
        noTimeline: "当前线程还没有时间线事件。",
        operatorStatus: "运行状态",
        runtimeDiagnostics: "运行诊断",
        runtimeStatus: "状态",
        runtimeTotalElapsed: "总耗时",
        runtimeFirstModel: "首个模型事件",
        runtimeFirstContent: "首个内容",
        runtimeCompleted: "完成耗时",
        runtimeSlowestPhase: "最慢阶段",
        runtimePhaseCount: (count: number) => `${count} 个阶段`,
        latestActivity: "最近活动",
        activeTools: "活动工具",
        completedTools: "完成工具",
        failedTools: "失败工具",
        pendingApprovals: "待审批",
        runningProcesses: "运行进程",
        activeSubagents: "活动子代理",
        noRecentTools: "当前线程还没有最近工具。",
        approvalNote: "审批备注",
        resumeApproval: "批准并继续",
        resumingApproval: "继续执行中…",
        cancelApproval: "取消请求",
        confirmPlan: "确认方案并继续",
        rejectPlan: "取消方案",
        planApprovalNote: "确认该方案后继续执行",
        approvalPolicy: "审批策略",
        needsApproval: "需要审批",
        restricted: "受限操作",
        recentApprovals: "最近审批",
        noRequestedPermissions: "没有请求权限",
        uploadedFiles: "已上传文件",
        noUploadedFiles: "还没有上传文件。",
        outputArtifacts: "输出产物",
        noOutputArtifacts: "还没有输出产物。",
        memoryWorkspace: "记忆工作区",
        sessionMemory: "会话记忆",
        userMemory: "用户记忆",
        workspaceMemory: "全局工作记忆",
        platformControls: "平台控制",
        searchPriorSessions: "搜索历史会话",
        sessionSearch: "会话搜索",
        sessionRecall: "会话召回",
        currentSnapshot: "当前快照",
        recentTurns: "最近轮次",
        recallEngineNotes: "召回引擎备注",
        recallInspector: "召回检查器",
        recallEvidence: "召回证据",
        matchedTurns: "匹配轮次",
        conflictQueue: "冲突队列",
        stalenessQueue: "过期队列",
        noConflicts: "没有待处理冲突。",
        noStaleness: "没有过期记忆。",
        activeProvider: "当前提供方",
        stores: "存储区",
        archiveTurns: "归档轮次",
        active: "已启用",
        activateProvider: (label: string) => `启用 ${label}`,
        entries: "条目",
        edit: "编辑",
        delete: "删除",
        noSkills: "没有可用技能。",
        enabled: "已启用",
        disabled: "已禁用",
        skillSource: "技能来源",
        skillPath: "技能路径",
        extensionSource: "配置来源",
        noSubagents: "当前线程没有子代理任务。",
        status: "状态",
        profile: "配置",
        subagentGraph: "依赖图",
        subagentGraphTotal: "总数",
        subagentExecutionPath: "执行链路",
        subagentCriticalBlockers: "关键阻塞",
        readySubagents: "就绪",
        waitingSubagents: "等待",
        blockedSubagents: "阻塞",
        dependencyEdges: "依赖关系",
        noDependencyEdges: "当前子代理没有声明依赖。",
        dependsOn: "依赖",
        blockedBy: "阻塞来源",
        waitingFor: "等待",
        downstreamTasks: "影响下游",
        missingDependencies: "缺失依赖",
        allowedTools: "允许工具",
        none: "无",
        wait: "等待",
        cancel: "取消",
        noProcesses: "当前线程没有进程会话。",
        command: "命令",
        backend: "终端后端",
        executable: "可执行",
        cwd: "目录",
        pid: "PID",
        exitCode: "退出码",
        dimensions: "尺寸",
        stdin: "标准输入",
        stdinClosed: "标准输入已关闭",
        stdinPlaceholder: "输入要发送给正在运行进程的内容",
        sendInput: "发送",
        submitInput: "提交一行",
        closeInput: "关闭输入",
        interrupt: "中断",
        resize: "调整尺寸",
        refreshLog: "刷新日志",
        logCursor: "日志游标",
        inputHistory: "输入历史",
        kill: "终止",
        gateway: "网关",
        url: "地址",
        threadSettings: "线程设置",
        workspaceMode: "工作区模式",
        workspaceRoot: "工作区根目录",
        resolvedWorkspacePath: "实际工作目录",
        anvilHome: "Anvil 状态根目录",
        anvilRepoRoot: "仓库级 .anvil",
        anvilUserRoot: "个人级 .anvil",
        currentMode: "当前模式",
        effectiveModel: "生效模型",
        contextWindow: "上下文窗口",
        threadDefaultModel: "线程默认模型",
        threadProfile: "线程 Profile",
        reasoningEffort: "推理强度",
        planMode: "计划模式",
        planModeEnabled: "已开启",
        planModeDisabled: "已关闭",
        saveThreadSettings: "保存线程设置",
      },
      misc: {
        live: "实时",
        completed: "已完成",
        hide: "收起",
        show: "展开",
        approvalRequired: "需要审批",
        toolActivity: "工具活动",
        toolFallback: "工具",
        artifactEmitted: "产物已生成",
        approvalEvent: "审批事件",
        processEvent: "进程事件",
      },
    };
  }

  return {
    drawerSections: {
      timeline: "Timeline",
      recent_tools: "Recent Tools",
      approvals: "Approvals",
      files: "Files",
      memory: "Memory",
      skills: "Skills",
      subagents: "Subagents",
      processes: "Processes",
      settings: "Settings",
      ops: "Ops",
    },
    modeLabels: {
      chat: "Chat",
      agent: "Agent",
      full_access: "Full Access",
    },
    modeSummaries: {
      chat: "Safe chat mode",
      agent: "Guided local action",
      full_access: "High-trust automation",
    },
    shell: {
      brand: "Anvil",
      newSessionTitle: "What are you working on?",
      newSessionSubtitle: "Describe a goal and Anvil will run it in the current workspace with traceable context.",
      createThread: "New chat",
      searchThreads: "Search chats",
      more: "More",
      openConfigCenter: "Configuration center",
      langsmith: "LangSmith",
      searchDialogTitle: "Search chats",
      statusOk: "ok",
      noActiveThread: "No active thread",
      openThreads: "Open threads",
      toggleTheme: "Toggle theme",
      openUtilities: "Open utilities",
      closeUtilities: "Close utilities",
      mobileThreadsTitle: "Threads",
      mobileThreadsDescription: "Select a conversation thread.",
      mobileUtilitiesDescription: "Operator utilities for the active thread.",
    },
    threadList: {
      approvals: "Approvals",
      tasks: "tasks",
      actions: "Thread actions",
      renameThread: "Rename",
      pinThread: "Pin",
      unpinThread: "Unpin",
      pinned: "Pinned",
      delete: "Delete",
      renamePrompt: "Enter a new thread name",
      noResults: "No matching chats",
      deleteThread: (label: string) => `Delete ${label}`,
    },
    transcript: {
      liveStream: "Live stream",
      thinkingStreaming: "Thinking and streaming…",
      userInteraction: "Input needed",
      userInteractionDetail: "The agent is paused and will continue in this thread after you submit.",
      contextUsage: "Context",
      compactThreshold: "Compact threshold",
      toCompaction: "to compaction",
      contextWindow: "Context window",
      contextTokens: "Context",
      reportedUsage: "Reported",
      contextEstimated: "estimated",
      requestCount: "Model calls",
      estimatedCost: "Estimated cost",
      compacted: "Context auto-compacted",
      compactedDetail: "Earlier turns were compressed into a summary and remain available to the runtime.",
      compactingSoon: "Auto-compaction approaching",
      compactingSoonDetail: "Older conversation turns will be summarized when the threshold is reached.",
    },
    composer: {
      attach: "Attach",
      add: "Add",
      autoModel: "Select model",
      executionMode: "Execution mode",
      deepThinking: "Deep thinking",
      deepThinkingOn: "On",
      deepThinkingOff: "Off",
      imageAttachment: "Image attachment",
      fileAttachment: "File attachment",
      previewImage: "Preview image",
      closePreview: "Close preview",
      queuedFollowups: "Follow-ups",
      queuedDuringRun: "Sends when the current run completes",
      guide: "Guide",
      guiding: "Guiding next",
      editQueued: "Edit",
      deleteQueued: "Delete",
      moreQueuedActions: "More queued actions",
      pastedFiles: "Pasted files",
      attachmentFromQueue: "Queued attachment",
      compactOptions: "Options",
      sending: "Sending…",
      stop: "Stop",
      planMode: "Plan mode",
      approveOnce: "Yes",
      approveSession: "Yes, don't ask for this command again",
      denyApproval: "No",
      manualApproval: "Add details",
      submitInteraction: "Confirm and continue",
      interactionCustom: "Other",
      interactionFreeText: "Additional context",
      interactionRequired: "Choose or fill in a response to continue",
    },
      drawer: {
      noThread: "No thread",
      togglePinnedDrawer: "Toggle pinned drawer",
      noTimeline: "No timeline events for this thread yet.",
      operatorStatus: "Operator status",
      runtimeDiagnostics: "Run diagnostics",
      runtimeStatus: "Status",
      runtimeTotalElapsed: "Total elapsed",
      runtimeFirstModel: "First model",
      runtimeFirstContent: "First content",
      runtimeCompleted: "Completed",
      runtimeSlowestPhase: "Slowest phase",
      runtimePhaseCount: (count: number) => `${count} phases`,
      latestActivity: "Latest activity",
      activeTools: "Active tools",
      completedTools: "Completed tools",
      failedTools: "Failed tools",
      pendingApprovals: "Pending approvals",
      runningProcesses: "Running processes",
      activeSubagents: "Active subagents",
      noRecentTools: "No recent tools for this thread.",
      approvalNote: "Approval note",
      resumeApproval: "Approve and continue",
      resumingApproval: "Resuming…",
      cancelApproval: "Cancel request",
      confirmPlan: "Confirm plan and continue",
      rejectPlan: "Cancel plan",
      planApprovalNote: "Approved plan for this turn",
      approvalPolicy: "Approval policy",
      needsApproval: "Needs approval",
      restricted: "Restricted",
      recentApprovals: "Recent approvals",
      noRequestedPermissions: "no requested permissions",
      uploadedFiles: "Uploaded files",
      noUploadedFiles: "No uploaded files yet.",
      outputArtifacts: "Output artifacts",
      noOutputArtifacts: "No output artifacts yet.",
      memoryWorkspace: "Memory Workspace",
      sessionMemory: "Session Memory",
      userMemory: "User Memory",
      workspaceMemory: "Workspace Memory",
      platformControls: "Platform Controls",
      searchPriorSessions: "Search prior sessions",
      sessionSearch: "Session Search",
      sessionRecall: "Session Recall",
      currentSnapshot: "Current Snapshot",
      recentTurns: "Recent Turns",
      recallEngineNotes: "Recall Engine Notes",
      recallInspector: "Recall Inspector",
      recallEvidence: "Recall Evidence",
      matchedTurns: "Matched Turns",
      conflictQueue: "Conflict Queue",
      stalenessQueue: "Staleness Queue",
      noConflicts: "No pending conflicts.",
      noStaleness: "No stale memories.",
      activeProvider: "Active provider",
      stores: "Stores",
      archiveTurns: "Archive turns",
      active: "Active",
      activateProvider: (label: string) => `Activate ${label}`,
      entries: "Entries",
      edit: "Edit",
      delete: "Delete",
      noSkills: "No skills available.",
      enabled: "enabled",
      disabled: "disabled",
      skillSource: "Skill source",
      skillPath: "Skill path",
      extensionSource: "Config source",
      noSubagents: "No subagent tasks for this thread.",
      status: "Status",
      profile: "Profile",
      subagentGraph: "Dependency graph",
      subagentGraphTotal: "Total",
      subagentExecutionPath: "Execution path",
      subagentCriticalBlockers: "Critical blockers",
      readySubagents: "Ready",
      waitingSubagents: "Waiting",
      blockedSubagents: "Blocked",
      dependencyEdges: "Dependencies",
      noDependencyEdges: "No declared dependencies for current subagents.",
      dependsOn: "Depends on",
      blockedBy: "Blocked by",
      waitingFor: "Waiting for",
      downstreamTasks: "Downstream",
      missingDependencies: "Missing dependencies",
      allowedTools: "Allowed tools",
      none: "none",
      wait: "Wait",
      cancel: "Cancel",
      noProcesses: "No process sessions for this thread.",
      command: "Command",
      backend: "Backend",
      executable: "Executable",
      cwd: "Directory",
      pid: "PID",
      exitCode: "Exit code",
      dimensions: "Size",
      stdin: "Stdin",
      stdinClosed: "Stdin closed",
      stdinPlaceholder: "Type input for the running process",
      sendInput: "Send",
      submitInput: "Submit line",
      closeInput: "Close stdin",
      interrupt: "Interrupt",
      resize: "Resize",
      refreshLog: "Refresh log",
      logCursor: "Log cursor",
      inputHistory: "Input history",
      kill: "Kill",
      gateway: "Gateway",
      url: "URL",
      threadSettings: "Thread settings",
      workspaceMode: "Workspace mode",
      workspaceRoot: "Workspace root",
      resolvedWorkspacePath: "Resolved workspace",
      anvilHome: "Anvil home",
      anvilRepoRoot: "Repo .anvil",
      anvilUserRoot: "User .anvil",
      currentMode: "Current mode",
      effectiveModel: "Effective model",
      contextWindow: "Context window",
      threadDefaultModel: "Thread default model",
      threadProfile: "Thread profile",
      reasoningEffort: "Reasoning effort",
      planMode: "Plan mode",
      planModeEnabled: "Enabled",
      planModeDisabled: "Disabled",
      saveThreadSettings: "Save thread settings",
    },
    misc: {
      live: "live",
      completed: "completed",
      hide: "Hide",
      show: "Show",
      approvalRequired: "Approval required",
      toolActivity: "Tool activity",
      toolFallback: "Tool",
      artifactEmitted: "Artifact emitted",
      approvalEvent: "approval event",
      processEvent: "process event",
    },
  };
}

function defaultApprovalContext(locale: Locale) {
  return locale === "zh-CN" ? "批准本轮继续执行" : "approved for this turn";
}

function defaultPlanApprovalContext(locale: Locale) {
  return locale === "zh-CN" ? "确认该方案后继续执行" : "approved plan for this turn";
}

function defaultApprovalCancelReason(locale: Locale) {
  return locale === "zh-CN" ? "用户已取消审批请求" : "Approval cancelled by user";
}

export function WorkspaceShell({ initialThreadId = null }: WorkspaceShellProps) {
  const { resolvedTheme, setTheme } = useTheme();
  const { locale, t } = useI18n();
  const ui = useMemo(() => workspaceCopy(locale), [locale]);
  const [drawerSection, setDrawerSection] = useState<DrawerSection>("recent_tools");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState(false);
  const [drawerPinned, setDrawerPinned] = useStoredBoolean(DRAWER_PINNED_KEY, false);
  const drawerDataVisible = drawerOpen || drawerPinned || mobileDrawerOpen;
  const desktopDrawerVisible = drawerOpen || drawerPinned;
  const memoryDrawerActive = drawerDataVisible && drawerSection === "memory";
  const settingsDrawerActive = drawerDataVisible && drawerSection === "settings";
  const processesDrawerActive = drawerDataVisible && drawerSection === "processes";
  const uploadsDrawerActive = drawerDataVisible && drawerSection === "files";

  const threads = useThreads();
  const createThread = useCreateThread();
  const models = useModels();
  const skills = useSkills();
  const extensions = useExtensions();
  const plugins = usePlugins();
  const health = useGatewayHealth();
  const memoryLayers = useMemoryLayers({ enabled: memoryDrawerActive });
  const memoryOverview = useMemoryOverview({ enabled: memoryDrawerActive });
  const memoryProviders = useMemoryProviders({ enabled: memoryDrawerActive });
  const memoryConflicts = useMemoryConflicts({ enabled: memoryDrawerActive });
  const memoryStaleness = useMemoryStaleness({ enabled: memoryDrawerActive });
  const memoryReview = useMemoryReview({ enabled: memoryDrawerActive });
  const memoryAudit = useMemoryAdminAudit({ enabled: memoryDrawerActive });
  const flushMemory = useFlushMemory();
  const approveMemoryReview = useApproveMemoryReview();
  const rejectMemoryReview = useRejectMemoryReview();
  const batchMemoryReview = useBatchMemoryReview();
  const resolveMemoryConflict = useResolveMemoryConflict();
  const reflectionJobs = useReflectionJobs({ enabled: memoryDrawerActive });
  const runReflectionJob = useRunReflectionJob();
  const pauseReflectionJob = usePauseReflectionJob();
  const resumeReflectionJob = useResumeReflectionJob();
  const removeReflectionJob = useRemoveReflectionJob();
  const activateProvider = useActivateMemoryProvider();
  const reloadMemoryProviders = useReloadMemoryProviders();
  const testMemoryProvider = useTestMemoryProvider();
  const exportMemoryAdmin = useExportMemoryAdmin();
  const importMemoryAdmin = useImportMemoryAdmin();

  const [activeThreadId, setActiveThreadId] = useState<string | null>(initialThreadId);
  const [isDraftSession, setIsDraftSession] = useState(false);
  const [loadedMessageWindowsByThread, setLoadedMessageWindowsByThread] = useState<Record<string, ThreadMessageWindowCache>>({});
  const sessionMemory = useSessionMemory(activeThreadId, { enabled: memoryDrawerActive });
  const memoryTrace = useMemoryTrace(activeThreadId);
  const needsFullThreadState = shouldRequestFullThreadState(drawerDataVisible, drawerSection);
  const detail = useThreadDetail(activeThreadId, {
    messageLimit: THREAD_DETAIL_WINDOW_LIMIT,
    stateScope: needsFullThreadState ? "full" : "chat",
  });
  const loadThreadMessageWindow = useThreadMessageWindowLoader(activeThreadId);
  const fallbackThreadState = useThreadState(activeThreadId, {
    enabled: Boolean(activeThreadId) && detail.isError,
  });
  const threadSettings = useThreadSettings(activeThreadId, { enabled: settingsDrawerActive });
  const runStream = useThreadRunStream(activeThreadId);
  const uploads = useUploads(activeThreadId, { enabled: uploadsDrawerActive });
  const uploadFiles = useUploadFiles(activeThreadId);
  const enqueueFollowup = useEnqueueThreadFollowup(activeThreadId);
  const updateFollowup = useUpdateThreadFollowup(activeThreadId);
  const deleteFollowup = useDeleteThreadFollowup(activeThreadId);
  const popNextFollowup = usePopNextThreadFollowup(activeThreadId);
  const deleteThread = useDeleteThread();
  const cancelThreadApproval = useCancelThreadApproval(activeThreadId);
  const updateThreadSettings = useUpdateThreadSettings(activeThreadId);
  const waitSubagentTask = useWaitSubagentTask(activeThreadId);
  const cancelSubagentTask = useCancelSubagentTask(activeThreadId);
  const waitProcessSession = useWaitProcessSession(activeThreadId);
  const killProcessSession = useKillProcessSession(activeThreadId);
  const writeProcessStdin = useWriteProcessStdin(activeThreadId);
  const closeProcessStdin = useCloseProcessStdin(activeThreadId);
  const interruptProcessSession = useInterruptProcessSession(activeThreadId);
  const resizeProcessSession = useResizeProcessSession(activeThreadId);
  const activeComposerSessionKey = activeThreadId ? `thread:${activeThreadId}` : DRAFT_SESSION_KEY;

  const [composerDrafts, setComposerDrafts] = useState<Record<string, ComposerDraft>>({});
  const [selectedModelName, setSelectedModelName] = useState<string>("");
  const [executionMode, setExecutionMode] = useState<ExecutionMode>("chat");
  const [selectedWorkspaceRoot, setSelectedWorkspaceRoot] = useState<string>("");
  const [mobileThreadsOpen, setMobileThreadsOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [isSubmittingRun, setIsSubmittingRun] = useState(false);
  const [optimisticRunningThreadId, setOptimisticRunningThreadId] = useState<string | null>(null);
  const [leftCollapsed, setLeftCollapsed] = useStoredBoolean(LEFT_COLLAPSED_KEY, false);
  const [pinnedThreadIds, setPinnedThreadIds] = useStoredStringArray(PINNED_THREADS_KEY, []);
  const [threadTitleOverrides, setThreadTitleOverrides] = useStoredRecord(RENAMED_THREADS_KEY, {});
  const [opsState, setOpsState] = useState<OpsUrlState>(() => {
    if (typeof window === "undefined") {
      return DEFAULT_OPS_URL_STATE;
    }
    return parseOpsUrlState(window.location.search);
  });
  const [memoryLayerId, setMemoryLayerId] = useState<MemoryLayerId>("session");
  const layerEntries = useMemoryLayerEntries(memoryLayerId, { enabled: memoryDrawerActive });
  const createMemoryLayerEntry = useCreateMemoryLayerEntry();
  const updateMemoryLayerEntry = useUpdateMemoryLayerEntry();
  const deleteMemoryLayerEntry = useDeleteMemoryLayerEntry();
  const sessionSearch = useSessionSearch();
  const [entryDraft, setEntryDraft] = useState("");
  const [entryCategory, setEntryCategory] = useState("note");
  const [editingEntryId, setEditingEntryId] = useState<string | null>(null);
  const [sessionSearchQuery, setSessionSearchQuery] = useState("");
  const [selectedProfile, setSelectedProfile] = useState("");
  const [selectedReasoningEffort, setSelectedReasoningEffort] = useState("medium");
  const [selectedPlanMode, setSelectedPlanMode] = useState(false);
  const [planModeSupported, setPlanModeSupported] = useState(false);
  const [selectedArtifactPreview, setSelectedArtifactPreview] = useState<{ label: string; artifactUrl: string } | null>(null);
  const [selectedProcessSessionId, setSelectedProcessSessionId] = useState<string | null>(null);
  const [processLogCursor, setProcessLogCursor] = useState(0);
  const [processLogOutput, setProcessLogOutput] = useState("");
  const [processStdinDraft, setProcessStdinDraft] = useState("");
  const [processColumns, setProcessColumns] = useState("120");
  const [processRows, setProcessRows] = useState("40");
  const consumedProcessLogKeyRef = useRef<string | null>(null);
  const [approvalNote, setApprovalNote] = useState(() => defaultApprovalContext(locale));
  const [optimisticTurn, setOptimisticTurn] = useState<OptimisticTurn | null>(null);
  const [dismissedApprovalKey, setDismissedApprovalKey] = useState<string | null>(null);
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [editingMessageDraft, setEditingMessageDraft] = useState("");
  const lastApprovalRequestKeyRef = useRef<string | null>(null);
  const composerDraft = composerDrafts[activeComposerSessionKey] ?? emptyComposerDraft();
  const message = composerDraft.message;
  const selectedFiles = composerDraft.selectedFiles;
  const queuedAttachments = composerDraft.queuedAttachments;
  const autoDispatchingFollowupRef = useRef<string | null>(null);
  const [hiddenQueuedFollowupIds, setHiddenQueuedFollowupIds] = useState<string[]>([]);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    const media = window.matchMedia("(max-width: 1024px)");
    const sync = () => setIsMobile(media.matches);
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    const sync = () => setOpsState(parseOpsUrlState(window.location.search));
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);

  function updateComposerDraft(updater: (current: ComposerDraft) => ComposerDraft) {
    setComposerDrafts((current) => {
      const nextDraft = updater(current[activeComposerSessionKey] ?? emptyComposerDraft());
      return {
        ...current,
        [activeComposerSessionKey]: nextDraft,
      };
    });
  }

  function clearComposerDraft(sessionKey: string) {
    setComposerDrafts((current) => ({
      ...current,
      [sessionKey]: emptyComposerDraft(),
    }));
  }

  function updateOpsState(patch: Partial<OpsUrlState>, replace = false) {
    setOpsState((current) => {
      const next = mergeOpsUrlState(current, patch);
      syncWorkspaceUrl(activeThreadId, next, replace);
      return next;
    });
  }

  useEffect(() => {
    setActiveThreadId(initialThreadId ?? null);
    setIsDraftSession(false);
  }, [initialThreadId]);

  const runStreamStart = runStream.start;

  const activeThread = useMemo(
    () => detail.data?.thread ?? threads.data?.find((thread) => thread.thread_id === activeThreadId) ?? null,
    [activeThreadId, detail.data?.thread, threads.data],
  );
  const threadState = detail.data?.state ?? fallbackThreadState.data ?? null;
  const settingsState = threadSettings.data ?? null;
  const selectedProcessSession =
    (threadState?.process_sessions ?? []).find((session: ProcessSessionView) => session.session_id === selectedProcessSessionId) ?? null;
  const processLog = useProcessLog(activeThreadId, selectedProcessSessionId, {
    cursor: processLogCursor,
    limit: 200,
    enabled: processesDrawerActive && Boolean(selectedProcessSessionId),
    refetchIntervalMs: selectedProcessSession?.status === "running" ? 1500 : false,
  });
  const processCapabilities = useProcessCapabilities(activeThreadId, {
    enabled: processesDrawerActive,
  });
  const detailMessagesSignature = useMemo(
    () => (detail.data?.messages ?? []).map((message) => messageSignature(message)).join("|"),
    [detail.data?.messages],
  );
  const detailMessageWindowSignature = useMemo(
    () => messageWindowSignature(detail.data?.message_window ?? null),
    [detail.data?.message_window],
  );
  useEffect(() => {
    if (!activeThreadId || !detail.data) {
      return;
    }
    const nextMessages = detail.data.messages;
    const nextMessageWindow = detail.data.message_window ?? null;
    setLoadedMessageWindowsByThread((current) => {
      const previous = current[activeThreadId];
      const mergedMessages = mergeIncomingDetailWindow(
        previous?.messages ?? [],
        nextMessages,
        previous?.window ?? null,
        nextMessageWindow,
      );
      const nextWindow = mergeMessageWindow(previous?.window ?? null, nextMessageWindow, mergedMessages);
      if (previous && messageWindowCacheEquivalent(previous.messages, mergedMessages, previous.window, nextWindow)) {
        return current;
      }
      return {
        ...current,
        [activeThreadId]: {
          messages: mergedMessages,
          window: nextWindow,
        },
      };
    });
  }, [activeThreadId, detailMessagesSignature, detailMessageWindowSignature]);

  const loadedMessageWindow = activeThreadId ? (loadedMessageWindowsByThread[activeThreadId] ?? null) : null;
  const transcriptMessages = useMemo(
    () => deduplicateTranscriptMessages(loadedMessageWindow?.messages ?? detail.data?.messages ?? []),
    [detail.data?.messages, loadedMessageWindow?.messages],
  );
  const reducedTranscriptMessages = useMessageReducer(transcriptMessages, runStream.events);
  const messageWindow = loadedMessageWindow?.window ?? detail.data?.message_window ?? null;
  const contextWindowUsageForDisplay = useMemo(
    () =>
      deriveContextWindowUsageForDisplay(
        threadState?.context_window_usage ?? null,
        threadState?.token_usage_summary ?? null,
        threadState?.token_usage ?? null,
      ),
    [threadState?.context_window_usage, threadState?.token_usage_summary, threadState?.token_usage],
  );
  const promptCacheDiagnostics = threadState?.prompt_cache_diagnostics ?? null;
  const promptSectionTokenLedger = threadState?.prompt_section_token_ledger ?? null;
  const contextCacheDiagnostics = threadState?.context_cache_diagnostics ?? null;
  const capabilityAssemblyDiagnostics = threadState?.capability_assembly_diagnostics ?? null;
  const memoryInjectionDiagnostics = threadState?.memory_injection_diagnostics ?? null;
  const compactionDiagnostics = threadState?.compaction_diagnostics ?? null;
  const availableModels = useMemo(() => models.data ?? [], [models.data]);
  const visibleSelectedModelName = selectPreferredModelName(selectedModelName, availableModels);
  const selectedModelRequestName = modelNameForRequest(visibleSelectedModelName);
  const composerRunning = shouldShowComposerRunning({
    activeThreadId,
    runStreamIsStreaming: runStream.isStreaming,
    isSubmittingRun,
    optimisticRunningThreadId,
    durableThreadStatus: threadState?.status ?? activeThread?.status ?? null,
    streamTerminalSeen: runStream.events.some((event) => event.event === "run_completed" || event.event === "run_failed"),
    streamMessageCompletedSeen: runStream.events.some((event) => event.event === "message_completed"),
  });
  const pendingApproval =
    detail.data?.pending_approval ??
    (threadState?.has_pending_approval
      ? {
          decision: "needs_user_approval",
          reason: threadState.pending_approval_reason,
          action_kind: null,
          request_id: null,
          requested_permissions: [],
          scope_options: [],
        }
      : null);
  const pendingUserInteraction = detail.data?.pending_user_interaction ?? threadState?.pending_user_interaction ?? null;
  const pendingApprovalKey = approvalIdentity(pendingApproval);
  const visiblePendingApproval = pendingApprovalKey && dismissedApprovalKey === pendingApprovalKey ? null : pendingApproval;
  const isPlanApproval = visiblePendingApproval?.action_kind === "plan_confirmation";
  const latestUserMessageId = useMemo(() => {
    for (let index = transcriptMessages.length - 1; index >= 0; index -= 1) {
      const message = transcriptMessages[index];
      if (message.role === "human" || message.role === "user") {
        return message.message_id;
      }
    }
    return null;
  }, [transcriptMessages]);

  async function handleLoadEarlierMessages() {
    if (!activeThreadId || !messageWindow?.has_more_before) {
      return;
    }
    const nextOffset = Math.max((messageWindow.offset ?? 0) - THREAD_DETAIL_WINDOW_LIMIT, 0);
    const olderDetail = await loadThreadMessageWindow({
      messageOffset: nextOffset,
      messageLimit: THREAD_DETAIL_WINDOW_LIMIT,
    });
    setLoadedMessageWindowsByThread((current) => {
      const previous = current[activeThreadId];
      const mergedMessages = mergeMessageWindows(
        previous?.messages ?? [],
        olderDetail.messages,
        previous?.window ?? null,
        olderDetail.message_window ?? null,
      );
      const nextWindow = mergeMessageWindow(previous?.window ?? null, olderDetail.message_window ?? null, mergedMessages);
      return {
        ...current,
        [activeThreadId]: {
          messages: mergedMessages,
          window: nextWindow,
        },
      };
    });
  }

  useEffect(() => {
    const nextExecutionMode = threadState?.execution_mode ?? (activeThreadId ? "chat" : null);
    if (nextExecutionMode) {
      setExecutionMode(nextExecutionMode);
    }
    setSelectedModelName((current) =>
      selectPreferredModelName(
        threadState?.selected_model ?? threadState?.effective_model ?? threadState?.active_model ?? current,
        availableModels,
      ),
    );
    setSelectedProfile(threadState?.selected_profile ?? "");
    setSelectedReasoningEffort(threadState?.selected_reasoning_effort ?? "medium");
    setSelectedPlanMode(Boolean(threadState?.is_plan_mode) && nextExecutionMode !== "chat");
    setSelectedWorkspaceRoot(settingsState?.workspace_root ?? threadState?.workspace_root ?? "");
    if (typeof threadState?.runtime_capabilities?.plan_mode_enabled === "boolean") {
      setPlanModeSupported(Boolean(threadState.runtime_capabilities.plan_mode_enabled));
    }
  }, [
    activeThreadId,
    settingsState?.workspace_root,
    threadState?.execution_mode,
    threadState?.active_model,
    threadState?.effective_model,
    threadState?.is_plan_mode,
    threadState?.selected_model,
    threadState?.selected_profile,
    threadState?.selected_reasoning_effort,
    threadState?.workspace_root,
    threadState?.runtime_capabilities?.plan_mode_enabled,
  ]);

  useEffect(() => {
    if (executionMode === "chat" && selectedPlanMode) {
      setSelectedPlanMode(false);
    }
  }, [executionMode, selectedPlanMode]);

  useEffect(() => {
    if (!memoryLayers.data?.length) {
      return;
    }
    if (!memoryLayers.data.some((layer) => layer.layer_id === memoryLayerId)) {
      setMemoryLayerId(memoryLayers.data[0]?.layer_id ?? "session");
    }
  }, [memoryLayerId, memoryLayers.data]);

  useEffect(() => {
    if (!memoryDrawerActive || !activeThreadId) {
      return;
    }
    void memoryTrace.mutateAsync({ limit: 10 });
  }, [activeThreadId, memoryDrawerActive]);

  useEffect(() => {
    setSelectedArtifactPreview(null);
    setSelectedProcessSessionId(null);
    setProcessLogCursor(0);
    setProcessLogOutput("");
    consumedProcessLogKeyRef.current = null;
    setProcessStdinDraft("");
    setDismissedApprovalKey(null);
    setEditingMessageId(null);
    setEditingMessageDraft("");
    setApprovalNote(defaultApprovalContext(locale));
  }, [activeThreadId, locale]);

  useEffect(() => {
    if (drawerSection !== "processes") {
      return;
    }
    const sessions = threadState?.process_sessions ?? [];
    if (selectedProcessSessionId && sessions.some((session: ProcessSessionView) => session.session_id === selectedProcessSessionId)) {
      return;
    }
    setSelectedProcessSessionId(sessions[0]?.session_id ?? null);
  }, [drawerSection, selectedProcessSessionId, threadState?.process_sessions]);

  useEffect(() => {
    setProcessLogCursor(0);
    setProcessLogOutput("");
    consumedProcessLogKeyRef.current = null;
    setProcessStdinDraft("");
    const session = (threadState?.process_sessions ?? []).find((item: ProcessSessionView) => item.session_id === selectedProcessSessionId);
    setProcessColumns(String(session?.columns ?? 120));
    setProcessRows(String(session?.rows ?? 40));
  }, [selectedProcessSessionId]);

  useEffect(() => {
    const data = processLog.data;
    if (!data || data.session_id !== selectedProcessSessionId) {
      return;
    }
    const logKey = `${data.session_id}:${data.start_offset}:${data.next_offset}:${data.output.length}`;
    if (consumedProcessLogKeyRef.current === logKey) {
      return;
    }
    consumedProcessLogKeyRef.current = logKey;
    setProcessLogCursor((current) => Math.max(current, data.next_offset));
    if (!data.output) {
      return;
    }
    setProcessLogOutput((current) => (current ? `${current}\n${data.output}` : data.output));
  }, [processLog.data, selectedProcessSessionId]);

  useEffect(() => {
    if (!optimisticTurn) {
      return;
    }
    if (!activeThreadId && optimisticTurn.threadId === DRAFT_SESSION_KEY) {
      return;
    }
    if (optimisticTurn.threadId !== activeThreadId) {
      setOptimisticTurn(null);
    }
  }, [activeThreadId, optimisticTurn]);

  useEffect(() => {
    if (!pendingApprovalKey) {
      setDismissedApprovalKey(null);
      setApprovalNote(isPlanApproval ? defaultPlanApprovalContext(locale) : defaultApprovalContext(locale));
      return;
    }
    if (dismissedApprovalKey === pendingApprovalKey) {
      return;
    }
    setApprovalNote(isPlanApproval ? defaultPlanApprovalContext(locale) : defaultApprovalContext(locale));
  }, [dismissedApprovalKey, isPlanApproval, locale, pendingApprovalKey]);

  useEffect(() => {
    if (!optimisticTurn || optimisticTurn.threadId !== activeThreadId) {
      return;
    }
    const hasPersistedUserMessage = transcriptMessages.some(
      (item) =>
        (item.role === "human" || item.role === "user") &&
        messageMatchesOptimisticTurn(item, optimisticTurn),
    );
    if (hasPersistedUserMessage) {
      setOptimisticTurn(null);
    }
  }, [activeThreadId, optimisticTurn, transcriptMessages]);

  useEffect(() => {
    if (!visiblePendingApproval) {
      lastApprovalRequestKeyRef.current = null;
      return;
    }
    const requestKey = approvalIdentity(visiblePendingApproval);
    if (lastApprovalRequestKeyRef.current === requestKey) {
      return;
    }
    lastApprovalRequestKeyRef.current = requestKey;
  }, [visiblePendingApproval]);

  const displayThreads = useMemo(() => {
    const pinned = new Set(pinnedThreadIds);
    return [...sortThreadsByRecency(threads.data ?? [])].sort((left, right) => {
      const leftPinned = pinned.has(left.thread_id);
      const rightPinned = pinned.has(right.thread_id);
      if (leftPinned !== rightPinned) {
        return leftPinned ? -1 : 1;
      }
      return 0;
    });
  }, [pinnedThreadIds, threads.data]);

  const semanticMemoryEntries = useMemo(
    () => (memoryLayerId === "session" ? [] : layerEntries.data ?? []),
    [layerEntries.data, memoryLayerId],
  );

  const activeModelName = threadState?.active_model ?? visibleSelectedModelName;
  const queuedFollowupSignature = useMemo(
    () => (threadState?.queued_followups ?? []).map((item) => `${item.queue_id}:${item.mode ?? "followup"}`).join("|"),
    [threadState?.queued_followups],
  );
  const queuedFollowupsForComposer = useMemo(() => {
    const hidden = new Set(hiddenQueuedFollowupIds);
    return (threadState?.queued_followups ?? []).filter((item) => !hidden.has(item.queue_id));
  }, [hiddenQueuedFollowupIds, threadState?.queued_followups]);

  useEffect(() => {
    if (hiddenQueuedFollowupIds.length === 0) {
      return;
    }
    if (!threadState) {
      return;
    }
    const queuedIds = new Set((threadState?.queued_followups ?? []).map((item) => item.queue_id));
    setHiddenQueuedFollowupIds((current) => current.filter((queueId) => queuedIds.has(queueId)));
  }, [hiddenQueuedFollowupIds.length, threadState, threadState?.queued_followups]);
  const visibleQueuedFollowupSignature = useMemo(
    () => queuedFollowupsForComposer.map((item) => `${item.queue_id}:${item.mode ?? "followup"}`).join("|"),
    [queuedFollowupsForComposer],
  );
  const queuedFollowupDispatchSignature = useMemo(
    () => buildQueuedFollowupDispatchSignature(threadState),
    [threadState],
  );

  const recentToolActivitySignature = useMemo(
    () => (threadState?.recent_tool_activity ?? []).map((tool: ToolActivityView) => toolActivitySignature(tool)).join("|"),
    [threadState?.recent_tool_activity],
  );
  const recentApprovalEventSignature = useMemo(
    () =>
      (threadState?.recent_approval_events ?? [])
        .map((approval: any) => `${approval.request_id ?? ""}:${approval.decision ?? ""}:${approval.status ?? ""}:${approval.reason ?? ""}`)
        .join("|"),
    [threadState?.recent_approval_events],
  );
  const subagentTaskSignature = useMemo(
    () =>
      (threadState?.subagent_tasks ?? [])
        .map((task: SubagentTaskView) => `${task.task_id}:${task.status}:${task.parent_run_id ?? ""}:${(task.depends_on_task_ids ?? []).join(",")}`)
        .join("|"),
    [threadState?.subagent_tasks],
  );
  const processSessionSignature = useMemo(
    () =>
      (threadState?.process_sessions ?? [])
        .map((session: ProcessSessionView) => `${session.session_id}:${session.status}:${session.exit_code ?? ""}:${session.last_output ?? ""}`)
        .join("|"),
    [threadState?.process_sessions],
  );
  const runStreamEventSignature = useMemo(() => runStream.events.map((event, index) => streamEventSignature(event, index)).join("|"), [runStream.events]);

  const recentTools = useMemo(
    () => buildRecentTools(threadState?.recent_tool_activity ?? [], runStream.events),
    [recentToolActivitySignature, runStreamEventSignature],
  );
  const timelineItems = useMemo(
    () =>
      buildTimelineItems(
        threadState?.runtime_operator_status ?? null,
        threadState?.recent_tool_activity ?? [],
        threadState?.recent_approval_events ?? [],
        threadState?.subagent_tasks ?? [],
        threadState?.process_sessions ?? [],
        runStream.events,
      ),
    [
      processSessionSignature,
      recentApprovalEventSignature,
      recentToolActivitySignature,
      threadState?.runtime_operator_status,
      runStreamEventSignature,
      subagentTaskSignature,
    ],
  );
  const optimisticUserMessage = useMemo(() => {
    const isDraftOptimisticTurn = !activeThreadId && optimisticTurn?.threadId === DRAFT_SESSION_KEY;
    if (!optimisticTurn || (optimisticTurn.threadId !== activeThreadId && !isDraftOptimisticTurn)) {
      return null;
    }
    const alreadyPersisted = transcriptMessages.some(
      (item) =>
        (item.role === "human" || item.role === "user") &&
        messageMatchesOptimisticTurn(item, optimisticTurn),
    );
    if (alreadyPersisted) {
      return null;
    }
    return optimisticTurn;
  }, [activeThreadId, optimisticTurn, transcriptMessages]);
  const showNewSessionStart = shouldUseNewSessionStart({
    activeThreadId,
    visibleMessageCount: reducedTranscriptMessages.length,
    hasOptimisticUserMessage: Boolean(optimisticUserMessage),
    hasPendingApproval: Boolean(visiblePendingApproval),
    hasPendingUserInteraction: Boolean(pendingUserInteraction),
    queuedFollowupCount: queuedFollowupsForComposer.length,
    isStreaming: runStream.isStreaming,
    isSubmitting: isSubmittingRun,
  });

  function handleOpenOpsConsole(nextPatch: Partial<OpsUrlState> = {}) {
    updateOpsState({ surface: "overview", item: null, server: null, action: null, ...nextPatch, open: true }, false);
  }

  async function handleCreateThread() {
    setIsDraftSession(true);
    setActiveThreadId(null);
    syncWorkspaceUrl(null, opsState);
    if (isMobile) {
      setMobileThreadsOpen(false);
    }
  }

  function handleSelectThread(threadId: string) {
    setIsDraftSession(false);
    setActiveThreadId(threadId);
    syncWorkspaceUrl(threadId, opsState);
    if (isMobile) {
      setMobileThreadsOpen(false);
    }
  }

  async function handleRun() {
    if (isSubmittingRun) {
      return;
    }
    const sourceSessionKey = activeComposerSessionKey;
    const submittedMessage = message.trim();
    const filesToSend = [...selectedFiles];
    const queuedAttachmentsToSend = [...queuedAttachments];
    if (!submittedMessage && filesToSend.length === 0 && queuedAttachmentsToSend.length === 0) {
      return;
    }
    if (composerRunning) {
      await enqueueComposerFollowup("followup");
      return;
    }
    let streamStarted = false;
    setIsSubmittingRun(true);
    setOptimisticRunningThreadId(activeThreadId ?? DRAFT_SESSION_KEY);
    const pendingMessage =
      submittedMessage || (filesToSend.length > 0 || queuedAttachmentsToSend.length > 0 ? defaultAttachmentPrompt(locale) : "");
    const clientMessageId = createClientMessageId();
    try {
      let targetThreadId = activeThreadId;
      setOptimisticTurn({
        threadId: targetThreadId ?? DRAFT_SESSION_KEY,
        clientMessageId,
        message: pendingMessage,
        artifactRefs: [],
      });
      if (!targetThreadId) {
        const created = await createThread.mutateAsync(undefined);
        targetThreadId = created.thread_id;
        setIsDraftSession(false);
        setActiveThreadId(targetThreadId);
        syncWorkspaceUrl(targetThreadId, opsState);
        setOptimisticRunningThreadId(targetThreadId);
        setOptimisticTurn({
          threadId: targetThreadId,
          clientMessageId,
          message: pendingMessage,
          artifactRefs: [],
        });
      }
      const uploadResult =
        filesToSend.length > 0
          ? await uploadFiles.mutateAsync({
              files: filesToSend,
              threadIdOverride: targetThreadId,
            })
          : null;
      const uploadedFiles = uploadResult?.files ?? [];
      const uploadedFilenames = [
        ...queuedAttachmentsToSend.map((file) => file.filename),
        ...uploadedFiles.map((file) => file.filename),
      ];
      const uploadedArtifactRefs = [
        ...queuedAttachmentsToSend.map((file) => file.artifactRef),
        ...uploadedFiles.map(uploadItemToArtifactRef),
      ];
      const effectiveMessage =
        submittedMessage || (uploadedFilenames.length > 0 ? defaultAttachmentPrompt(locale) : "");
      const nextPlanMode = planModeSupported && executionMode !== "chat" ? selectedPlanMode : null;
      await updateThreadSettings.mutateAsync({
        body: {
          execution_mode: executionMode,
          selected_model: selectedModelRequestName,
          selected_profile: selectedProfile || null,
          selected_reasoning_effort: selectedReasoningEffort || null,
          is_plan_mode: nextPlanMode,
        },
        threadIdOverride: targetThreadId,
      });
      setLoadedMessageWindowsByThread((current) => {
        const next = { ...current };
        delete next[targetThreadId];
        return next;
      });
      setOptimisticTurn({
        threadId: targetThreadId,
        clientMessageId,
        message: effectiveMessage,
        artifactRefs: uploadedArtifactRefs,
      });
      clearComposerDraft(sourceSessionKey);
      clearComposerDraft(`thread:${targetThreadId}`);
      const pendingRun = runStream.start(
        {
          message: effectiveMessage,
          client_message_id: clientMessageId,
          execution_mode: executionMode,
          selected_model: selectedModelRequestName,
          profile: selectedProfile || null,
          selected_reasoning_effort: selectedReasoningEffort || null,
          uploaded_filenames: uploadedFilenames,
          is_plan_mode: nextPlanMode,
        },
        targetThreadId,
      );
      streamStarted = true;
      setIsSubmittingRun(false);
      await pendingRun;
    } catch (error) {
      if (!streamStarted) {
        setOptimisticTurn(null);
      }
      throw error;
    } finally {
      setOptimisticRunningThreadId(null);
      if (!streamStarted) {
        setIsSubmittingRun(false);
      }
    }
  }

  async function enqueueComposerFollowup(mode: "followup" | "guidance") {
    const sourceSessionKey = activeComposerSessionKey;
    const submittedMessage = message.trim();
    const filesToSend = [...selectedFiles];
    const queuedAttachmentsToSend = [...queuedAttachments];
    if (!submittedMessage && filesToSend.length === 0 && queuedAttachmentsToSend.length === 0) {
      return;
    }
    let targetThreadId = activeThreadId;
    if (!targetThreadId) {
      const created = await createThread.mutateAsync(undefined);
      targetThreadId = created.thread_id;
      setIsDraftSession(false);
      setActiveThreadId(targetThreadId);
      syncWorkspaceUrl(targetThreadId, opsState);
    }
    const uploadResult =
      filesToSend.length > 0
        ? await uploadFiles.mutateAsync({
            files: filesToSend,
            threadIdOverride: targetThreadId,
          })
        : null;
    const uploadedFiles = uploadResult?.files ?? [];
    const queuedArtifactRefs = queuedAttachmentsToSend.map((file) => file.artifactRef);
    const uploadedArtifactRefs = uploadedFiles.map(uploadItemToArtifactRef);
    const uploadedFilenames = [
      ...queuedAttachmentsToSend.map((file) => file.filename),
      ...uploadedFiles.map((file) => file.filename),
    ];
    const effectiveMessage =
      submittedMessage || (uploadedFilenames.length > 0 ? defaultAttachmentPrompt(locale) : "");
    const nextPlanMode = planModeSupported && executionMode !== "chat" ? selectedPlanMode : null;
    await enqueueFollowup.mutateAsync({
      body: {
        message: effectiveMessage,
        mode,
        execution_mode: executionMode,
        selected_model: selectedModelRequestName,
        profile: selectedProfile || null,
        selected_reasoning_effort: selectedReasoningEffort || null,
        uploaded_filenames: uploadedFilenames,
        uploaded_file_refs: [...queuedArtifactRefs, ...uploadedArtifactRefs],
        is_plan_mode: nextPlanMode,
      },
      threadIdOverride: targetThreadId,
    });
    clearComposerDraft(sourceSessionKey);
    clearComposerDraft(`thread:${targetThreadId}`);
  }

  async function handleGuideQueuedFollowup(item: QueuedFollowUpView) {
    if (!activeThreadId) {
      return;
    }
    if (!composerRunning && canDispatchQueuedFollowup(threadState)) {
      if (autoDispatchingFollowupRef.current) {
        return;
      }
      autoDispatchingFollowupRef.current = item.queue_id;
      setHiddenQueuedFollowupIds((current) => (current.includes(item.queue_id) ? current : [...current, item.queue_id]));
      setOptimisticTurn({
        threadId: activeThreadId,
        clientMessageId: queuedFollowupClientMessageId(item.queue_id),
        message: item.message,
        artifactRefs: item.uploaded_file_refs ?? [],
      });
      let removedFromQueue = false;
      try {
        await deleteFollowup.mutateAsync({
          queueId: item.queue_id,
          threadIdOverride: activeThreadId,
        });
        removedFromQueue = true;
        const nextPlanMode =
          typeof item.is_plan_mode === "boolean"
            ? item.is_plan_mode
            : planModeSupported && (item.execution_mode ?? executionMode) !== "chat"
              ? selectedPlanMode
              : null;
        await updateThreadSettings.mutateAsync({
          body: {
            execution_mode: item.execution_mode ?? executionMode,
            selected_model: item.selected_model ?? selectedModelRequestName,
            selected_profile: item.profile ?? (selectedProfile || null),
            selected_reasoning_effort: item.selected_reasoning_effort ?? (selectedReasoningEffort || null),
            is_plan_mode: nextPlanMode,
          },
          threadIdOverride: activeThreadId,
        });
        const result = await runStream.start(
          buildQueuedFollowupRunBody(item, {
            executionMode,
            selectedModelName: visibleSelectedModelName,
            selectedProfile,
            selectedReasoningEffort,
            selectedPlanMode,
          }),
          activeThreadId,
        );
        if (result?.status !== "completed") {
          await enqueueFollowup.mutateAsync({
            body: buildQueuedFollowupRestoreRequest(item),
            threadIdOverride: activeThreadId,
          });
          setOptimisticTurn((current) =>
            current?.threadId === activeThreadId && messagesEquivalentForOptimistic(current.message, item.message) ? null : current,
          );
          setHiddenQueuedFollowupIds((current) => current.filter((queueId) => queueId !== item.queue_id));
          removedFromQueue = false;
        }
      } catch (error) {
        if (removedFromQueue) {
          await enqueueFollowup.mutateAsync({
            body: buildQueuedFollowupRestoreRequest(item),
            threadIdOverride: activeThreadId,
          });
        }
        setOptimisticTurn((current) =>
          current?.threadId === activeThreadId && messagesEquivalentForOptimistic(current.message, item.message) ? null : current,
        );
        setHiddenQueuedFollowupIds((current) => current.filter((queueId) => queueId !== item.queue_id));
        throw error;
      } finally {
        autoDispatchingFollowupRef.current = null;
      }
      return;
    }
    await updateFollowup.mutateAsync({
      queueId: item.queue_id,
      body: { mode: "guidance" },
      threadIdOverride: activeThreadId,
    });
  }

  async function handleDeleteQueuedFollowup(item: QueuedFollowUpView) {
    if (!activeThreadId) {
      return;
    }
    await deleteFollowup.mutateAsync({
      queueId: item.queue_id,
      threadIdOverride: activeThreadId,
    });
  }

  async function handleEditQueuedFollowup(item: QueuedFollowUpView) {
    if (!activeThreadId) {
      return;
    }
    const attachments = queuedFollowupAttachments(item);
    if (item.execution_mode) {
      setExecutionMode(item.execution_mode);
    }
    setSelectedModelName(selectPreferredModelName(item.selected_model ?? visibleSelectedModelName, availableModels));
    setSelectedProfile(item.profile ?? "");
    setSelectedReasoningEffort(item.selected_reasoning_effort ?? "medium");
    if (typeof item.is_plan_mode === "boolean") {
      setSelectedPlanMode(item.is_plan_mode);
    }
    updateComposerDraft((current) => ({
      ...current,
      message: item.message,
      queuedAttachments: mergeQueuedAttachments(current.queuedAttachments, attachments),
    }));
    await deleteFollowup.mutateAsync({
      queueId: item.queue_id,
      threadIdOverride: activeThreadId,
    });
  }

  useEffect(() => {
    if (!activeThreadId || composerRunning || isSubmittingRun || popNextFollowup.isPending || updateThreadSettings.isPending) {
      return;
    }
    if (!canDispatchQueuedFollowup(threadState)) {
      return;
    }
    const next = selectNextQueuedFollowup(queuedFollowupsForComposer);
    if (!next || autoDispatchingFollowupRef.current) {
      return;
    }
    autoDispatchingFollowupRef.current = next.queue_id;
    setHiddenQueuedFollowupIds((current) => (current.includes(next.queue_id) ? current : [...current, next.queue_id]));
    void (async () => {
      let popped: QueuedFollowUpView | null = null;
      try {
        popped = await popNextFollowup.mutateAsync(activeThreadId);
        if (!popped) {
          setHiddenQueuedFollowupIds((current) => current.filter((queueId) => queueId !== next.queue_id));
          return;
        }
        setHiddenQueuedFollowupIds((current) => (current.includes(popped!.queue_id) ? current : [...current, popped!.queue_id]));
        setOptimisticTurn({
          threadId: activeThreadId,
          clientMessageId: queuedFollowupClientMessageId(popped.queue_id),
          message: popped.message,
          artifactRefs: popped.uploaded_file_refs ?? [],
        });
        const nextPlanMode =
          typeof popped.is_plan_mode === "boolean"
            ? popped.is_plan_mode
            : planModeSupported && (popped.execution_mode ?? executionMode) !== "chat"
              ? selectedPlanMode
              : null;
        const selectedProfileForSettings = popped.profile ?? (selectedProfile || null);
        await updateThreadSettings.mutateAsync({
          body: {
            execution_mode: popped.execution_mode ?? executionMode,
            selected_model: popped.selected_model ?? selectedModelRequestName,
            selected_profile: selectedProfileForSettings,
            selected_reasoning_effort: popped.selected_reasoning_effort ?? (selectedReasoningEffort || null),
            is_plan_mode: nextPlanMode,
          },
          threadIdOverride: activeThreadId,
        });
        const result = await runStreamStart(
          buildQueuedFollowupRunBody(popped, {
            executionMode,
            selectedModelName: visibleSelectedModelName,
            selectedProfile,
            selectedReasoningEffort,
            selectedPlanMode,
          }),
          activeThreadId,
        );
        if (result?.status !== "completed") {
          await enqueueFollowup.mutateAsync({
            body: buildQueuedFollowupRestoreRequest(popped),
            threadIdOverride: activeThreadId,
          });
          setOptimisticTurn((current) =>
            current?.threadId === activeThreadId && popped && messagesEquivalentForOptimistic(current.message, popped.message) ? null : current,
          );
          setHiddenQueuedFollowupIds((current) => current.filter((queueId) => queueId !== popped?.queue_id));
          popped = null;
        } else {
          popped = null;
        }
      } catch {
        if (popped) {
          await enqueueFollowup.mutateAsync({
            body: buildQueuedFollowupRestoreRequest(popped),
            threadIdOverride: activeThreadId,
          });
          setOptimisticTurn((current) =>
            current?.threadId === activeThreadId && popped && messagesEquivalentForOptimistic(current.message, popped.message) ? null : current,
          );
        }
        setHiddenQueuedFollowupIds((current) => current.filter((queueId) => queueId !== (popped?.queue_id ?? next.queue_id)));
      } finally {
        autoDispatchingFollowupRef.current = null;
      }
    })();
  }, [
    activeThreadId,
    composerRunning,
    executionMode,
    enqueueFollowup,
    isSubmittingRun,
    popNextFollowup,
    runStreamStart,
    visibleSelectedModelName,
    selectedPlanMode,
    selectedProfile,
    selectedReasoningEffort,
    visibleQueuedFollowupSignature,
    queuedFollowupsForComposer,
    queuedFollowupDispatchSignature,
    updateThreadSettings,
  ]);

  useEffect(() => {
    setSelectedModelName((current) => selectPreferredModelName(current, availableModels));
  }, [availableModels]);

  async function handleApprove() {
    if (!activeThreadId || !pendingApprovalKey) {
      return;
    }
    setDismissedApprovalKey(pendingApprovalKey);
    try {
      await runStream.resumeApproval(
        {
          approval_context: approvalNote.trim() || defaultApprovalContext(locale),
        },
        activeThreadId,
      );
    } catch (error) {
      setDismissedApprovalKey(null);
      throw error;
    }
    setApprovalNote(isPlanApproval ? defaultPlanApprovalContext(locale) : defaultApprovalContext(locale));
  }

  async function handleApproveSession() {
    if (!activeThreadId || !pendingApprovalKey || !visiblePendingApproval) {
      return;
    }
    const permissions = visiblePendingApproval.requested_permissions.join(", ") || visiblePendingApproval.reason || "requested action";
    const approvalContext =
      locale === "zh-CN"
        ? `批准本轮继续执行，并在本会话中对同类命令不再询问：${permissions}`
        : `approved for this turn; do not ask again in this session for: ${permissions}`;
    setApprovalNote(approvalContext);
    setDismissedApprovalKey(pendingApprovalKey);
    try {
      await runStream.resumeApproval(
        {
          approval_context: approvalContext,
        },
        activeThreadId,
      );
    } catch (error) {
      setDismissedApprovalKey(null);
      throw error;
    }
    setApprovalNote(defaultApprovalContext(locale));
  }

  async function handleCancelApproval() {
    if (!activeThreadId || !pendingApprovalKey) {
      return;
    }
    setDismissedApprovalKey(pendingApprovalKey);
    try {
      await cancelThreadApproval.mutateAsync(approvalNote.trim() || defaultApprovalCancelReason(locale));
    } catch (error) {
      setDismissedApprovalKey(null);
      throw error;
    }
    setApprovalNote(isPlanApproval ? defaultPlanApprovalContext(locale) : defaultApprovalContext(locale));
  }

  async function handleSubmitUserInteraction(body: UserInteractionSubmitDraft) {
    if (!activeThreadId || !pendingUserInteraction) {
      return;
    }
    const nextPlanMode = planModeSupported && executionMode !== "chat" ? selectedPlanMode : null;
    const request: UserInteractionResumeRequest = {
      request_id: pendingUserInteraction.request_id,
      selected_option_ids: body.selectedOptionIds,
      custom_response: body.customResponse || null,
      free_text: body.freeText || null,
      field_responses: body.fieldResponses?.map((item) => ({
        field_id: item.fieldId,
        selected_option_ids: item.selectedOptionIds,
        custom_response: item.customResponse || null,
        free_text: item.freeText || null,
      })) ?? [],
      selected_model: selectedModelRequestName,
      profile: selectedProfile || null,
      selected_reasoning_effort: selectedReasoningEffort || null,
      is_plan_mode: nextPlanMode,
    };
    await runStream.resumeUserInteraction(request, activeThreadId);
    void updateThreadSettings.mutateAsync({
      body: {
        execution_mode: executionMode,
        selected_model: selectedModelRequestName,
        selected_profile: selectedProfile || null,
        selected_reasoning_effort: selectedReasoningEffort || null,
        is_plan_mode: nextPlanMode,
      },
      threadIdOverride: activeThreadId,
    });
  }

  async function handleWriteProcessInput(sessionId: string, submit: boolean) {
    if (!processStdinDraft && !submit) {
      return;
    }
    await writeProcessStdin.mutateAsync({
      sessionId,
      body: {
        data: processStdinDraft,
        submit,
      },
    });
    setProcessStdinDraft("");
  }

  async function handleResizeProcess(sessionId: string) {
    await resizeProcessSession.mutateAsync({
      sessionId,
      body: {
        columns: Math.max(Number.parseInt(processColumns, 10) || 120, 1),
        rows: Math.max(Number.parseInt(processRows, 10) || 40, 1),
      },
    });
  }

  function handleRefreshProcessLog() {
    consumedProcessLogKeyRef.current = null;
    setProcessLogCursor(0);
    setProcessLogOutput("");
  }

  async function handleDeleteThread(threadId: string) {
    const previousThreadId = activeThreadId;

    if (previousThreadId === threadId) {
      setIsDraftSession(true);
      setActiveThreadId(null);
      syncWorkspaceUrl(null, opsState);
    }

    try {
      await deleteThread.mutateAsync(threadId);
      setPinnedThreadIds((current) => current.filter((item) => item !== threadId));
      setThreadTitleOverrides((current) => {
        if (!current[threadId]) {
          return current;
        }
        const { [threadId]: _removed, ...rest } = current;
        return rest;
      });
    } catch (error) {
      if (previousThreadId === threadId) {
        setActiveThreadId(previousThreadId);
        setIsDraftSession(false);
        syncWorkspaceUrl(previousThreadId, opsState);
      }
      throw error;
    }
  }

  function handleRenameThread(threadId: string, currentTitle: string) {
    if (typeof window === "undefined") {
      return;
    }
    const nextTitle = window.prompt(ui.threadList.renamePrompt, currentTitle)?.trim();
    if (!nextTitle) {
      return;
    }
    setThreadTitleOverrides((current) => ({
      ...current,
      [threadId]: nextTitle,
    }));
  }

  function handleTogglePinnedThread(threadId: string) {
    setPinnedThreadIds((current) =>
      current.includes(threadId)
        ? current.filter((item) => item !== threadId)
        : [threadId, ...current],
    );
  }

  async function handleCopyMessage(content: string) {
    try {
      await navigator.clipboard.writeText(content);
    } catch {
      return;
    }
  }

  function handleStartEditMessage(message: MessageView) {
    setEditingMessageId(message.message_id);
    setEditingMessageDraft(message.content);
  }

  function handleCancelEditMessage() {
    setEditingMessageId(null);
    setEditingMessageDraft("");
  }

  async function handleSubmitEditMessage(messageId: string) {
    if (!activeThreadId || !editingMessageDraft.trim()) {
      return;
    }
    const nextPlanMode = planModeSupported && executionMode !== "chat" ? selectedPlanMode : null;
    await updateThreadSettings.mutateAsync({
      body: {
        execution_mode: executionMode,
        selected_model: selectedModelRequestName,
        selected_profile: selectedProfile || null,
        selected_reasoning_effort: selectedReasoningEffort || null,
        is_plan_mode: nextPlanMode,
      },
      threadIdOverride: activeThreadId,
    });
    await runStream.editLatestAndResend(
      messageId,
      {
        message: editingMessageDraft.trim(),
        execution_mode: executionMode,
        selected_model: selectedModelRequestName,
        profile: selectedProfile || null,
        selected_reasoning_effort: selectedReasoningEffort || null,
        is_plan_mode: nextPlanMode,
      },
      activeThreadId,
    );
    setEditingMessageId(null);
    setEditingMessageDraft("");
  }

  async function handleSaveThreadSettings() {
    if (!activeThreadId) {
      return;
    }
    await updateThreadSettings.mutateAsync({
      execution_mode: executionMode,
      selected_model: selectedModelRequestName,
      selected_profile: selectedProfile || null,
      selected_reasoning_effort: selectedReasoningEffort || null,
      is_plan_mode: planModeSupported && executionMode !== "chat" ? selectedPlanMode : null,
      workspace_root: selectedWorkspaceRoot.trim() || null,
    });
  }

  async function handleSaveMemoryEntry() {
    if (!entryDraft.trim() || memoryLayerId === "session") {
      return;
    }
    if (editingEntryId) {
      await updateMemoryLayerEntry.mutateAsync({
        layerId: memoryLayerId,
        entryId: editingEntryId,
        body: { content: entryDraft, category: entryCategory },
      });
    } else {
      await createMemoryLayerEntry.mutateAsync({
        layerId: memoryLayerId,
        body: { content: entryDraft, category: entryCategory },
      });
    }
    setEditingEntryId(null);
    setEntryDraft("");
    setEntryCategory("note");
  }

  async function handleDeleteMemoryEntry(entryId: string) {
    if (memoryLayerId === "session") {
      return;
    }
    await deleteMemoryLayerEntry.mutateAsync({ layerId: memoryLayerId, entryId });
    if (editingEntryId === entryId) {
      setEditingEntryId(null);
      setEntryDraft("");
      setEntryCategory("note");
    }
  }

  async function handleFlushMemory() {
    await flushMemory.mutateAsync({ threadId: activeThreadId });
  }

  async function handleApproveMemoryReview(reviewId: string) {
    await approveMemoryReview.mutateAsync(reviewId);
  }

  async function handleRejectMemoryReview(reviewId: string) {
    await rejectMemoryReview.mutateAsync(reviewId);
  }

  async function handleBatchMemoryReview(action: "approve" | "reject", reviewIds: string[]) {
    const cleanIds = reviewIds.filter(Boolean);
    if (!cleanIds.length) {
      return;
    }
    await batchMemoryReview.mutateAsync(
      action === "approve" ? { approve: cleanIds, reject: [] } : { approve: [], reject: cleanIds },
    );
  }

  async function handleResolveMemoryConflict(conflictId: string, action: string) {
    await resolveMemoryConflict.mutateAsync({ conflictId, action });
  }

  const threadRail = (
    <ThreadRail
      collapsed={leftCollapsed}
      threads={displayThreads}
      activeThreadId={activeThreadId}
      threadTitleOverrides={threadTitleOverrides}
      pinnedThreadIds={pinnedThreadIds}
      onCreateThread={handleCreateThread}
      onSelectThread={handleSelectThread}
      onDeleteThread={handleDeleteThread}
      onRenameThread={handleRenameThread}
      onTogglePinnedThread={handleTogglePinnedThread}
      onOpenOpsConsole={() => handleOpenOpsConsole()}
      onToggleCollapsed={() => setLeftCollapsed((current) => !current)}
    />
  );
  const composerCard = (
    <ComposerCard
      message={message}
      onMessageChange={(value) =>
        updateComposerDraft((current) => ({
          ...current,
          message: value,
        }))
      }
      selectedFiles={selectedFiles}
      onFileSelection={(files) =>
        updateComposerDraft((current) => ({
          ...current,
          selectedFiles: mergeSelectedFiles(current.selectedFiles, files),
        }))
      }
      onRemoveSelectedFile={(fileIndex) =>
        updateComposerDraft((current) => ({
          ...current,
          selectedFiles: current.selectedFiles.filter((_, index) => index !== fileIndex),
        }))
      }
      queuedAttachments={queuedAttachments}
      onRemoveQueuedAttachment={(fileIndex) =>
        updateComposerDraft((current) => ({
          ...current,
          queuedAttachments: current.queuedAttachments.filter((_, index) => index !== fileIndex),
        }))
      }
      queuedFollowups={queuedFollowupsForComposer}
      onGuideFollowup={handleGuideQueuedFollowup}
      onDeleteFollowup={handleDeleteQueuedFollowup}
      onEditFollowup={handleEditQueuedFollowup}
      onRun={handleRun}
      onStop={runStream.stop}
      pendingApproval={visiblePendingApproval}
      pendingUserInteraction={pendingUserInteraction}
      onSubmitUserInteraction={handleSubmitUserInteraction}
      approvalNote={approvalNote}
      onApprovalNoteChange={setApprovalNote}
      onApprove={handleApprove}
      onApproveSession={handleApproveSession}
      onCancelApproval={handleCancelApproval}
      approvalBusy={isSubmittingRun || composerRunning || cancelThreadApproval.isPending}
      interactionBusy={isSubmittingRun || composerRunning}
      onExecutionModeChange={setExecutionMode}
      executionMode={executionMode}
      isStreaming={composerRunning}
      isSubmitting={isSubmittingRun && !composerRunning}
      models={availableModels}
      selectedModelName={visibleSelectedModelName}
      onSelectedModelNameChange={setSelectedModelName}
      selectedReasoningEffort={selectedReasoningEffort}
      onSelectedReasoningEffortChange={setSelectedReasoningEffort}
      selectedPlanMode={selectedPlanMode}
      onSelectedPlanModeChange={setSelectedPlanMode}
      planModeSupported={planModeSupported}
      contextWindowUsage={contextWindowUsageForDisplay}
      promptCacheDiagnostics={promptCacheDiagnostics}
      promptSectionTokenLedger={promptSectionTokenLedger}
      contextCacheDiagnostics={contextCacheDiagnostics}
      capabilityAssemblyDiagnostics={capabilityAssemblyDiagnostics}
      memoryInjectionDiagnostics={memoryInjectionDiagnostics}
      compactionDiagnostics={compactionDiagnostics}
      activeModelName={threadState?.effective_model ?? threadState?.active_model ?? activeModelName}
    />
  );

  return (
    <div className="flex h-[100dvh] overflow-hidden bg-[var(--canvas)] text-[13px] text-[var(--ink)]">
      {!isMobile ? threadRail : null}

      <div className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-[linear-gradient(180deg,var(--canvas)_0%,var(--canvas-elevated)_100%)]">
        <TopBar
          isMobile={isMobile}
          drawerOpen={isMobile ? mobileDrawerOpen : desktopDrawerVisible}
          isDark={resolvedTheme === "dark"}
          onToggleTheme={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
          onOpenThreads={() => setMobileThreadsOpen(true)}
          onToggleDrawer={() => {
            if (isMobile) {
              setMobileDrawerOpen((current) => !current);
            } else if (desktopDrawerVisible) {
              setDrawerOpen(false);
              setDrawerPinned(false);
            } else {
              setDrawerOpen(true);
            }
          }}
        />

        <div className="flex min-h-0 flex-1">
          <main className="relative flex min-h-0 min-w-0 flex-1">
            <div className="mx-auto flex min-h-0 w-full max-w-[1440px] min-w-0 flex-1 flex-col px-2 pb-2 pt-2 md:px-3">
              {showNewSessionStart ? (
                <NewSessionStart title={ui.shell.newSessionTitle} subtitle={ui.shell.newSessionSubtitle}>
                  {composerCard}
                </NewSessionStart>
              ) : (
                <>
                  <TranscriptColumn
                    messages={activeThreadId ? reducedTranscriptMessages : []}
                    messageWindow={messageWindow}
                    contextWindowUsage={contextWindowUsageForDisplay}
                    isFetchingMessages={detail.isFetching}
                    latestUserMessageId={latestUserMessageId}
                    optimisticUserMessage={optimisticUserMessage}
                    pendingApproval={visiblePendingApproval}
                    pendingUserInteraction={pendingUserInteraction}
                    isStreaming={runStream.isStreaming}
                    streamError={runStream.error}
                    editingMessageId={editingMessageId}
                    editingMessageDraft={editingMessageDraft}
                    onEditingMessageDraftChange={setEditingMessageDraft}
                    onCopyMessage={handleCopyMessage}
                    onStartEditMessage={handleStartEditMessage}
                    onCancelEditMessage={handleCancelEditMessage}
                    onSubmitEditMessage={handleSubmitEditMessage}
                    onLoadEarlierMessages={handleLoadEarlierMessages}
                  />
                  {composerCard}
                </>
              )}
            </div>
          </main>

          {!isMobile && desktopDrawerVisible ? (
            <RightDrawer
              open={desktopDrawerVisible}
              pinned={drawerPinned}
              section={drawerSection}
              onSectionChange={setDrawerSection}
              onClose={() => {
                setDrawerOpen(false);
                setDrawerPinned(false);
              }}
              onTogglePinned={() => {
                setDrawerPinned((current) => !current);
                setDrawerOpen(true);
              }}
              pendingApproval={visiblePendingApproval}
              onApprove={handleApprove}
              onCancelApproval={handleCancelApproval}
              approvalNote={approvalNote}
              onApprovalNoteChange={setApprovalNote}
              approvalBusy={composerRunning || cancelThreadApproval.isPending}
              threadState={threadState}
              contextWindowUsage={contextWindowUsageForDisplay}
              promptCacheDiagnostics={promptCacheDiagnostics}
              promptSectionTokenLedger={promptSectionTokenLedger}
              contextCacheDiagnostics={contextCacheDiagnostics}
              capabilityAssemblyDiagnostics={capabilityAssemblyDiagnostics}
              memoryInjectionDiagnostics={memoryInjectionDiagnostics}
              compactionDiagnostics={compactionDiagnostics}
              threadSettings={settingsState}
              timelineItems={timelineItems}
              recentTools={recentTools}
              uploads={uploads.data?.files ?? []}
              activeThread={activeThread}
              models={availableModels}
              skills={skills.data ?? []}
              subagentTasks={threadState?.subagent_tasks ?? []}
              processSessions={threadState?.process_sessions ?? []}
              processCapabilities={processCapabilities.data ?? null}
              healthStatus={health.data?.status ?? "unknown"}
              gatewayUrl={process.env.NEXT_PUBLIC_ANVIL_GATEWAY_URL ?? "http://127.0.0.1:18000"}
              onOpenOpsConsole={() => handleOpenOpsConsole()}
              memoryLayers={memoryLayers.data ?? []}
              memoryLayerId={memoryLayerId}
              onMemoryLayerChange={setMemoryLayerId}
              sessionMemory={sessionMemory.data ?? null}
              memoryOverview={memoryOverview.data}
              memoryAudit={memoryAudit.data ?? null}
              memoryEntries={semanticMemoryEntries}
              memoryProviders={memoryProviders.data ?? []}
              memoryConflicts={memoryConflicts.data ?? []}
              memoryStaleness={memoryStaleness.data ?? []}
              memoryReviewItems={memoryReview.data ?? []}
              memoryTraceItems={memoryTrace.data?.items ?? []}
              onActivateProvider={(providerId) => activateProvider.mutateAsync(providerId)}
              onFlushMemory={handleFlushMemory}
              onApproveMemoryReview={handleApproveMemoryReview}
              onRejectMemoryReview={handleRejectMemoryReview}
              onBatchMemoryReview={handleBatchMemoryReview}
              onResolveMemoryConflict={handleResolveMemoryConflict}
              onReloadProviders={() => reloadMemoryProviders.mutateAsync()}
              onTestProvider={(providerId) => testMemoryProvider.mutateAsync(providerId)}
              onExportMemory={() => exportMemoryAdmin.mutateAsync()}
              onImportMemory={(payload) => importMemoryAdmin.mutateAsync(payload)}
              entryDraft={entryDraft}
              onEntryDraftChange={setEntryDraft}
              entryCategory={entryCategory}
              onEntryCategoryChange={setEntryCategory}
              editingEntryId={editingEntryId}
              onEditEntry={(entry) => {
                setEditingEntryId(entry.entry_id);
                setEntryDraft(entry.content);
                setEntryCategory(entry.category);
              }}
              onSaveEntry={handleSaveMemoryEntry}
              onDeleteEntry={handleDeleteMemoryEntry}
              sessionSearchQuery={sessionSearchQuery}
              onSessionSearchQueryChange={setSessionSearchQuery}
              onSessionSearch={() => sessionSearch.mutateAsync({ query: sessionSearchQuery, threadId: activeThreadId, limit: 6 })}
              sessionSearchResult={sessionSearch.data ?? null}
              reflectionJobs={reflectionJobs.data ?? []}
              onRunReflection={(jobId) => runReflectionJob.mutateAsync(jobId)}
              onPauseReflection={(jobId) => pauseReflectionJob.mutateAsync(jobId)}
              onResumeReflection={(jobId) => resumeReflectionJob.mutateAsync(jobId)}
              onRemoveReflection={(jobId) => removeReflectionJob.mutateAsync(jobId)}
              selectedModelName={visibleSelectedModelName}
              onSelectedModelNameChange={setSelectedModelName}
              selectedProfile={selectedProfile}
              onSelectedProfileChange={setSelectedProfile}
              selectedReasoningEffort={selectedReasoningEffort}
              onSelectedReasoningEffortChange={setSelectedReasoningEffort}
              selectedWorkspaceRoot={selectedWorkspaceRoot}
              onSelectedWorkspaceRootChange={setSelectedWorkspaceRoot}
              selectedPlanMode={selectedPlanMode}
              onSelectedPlanModeChange={setSelectedPlanMode}
              planModeSupported={planModeSupported}
              onSaveThreadSettings={handleSaveThreadSettings}
              selectedArtifactPreview={selectedArtifactPreview}
              onSelectedArtifactPreviewChange={setSelectedArtifactPreview}
              selectedProcessSessionId={selectedProcessSessionId}
              onSelectedProcessSessionIdChange={setSelectedProcessSessionId}
              processLog={processLog.data ?? null}
              processLogOutput={processLogOutput}
              processLogFetching={processLog.isFetching}
              processStdinDraft={processStdinDraft}
              onProcessStdinDraftChange={setProcessStdinDraft}
              processColumns={processColumns}
              processRows={processRows}
              onProcessColumnsChange={setProcessColumns}
              onProcessRowsChange={setProcessRows}
              onWriteProcessInput={handleWriteProcessInput}
              onCloseProcessInput={(sessionId) => closeProcessStdin.mutateAsync(sessionId)}
              onInterruptProcess={(sessionId) => interruptProcessSession.mutateAsync(sessionId)}
              onResizeProcess={handleResizeProcess}
              onRefreshProcessLog={handleRefreshProcessLog}
              onWaitSubagent={(taskId) => waitSubagentTask.mutateAsync({ taskId })}
              onCancelSubagent={(taskId) => cancelSubagentTask.mutateAsync(taskId)}
              onWaitProcess={(sessionId) => waitProcessSession.mutateAsync({ sessionId })}
              onKillProcess={(sessionId) => killProcessSession.mutateAsync(sessionId)}
            />
          ) : null}
        </div>

        <Dialog open={mobileThreadsOpen} onOpenChange={setMobileThreadsOpen}>
          <DialogContent className="max-h-[92dvh] max-w-[min(96vw,28rem)] overflow-hidden p-0">
            <DialogHeader className="px-4 pt-4">
              <DialogTitle>{ui.shell.mobileThreadsTitle}</DialogTitle>
              <DialogDescription>{ui.shell.mobileThreadsDescription}</DialogDescription>
            </DialogHeader>
            {threadRail}
          </DialogContent>
        </Dialog>

        <Dialog open={mobileDrawerOpen} onOpenChange={setMobileDrawerOpen}>
          <DialogContent className="max-h-[92dvh] max-w-[min(96vw,34rem)] overflow-hidden p-0">
            <DialogHeader className="px-4 pt-4">
              <DialogTitle>{sectionLabel(drawerSection, locale)}</DialogTitle>
              <DialogDescription>{ui.shell.mobileUtilitiesDescription}</DialogDescription>
            </DialogHeader>
            <RightDrawer
              open
              pinned={false}
              mobile
              section={drawerSection}
              onSectionChange={setDrawerSection}
              onClose={() => setMobileDrawerOpen(false)}
              onTogglePinned={() => undefined}
              pendingApproval={visiblePendingApproval}
              onApprove={handleApprove}
              onCancelApproval={handleCancelApproval}
              approvalNote={approvalNote}
              onApprovalNoteChange={setApprovalNote}
              approvalBusy={composerRunning || cancelThreadApproval.isPending}
              threadState={threadState}
              contextWindowUsage={contextWindowUsageForDisplay}
              promptCacheDiagnostics={promptCacheDiagnostics}
              promptSectionTokenLedger={promptSectionTokenLedger}
              contextCacheDiagnostics={contextCacheDiagnostics}
              capabilityAssemblyDiagnostics={capabilityAssemblyDiagnostics}
              memoryInjectionDiagnostics={memoryInjectionDiagnostics}
              compactionDiagnostics={compactionDiagnostics}
              threadSettings={settingsState}
              timelineItems={timelineItems}
              recentTools={recentTools}
              uploads={uploads.data?.files ?? []}
              activeThread={activeThread}
              models={availableModels}
              skills={skills.data ?? []}
              subagentTasks={threadState?.subagent_tasks ?? []}
              processSessions={threadState?.process_sessions ?? []}
              processCapabilities={processCapabilities.data ?? null}
              healthStatus={health.data?.status ?? "unknown"}
              gatewayUrl={process.env.NEXT_PUBLIC_ANVIL_GATEWAY_URL ?? "http://127.0.0.1:18000"}
              onOpenOpsConsole={() => handleOpenOpsConsole()}
              memoryLayers={memoryLayers.data ?? []}
              memoryLayerId={memoryLayerId}
              onMemoryLayerChange={setMemoryLayerId}
              sessionMemory={sessionMemory.data ?? null}
              memoryOverview={memoryOverview.data}
              memoryAudit={memoryAudit.data ?? null}
              memoryEntries={semanticMemoryEntries}
              memoryProviders={memoryProviders.data ?? []}
              memoryConflicts={memoryConflicts.data ?? []}
              memoryStaleness={memoryStaleness.data ?? []}
              memoryReviewItems={memoryReview.data ?? []}
              memoryTraceItems={memoryTrace.data?.items ?? []}
              onActivateProvider={(providerId) => activateProvider.mutateAsync(providerId)}
              onFlushMemory={handleFlushMemory}
              onApproveMemoryReview={handleApproveMemoryReview}
              onRejectMemoryReview={handleRejectMemoryReview}
              onBatchMemoryReview={handleBatchMemoryReview}
              onResolveMemoryConflict={handleResolveMemoryConflict}
              onReloadProviders={() => reloadMemoryProviders.mutateAsync()}
              onTestProvider={(providerId) => testMemoryProvider.mutateAsync(providerId)}
              onExportMemory={() => exportMemoryAdmin.mutateAsync()}
              onImportMemory={(payload) => importMemoryAdmin.mutateAsync(payload)}
              entryDraft={entryDraft}
              onEntryDraftChange={setEntryDraft}
              entryCategory={entryCategory}
              onEntryCategoryChange={setEntryCategory}
              editingEntryId={editingEntryId}
              onEditEntry={(entry) => {
                setEditingEntryId(entry.entry_id);
                setEntryDraft(entry.content);
                setEntryCategory(entry.category);
              }}
              onSaveEntry={handleSaveMemoryEntry}
              onDeleteEntry={handleDeleteMemoryEntry}
              sessionSearchQuery={sessionSearchQuery}
              onSessionSearchQueryChange={setSessionSearchQuery}
              onSessionSearch={() => sessionSearch.mutateAsync({ query: sessionSearchQuery, threadId: activeThreadId, limit: 6 })}
              sessionSearchResult={sessionSearch.data ?? null}
              reflectionJobs={reflectionJobs.data ?? []}
              onRunReflection={(jobId) => runReflectionJob.mutateAsync(jobId)}
              onPauseReflection={(jobId) => pauseReflectionJob.mutateAsync(jobId)}
              onResumeReflection={(jobId) => resumeReflectionJob.mutateAsync(jobId)}
              onRemoveReflection={(jobId) => removeReflectionJob.mutateAsync(jobId)}
              selectedModelName={visibleSelectedModelName}
              onSelectedModelNameChange={setSelectedModelName}
              selectedProfile={selectedProfile}
              onSelectedProfileChange={setSelectedProfile}
              selectedReasoningEffort={selectedReasoningEffort}
              onSelectedReasoningEffortChange={setSelectedReasoningEffort}
              selectedWorkspaceRoot={selectedWorkspaceRoot}
              onSelectedWorkspaceRootChange={setSelectedWorkspaceRoot}
              selectedPlanMode={selectedPlanMode}
              onSelectedPlanModeChange={setSelectedPlanMode}
              planModeSupported={planModeSupported}
              onSaveThreadSettings={handleSaveThreadSettings}
              selectedArtifactPreview={selectedArtifactPreview}
              onSelectedArtifactPreviewChange={setSelectedArtifactPreview}
              selectedProcessSessionId={selectedProcessSessionId}
              onSelectedProcessSessionIdChange={setSelectedProcessSessionId}
              processLog={processLog.data ?? null}
              processLogOutput={processLogOutput}
              processLogFetching={processLog.isFetching}
              processStdinDraft={processStdinDraft}
              onProcessStdinDraftChange={setProcessStdinDraft}
              processColumns={processColumns}
              processRows={processRows}
              onProcessColumnsChange={setProcessColumns}
              onProcessRowsChange={setProcessRows}
              onWriteProcessInput={handleWriteProcessInput}
              onCloseProcessInput={(sessionId) => closeProcessStdin.mutateAsync(sessionId)}
              onInterruptProcess={(sessionId) => interruptProcessSession.mutateAsync(sessionId)}
              onResizeProcess={handleResizeProcess}
              onRefreshProcessLog={handleRefreshProcessLog}
              onWaitSubagent={(taskId) => waitSubagentTask.mutateAsync({ taskId })}
              onCancelSubagent={(taskId) => cancelSubagentTask.mutateAsync(taskId)}
              onWaitProcess={(sessionId) => waitProcessSession.mutateAsync({ sessionId })}
              onKillProcess={(sessionId) => killProcessSession.mutateAsync(sessionId)}
            />
          </DialogContent>
        </Dialog>

        <OpsConsole
          open={opsState.open}
          locale={locale}
          urlState={opsState}
          activeThreadId={activeThreadId}
          threadState={threadState}
          onOpenChange={(open) => {
            if (!open) {
              updateOpsState({ open: false, action: null }, true);
            }
          }}
          onStateChange={(patch, replace) => updateOpsState(patch, replace)}
        />
      </div>
    </div>
  );
}

function TopBar({
  isMobile,
  drawerOpen,
  isDark,
  onToggleTheme,
  onOpenThreads,
  onToggleDrawer,
}: {
  isMobile: boolean;
  drawerOpen: boolean;
  isDark: boolean;
  onToggleTheme(): void;
  onOpenThreads(): void;
  onToggleDrawer(): void;
}) {
  const { locale } = useI18n();
  const ui = workspaceCopy(locale);

  return (
    <header className="sticky top-0 z-30 border-b border-[var(--line)] bg-[var(--canvas)]/92 backdrop-blur-xl">
      <div className="mx-auto flex h-12 max-w-[1440px] items-center justify-end gap-2 px-3">
        <div className="mr-auto flex items-center">
          {isMobile ? (
            <Button variant="ghost" size="icon" onClick={onOpenThreads} aria-label={ui.shell.openThreads}>
              <MenuIcon className="size-4" />
            </Button>
          ) : null}
        </div>

        <div className="flex items-center gap-1.5">
          <Button variant="ghost" size="icon" onClick={onToggleTheme} aria-label={ui.shell.toggleTheme}>
            {isDark ? <SunMediumIcon className="size-4" /> : <MoonStarIcon className="size-4" />}
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={onToggleDrawer}
            aria-label={drawerOpen ? ui.shell.closeUtilities : ui.shell.openUtilities}
            aria-expanded={drawerOpen}
            data-testid="utility-drawer-toggle"
          >
            {drawerOpen ? <ChevronRightIcon className="size-4" /> : <ChevronLeftIcon className="size-4" />}
          </Button>
        </div>
      </div>
    </header>
  );
}

function ThreadRail({
  collapsed,
  forceVisible = false,
  threads,
  activeThreadId,
  threadTitleOverrides,
  pinnedThreadIds,
  onCreateThread,
  onSelectThread,
  onDeleteThread,
  onRenameThread,
  onTogglePinnedThread,
  onOpenOpsConsole,
  onToggleCollapsed,
}: {
  collapsed: boolean;
  forceVisible?: boolean;
  threads: ThreadView[];
  activeThreadId: string | null;
  threadTitleOverrides: Record<string, string>;
  pinnedThreadIds: string[];
  onCreateThread(): void;
  onSelectThread(threadId: string): void;
  onDeleteThread(threadId: string): Promise<void>;
  onRenameThread(threadId: string, currentTitle: string): void;
  onTogglePinnedThread(threadId: string): void;
  onOpenOpsConsole(): void;
  onToggleCollapsed(): void;
}) {
  const { locale } = useI18n();
  const ui = workspaceCopy(locale);
  const activityNowMs = useThreadActivityClock();
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [moreOpen, setMoreOpen] = useState(false);
  const [activeMenuThreadId, setActiveMenuThreadId] = useState<string | null>(null);
  const moreMenuRef = useRef<HTMLDivElement | null>(null);
  const activeThreadMenuRef = useRef<HTMLDivElement | null>(null);
  const pinned = useMemo(() => new Set(pinnedThreadIds), [pinnedThreadIds]);
  const langsmithUrl = process.env.NEXT_PUBLIC_LANGSMITH_URL ?? "https://smith.langchain.com";
  const searchResults = useMemo(() => {
    const normalized = searchQuery.trim().toLowerCase();
    if (!normalized) {
      return threads;
    }
    return threads.filter((thread) =>
      `${thread.thread_id} ${displayThreadTitle(thread, threadTitleOverrides)}`
        .toLowerCase()
        .includes(normalized),
    );
  }, [searchQuery, threadTitleOverrides, threads]);
  useDismissablePopup(moreOpen, moreMenuRef, () => setMoreOpen(false));
  useDismissablePopup(Boolean(activeMenuThreadId), activeThreadMenuRef, () => setActiveMenuThreadId(null));

  return (
    <aside
      data-testid="thread-rail"
      className={cn(
        "relative z-50 h-full shrink-0 overflow-visible border-r border-[var(--line)] bg-[var(--sidebar)]",
        forceVisible ? "flex flex-col" : "hidden md:flex md:flex-col",
        collapsed ? "w-14 min-w-14 max-w-14" : "w-[272px] min-w-[272px] max-w-[272px]",
      )}
    >
      <div className="flex h-full min-h-0 w-full min-w-0 flex-col overflow-visible">
        <div className="sticky top-0 z-20 shrink-0 bg-[var(--sidebar)] px-2 pb-2 pt-2">
          <div className={cn("flex h-9 items-center gap-2 px-1", collapsed ? "justify-center" : "justify-between")}>
            {collapsed ? (
              <Button variant="ghost" size="icon" onClick={onToggleCollapsed} aria-label="Expand sidebar">
                <PanelLeftOpenIcon className="size-4" />
              </Button>
            ) : (
              <button
                type="button"
                className="flex min-w-0 items-center gap-2 rounded-xl px-2 py-1.5 text-left transition hover:bg-[var(--panel-muted)]"
                onClick={onCreateThread}
                aria-label={ui.shell.brand}
              >
                <span className="inline-flex size-7 shrink-0 items-center justify-center rounded-xl border border-[var(--line)] bg-[var(--panel)] text-[var(--ink)]">
                  <AnvilIcon className="size-4" />
                </span>
                <span className="truncate font-[var(--display-font)] text-[14px] font-semibold text-[var(--ink)]">
                  {ui.shell.brand}
                </span>
              </button>
            )}
            {!collapsed ? (
              <Button variant="ghost" size="icon" onClick={onToggleCollapsed} aria-label="Collapse sidebar">
                <PanelLeftCloseIcon className="size-4" />
              </Button>
            ) : null}
          </div>

          <div className="mt-2 space-y-0.5">
            <RailActionButton collapsed={collapsed} label={ui.shell.createThread} icon={<MessageSquarePlusIcon className="size-4" />} onClick={onCreateThread} />
            <RailActionButton collapsed={collapsed} label={ui.shell.searchThreads} icon={<SearchIcon className="size-4" />} onClick={() => setSearchOpen(true)} />
            <div ref={moreMenuRef} className="relative">
              <RailActionButton collapsed={collapsed} label={ui.shell.more} icon={<EllipsisIcon className="size-4" />} onClick={() => setMoreOpen((current) => !current)} />
              {moreOpen ? (
                <div
                  className={cn("absolute left-0 top-full z-[80] mt-1 w-52 rounded-xl border border-[var(--line)] bg-[var(--panel)] p-1 shadow-[var(--panel-shadow)]", collapsed ? "left-11 top-0 mt-0" : "")}
                  onPointerDown={(event) => event.stopPropagation()}
                >
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] text-[var(--ink)] hover:bg-[var(--panel-muted)]"
                    onClick={() => {
                      setMoreOpen(false);
                      onOpenOpsConsole();
                    }}
                  >
                    <SettingsIcon className="size-4 text-[var(--muted)]" />
                    {ui.shell.openConfigCenter}
                  </button>
                  <a
                    href={langsmithUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] text-[var(--ink)] hover:bg-[var(--panel-muted)]"
                    onClick={() => setMoreOpen(false)}
                  >
                    <ExternalLinkIcon className="size-4 text-[var(--muted)]" />
                    {ui.shell.langsmith}
                  </a>
                </div>
              ) : null}
            </div>
          </div>
        </div>

        <Dialog open={searchOpen} onOpenChange={setSearchOpen}>
          <DialogContent className="max-h-[min(78dvh,42rem)] max-w-[min(92vw,54rem)] overflow-hidden rounded-3xl p-0">
            <div className="border-b border-[var(--line)] px-5 py-4">
              <Input
                autoFocus
                aria-label={ui.shell.searchDialogTitle}
                placeholder={locale === "zh-CN" ? "搜索聊天..." : "Search chats..."}
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                className="h-11 border-0 bg-transparent px-0 text-[16px] shadow-none focus-visible:ring-0"
              />
            </div>
            <div className="max-h-[calc(min(78dvh,42rem)-5rem)] overflow-y-auto overflow-x-hidden">
              <div className="space-y-1 px-4 py-4">
                <button
                  type="button"
                  className="flex w-full items-center gap-3 rounded-2xl px-3 py-3 text-left text-[14px] text-[var(--ink)] hover:bg-[var(--panel-muted)]"
                  onClick={() => {
                    setSearchOpen(false);
                    onCreateThread();
                  }}
                >
                  <MessageSquarePlusIcon className="size-4 text-[var(--muted)]" />
                  {ui.shell.createThread}
                </button>
                {searchResults.length > 0 ? (
                  searchResults.map((thread) => {
                    const label = displayThreadTitle(thread, threadTitleOverrides);
                    const activityAt = threadActivityAt(thread);
                    const ageLabel = formatThreadActivityAge(activityAt, locale, activityNowMs);
                    return (
                      <button
                        key={thread.thread_id}
                        type="button"
                        className="grid w-full grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 rounded-2xl px-3 py-3 text-left hover:bg-[var(--panel-muted)]"
                        onClick={() => {
                          setSearchOpen(false);
                          onSelectThread(thread.thread_id);
                        }}
                      >
                        <CircleIcon className="size-4 text-[var(--muted)]" />
                        <HoverRevealText value={label} className="text-[13px] text-[var(--ink)]" showTooltip={false} />
                        {ageLabel ? <span className="text-xs text-[var(--muted)]">{ageLabel}</span> : null}
                      </button>
                    );
                  })
                ) : (
                  <EmptyPanelText text={ui.threadList.noResults} />
                )}
              </div>
            </div>
          </DialogContent>
        </Dialog>

        {!collapsed ? (
          <div
            data-testid="thread-rail-scroll"
            className="min-h-0 w-full min-w-0 flex-1 overflow-y-auto overflow-x-hidden px-2 pb-3 pt-1"
          >
            <div className="w-full min-w-0 space-y-1">
              {threads.map((thread) => {
                const active = thread.thread_id === activeThreadId;
                const primaryLabel = displayThreadTitle(thread, threadTitleOverrides);
                const activityAt = threadActivityAt(thread);
                const ageLabel = formatThreadActivityAge(activityAt, locale, activityNowMs);
                const running = thread.status === "running" || thread.has_active_subagent_tasks;
                const isPinned = pinned.has(thread.thread_id);
                const menuOpen = activeMenuThreadId === thread.thread_id;
                return (
                  <div
                    key={thread.thread_id}
                    data-testid={`thread-card-${thread.thread_id}`}
                    className={cn(
                      "group relative w-full min-w-0 rounded-xl transition-[background,box-shadow,transform] duration-150 active:translate-y-px",
                      active ? "bg-[color-mix(in_srgb,var(--ink)_9%,var(--panel)_91%)]" : "hover:bg-[color-mix(in_srgb,var(--ink)_6%,transparent)]",
                      running ? "animate-[threadPulse_1.8s_ease-in-out_infinite]" : "",
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => onSelectThread(thread.thread_id)}
                      className="flex h-10 w-full min-w-0 items-center overflow-hidden rounded-xl px-2.5 pr-9 text-left"
                    >
                      <div className="grid min-w-0 flex-1 grid-cols-[minmax(0,1fr)_auto] items-center gap-2">
                        <span className="flex min-w-0 items-center gap-1.5">
                          {isPinned ? <PinIcon className="size-3.5 shrink-0 text-[var(--muted)]" /> : null}
                          <HoverRevealText
                            testId={`thread-card-primary-${thread.thread_id}`}
                            value={primaryLabel}
                            className="text-[13px] leading-5 text-[var(--ink)]"
                            showTooltip={false}
                          />
                        </span>
                        {ageLabel ? (
                          <span
                            data-testid={`thread-card-age-${thread.thread_id}`}
                            className="whitespace-nowrap text-right text-[12px] leading-5 text-[var(--muted)]"
                            title={activityAt ?? undefined}
                          >
                            {ageLabel}
                          </span>
                        ) : null}
                      </div>
                    </button>
                    <button
                      type="button"
                      className="absolute right-1 top-1/2 z-20 inline-flex size-7 -translate-y-1/2 items-center justify-center rounded-full text-[var(--muted)] opacity-0 transition hover:bg-[var(--panel)] hover:text-[var(--ink)] group-hover:opacity-100"
                      aria-label={ui.threadList.actions}
                      onClick={(event) => {
                        event.stopPropagation();
                        setActiveMenuThreadId(menuOpen ? null : thread.thread_id);
                      }}
                    >
                      <EllipsisIcon className="size-4" />
                    </button>
                    {menuOpen ? (
                      <ThreadActionMenu
                        ref={activeThreadMenuRef}
                        label={primaryLabel}
                        pinned={isPinned}
                        onRename={() => {
                          setActiveMenuThreadId(null);
                          onRenameThread(thread.thread_id, primaryLabel);
                        }}
                        onTogglePinned={() => {
                          setActiveMenuThreadId(null);
                          onTogglePinnedThread(thread.thread_id);
                        }}
                        onDelete={() => {
                          setActiveMenuThreadId(null);
                          void onDeleteThread(thread.thread_id);
                        }}
                      />
                    ) : null}
                  </div>
                );
              })}
            </div>
          </div>
        ) : (
          <div data-testid="thread-rail-scroll" className="min-h-0 flex-1 overflow-hidden" />
        )}
      </div>
    </aside>
  );
}

function RailActionButton({
  collapsed,
  icon,
  label,
  onClick,
}: {
  collapsed: boolean;
  icon: React.ReactNode;
  label: string;
  onClick(): void;
}) {
  return (
    <button
      type="button"
      className={cn(
        "flex h-9 w-full items-center gap-3 rounded-xl px-2.5 text-left text-[13px] text-[var(--ink)] transition hover:bg-[var(--panel-muted)]",
        collapsed ? "justify-center px-1" : "",
      )}
      onClick={onClick}
      aria-label={label}
    >
      <span className="shrink-0 text-[var(--ink)]">{icon}</span>
      {!collapsed ? (
        <span className="min-w-0 truncate">{label}</span>
      ) : null}
    </button>
  );
}

type ThreadActionMenuProps = {
  label: string;
  pinned: boolean;
  onRename(): void;
  onTogglePinned(): void;
  onDelete(): void;
};

const ThreadActionMenu = React.forwardRef<HTMLDivElement, ThreadActionMenuProps>(function ThreadActionMenu(
  {
    label,
    pinned,
    onRename,
    onTogglePinned,
    onDelete,
  },
  ref,
) {
  const { locale } = useI18n();
  const ui = workspaceCopy(locale);
  const handledPointerActionRef = useRef(false);
  function runPointerAction(event: React.PointerEvent<HTMLButtonElement>, action: () => void) {
    handledPointerActionRef.current = true;
    event.preventDefault();
    event.stopPropagation();
    action();
    window.requestAnimationFrame(() => {
      handledPointerActionRef.current = false;
    });
  }
  function runClickAction(event: React.MouseEvent<HTMLButtonElement>, action: () => void) {
    event.stopPropagation();
    if (handledPointerActionRef.current) {
      return;
    }
    action();
  }
  return (
    <div
      ref={ref}
      className="absolute right-1 top-9 z-[240] w-44 rounded-xl border border-[var(--line)] bg-[var(--panel)] p-1 shadow-[var(--panel-shadow)]"
      onPointerDown={(event) => {
        event.preventDefault();
        event.stopPropagation();
      }}
      onClick={(event) => event.stopPropagation()}
    >
      <button
        type="button"
        className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] text-[var(--ink)] hover:bg-[var(--panel-muted)]"
        onPointerDown={(event) => runPointerAction(event, onRename)}
        onClick={(event) => runClickAction(event, onRename)}
      >
        <PencilIcon className="size-4 text-[var(--muted)]" />
        {ui.threadList.renameThread}
      </button>
      <button
        type="button"
        className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] text-[var(--ink)] hover:bg-[var(--panel-muted)]"
        onPointerDown={(event) => runPointerAction(event, onTogglePinned)}
        onClick={(event) => runClickAction(event, onTogglePinned)}
      >
        <PinIcon className="size-4 text-[var(--muted)]" />
        {pinned ? ui.threadList.unpinThread : ui.threadList.pinThread}
      </button>
      <div className="my-1 h-px bg-[var(--line)]" />
      <button
        type="button"
        className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] text-[var(--danger)] hover:bg-[var(--danger-soft)]"
        onPointerDown={(event) => runPointerAction(event, onDelete)}
        onClick={(event) => runClickAction(event, onDelete)}
        aria-label={ui.threadList.deleteThread(label)}
      >
        <Trash2Icon className="size-4" />
        {ui.threadList.delete}
      </button>
    </div>
  );
});

function displayThreadTitle(thread: ThreadView, overrides: Record<string, string>) {
  return overrides[thread.thread_id]?.trim() || thread.title || thread.thread_id;
}

function TranscriptColumn({
  messages,
  messageWindow,
  contextWindowUsage,
  isFetchingMessages,
  latestUserMessageId,
  optimisticUserMessage,
  pendingApproval,
  pendingUserInteraction,
  isStreaming,
  streamError,
  editingMessageId,
  editingMessageDraft,
  onEditingMessageDraftChange,
  onCopyMessage,
  onStartEditMessage,
  onCancelEditMessage,
  onSubmitEditMessage,
  onLoadEarlierMessages,
}: {
  messages: StepTranscriptMessage[];
  messageWindow: MessageWindowView | null;
  contextWindowUsage: ContextWindowUsageDisplay | null;
  isFetchingMessages: boolean;
  latestUserMessageId: string | null;
  optimisticUserMessage: OptimisticTurn | null;
  pendingApproval: ApprovalView | null;
  pendingUserInteraction: UserInteractionRequestView | null;
  isStreaming: boolean;
  streamError: string | null;
  editingMessageId: string | null;
  editingMessageDraft: string;
  onEditingMessageDraftChange(value: string): void;
  onCopyMessage(content: string): Promise<void>;
  onStartEditMessage(message: MessageView): void;
  onCancelEditMessage(): void;
  onSubmitEditMessage(messageId: string): Promise<void>;
  onLoadEarlierMessages(): void;
}) {
  const { t, locale } = useI18n();
  const ui = workspaceCopy(locale);
  const scrollViewportRef = useRef<HTMLDivElement | null>(null);
  const shouldStickToBottomRef = useRef(true);
  const displayMessages = useMemo(() => {
    const referencedToolCallIds = new Set(
      messages.flatMap((message) => message.tool_calls.map((toolCall) => toolCall.tool_call_id).filter(Boolean) as string[]),
    );
    return messages.filter((message) => {
      if (message.role !== "tool") {
        return true;
      }
      if (!message.tool_call_id) {
        return true;
      }
      return !referencedToolCallIds.has(message.tool_call_id);
    });
  }, [messages]);
  const displayMessagesWithOptimistic = useMemo(() => {
    if (!optimisticUserMessage) {
      return displayMessages;
    }
    const optimisticMessage = buildOptimisticUserMessage(
      optimisticUserMessage.message,
      optimisticUserMessage.artifactRefs,
      optimisticUserMessage.clientMessageId,
    );
    const firstLiveAssistantIndex = displayMessages.findIndex(
      (message) => Boolean(message.live) && (message.role === "ai" || message.role === "assistant"),
    );
    if (firstLiveAssistantIndex >= 0) {
      return [
        ...displayMessages.slice(0, firstLiveAssistantIndex),
        optimisticMessage,
        ...displayMessages.slice(firstLiveAssistantIndex),
      ];
    }
    return [...displayMessages, optimisticMessage];
  }, [displayMessages, optimisticUserMessage]);
  const groupedTurns = useMemo(() => buildTranscriptTurns(displayMessagesWithOptimistic), [displayMessagesWithOptimistic]);
  const hasEarlierMessages = Boolean(messageWindow?.has_more_before);
  const windowStart = typeof messageWindow?.offset === "number" ? messageWindow.offset + 1 : 1;
  const windowEnd =
    typeof messageWindow?.offset === "number" && typeof messageWindow?.returned === "number"
      ? messageWindow.offset + messageWindow.returned
      : messages.length;
  const windowTotal = messageWindow?.total ?? messages.length;
  const contextUsageSummary = useMemo(
    () => (contextWindowUsage ? buildContextUsageSummary(contextWindowUsage, locale, null) : null),
    [contextWindowUsage, locale],
  );
  const showCompactionNotice = Boolean(contextUsageSummary?.isCompacted);

  useEffect(() => {
    const viewport = scrollViewportRef.current;
    if (!viewport) {
      return;
    }
    const updateStickiness = () => {
      shouldStickToBottomRef.current =
        viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 96;
    };
    updateStickiness();
    viewport.addEventListener("scroll", updateStickiness, { passive: true });
    return () => viewport.removeEventListener("scroll", updateStickiness);
  }, []);

  useEffect(() => {
    const viewport = scrollViewportRef.current;
    if (!viewport || !shouldStickToBottomRef.current) {
      return;
    }
    if (typeof viewport.scrollTo === "function") {
      viewport.scrollTo({
        top: viewport.scrollHeight,
        behavior: "smooth",
      });
      return;
    }
    viewport.scrollTop = viewport.scrollHeight;
  }, [displayMessagesWithOptimistic, isStreaming, pendingApproval, pendingUserInteraction, streamError]);

  return (
    <div className="min-h-0 flex-1 overflow-hidden">
      <div ref={scrollViewportRef} className="h-full overflow-y-auto overflow-x-hidden pr-2">
        <div className="mx-auto flex w-full max-w-[900px] min-w-0 flex-col gap-4 px-3 pb-7 pt-1 sm:px-4">
          {messages.length === 0 && !optimisticUserMessage ? (
            <div className="flex min-h-[42vh] flex-col items-center justify-center text-center">
              <div className="text-[clamp(1.55rem,2.4vw,2.05rem)] font-semibold tracking-normal text-[var(--ink)]">
                {ui.shell.newSessionTitle}
              </div>
              <p className="mt-2 max-w-xl text-[13px] leading-6 text-[var(--muted)]">
                {ui.shell.newSessionSubtitle}
              </p>
            </div>
          ) : null}

          {hasEarlierMessages ? (
            <div className="flex justify-center py-1">
              <Button
                variant="ghost"
                size="sm"
                onClick={onLoadEarlierMessages}
                disabled={isFetchingMessages}
                className="h-8 rounded-full px-3 text-xs"
              >
                {isFetchingMessages ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <ChevronsUpDownIcon className="size-3.5" />
                )}
                {locale === "zh-CN"
                  ? `当前 ${windowStart}-${windowEnd} / 共 ${windowTotal} 条，加载更早消息`
                  : `Showing ${windowStart}-${windowEnd} of ${windowTotal}. Load earlier`}
              </Button>
            </div>
          ) : null}

          {showCompactionNotice ? (
            <div className="flex items-start gap-2 border-l border-[var(--line)] px-3 py-2 text-[13px]">
              <BrainCircuitIcon className="mt-0.5 size-4 shrink-0 text-[var(--primary)]" />
              <div className="min-w-0">
                <div className="font-semibold text-[var(--ink)]">{ui.transcript.compacted}</div>
                <div className="mt-0.5 leading-5 text-[var(--muted)]">{ui.transcript.compactedDetail}</div>
              </div>
            </div>
          ) : null}

          {groupedTurns.map((turn, index) => (
            <TranscriptTurnBlock
              key={turn.user?.message_id ?? `assistant-turn-${index}`}
              turn={turn}
              latestUserMessageId={latestUserMessageId}
              editingMessageId={editingMessageId}
              editingMessageDraft={editingMessageDraft}
              onEditingMessageDraftChange={onEditingMessageDraftChange}
              onCopyMessage={onCopyMessage}
              onStartEditMessage={onStartEditMessage}
              onCancelEditMessage={onCancelEditMessage}
              onSubmitEditMessage={onSubmitEditMessage}
            />
          ))}

          {pendingApproval && !messages.some((message) => message.approval) ? (
            <ApprovalCard approval={pendingApproval} />
          ) : null}

          {pendingUserInteraction && !pendingApproval ? (
            <UserInteractionCard interaction={pendingUserInteraction} readonly compact />
          ) : null}

          {isStreaming && !displayMessages.some((message) => message.live) ? (
            <div className="flex items-center gap-2.5 px-1 py-2 text-[13px] text-[var(--muted)]">
              <BrainCircuitIcon className="size-4 animate-pulse text-[var(--primary)]" />
              {ui.transcript.thinkingStreaming}
            </div>
          ) : null}

          {streamError ? (
            <div className="rounded-[0.8rem] border border-[var(--danger)]/30 bg-[var(--danger)]/10 px-3 py-2 text-[13px] text-[var(--danger)]">
              {streamError}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export function ContextWindowUsageControl({
  usage,
  promptCacheDiagnostics,
  promptSectionTokenLedger,
  contextCacheDiagnostics,
  capabilityAssemblyDiagnostics,
  memoryInjectionDiagnostics,
  compactionDiagnostics,
  activeModelName,
}: {
  usage: ContextWindowUsageDisplay;
  promptCacheDiagnostics?: PromptCacheDiagnosticsDisplay | null;
  promptSectionTokenLedger?: PromptSectionTokenLedgerDisplay | null;
  contextCacheDiagnostics?: ContextCacheDiagnosticsDisplay | null;
  capabilityAssemblyDiagnostics?: CapabilityAssemblyDiagnosticsDisplay | null;
  memoryInjectionDiagnostics?: MemoryInjectionDiagnosticsDisplay | null;
  compactionDiagnostics?: CompactionDiagnosticsDisplay | null;
  activeModelName: string | null;
}) {
  const { locale } = useI18n();
  const ui = workspaceCopy(locale);
  const [open, setOpen] = useState(false);
  const [popoverMaxHeight, setPopoverMaxHeight] = useState<number | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const summary = buildContextUsageSummary(usage, locale, activeModelName);
  const hasMeasuredUsage = typeof summary.contextTokens === "number" && Number.isFinite(summary.contextTokens);
  const contextLabel = formatTokenCount(summary.contextTokens, "--");
  const windowLabel = formatTokenCount(summary.windowTokens, "--");
  const compactThresholdLabel = formatTokenCount(summary.thresholdTokens, "--");
  const remainingLabel = formatTokenCount(summary.remainingTokens, "--");
  const circleRadius = 7;
  const circumference = 2 * Math.PI * circleRadius;
  const dashOffset = circumference * (1 - Math.min(Math.max(summary.pressureRatio, 0), 1));
  const detailLabel =
    hasMeasuredUsage
      ? `${contextLabel} / ${windowLabel}${summary.usedPercentLabel ? ` (${summary.usedPercentLabel})` : ""}`
      : locale === "zh-CN"
        ? "待采集"
        : "pending";
  const usageAriaLabel = `${ui.transcript.contextWindow} ${detailLabel}`;

  useEffect(() => {
    if (!open) {
      return;
    }
    function handlePointerDown(event: PointerEvent) {
      const target = event.target;
      if (target instanceof Node && containerRef.current?.contains(target)) {
        return;
      }
      setOpen(false);
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  useLayoutEffect(() => {
    if (!open) {
      setPopoverMaxHeight(null);
      return;
    }

    function updatePopoverMaxHeight() {
      const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
      const rect = containerRef.current?.getBoundingClientRect();
      const chromeGap = 16;
      const hardCap = 672;
      const viewportCap = Math.max(0, viewportHeight - chromeGap * 2);
      const availableAbove = Math.max(0, (rect?.top ?? Math.floor(viewportHeight * 0.76)) - chromeGap);
      const nextMaxHeight = Math.floor(Math.min(availableAbove, viewportCap, hardCap));
      setPopoverMaxHeight(nextMaxHeight);
    }

    updatePopoverMaxHeight();
    window.addEventListener("resize", updatePopoverMaxHeight);
    window.addEventListener("scroll", updatePopoverMaxHeight, true);
    return () => {
      window.removeEventListener("resize", updatePopoverMaxHeight);
      window.removeEventListener("scroll", updatePopoverMaxHeight, true);
    };
  }, [open]);

  return (
    <div className="relative flex shrink-0 items-center" ref={containerRef}>
      {open ? (
        <div
          data-testid="context-window-popover"
          className="absolute bottom-[calc(100%+0.6rem)] right-0 z-40 max-h-[min(76dvh,42rem)] w-[28rem] max-w-[calc(100vw-2rem)] overflow-y-auto overflow-x-hidden overscroll-contain rounded-[0.9rem] text-left"
          style={{
            maxHeight: popoverMaxHeight !== null ? `${popoverMaxHeight}px` : "min(76dvh, 42rem)",
            overflowY: "auto",
            overflowX: "hidden",
            scrollbarGutter: "stable",
          }}
        >
          <ContextWindowUsagePanel
            usage={usage}
            promptCacheDiagnostics={promptCacheDiagnostics}
            promptSectionTokenLedger={promptSectionTokenLedger}
            contextCacheDiagnostics={contextCacheDiagnostics}
            capabilityAssemblyDiagnostics={capabilityAssemblyDiagnostics}
            memoryInjectionDiagnostics={memoryInjectionDiagnostics}
            compactionDiagnostics={compactionDiagnostics}
            activeModelName={activeModelName}
            variant="popover"
            defaultExpanded={false}
          />
        </div>
      ) : null}
      <button
        type="button"
        aria-label={usageAriaLabel}
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        className={cn(
          "relative inline-flex size-7 shrink-0 items-center justify-center rounded-full bg-transparent text-left transition hover:bg-[var(--panel-muted)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]",
          summary.isApproaching ? "text-[var(--warning)]" : "text-[var(--muted)]",
        )}
      >
        <svg data-testid="context-window-indicator" className="size-5 -rotate-90" viewBox="0 0 20 20" aria-hidden="true">
          <circle cx="10" cy="10" r={circleRadius} fill="none" stroke="var(--line)" strokeWidth="2" />
          <circle
            cx="10"
            cy="10"
            r={circleRadius}
            fill="none"
            stroke={summary.isApproaching ? "var(--warning)" : "var(--ink)"}
            strokeLinecap="round"
            strokeWidth="2"
            strokeDasharray={circumference}
            strokeDashoffset={dashOffset}
            className="transition-[stroke-dashoffset] duration-300"
          />
        </svg>
        <span className="sr-only">
          {detailLabel}; {locale === "zh-CN" ? `压缩阈值 ${compactThresholdLabel}，剩余 ${remainingLabel}` : `Compact threshold ${compactThresholdLabel}, ${remainingLabel} free`}
        </span>
      </button>
    </div>
  );
}

export function ContextWindowUsagePanel({
  usage,
  promptCacheDiagnostics,
  promptSectionTokenLedger,
  contextCacheDiagnostics,
  capabilityAssemblyDiagnostics,
  memoryInjectionDiagnostics,
  compactionDiagnostics,
  activeModelName,
  variant = "drawer",
  defaultExpanded = false,
}: {
  usage: ContextWindowUsageDisplay | null;
  promptCacheDiagnostics?: PromptCacheDiagnosticsDisplay | null;
  promptSectionTokenLedger?: PromptSectionTokenLedgerDisplay | null;
  contextCacheDiagnostics?: ContextCacheDiagnosticsDisplay | null;
  capabilityAssemblyDiagnostics?: CapabilityAssemblyDiagnosticsDisplay | null;
  memoryInjectionDiagnostics?: MemoryInjectionDiagnosticsDisplay | null;
  compactionDiagnostics?: CompactionDiagnosticsDisplay | null;
  activeModelName?: string | null;
  variant?: "drawer" | "popover";
  defaultExpanded?: boolean;
}) {
  const { locale } = useI18n();
  const ui = workspaceCopy(locale);
  const [expanded, setExpanded] = useState(defaultExpanded);
  const copy =
    locale === "zh-CN"
      ? {
          title: "上下文窗口",
          billed: "账单用量",
          current: "当前上下文",
          threshold: "压缩阈值",
          window: "最大窗口",
          source: "来源",
          model: "模型",
          provider: "Provider",
          details: "详细构成",
          showDetails: "展开详情",
          hideDetails: "收起详情",
          lastCall: "最近调用",
          input: "输入",
          output: "输出",
          total: "总量",
          cacheWrite: "缓存写入",
          cacheRead: "缓存读取",
          requests: "请求",
          dominant: "主要压力",
          cost: "预估成本",
          cacheHit: "缓存命中",
          cacheSaved: "缓存节省",
          compactionLevel: "压缩层级",
          compactionReason: "压缩原因",
          compactionInput: "压缩前",
          compactionSummary: "摘要",
          compactionSaved: "节省",
          keepRecent: "保留最近",
          promptCache: "Prompt cache",
          contextCache: "上下文缓存",
          projectContextCache: "项目上下文",
          runtimePathCache: "运行路径",
          cacheStatus: "缓存状态",
          fingerprint: "Fingerprint",
          contextFiles: "文件",
          truncatedFiles: "截断文件",
          totalChars: "字符",
          scanBudget: "扫描预算",
          scanTruncated: "扫描截断",
          scopes: "Scopes",
          appliesTo: "Applies to",
          runtimeRoots: "路径根",
          hostBridges: "Host bridges",
          promptSections: "Prompt sections",
          capabilityDiagnostics: "Capability diagnostics",
          memoryInjection: "记忆注入",
          compactionDiagnostics: "压缩诊断",
          summarySource: "摘要来源",
          summaryModel: "摘要模型",
          archivedMessages: "归档消息",
          toolCalls: "工具调用",
          toolResults: "工具结果",
          imageBlocks: "图片块",
          truncatedMessages: "截断消息",
          prunedToolResults: "裁剪工具结果",
          serializedTokens: "序列化 Token",
          summaryPromptTokens: "摘要 Prompt",
          stablePrompt: "Stable prompt",
          volatilePrompt: "Turn injection",
          memoryStatus: "状态",
          memoryRecall: "召回",
          memoryStores: "记忆库",
          memorySources: "来源类型",
          memoryRendered: "注入 Token",
          memoryBudget: "注入预算",
          memoryQuery: "查询 Token",
          memoryCurated: "精选",
          memoryArchive: "归档",
          memoryEvidence: "证据",
          memoryProviderNotes: "Provider notes",
          memoryTruncated: "已截断",
          memorySnapshot: "Snapshot",
          memoryError: "错误",
          visibleTools: "可见工具",
          deferredTools: "延迟工具",
          schemaVisible: "Schema 可见",
          schemaDeferredTokens: "Schema 延迟量",
          schemaTotal: "Schema 总量",
          schemaBudget: "Schema 预算",
          schemaRemaining: "剩余预算",
          schemaCompacted: "Schema 压缩",
          schemaDeferred: "Schema 延迟",
          actionDeferred: "任务过滤延迟",
          schemaTruncated: "Schema 截断",
          assemblyStages: "装配阶段",
          slowestAssemblyStage: "最慢装配",
          skillsDiscovery: "Skills 发现",
          skillsDiscoveryCache: "Skills 缓存",
          skillsDiscoveryRoots: "Skill roots",
          skillsDiscoveryManifests: "Skills 总量",
          skillsDiscoveryEnabled: "Skills 可用",
          skillsDiscoveryPackages: "安装包",
          skillsDiscoveryStages: "Skills 阶段",
          slowestSkillsDiscoveryStage: "最慢 Skills",
          visibleSources: "可见来源",
          deferredSources: "延迟来源",
          visibleGroups: "可见分组",
          deferredGroups: "延迟分组",
          promptCacheDelta: "本次",
          promptCacheCumulative: "累计",
          promptCacheHits: "命中",
          promptCacheMisses: "未命中",
          promptCacheWrites: "写入",
          promptCacheBypasses: "绕过",
          promptCacheEvictions: "淘汰",
          promptCacheSize: "大小",
          empty: "尚未采集",
          pending: "待上报",
          toCompaction: "距自动压缩",
          remaining: "剩余",
          used: "已用",
          compacted: "已自动压缩",
          approaching: "接近阈值",
          onTrack: "正常",
        }
      : {
          title: "Context window",
          billed: "Billed usage",
          current: "Current context",
          threshold: "Compact threshold",
          window: "Max window",
          source: "Source",
          model: "Model",
          provider: "Provider",
          details: "Breakdown",
          showDetails: "Show details",
          hideDetails: "Hide details",
          lastCall: "Last call",
          input: "Input",
          output: "Output",
          total: "Total",
          cacheWrite: "Cache write",
          cacheRead: "Cache read",
          requests: "Requests",
          dominant: "Main pressure",
          cost: "Estimated cost",
          cacheHit: "Cache hit",
          cacheSaved: "Cache saved",
          compactionLevel: "Compaction level",
          compactionReason: "Reason",
          compactionInput: "Before compaction",
          compactionSummary: "Summary",
          compactionSaved: "Saved",
          keepRecent: "Keep recent",
          promptCache: "Prompt cache",
          contextCache: "Context cache",
          projectContextCache: "Project context",
          runtimePathCache: "Runtime paths",
          cacheStatus: "Cache status",
          fingerprint: "Fingerprint",
          contextFiles: "Files",
          truncatedFiles: "Truncated files",
          totalChars: "Chars",
          scanBudget: "Scan budget",
          scanTruncated: "Scan truncated",
          scopes: "Scopes",
          appliesTo: "Applies to",
          runtimeRoots: "Roots",
          hostBridges: "Host bridges",
          promptSections: "Prompt sections",
          capabilityDiagnostics: "Capability diagnostics",
          memoryInjection: "Memory injection",
          compactionDiagnostics: "Compaction diagnostics",
          summarySource: "Summary source",
          summaryModel: "Summary model",
          archivedMessages: "Archived messages",
          toolCalls: "Tool calls",
          toolResults: "Tool results",
          imageBlocks: "Image blocks",
          truncatedMessages: "Truncated messages",
          prunedToolResults: "Pruned tool results",
          serializedTokens: "Serialized tokens",
          summaryPromptTokens: "Summary prompt",
          stablePrompt: "Stable prompt",
          volatilePrompt: "Turn injection",
          memoryStatus: "Status",
          memoryRecall: "Recall",
          memoryStores: "Stores",
          memorySources: "Sources",
          memoryRendered: "Rendered tokens",
          memoryBudget: "Budget",
          memoryQuery: "Query tokens",
          memoryCurated: "Curated",
          memoryArchive: "Archive",
          memoryEvidence: "Evidence",
          memoryProviderNotes: "Provider notes",
          memoryTruncated: "Truncated",
          memorySnapshot: "Snapshot",
          memoryError: "Error",
          visibleTools: "Visible tools",
          deferredTools: "Deferred tools",
          schemaVisible: "Visible schema",
          schemaDeferredTokens: "Deferred schema tokens",
          schemaTotal: "Schema total",
          schemaBudget: "Schema budget",
          schemaRemaining: "Remaining budget",
          schemaCompacted: "Schema compacted",
          schemaDeferred: "Schema deferred",
          actionDeferred: "Action deferred",
          schemaTruncated: "Schema truncated",
          assemblyStages: "Assembly stages",
          slowestAssemblyStage: "Slowest assembly",
          skillsDiscovery: "Skills discovery",
          skillsDiscoveryCache: "Skills cache",
          skillsDiscoveryRoots: "Skill roots",
          skillsDiscoveryManifests: "Skills total",
          skillsDiscoveryEnabled: "Skills enabled",
          skillsDiscoveryPackages: "Packages",
          skillsDiscoveryStages: "Skills stages",
          slowestSkillsDiscoveryStage: "Slowest skills",
          visibleSources: "Visible sources",
          deferredSources: "Deferred sources",
          visibleGroups: "Visible groups",
          deferredGroups: "Deferred groups",
          promptCacheDelta: "Run delta",
          promptCacheCumulative: "Cumulative",
          promptCacheHits: "Hits",
          promptCacheMisses: "Misses",
          promptCacheWrites: "Writes",
          promptCacheBypasses: "Bypasses",
          promptCacheEvictions: "Evictions",
          promptCacheSize: "Size",
          empty: "Not collected",
          pending: "Pending",
          toCompaction: "to compaction",
          remaining: "left",
          used: "used",
          compacted: "Compacted",
          approaching: "Approaching",
          onTrack: "Normal",
        };

  if (!usage) {
    return <InfoRow label={copy.title} value={copy.empty} />;
  }

  const summary = buildContextUsageSummary(usage, locale, activeModelName);
  const promptLedgerSummary = buildPromptSectionTokenLedgerSummary(promptSectionTokenLedger);
  const contextCacheSummary = buildContextCacheDiagnosticsSummary(contextCacheDiagnostics);
  const capabilityDiagnosticsSummary = buildCapabilityAssemblyDiagnosticsSummary(capabilityAssemblyDiagnostics);
  const memoryInjectionSummary = buildMemoryInjectionDiagnosticsSummary(memoryInjectionDiagnostics);
  const compactionDiagnosticsSummary = buildCompactionDiagnosticsSummary(compactionDiagnostics ?? usage.compaction_diagnostics);
  const tone = summary.isCompacted ? "accent" : summary.isApproaching ? "warning" : "neutral";
  const contextLabel = formatTokenCount(summary.contextTokens, copy.pending);
  const thresholdLabel = formatTokenCount(summary.thresholdTokens, copy.pending);
  const windowLabel = formatTokenCount(summary.windowTokens, copy.pending);
  const billedLabel = formatTokenCount(summary.providerTotalTokens, copy.pending);
  const progressStyle = {
    width: `${Math.round(summary.pressureRatio * 100)}%`,
  };
  const rows = summary.claudeRows.length > 0 ? summary.claudeRows : summary.breakdownRows;
  const cardClass =
    variant === "popover"
      ? "rounded-[0.8rem] border border-[var(--line)] bg-[var(--panel)] p-3 shadow-none"
      : "rounded-[0.8rem] border border-[var(--line)] bg-[var(--panel)] p-3 shadow-sm";

  return (
    <div className={cn(cardClass, "min-w-0")} data-testid="context-window-usage-panel">
      <button
        type="button"
        onClick={() => setExpanded((current) => !current)}
        className="flex w-full items-center justify-between gap-3 text-left"
        aria-expanded={expanded}
        aria-label={expanded ? copy.hideDetails : copy.showDetails}
      >
        <span className="flex min-w-0 flex-1 items-center gap-2">
          <span className="h-2 w-2 shrink-0 rounded-full bg-[var(--primary)]" />
          <span className="min-w-0 truncate text-[13px] text-[var(--muted)]" title={copy.title}>
            {copy.title}
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-2">
          <span className="font-[var(--mono-font)] text-[13px] text-[var(--ink)]">
            {contextLabel} / {windowLabel}
            {summary.usedPercentLabel ? ` (${summary.usedPercentLabel})` : ""}
          </span>
          <ChevronDownIcon className={cn("size-4 text-[var(--muted)] transition", expanded ? "rotate-180" : "")} />
        </span>
      </button>

      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[var(--line)]">
        <div
          className={cn(
            "h-full rounded-full transition-[width] duration-300",
            summary.isCompacted
              ? "bg-[var(--primary)]"
              : summary.isApproaching
                ? "bg-[var(--warning)]"
                : "bg-[var(--primary)]",
          )}
          style={progressStyle}
        />
      </div>

      <div className="mt-2 space-y-1.5">
        {rows.map((item) =>
          item.tokens !== null ? (
            <ContextCompactBreakdownRow
              key={item.key}
              color={item.color}
              label={item.label}
              tokens={item.tokens}
              percentage={item.percentage}
              total={summary.windowTokens}
            />
          ) : null,
        )}
      </div>

      {expanded ? (
        <div className="mt-3 space-y-2 border-t border-[var(--line)] pt-3">
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge tone={tone}>{summary.isCompacted ? copy.compacted : summary.isApproaching ? copy.approaching : copy.onTrack}</Badge>
            {summary.remainingPercentLabel ? <Badge tone="neutral">{summary.remainingPercentLabel} {copy.remaining}</Badge> : null}
            {summary.isEstimated ? <Badge tone="neutral">{ui.transcript.contextEstimated}</Badge> : null}
            {summary.compactionLevel > 0 ? <Badge tone="neutral">L{summary.compactionLevel} {summary.compactionLevelLabel}</Badge> : null}
          </div>
          <div className="grid gap-1 text-xs leading-5 text-[var(--muted)]">
            <div className="flex justify-between gap-3">
              <span>{copy.current}</span>
              <span className="text-right font-[var(--mono-font)] text-[var(--ink)]">{contextLabel}</span>
            </div>
            <div className="flex justify-between gap-3">
              <span>{copy.threshold}</span>
              <span className="text-right font-[var(--mono-font)] text-[var(--ink)]">{thresholdLabel}</span>
            </div>
            <div className="flex justify-between gap-3">
              <span>{copy.source}</span>
              <span className="text-right font-[var(--mono-font)] text-[var(--ink)]">{summary.contextSourceLabel ?? copy.pending}</span>
            </div>
            <div className="flex justify-between gap-3">
              <span>{copy.model}</span>
              <HoverRevealText
                value={summary.modelLabel}
                className="max-w-[12rem] text-right font-[var(--mono-font)] text-[var(--ink)]"
              />
            </div>
            {summary.providerLabel ? (
              <div className="flex justify-between gap-3">
                <span>{copy.provider}</span>
                <span className="font-[var(--mono-font)] text-[var(--ink)]">{summary.providerLabel}</span>
              </div>
            ) : null}
            {summary.requestCount !== null ? (
              <div className="flex justify-between gap-3">
                <span>{copy.requests}</span>
                <span className="font-[var(--mono-font)] text-[var(--ink)]">{formatTokenCount(summary.requestCount)}</span>
              </div>
            ) : null}
            {summary.dominantContextLabel ? (
              <div className="flex justify-between gap-3">
                <span>{copy.dominant}</span>
                <HoverRevealText
                  value={summary.dominantContextLabel}
                  className="max-w-[12rem] text-right font-[var(--mono-font)] text-[var(--ink)]"
                />
              </div>
            ) : null}
            {summary.compactionLevel > 0 ? (
              <>
                <div className="flex justify-between gap-3">
                  <span>{copy.compactionLevel}</span>
                  <span className="text-right font-[var(--mono-font)] text-[var(--ink)]">
                    L{summary.compactionLevel} {summary.compactionLevelLabel}
                  </span>
                </div>
                {summary.compactionReason ? (
                  <div className="flex justify-between gap-3">
                    <span>{copy.compactionReason}</span>
                    <HoverRevealText
                      value={summary.compactionReason}
                      className="max-w-[13rem] text-right font-[var(--mono-font)] text-[var(--ink)]"
                    />
                  </div>
                ) : null}
                {summary.compactionInputTokens !== null ? (
                  <div className="flex justify-between gap-3">
                    <span>{copy.compactionInput}</span>
                    <span className="font-[var(--mono-font)] text-[var(--ink)]">{formatTokenCount(summary.compactionInputTokens)}</span>
                  </div>
                ) : null}
                {summary.compactionSummaryTokens !== null ? (
                  <div className="flex justify-between gap-3">
                    <span>{copy.compactionSummary}</span>
                    <span className="font-[var(--mono-font)] text-[var(--ink)]">{formatTokenCount(summary.compactionSummaryTokens)}</span>
                  </div>
                ) : null}
                {summary.compactionSavingsTokens !== null ? (
                  <div className="flex justify-between gap-3">
                    <span>{copy.compactionSaved}</span>
                    <span className="font-[var(--mono-font)] text-[var(--ink)]">{formatTokenCount(summary.compactionSavingsTokens)}</span>
                  </div>
                ) : null}
                {summary.compactionKeepRecentTurns !== null ? (
                  <div className="flex justify-between gap-3">
                    <span>{copy.keepRecent}</span>
                    <span className="font-[var(--mono-font)] text-[var(--ink)]">{summary.compactionKeepRecentTurns}</span>
                  </div>
                ) : null}
              </>
            ) : null}
            {summary.costLabel ? (
              <div className="flex justify-between gap-3">
                <span>{copy.cost}</span>
                <span className="font-[var(--mono-font)] text-[var(--ink)]">{summary.costLabel}</span>
              </div>
            ) : null}
          </div>

          {summary.hasBreakdown ? (
            <div className="rounded-[0.7rem] border border-[var(--line)] bg-[var(--panel)] p-2">
              <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.06em] text-[var(--muted)]">
                {copy.details}
              </div>
              <div className="space-y-1.5">
                {summary.breakdownRows.map((item) =>
                  item.tokens !== null ? (
                    <ContextBreakdownRow
                      key={item.key}
                      color={item.color}
                      label={item.label}
                      tokens={item.tokens}
                      percentage={item.percentage}
                      total={item.group === "reserve" ? summary.windowTokens : summary.contextTokens}
                    />
                  ) : null,
                )}
              </div>
            </div>
            ) : null}

          {promptCacheDiagnostics ? (
            <PromptCacheDiagnosticsPanel diagnostics={promptCacheDiagnostics} labels={copy} />
          ) : null}

          {contextCacheSummary.hasDiagnostics ? (
            <ContextCacheDiagnosticsPanel summary={contextCacheSummary} labels={copy} />
          ) : null}

          {promptLedgerSummary.hasLedger ? (
            <PromptSectionTokenLedgerPanel summary={promptLedgerSummary} labels={copy} />
          ) : null}

          {memoryInjectionSummary.hasDiagnostics ? (
            <MemoryInjectionDiagnosticsPanel summary={memoryInjectionSummary} labels={copy} />
          ) : null}

          {compactionDiagnosticsSummary.hasDiagnostics ? (
            <CompactionDiagnosticsPanel summary={compactionDiagnosticsSummary} labels={copy} />
          ) : null}

          {capabilityDiagnosticsSummary.hasDiagnostics ? (
            <CapabilityAssemblyDiagnosticsPanel summary={capabilityDiagnosticsSummary} labels={copy} />
          ) : null}

          {(summary.cacheWriteTokens !== null || summary.cacheReadTokens !== null) ? (
            <div className="grid grid-cols-2 gap-1.5">
              {summary.cacheWriteTokens !== null ? (
                <MetricSmall label={copy.cacheWrite} value={formatTokenCount(summary.cacheWriteTokens, copy.pending)} tone="primary" />
              ) : null}
              {summary.cacheReadTokens !== null ? (
                <MetricSmall label={copy.cacheRead} value={formatTokenCount(summary.cacheReadTokens, copy.pending)} tone="success" />
              ) : null}
              {summary.cacheHitRatio !== null ? (
                <MetricSmall label={copy.cacheHit} value={formatPercent(summary.cacheHitRatio)} tone="success" />
              ) : null}
              {summary.cacheSavingsTokens !== null ? (
                <MetricSmall label={copy.cacheSaved} value={formatTokenCount(summary.cacheSavingsTokens, copy.pending)} tone="success" />
              ) : null}
            </div>
          ) : null}

          {summary.hasProviderUsage ? (
            <div className="rounded-[0.7rem] border border-[var(--line)] bg-[var(--panel-muted)] p-2 text-xs text-[var(--muted)]">
              <div className="mb-1 font-semibold uppercase tracking-[0.06em]">{copy.billed}</div>
              <div className="grid grid-cols-3 gap-1.5">
                <MetricSmall label={copy.total} value={billedLabel} />
                <MetricSmall label={copy.input} value={formatTokenCount(summary.inputTokens, copy.pending)} />
                <MetricSmall label={copy.output} value={formatTokenCount(summary.outputTokens, copy.pending)} />
              </div>
            </div>
          ) : null}

          {summary.hasLastUsage ? (
            <div className="rounded-[0.7rem] border border-[var(--line)] bg-[var(--panel-muted)] p-2 text-xs text-[var(--muted)]">
              <div className="mb-1 font-semibold uppercase tracking-[0.06em]">{copy.lastCall}</div>
              <div className="grid gap-1">
                <div>{copy.total}: {formatTokenCount(summary.lastTotalTokens, copy.pending)}</div>
                <div>{copy.input}: {formatTokenCount(summary.lastInputTokens, copy.pending)} / {copy.output}: {formatTokenCount(summary.lastOutputTokens, copy.pending)}</div>
                {(summary.lastCacheWriteTokens !== null || summary.lastCacheReadTokens !== null) ? (
                  <div>
                    {summary.lastCacheWriteTokens !== null ? `${copy.cacheWrite}: ${formatTokenCount(summary.lastCacheWriteTokens, copy.pending)} ` : ""}
                    {summary.lastCacheReadTokens !== null ? `${copy.cacheRead}: ${formatTokenCount(summary.lastCacheReadTokens, copy.pending)}` : ""}
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}
        </div>
      ) : (
        <div className="mt-2 flex items-center justify-between gap-2 text-[11px] text-[var(--muted)]">
          <span>{copy.current}: {contextLabel}</span>
          <span>{summary.contextSourceLabel ?? copy.pending}</span>
        </div>
      )}
    </div>
  );
}

function ContextWindowSummary({
  contextWindowUsage,
  promptCacheDiagnostics,
  promptSectionTokenLedger,
  contextCacheDiagnostics,
  capabilityAssemblyDiagnostics,
  memoryInjectionDiagnostics,
  compactionDiagnostics,
}: {
  contextWindowUsage?: ContextWindowUsageDisplay | null;
  promptCacheDiagnostics?: PromptCacheDiagnosticsDisplay | null;
  promptSectionTokenLedger?: PromptSectionTokenLedgerDisplay | null;
  contextCacheDiagnostics?: ContextCacheDiagnosticsDisplay | null;
  capabilityAssemblyDiagnostics?: CapabilityAssemblyDiagnosticsDisplay | null;
  memoryInjectionDiagnostics?: MemoryInjectionDiagnosticsDisplay | null;
  compactionDiagnostics?: CompactionDiagnosticsDisplay | null;
}) {
  const { locale } = useI18n();
  const ui = workspaceCopy(locale);

  if (!contextWindowUsage) {
    return <InfoRow label={ui.drawer.contextWindow} value={locale === "zh-CN" ? "尚未采集" : "Not collected"} />;
  }

  return (
    <ContextWindowUsagePanel
      usage={contextWindowUsage}
      promptCacheDiagnostics={promptCacheDiagnostics}
      promptSectionTokenLedger={promptSectionTokenLedger}
      contextCacheDiagnostics={contextCacheDiagnostics}
      capabilityAssemblyDiagnostics={capabilityAssemblyDiagnostics}
      memoryInjectionDiagnostics={memoryInjectionDiagnostics}
      compactionDiagnostics={compactionDiagnostics}
      activeModelName={null}
      variant="drawer"
      defaultExpanded={false}
    />
  );
}

function PromptCacheDiagnosticsPanel({
  diagnostics,
  labels,
}: {
  diagnostics: PromptCacheDiagnosticsDisplay;
  labels: {
    promptCache: string;
    promptCacheDelta: string;
    promptCacheCumulative: string;
    promptCacheHits: string;
    promptCacheMisses: string;
    promptCacheWrites: string;
    promptCacheBypasses: string;
    promptCacheEvictions: string;
    promptCacheSize: string;
    pending: string;
  };
}) {
  const deltaHits = normalizeInteger(diagnostics.hits) ?? 0;
  const deltaMisses = normalizeInteger(diagnostics.misses) ?? 0;
  const deltaWrites = normalizeInteger(diagnostics.writes) ?? 0;
  const deltaBypasses = normalizeInteger(diagnostics.bypasses) ?? 0;
  const deltaEvictions = normalizeInteger(diagnostics.evictions) ?? 0;
  const cumulativeHits = normalizeInteger(diagnostics.cumulative_hits);
  const cumulativeMisses = normalizeInteger(diagnostics.cumulative_misses);
  const cumulativeEvictions = normalizeInteger(diagnostics.cumulative_evictions);
  const cumulativeSize = normalizeInteger(diagnostics.cumulative_size);
  const maxEntries = normalizeInteger(diagnostics.max_entries);
  const sizeAfter = normalizeInteger(diagnostics.size_after) ?? cumulativeSize;
  const deltaParts = [
    `${labels.promptCacheHits} ${formatTokenCount(deltaHits)}`,
    `${labels.promptCacheMisses} ${formatTokenCount(deltaMisses)}`,
    `${labels.promptCacheWrites} ${formatTokenCount(deltaWrites)}`,
    `${labels.promptCacheBypasses} ${formatTokenCount(deltaBypasses)}`,
  ];
  const cumulativeParts = [
    cumulativeHits !== null ? `${labels.promptCacheHits} ${formatTokenCount(cumulativeHits)}` : null,
    cumulativeMisses !== null ? `${labels.promptCacheMisses} ${formatTokenCount(cumulativeMisses)}` : null,
    cumulativeSize !== null ? `${labels.promptCacheSize} ${formatTokenCount(cumulativeSize)}` : null,
    maxEntries !== null ? `max ${formatTokenCount(maxEntries)}` : null,
  ].filter((item): item is string => Boolean(item));
  const sizeLabel =
    sizeAfter !== null
      ? `${formatTokenCount(sizeAfter)}${maxEntries !== null ? ` / ${formatTokenCount(maxEntries)}` : ""}`
      : labels.pending;

  return (
    <div className="rounded-[0.7rem] border border-[var(--line)] bg-[var(--panel-muted)] p-2 text-xs text-[var(--muted)]">
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="font-semibold uppercase tracking-[0.06em]">{labels.promptCache}</span>
        <span className="font-[var(--mono-font)] text-[var(--ink)]">{sizeLabel}</span>
      </div>
      <div className="grid gap-1">
        <div>
          <span className="text-[var(--muted)]">{labels.promptCacheDelta}: </span>
          <span className="font-[var(--mono-font)] text-[var(--ink)]">{deltaParts.join(" · ")}</span>
        </div>
        {cumulativeParts.length > 0 ? (
          <div>
            <span className="text-[var(--muted)]">{labels.promptCacheCumulative}: </span>
            <span className="font-[var(--mono-font)] text-[var(--ink)]">{cumulativeParts.join(" · ")}</span>
          </div>
        ) : null}
        {(deltaEvictions > 0 || cumulativeEvictions !== null) ? (
          <div>
            <span className="text-[var(--muted)]">{labels.promptCacheEvictions}: </span>
            <span className="font-[var(--mono-font)] text-[var(--ink)]">
              {formatTokenCount(deltaEvictions)}
              {cumulativeEvictions !== null ? ` / ${formatTokenCount(cumulativeEvictions)}` : ""}
            </span>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function PromptSectionTokenLedgerPanel({
  summary,
  labels,
}: {
  summary: PromptSectionTokenLedgerSummary;
  labels: {
    promptSections: string;
    stablePrompt: string;
    volatilePrompt: string;
    pending: string;
  };
}) {
  return (
    <div className="rounded-[0.7rem] border border-[var(--line)] bg-[var(--panel-muted)] p-2 text-xs text-[var(--muted)]">
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="font-semibold uppercase tracking-[0.06em]">{labels.promptSections}</span>
        <span className="font-[var(--mono-font)] text-[var(--ink)]">
          {formatTokenCount(summary.stableTotal, labels.pending)}
          {" + "}
          {formatTokenCount(summary.volatileTotal, labels.pending)}
        </span>
      </div>
      <div className="grid gap-1">
        <PromptSectionTokenLine
          label={labels.stablePrompt}
          total={summary.stableTotal}
          sections={summary.stableSections}
          pending={labels.pending}
        />
        <PromptSectionTokenLine
          label={labels.volatilePrompt}
          total={summary.volatileTotal}
          sections={summary.volatileSections}
          pending={labels.pending}
        />
      </div>
    </div>
  );
}

function PromptSectionTokenLine({
  label,
  total,
  sections,
  pending,
}: {
  label: string;
  total: number | null;
  sections: Array<{ name: string; tokens: number }>;
  pending: string;
}) {
  const topSections = sections.slice(0, 3).map((section) => `${section.name}:${formatTokenCount(section.tokens)}`);
  return (
    <div>
      <span className="text-[var(--muted)]">{label}: </span>
      <span className="font-[var(--mono-font)] text-[var(--ink)]">
        {formatTokenCount(total, pending)}
        {topSections.length > 0 ? ` (${topSections.join(" · ")})` : ""}
      </span>
    </div>
  );
}

function ContextCacheDiagnosticsPanel({
  summary,
  labels,
}: {
  summary: ContextCacheDiagnosticsSummary;
  labels: {
    contextCache: string;
    projectContextCache: string;
    runtimePathCache: string;
    cacheStatus: string;
    fingerprint: string;
    contextFiles: string;
    truncatedFiles: string;
    totalChars: string;
    scanBudget: string;
    scanTruncated: string;
    scopes: string;
    appliesTo: string;
    runtimeRoots: string;
    hostBridges: string;
    pending: string;
  };
}) {
  const projectLabel = summary.projectStatus
    ? `${labels.projectContextCache}: ${summary.projectStatus}`
    : `${labels.projectContextCache}: ${labels.pending}`;
  const runtimeLabel = summary.runtimeStatus
    ? `${labels.runtimePathCache}: ${summary.runtimeStatus}`
    : `${labels.runtimePathCache}: ${labels.pending}`;
  const scopeLine = formatCapabilityCountLine(labels.scopes, summary.projectScopeCounts);
  const appliesToLine = formatCapabilityCountLine(labels.appliesTo, summary.projectAppliesToCounts);

  return (
    <div className="rounded-[0.7rem] border border-[var(--line)] bg-[var(--panel-muted)] p-2 text-xs text-[var(--muted)]">
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="font-semibold uppercase tracking-[0.06em]">{labels.contextCache}</span>
        <HoverRevealText
          value={`${summary.projectStatus ?? labels.pending} / ${summary.runtimeStatus ?? labels.pending}`}
          className="max-w-[12rem] font-[var(--mono-font)] text-[var(--ink)]"
        />
      </div>
      <div className="grid grid-cols-2 gap-1.5">
        <MetricSmall label={labels.projectContextCache} value={projectLabel} tone={cacheStatusTone(summary.projectStatus)} />
        <MetricSmall label={labels.runtimePathCache} value={runtimeLabel} tone={cacheStatusTone(summary.runtimeStatus)} />
        <MetricSmall label={labels.contextFiles} value={formatTokenCount(summary.projectFileCount, labels.pending)} />
        <MetricSmall label={labels.truncatedFiles} value={formatTokenCount(summary.projectTruncatedFileCount, labels.pending)} tone={summary.projectTruncatedFileCount ? "neutral" : "success"} />
        <MetricSmall label={labels.totalChars} value={formatTokenCount(summary.projectTotalChars, labels.pending)} />
        <MetricSmall label={labels.scanBudget} value={formatDiscoveryScanBudget(summary, labels.pending)} tone={summary.projectDiscoveryScanTruncated ? "warning" : "success"} />
        <MetricSmall label={labels.scanTruncated} value={summary.projectDiscoveryScanTruncated ? "true" : "false"} tone={summary.projectDiscoveryScanTruncated ? "warning" : "success"} />
        <MetricSmall label={labels.runtimeRoots} value={formatTokenCount(summary.runtimeRootCount, labels.pending)} />
        <MetricSmall label={labels.hostBridges} value={formatTokenCount(summary.runtimeHostBridgeCount, labels.pending)} />
      </div>
      <div className="mt-2 grid gap-1 font-[var(--mono-font)] text-[11px] text-[var(--ink)]">
        {summary.projectFingerprint ? <div>{labels.projectContextCache} {labels.fingerprint}: {shortFingerprint(summary.projectFingerprint)}</div> : null}
        {summary.runtimeFingerprint ? <div>{labels.runtimePathCache} {labels.fingerprint}: {shortFingerprint(summary.runtimeFingerprint)}</div> : null}
        {scopeLine ? <div>{scopeLine}</div> : null}
        {appliesToLine ? <div>{appliesToLine}</div> : null}
      </div>
    </div>
  );
}

function MemoryInjectionDiagnosticsPanel({
  summary,
  labels,
}: {
  summary: MemoryInjectionDiagnosticsSummary;
  labels: {
    memoryInjection: string;
    memoryStatus: string;
    memoryRecall: string;
    memoryStores: string;
    memorySources: string;
    memoryRendered: string;
    memoryBudget: string;
    memoryQuery: string;
    memoryCurated: string;
    memoryArchive: string;
    memoryEvidence: string;
    memoryProviderNotes: string;
    memoryTruncated: string;
    memorySnapshot: string;
    memoryError: string;
    pending: string;
  };
}) {
  const recallLabel = [
    `${labels.memoryCurated} ${formatTokenCount(summary.curatedMatchCount, labels.pending)}`,
    `${labels.memoryArchive} ${formatTokenCount(summary.archiveHitCount, labels.pending)}`,
    `${labels.memoryEvidence} ${formatTokenCount(summary.evidenceCount, labels.pending)}`,
  ].join(" · ");
  const renderedLabel =
    summary.renderedTokens !== null || summary.tokenBudget !== null
      ? `${formatTokenCount(summary.renderedTokens, labels.pending)} / ${formatTokenCount(summary.tokenBudget, labels.pending)}`
      : labels.pending;
  const storesLine = formatCapabilityCountLine(labels.memoryStores, summary.topStores);
  const sourcesLine = formatCapabilityCountLine(labels.memorySources, summary.topSourceKinds);

  return (
    <div className="rounded-[0.7rem] border border-[var(--line)] bg-[var(--panel-muted)] p-2 text-xs text-[var(--muted)]">
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="font-semibold uppercase tracking-[0.06em]">{labels.memoryInjection}</span>
        <HoverRevealText
          value={`${summary.status ?? labels.pending}${summary.truncated ? ` · ${labels.memoryTruncated}` : ""}`}
          className="max-w-[12rem] font-[var(--mono-font)] text-[var(--ink)]"
        />
      </div>
      <div className="grid grid-cols-2 gap-1.5">
        <MetricSmall
          label={labels.memoryStatus}
          value={summary.source ? `${summary.source}:${summary.status ?? "unknown"}` : labels.pending}
          tone={summary.errorType ? "neutral" : "primary"}
        />
        <MetricSmall label={labels.memoryRendered} value={renderedLabel} tone={summary.truncated ? "neutral" : "success"} />
        <MetricSmall label={labels.memoryQuery} value={formatTokenCount(summary.queryTokens, labels.pending)} />
        <MetricSmall label={labels.memoryBudget} value={formatTokenCount(summary.tokenBudget, labels.pending)} />
        <MetricSmall label={labels.memoryCurated} value={formatTokenCount(summary.curatedMatchCount, labels.pending)} tone="primary" />
        <MetricSmall label={labels.memoryArchive} value={formatTokenCount(summary.archiveHitCount, labels.pending)} />
        <MetricSmall label={labels.memoryEvidence} value={formatTokenCount(summary.evidenceCount, labels.pending)} />
        <MetricSmall label={labels.memoryProviderNotes} value={formatTokenCount(summary.providerNoteCount, labels.pending)} />
      </div>
      <div className="mt-2 grid gap-1 font-[var(--mono-font)] text-[11px] text-[var(--ink)]">
        <div>{labels.memoryRecall}: {recallLabel}</div>
        {summary.snapshotId ? <div>{labels.memorySnapshot}: {summary.snapshotId}</div> : null}
        {summary.errorType ? <div>{labels.memoryError}: {summary.errorType}</div> : null}
        {storesLine ? <div>{storesLine}</div> : null}
        {sourcesLine ? <div>{sourcesLine}</div> : null}
      </div>
    </div>
  );
}

function CompactionDiagnosticsPanel({
  summary,
  labels,
}: {
  summary: CompactionDiagnosticsSummary;
  labels: {
    compactionDiagnostics: string;
    summarySource: string;
    summaryModel: string;
    archivedMessages: string;
    toolCalls: string;
    toolResults: string;
    imageBlocks: string;
    truncatedMessages: string;
    prunedToolResults: string;
    serializedTokens: string;
    summaryPromptTokens: string;
    pending: string;
  };
}) {
  const sourceLabel = summary.summarySource ?? labels.pending;
  const modelLabel = summary.summaryModel ?? labels.pending;
  const toolEvidence =
    summary.toolCallCount !== null || summary.toolResultCount !== null
      ? `${formatTokenCount(summary.toolCallCount, labels.pending)} / ${formatTokenCount(summary.toolResultCount, labels.pending)}`
      : labels.pending;

  return (
    <div className="rounded-[0.7rem] border border-[var(--line)] bg-[var(--panel-muted)] p-2 text-xs text-[var(--muted)]">
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="font-semibold uppercase tracking-[0.06em]">{labels.compactionDiagnostics}</span>
        <HoverRevealText
          value={sourceLabel}
          className="max-w-[12rem] font-[var(--mono-font)] text-[var(--ink)]"
        />
      </div>
      <div className="grid grid-cols-2 gap-1.5">
        <MetricSmall label={labels.summarySource} value={sourceLabel} tone={summary.summarySource === "model" ? "success" : "neutral"} />
        <MetricSmall label={labels.summaryModel} value={modelLabel} />
        <MetricSmall label={labels.archivedMessages} value={formatTokenCount(summary.archivedMessageCount, labels.pending)} tone="primary" />
        <MetricSmall label={`${labels.toolCalls} / ${labels.toolResults}`} value={toolEvidence} />
        <MetricSmall label={labels.imageBlocks} value={formatTokenCount(summary.imageBlockCount, labels.pending)} />
        <MetricSmall label={labels.truncatedMessages} value={formatTokenCount(summary.truncatedMessageCount, labels.pending)} tone={summary.truncatedMessageCount ? "neutral" : "success"} />
        <MetricSmall label={labels.prunedToolResults} value={formatTokenCount(summary.prunedToolResultCount, labels.pending)} tone={summary.prunedToolResultCount ? "neutral" : "success"} />
        <MetricSmall label={labels.serializedTokens} value={formatTokenCount(summary.serializedTokens, labels.pending)} />
        <MetricSmall label={labels.summaryPromptTokens} value={formatTokenCount(summary.summaryPromptTokens, labels.pending)} />
      </div>
    </div>
  );
}

function CapabilityAssemblyDiagnosticsPanel({
  summary,
  labels,
}: {
  summary: CapabilityAssemblyDiagnosticsSummary;
  labels: {
    capabilityDiagnostics: string;
    visibleTools: string;
    deferredTools: string;
    schemaVisible: string;
    schemaDeferredTokens: string;
    schemaTotal: string;
    schemaBudget: string;
    schemaRemaining: string;
    schemaCompacted: string;
    schemaDeferred: string;
    actionDeferred: string;
    schemaTruncated: string;
    assemblyStages: string;
    slowestAssemblyStage: string;
    skillsDiscovery: string;
    skillsDiscoveryCache: string;
    skillsDiscoveryRoots: string;
    skillsDiscoveryManifests: string;
    skillsDiscoveryEnabled: string;
    skillsDiscoveryPackages: string;
    skillsDiscoveryStages: string;
    slowestSkillsDiscoveryStage: string;
    visibleSources: string;
    deferredSources: string;
    visibleGroups: string;
    deferredGroups: string;
    pending: string;
  };
}) {
  const visibleToolsLabel = labels.visibleTools.toLowerCase();
  const deferredToolsLabel = labels.deferredTools.toLowerCase();
  const toolSummary =
    summary.visibleToolCount !== null || summary.deferredToolCount !== null
      ? `${formatTokenCount(summary.visibleToolCount, labels.pending)} ${visibleToolsLabel} / ${formatTokenCount(summary.deferredToolCount, labels.pending)} ${deferredToolsLabel}`
      : labels.pending;
  const schemaBudgetLabel =
    summary.visibleSchemaTokens !== null || summary.visibleSchemaTokenBudget !== null
      ? `${formatTokenCount(summary.visibleSchemaTokens, labels.pending)} / ${formatTokenCount(summary.visibleSchemaTokenBudget, labels.pending)}`
      : labels.pending;
  const sourceLine = formatCapabilityCountLine(labels.visibleSources, summary.topVisibleSources)
    ?? formatCapabilityCountLine(labels.deferredSources, summary.topDeferredSources);
  const deferredSourceLine = formatCapabilityCountLine(labels.deferredSources, summary.topDeferredSources);
  const groupLine = formatCapabilityCountLine(labels.visibleGroups, summary.topVisibleGroups)
    ?? formatCapabilityCountLine(labels.deferredGroups, summary.topDeferredGroups);
  const deferredGroupLine = formatCapabilityCountLine(labels.deferredGroups, summary.topDeferredGroups);
  const skillsDiscoveryCacheLabel =
    summary.skillsDiscoveryCacheHit === null
      ? labels.pending
      : summary.skillsDiscoveryCacheHit
        ? "hit"
        : "miss";

  return (
    <div className="rounded-[0.7rem] border border-[var(--line)] bg-[var(--panel-muted)] p-2 text-xs text-[var(--muted)]">
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="font-semibold uppercase tracking-[0.06em]">{labels.capabilityDiagnostics}</span>
        <span className="font-[var(--mono-font)] text-[var(--ink)]">{toolSummary}</span>
      </div>
      <div className="grid grid-cols-2 gap-1.5">
        <MetricSmall label={labels.visibleTools} value={formatTokenCount(summary.visibleToolCount, labels.pending)} tone="primary" />
        <MetricSmall label={labels.deferredTools} value={formatTokenCount(summary.deferredToolCount, labels.pending)} />
        <MetricSmall label={labels.schemaVisible} value={formatTokenCount(summary.visibleSchemaTokens, labels.pending)} tone="primary" />
        <MetricSmall label={labels.schemaDeferredTokens} value={formatTokenCount(summary.deferredSchemaTokens, labels.pending)} />
        <MetricSmall label={labels.schemaTotal} value={formatTokenCount(summary.totalSchemaTokens, labels.pending)} />
        <MetricSmall label={labels.schemaBudget} value={schemaBudgetLabel} tone="primary" />
        <MetricSmall label={labels.schemaRemaining} value={formatTokenCount(summary.visibleSchemaBudgetRemainingTokens, labels.pending)} tone="success" />
        <MetricSmall label={labels.schemaDeferred} value={formatTokenCount(summary.schemaDeferredToolCount, labels.pending)} />
        <MetricSmall label={labels.schemaCompacted} value={formatTokenCount(summary.schemaCompactedToolCount, labels.pending)} />
        <MetricSmall label={labels.actionDeferred} value={formatTokenCount(summary.actionPrefilterDeferredToolCount, labels.pending)} />
        <MetricSmall label={labels.schemaTruncated} value={formatTokenCount(summary.sanitizerTruncatedToolCount, labels.pending)} />
        <MetricSmall label={labels.slowestAssemblyStage} value={formatAssemblyStageValue(summary.slowestAssemblyStage, summary.slowestAssemblyStageDurationMs, labels.pending)} />
      </div>
      <div className="mt-2 grid grid-cols-2 gap-1.5 border-t border-[var(--line)] pt-2">
        <MetricSmall label={labels.skillsDiscoveryCache} value={skillsDiscoveryCacheLabel} tone={summary.skillsDiscoveryCacheHit ? "success" : "neutral"} />
        <MetricSmall label={labels.skillsDiscoveryRoots} value={formatTokenCount(summary.skillsDiscoveryRootCount, labels.pending)} />
        <MetricSmall label={labels.skillsDiscoveryManifests} value={formatTokenCount(summary.skillsDiscoveryManifestCount, labels.pending)} tone="primary" />
        <MetricSmall label={labels.skillsDiscoveryEnabled} value={formatTokenCount(summary.skillsDiscoveryEnabledCount, labels.pending)} />
        <MetricSmall label={labels.skillsDiscoveryPackages} value={formatTokenCount(summary.skillsDiscoveryPackageCount, labels.pending)} />
        <MetricSmall label={labels.slowestSkillsDiscoveryStage} value={formatAssemblyStageValue(summary.slowestSkillsDiscoveryStage, summary.slowestSkillsDiscoveryStageDurationMs, labels.pending)} />
      </div>
      <div className="mt-2 grid gap-1 font-[var(--mono-font)] text-[11px] text-[var(--ink)]">
        {summary.topAssemblyStages.length > 0 ? <div>{formatCapabilityCountLine(labels.assemblyStages, summary.topAssemblyStages)}</div> : null}
        {summary.topSkillsDiscoveryStages.length > 0 ? <div>{formatCapabilityCountLine(labels.skillsDiscoveryStages, summary.topSkillsDiscoveryStages)}</div> : null}
        {sourceLine ? <div>{sourceLine}</div> : null}
        {deferredSourceLine && deferredSourceLine !== sourceLine ? <div>{deferredSourceLine}</div> : null}
        {groupLine ? <div>{groupLine}</div> : null}
        {deferredGroupLine && deferredGroupLine !== groupLine ? <div>{deferredGroupLine}</div> : null}
      </div>
    </div>
  );
}

function buildPromptSectionTokenLedgerSummary(
  ledger: PromptSectionTokenLedgerDisplay | null | undefined,
): PromptSectionTokenLedgerSummary {
  const stableSections = normalizeSectionTokenEntries(ledger?.stable_section_tokens);
  const volatileSections = normalizeSectionTokenEntries(ledger?.volatile_section_tokens);
  const stableTotal =
    normalizeNumber(ledger?.stable_prompt_tokens) ?? sumSectionTokens(stableSections);
  const volatileTotal =
    normalizeNumber(ledger?.volatile_prompt_tokens) ?? sumSectionTokens(volatileSections);
  return {
    stableTotal,
    volatileTotal,
    stableSections,
    volatileSections,
    hasLedger: stableTotal !== null || volatileTotal !== null || stableSections.length > 0 || volatileSections.length > 0,
  };
}

function normalizeSectionTokenEntries(source: Record<string, number> | null | undefined): Array<{ name: string; tokens: number }> {
  if (!source) {
    return [];
  }
  return Object.entries(source)
    .filter(([, value]) => typeof value === "number" && Number.isFinite(value))
    .map(([name, tokens]) => ({ name, tokens }))
    .sort((left, right) => right.tokens - left.tokens || left.name.localeCompare(right.name));
}

function sumSectionTokens(sections: Array<{ tokens: number }>): number | null {
  if (sections.length === 0) {
    return null;
  }
  return sections.reduce((total, section) => total + section.tokens, 0);
}

function buildCapabilityAssemblyDiagnosticsSummary(diagnostics: CapabilityAssemblyDiagnosticsDisplay | null | undefined): CapabilityAssemblyDiagnosticsSummary {
  const visibleToolCount = normalizeInteger(diagnostics?.visible_tool_count);
  const deferredToolCount = normalizeInteger(diagnostics?.deferred_tool_count);
  const visibleSchemaTokens = normalizeInteger(diagnostics?.visible_schema_tokens);
  const deferredSchemaTokens = normalizeInteger(diagnostics?.deferred_schema_tokens);
  const totalSchemaTokens = normalizeInteger(diagnostics?.total_schema_tokens);
  const visibleSchemaTokenBudget = normalizeInteger(diagnostics?.visible_schema_token_budget);
  const visibleSchemaBudgetRemainingTokens = normalizeInteger(diagnostics?.visible_schema_budget_remaining_tokens);
  const schemaCompactedToolCount = normalizeInteger(diagnostics?.schema_compacted_tool_count);
  const schemaDeferredToolCount = normalizeInteger(diagnostics?.schema_deferred_tool_count);
  const actionPrefilterDeferredToolCount = normalizeInteger(diagnostics?.action_prefilter_deferred_tool_count);
  const sanitizerTruncatedToolCount = normalizeInteger(diagnostics?.sanitizer_truncated_tool_count);
  const slowestAssemblyStage = typeof diagnostics?.slowest_assembly_stage === "string" && diagnostics.slowest_assembly_stage.trim()
    ? diagnostics.slowest_assembly_stage.trim()
    : null;
  const slowestAssemblyStageDurationMs = normalizeInteger(diagnostics?.slowest_assembly_stage_duration_ms);
  const topAssemblyStages = normalizeCapabilityCountEntries(diagnostics?.assembly_stage_durations_ms)
    .filter((entry) => entry.name !== "total")
    .slice(0, 4);
  const skillsDiscoveryCacheHit = typeof diagnostics?.skills_discovery_cache_hit === "boolean"
    ? diagnostics.skills_discovery_cache_hit
    : null;
  const skillsDiscoveryWatchEnabled = typeof diagnostics?.skills_discovery_watch_enabled === "boolean"
    ? diagnostics.skills_discovery_watch_enabled
    : null;
  const skillsDiscoveryRootCount = normalizeInteger(diagnostics?.skills_discovery_root_count);
  const skillsDiscoveryManifestCount = normalizeInteger(diagnostics?.skills_discovery_manifest_count);
  const skillsDiscoveryEnabledCount = normalizeInteger(diagnostics?.skills_discovery_enabled_count);
  const skillsDiscoveryPackageCount = normalizeInteger(diagnostics?.skills_discovery_package_count);
  const slowestSkillsDiscoveryStage = typeof diagnostics?.slowest_skills_discovery_stage === "string" && diagnostics.slowest_skills_discovery_stage.trim()
    ? diagnostics.slowest_skills_discovery_stage.trim()
    : null;
  const slowestSkillsDiscoveryStageDurationMs = normalizeInteger(diagnostics?.slowest_skills_discovery_stage_duration_ms);
  const topSkillsDiscoveryStages = normalizeCapabilityCountEntries(diagnostics?.skills_discovery_stage_durations_ms)
    .filter((entry) => entry.name !== "total")
    .slice(0, 4);
  const topVisibleSources = normalizeCapabilityCountEntries(diagnostics?.visible_by_source_kind);
  const topDeferredSources = normalizeCapabilityCountEntries(diagnostics?.deferred_by_source_kind);
  const topVisibleGroups = normalizeCapabilityCountEntries(diagnostics?.visible_by_group);
  const topDeferredGroups = normalizeCapabilityCountEntries(diagnostics?.deferred_by_group);
  return {
    visibleToolCount,
    deferredToolCount,
    visibleSchemaTokens,
    deferredSchemaTokens,
    totalSchemaTokens,
    visibleSchemaTokenBudget,
    visibleSchemaBudgetRemainingTokens,
    schemaCompactedToolCount,
    schemaDeferredToolCount,
    actionPrefilterDeferredToolCount,
    sanitizerTruncatedToolCount,
    slowestAssemblyStage,
    slowestAssemblyStageDurationMs,
    topAssemblyStages,
    skillsDiscoveryCacheHit,
    skillsDiscoveryWatchEnabled,
    skillsDiscoveryRootCount,
    skillsDiscoveryManifestCount,
    skillsDiscoveryEnabledCount,
    skillsDiscoveryPackageCount,
    slowestSkillsDiscoveryStage,
    slowestSkillsDiscoveryStageDurationMs,
    topSkillsDiscoveryStages,
    topVisibleSources,
    topDeferredSources,
    topVisibleGroups,
    topDeferredGroups,
    hasDiagnostics:
      visibleToolCount !== null ||
      deferredToolCount !== null ||
      visibleSchemaTokens !== null ||
      deferredSchemaTokens !== null ||
      totalSchemaTokens !== null ||
      visibleSchemaTokenBudget !== null ||
      visibleSchemaBudgetRemainingTokens !== null ||
      schemaCompactedToolCount !== null ||
      schemaDeferredToolCount !== null ||
      actionPrefilterDeferredToolCount !== null ||
      sanitizerTruncatedToolCount !== null ||
      slowestAssemblyStage !== null ||
      slowestAssemblyStageDurationMs !== null ||
      topAssemblyStages.length > 0 ||
      skillsDiscoveryCacheHit !== null ||
      skillsDiscoveryWatchEnabled !== null ||
      skillsDiscoveryRootCount !== null ||
      skillsDiscoveryManifestCount !== null ||
      skillsDiscoveryEnabledCount !== null ||
      skillsDiscoveryPackageCount !== null ||
      slowestSkillsDiscoveryStage !== null ||
      slowestSkillsDiscoveryStageDurationMs !== null ||
      topSkillsDiscoveryStages.length > 0 ||
      topVisibleSources.length > 0 ||
      topDeferredSources.length > 0 ||
      topVisibleGroups.length > 0 ||
      topDeferredGroups.length > 0,
  };
}

function buildContextCacheDiagnosticsSummary(
  diagnostics: ContextCacheDiagnosticsDisplay | null | undefined,
): ContextCacheDiagnosticsSummary {
  const projectStatus = normalizeString(diagnostics?.project_context_cache_status);
  const projectFingerprint = normalizeString(diagnostics?.project_context_fingerprint);
  const projectFileCount = normalizeInteger(diagnostics?.project_context_file_count);
  const projectTruncatedFileCount = normalizeInteger(diagnostics?.project_context_truncated_file_count);
  const projectTotalChars = normalizeInteger(diagnostics?.project_context_total_chars);
  const projectDiscoveryScannedPathCount = normalizeInteger(diagnostics?.project_context_discovery_scanned_path_count);
  const projectDiscoveryMaxScannedPaths = normalizeInteger(diagnostics?.project_context_discovery_max_scanned_paths);
  const projectDiscoveryScanTruncated = Boolean(diagnostics?.project_context_discovery_scan_truncated);
  const projectScopeCounts = normalizeCapabilityCountEntries(diagnostics?.project_context_scope_counts);
  const projectAppliesToCounts = normalizeCapabilityCountEntries(diagnostics?.project_context_applies_to_counts);
  const runtimeStatus = normalizeString(diagnostics?.runtime_path_cache_status);
  const runtimeFingerprint = normalizeString(diagnostics?.runtime_path_fingerprint);
  const runtimeRootCount = normalizeInteger(diagnostics?.runtime_path_root_count);
  const runtimeHostBridgeCount = normalizeInteger(diagnostics?.runtime_path_host_bridge_count);
  return {
    projectStatus,
    projectFingerprint,
    projectFileCount,
    projectTruncatedFileCount,
    projectTotalChars,
    projectDiscoveryScannedPathCount,
    projectDiscoveryMaxScannedPaths,
    projectDiscoveryScanTruncated,
    projectScopeCounts,
    projectAppliesToCounts,
    runtimeStatus,
    runtimeFingerprint,
    runtimeRootCount,
    runtimeHostBridgeCount,
    hasDiagnostics:
      projectStatus !== null ||
      projectFingerprint !== null ||
      projectFileCount !== null ||
      projectTruncatedFileCount !== null ||
      projectTotalChars !== null ||
      projectDiscoveryScannedPathCount !== null ||
      projectDiscoveryMaxScannedPaths !== null ||
      projectDiscoveryScanTruncated ||
      projectScopeCounts.length > 0 ||
      projectAppliesToCounts.length > 0 ||
      runtimeStatus !== null ||
      runtimeFingerprint !== null ||
      runtimeRootCount !== null ||
      runtimeHostBridgeCount !== null,
  };
}

function buildMemoryInjectionDiagnosticsSummary(
  diagnostics: MemoryInjectionDiagnosticsDisplay | null | undefined,
): MemoryInjectionDiagnosticsSummary {
  const source = normalizeString(diagnostics?.source);
  const status = normalizeString(diagnostics?.status);
  const snapshotId = normalizeString(diagnostics?.snapshot_id);
  const queryTokens = normalizeInteger(diagnostics?.query_tokens);
  const curatedMatchCount = normalizeInteger(diagnostics?.curated_match_count);
  const archiveHitCount = normalizeInteger(diagnostics?.archive_hit_count);
  const evidenceCount = normalizeInteger(diagnostics?.evidence_count);
  const providerNoteCount = normalizeInteger(diagnostics?.provider_note_count);
  const renderedTokensBeforeTruncation = normalizeInteger(diagnostics?.rendered_tokens_before_truncation);
  const renderedTokens = normalizeInteger(diagnostics?.rendered_tokens);
  const tokenBudget = normalizeInteger(diagnostics?.token_budget);
  const truncated = Boolean(diagnostics?.truncated);
  const errorType = normalizeString(diagnostics?.error_type);
  const topStores = normalizeCapabilityCountEntries(diagnostics?.store_counts);
  const topSourceKinds = normalizeCapabilityCountEntries(diagnostics?.source_kind_counts);
  return {
    source,
    status,
    snapshotId,
    queryTokens,
    curatedMatchCount,
    archiveHitCount,
    evidenceCount,
    providerNoteCount,
    renderedTokensBeforeTruncation,
    renderedTokens,
    tokenBudget,
    truncated,
    errorType,
    topStores,
    topSourceKinds,
    hasDiagnostics:
      source !== null ||
      status !== null ||
      snapshotId !== null ||
      queryTokens !== null ||
      curatedMatchCount !== null ||
      archiveHitCount !== null ||
      evidenceCount !== null ||
      providerNoteCount !== null ||
      renderedTokensBeforeTruncation !== null ||
      renderedTokens !== null ||
      tokenBudget !== null ||
      truncated ||
      errorType !== null ||
      topStores.length > 0 ||
      topSourceKinds.length > 0,
  };
}

function buildCompactionDiagnosticsSummary(
  diagnostics: CompactionDiagnosticsDisplay | null | undefined,
): CompactionDiagnosticsSummary {
  const summarySource = normalizeString(diagnostics?.summary_source);
  const summaryModel = normalizeString(diagnostics?.summary_model);
  const archivedMessageCount = normalizeInteger(diagnostics?.archived_message_count);
  const toolCallCount = normalizeInteger(diagnostics?.tool_call_count);
  const toolResultCount = normalizeInteger(diagnostics?.tool_result_count);
  const imageBlockCount = normalizeInteger(diagnostics?.image_block_count);
  const truncatedMessageCount = normalizeInteger(diagnostics?.truncated_message_count);
  const prunedToolResultCount = normalizeInteger(diagnostics?.pruned_tool_result_count);
  const serializedTokens = normalizeInteger(diagnostics?.serialized_tokens);
  const summaryPromptTokens = normalizeInteger(diagnostics?.summary_prompt_tokens);
  return {
    summarySource,
    summaryModel,
    archivedMessageCount,
    toolCallCount,
    toolResultCount,
    imageBlockCount,
    truncatedMessageCount,
    prunedToolResultCount,
    serializedTokens,
    summaryPromptTokens,
    hasDiagnostics:
      summarySource !== null ||
      summaryModel !== null ||
      archivedMessageCount !== null ||
      toolCallCount !== null ||
      toolResultCount !== null ||
      imageBlockCount !== null ||
      truncatedMessageCount !== null ||
      prunedToolResultCount !== null ||
      serializedTokens !== null ||
      summaryPromptTokens !== null,
  };
}

function normalizeCapabilityCountEntries(source: Record<string, number> | null | undefined): Array<{ name: string; count: number }> {
  if (!source) {
    return [];
  }
  return Object.entries(source)
    .filter(([, value]) => typeof value === "number" && Number.isFinite(value))
    .map(([name, count]) => ({ name, count: Math.trunc(count) }))
    .filter((entry) => entry.count > 0)
    .sort((left, right) => right.count - left.count || left.name.localeCompare(right.name))
    .slice(0, 3);
}

function normalizeString(value: string | null | undefined): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function formatCapabilityCountLine(label: string, entries: Array<{ name: string; count: number }>): string | null {
  if (entries.length === 0) {
    return null;
  }
  return `${label}: ${entries.map((entry) => `${entry.name}:${formatTokenCount(entry.count)}`).join(" · ")}`;
}

function formatAssemblyStageValue(stage: string | null, durationMs: number | null, pending = "pending"): string {
  if (!stage && durationMs === null) {
    return pending;
  }
  const duration = durationMs !== null ? `${durationMs}ms` : pending;
  return stage ? `${stage}:${duration}` : duration;
}

function formatDiscoveryScanBudget(summary: ContextCacheDiagnosticsSummary, pending: string): string {
  if (summary.projectDiscoveryScannedPathCount === null && summary.projectDiscoveryMaxScannedPaths === null) {
    return pending;
  }
  return `${formatTokenCount(summary.projectDiscoveryScannedPathCount, pending)} / ${formatTokenCount(summary.projectDiscoveryMaxScannedPaths, pending)}`;
}

function cacheStatusTone(status: string | null): "primary" | "success" | "neutral" {
  if (status === "hit") {
    return "success";
  }
  if (status === "miss") {
    return "primary";
  }
  return "neutral";
}

function shortFingerprint(value: string): string {
  const normalized = value.trim();
  if (normalized.length <= 18) {
    return normalized;
  }
  return `${normalized.slice(0, 12)}...${normalized.slice(-4)}`;
}

function readNumberField(source: Record<string, unknown> | null | undefined, key: string): number | null {
  const value = source?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function readRecordField(source: Record<string, unknown> | null | undefined, key: string): Record<string, unknown> | null {
  const value = source?.[key];
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function buildContextUsageSummary(
  usage: ContextWindowUsageDisplay,
  locale: Locale,
  activeModelName?: string | null,
): ContextUsageSummary {
  const contextTokens = normalizeNumber(usage.context_tokens);
  const thresholdTokens = normalizeNumber(usage.auto_compact_threshold_tokens);
  const windowTokens = normalizeNumber(usage.context_window_tokens);
  const inputTokens = normalizeNumber(usage.input_tokens);
  const outputTokens = normalizeNumber(usage.output_tokens);
  const providerTotalTokens = normalizeNumber(usage.total_tokens);
  const compactRatio = normalizeRatio(usage.compact_ratio);
  const usageRatio = normalizeRatio(usage.usage_ratio);
  const windowRatio =
    usageRatio ??
    (contextTokens !== null && windowTokens !== null && windowTokens > 0 ? clampRatio(contextTokens / windowTokens) : null);
  const compactPressureRatio =
    compactRatio ??
    (contextTokens !== null && thresholdTokens !== null && thresholdTokens > 0 ? clampRatio(contextTokens / thresholdTokens) : null);
  const pressureRatio = windowRatio ?? compactPressureRatio ?? 0;
  const isCompacted = usage.compact_status === "compacted" || Boolean(usage.summarization_triggered);
  const isApproaching = !isCompacted && (usage.compact_status === "over_threshold" || (compactPressureRatio ?? pressureRatio) >= 0.85);
  const breakdownRows = contextBreakdownItems(usage, locale);
  const claudeRows = claudeContextRows(usage, locale);
  const hasBreakdown = breakdownRows.some((item) => item.tokens !== null);
  const costLabel = formatCost(
    normalizeNumber(usage.estimated_cost_usd),
    usage.currency ?? "USD",
    usage.cost_status ?? null,
  );
  const cacheReadTokens = normalizeNumber(usage.cache_read_tokens);
  const cacheWriteTokens = normalizeNumber(usage.cache_write_tokens ?? usage.cache_creation_tokens);
  const lastInputTokens = normalizeNumber(usage.last_input_tokens);
  const lastOutputTokens = normalizeNumber(usage.last_output_tokens);
  const lastTotalTokens = normalizeNumber(usage.last_total_tokens);
  const lastCacheReadTokens = normalizeNumber(usage.last_cache_read_tokens);
  const lastCacheWriteTokens = normalizeNumber(usage.last_cache_write_tokens);
  const hasProviderUsage =
    providerTotalTokens !== null ||
    inputTokens !== null ||
    outputTokens !== null ||
    cacheReadTokens !== null ||
    cacheWriteTokens !== null;
  const cacheHitRatio = normalizeRatio(usage.cache_hit_ratio);
  const cacheSavingsTokens = normalizeNumber(usage.cache_savings_tokens);
  const compactionLevel = normalizeInteger(usage.compaction_level) ?? 0;
  const compactionLevelLabel = compactionLevelLabelFor(usage.compaction_level_label, compactionLevel, locale);
  const compactionReason = typeof usage.compaction_reason === "string" && usage.compaction_reason.trim()
    ? usage.compaction_reason.trim()
    : null;
  const compactionInputTokens = normalizeNumber(usage.compaction_input_tokens);
  const compactionSummaryTokens = normalizeNumber(usage.compaction_summary_tokens);
  const compactionSavingsTokens = normalizeNumber(usage.compaction_savings_tokens);
  const compactionKeepRecentTurns = normalizeInteger(usage.compaction_keep_recent_turns);
  const hasLastUsage =
    lastTotalTokens !== null ||
    lastInputTokens !== null ||
    lastOutputTokens !== null ||
    lastCacheReadTokens !== null ||
    lastCacheWriteTokens !== null;
  const remainingTokens =
    contextTokens !== null && windowTokens !== null ? Math.max(windowTokens - contextTokens, 0) : null;

  return {
    contextTokens,
    thresholdTokens,
    windowTokens,
    providerTotalTokens,
    inputTokens,
    outputTokens,
    lastInputTokens,
    lastOutputTokens,
    lastTotalTokens,
    cacheReadTokens,
    cacheWriteTokens,
    lastCacheReadTokens,
    lastCacheWriteTokens,
    requestCount: normalizeNumber(usage.request_count),
    dominantContextLabel: contextCategoryLabel(usage.dominant_context_category, locale),
    cacheHitRatio,
    cacheSavingsTokens,
    compactionLevel,
    compactionLevelLabel,
    compactionReason,
    compactionInputTokens,
    compactionSummaryTokens,
    compactionSavingsTokens,
    compactionKeepRecentTurns,
    costLabel,
    compactRatio,
    usageRatio,
    pressureRatio,
    usedPercentLabel: windowRatio !== null ? formatPercent(windowRatio) : null,
    remainingPercentLabel: windowRatio !== null ? formatPercent(1 - windowRatio) : null,
    remainingTokens,
    contextSourceLabel: contextSourceLabel(usage.context_source, locale),
    isEstimated: isContextEstimated(usage.context_source),
    isCompacted,
    isApproaching,
    modelLabel: usage.concrete_model ?? usage.model ?? activeModelName ?? "n/a",
    providerLabel: usage.provider ?? null,
    breakdownRows,
    claudeRows,
    hasBreakdown,
    hasProviderUsage,
    hasLastUsage,
  };
}

function normalizeNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function normalizeInteger(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return Math.trunc(value);
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? Math.trunc(parsed) : null;
  }
  return null;
}

function normalizeRatio(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return null;
  }
  return clampRatio(value);
}

function compactionLevelLabelFor(value: unknown, level: number, locale: Locale): string {
  const normalized = typeof value === "string" ? value.trim() : "";
  const fallback = normalized || (
    level >= 3 ? "emergency" : level === 2 ? "recursive_summary" : level === 1 ? "summary" : "none"
  );
  if (locale !== "zh-CN") {
    return fallback.replace(/_/g, " ");
  }
  const labels: Record<string, string> = {
    none: "未压缩",
    summary: "摘要",
    recursive_summary: "递归摘要",
    emergency: "紧急压缩",
  };
  return labels[fallback] ?? fallback;
}

function isContextEstimated(source: string | null | undefined): boolean {
  return typeof source === "string" && source.includes("estimated");
}

function contextSourceLabel(source: string | null | undefined, locale: Locale): string | null {
  if (!source) {
    return null;
  }
  const zh = locale === "zh-CN";
  const labels: Record<string, string> = zh
    ? {
        backend: "后端采集",
        estimated: "后端估算",
        provider: "模型上报",
        provider_last_input: "最近输入下界",
        "provider+estimated": "模型上报 + 后端估算",
      }
    : {
        backend: "Backend",
        estimated: "Backend estimate",
        provider: "Provider",
        provider_last_input: "Last provider input",
        "provider+estimated": "Provider + estimate",
      };
  return labels[source] ?? source;
}

function contextCategoryLabel(category: string | null | undefined, locale: Locale): string | null {
  if (!category) {
    return null;
  }
  const zh = locale === "zh-CN";
  const labels: Record<string, string> = zh
    ? {
        messages: "消息",
        system: "系统提示",
        tool_schemas: "工具 Schema",
        skills: "Skills",
        memory: "Memory",
        project_context: "项目上下文",
        runtime_paths: "路径边界",
        visible_capabilities: "可见能力",
        deferred_capabilities: "延迟能力",
        request_context: "本轮请求上下文",
        upload_context: "上传上下文",
        approval_context: "审批上下文",
        plan_context: "计划上下文",
        memory_context: "动态记忆",
        conversation_summary: "会话摘要",
        todo_state: "Todo 状态",
        view_image_context: "图片上下文",
        promoted_capabilities: "本轮提升能力",
      }
    : {
        messages: "Messages",
        system: "System Prompt",
        tool_schemas: "Tool Schemas",
        skills: "Skills",
        memory: "Memory",
        project_context: "Project Context",
        runtime_paths: "Runtime Paths",
        visible_capabilities: "Visible Capabilities",
        deferred_capabilities: "Deferred Capabilities",
        request_context: "Request Context",
        upload_context: "Upload Context",
        approval_context: "Approval Context",
        plan_context: "Plan Context",
        memory_context: "Dynamic Memory",
        conversation_summary: "Conversation Summary",
        todo_state: "Todo State",
        view_image_context: "Image Context",
        promoted_capabilities: "Promoted Capabilities",
      };
  return labels[category] ?? category;
}

function contextCategoryColor(category: string): string {
  const colors: Record<string, string> = {
    messages: "#4f7df3",
    system: "#7aa2ff",
    tool_schemas: "#8fb7ef",
    skills: "#9f8cf2",
    memory: "#55b585",
    memory_context: "#4ba978",
    conversation_summary: "#72a980",
    project_context: "#c08a3e",
    runtime_paths: "#6e9aa6",
    visible_capabilities: "#6b8fc9",
    deferred_capabilities: "#94a3b8",
    request_context: "#5d8ff0",
    upload_context: "#b88a35",
    approval_context: "#d07a58",
    plan_context: "#58a7a2",
    todo_state: "#7c96d6",
    view_image_context: "#9d80d8",
    promoted_capabilities: "#5f8ab9",
  };
  return colors[category] ?? "#94a3b8";
}

function contextBreakdownItems(usage: ContextWindowUsageDisplay | null | undefined, locale: Locale): ContextBreakdownItem[] {
  const labels =
    locale === "zh-CN"
      ? {
          messages: "消息",
          system: "系统提示",
          tools: "系统工具",
          skills: "Skills",
          memory: "Memory",
          project: "项目上下文",
          paths: "路径边界",
          buffer: "自动压缩缓冲",
          free: "剩余空间",
        }
      : {
          messages: "Messages",
          system: "System Prompt",
          tools: "Tool Schemas",
          skills: "Skills",
          memory: "Memory",
          project: "Project Context",
          paths: "Runtime Paths",
          buffer: "Autocompact Buffer",
          free: "Free Space",
        };
  const percentages = usage?.context_breakdown_percentages ?? {};
  const contextBreakdown = usage?.context_breakdown ?? {};
  const readCategoryTokens = (category: string, fallback: number | null | undefined) => {
    const value = contextBreakdown[category];
    return typeof value === "number" && Number.isFinite(value) ? value : fallback ?? null;
  };
  const readCategoryPercentage = (category: string) => {
    const value = percentages[category];
    return typeof value === "number" && Number.isFinite(value) ? value : null;
  };
  const knownCategoryRows: ContextBreakdownItem[] = [
    { key: "messages", label: labels.messages, tokens: readCategoryTokens("messages", usage?.message_tokens), percentage: readCategoryPercentage("messages"), color: contextCategoryColor("messages"), group: "context" },
    { key: "system", label: labels.system, tokens: readCategoryTokens("system", usage?.system_tokens), percentage: readCategoryPercentage("system"), color: contextCategoryColor("system"), group: "context" },
    { key: "tools", label: labels.tools, tokens: readCategoryTokens("tool_schemas", usage?.tool_schema_tokens), percentage: readCategoryPercentage("tool_schemas"), color: contextCategoryColor("tool_schemas"), group: "context" },
    { key: "skills", label: labels.skills, tokens: readCategoryTokens("skills", usage?.skill_tokens), percentage: readCategoryPercentage("skills"), color: contextCategoryColor("skills"), group: "context" },
    { key: "memory", label: labels.memory, tokens: readCategoryTokens("memory", usage?.memory_tokens), percentage: readCategoryPercentage("memory"), color: contextCategoryColor("memory"), group: "context" },
    { key: "project", label: labels.project, tokens: readCategoryTokens("project_context", usage?.project_context_tokens), percentage: readCategoryPercentage("project_context"), color: contextCategoryColor("project_context"), group: "context" },
    { key: "paths", label: labels.paths, tokens: readCategoryTokens("runtime_paths", usage?.runtime_path_tokens), percentage: readCategoryPercentage("runtime_paths"), color: contextCategoryColor("runtime_paths"), group: "context" },
    { key: "visible-capabilities", label: contextCategoryLabel("visible_capabilities", locale) ?? "Visible Capabilities", tokens: readCategoryTokens("visible_capabilities", null), percentage: readCategoryPercentage("visible_capabilities"), color: contextCategoryColor("visible_capabilities"), group: "context" },
    { key: "deferred-capabilities", label: contextCategoryLabel("deferred_capabilities", locale) ?? "Deferred Capabilities", tokens: readCategoryTokens("deferred_capabilities", null), percentage: readCategoryPercentage("deferred_capabilities"), color: contextCategoryColor("deferred_capabilities"), group: "context" },
  ];
  const knownBreakdownKeys = new Set([
    "messages",
    "system",
    "tool_schemas",
    "skills",
    "memory",
    "project_context",
    "runtime_paths",
    "visible_capabilities",
    "deferred_capabilities",
  ]);
  const dynamicCategoryRows = Object.entries(contextBreakdown)
    .filter(([category, value]) => !knownBreakdownKeys.has(category) && typeof value === "number" && Number.isFinite(value) && value > 0)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([category, value]) => ({
      key: category,
      label: contextCategoryLabel(category, locale) ?? category,
      tokens: value,
      percentage: readCategoryPercentage(category),
      color: contextCategoryColor(category),
      group: "context" as const,
    }));
  return [
    ...knownCategoryRows,
    ...dynamicCategoryRows,
    { key: "buffer", label: labels.buffer, tokens: usage?.autocompact_buffer_tokens ?? null, percentage: null, color: "#c6cbd3", group: "reserve" },
    { key: "free", label: labels.free, tokens: usage?.free_space_tokens ?? null, percentage: null, color: "#e1e5ea", group: "reserve" },
  ];
}

function claudeContextRows(usage: ContextWindowUsageDisplay | null | undefined, locale: Locale): ContextBreakdownItem[] {
  if (!usage) {
    return [];
  }
  const labels =
    locale === "zh-CN"
      ? {
          messages: "Messages",
          tools: "System tools",
          skills: "Skills",
          buffer: "Autocompact buffer",
          free: "Free space",
        }
      : {
          messages: "Messages",
          tools: "System tools",
          skills: "Skills",
          buffer: "Autocompact buffer",
          free: "Free space",
        };
  const breakdown = usage.context_breakdown ?? {};
  const read = (key: string, fallback?: number | null) => {
    const value = breakdown[key];
    return typeof value === "number" && Number.isFinite(value) ? value : fallback ?? null;
  };
  const contextTokens = normalizeNumber(usage.context_tokens);
  const thresholdTokens = normalizeNumber(usage.auto_compact_threshold_tokens);
  const windowTokens = normalizeNumber(usage.context_window_tokens);
  const hasSpecificCategoryTokens = [
    read("messages", usage.message_tokens),
    read("request_context", null),
    read("upload_context", null),
    read("approval_context", null),
    read("plan_context", null),
    read("memory_context", null),
    read("conversation_summary", null),
    read("todo_state", null),
    read("view_image_context", null),
    read("system", usage.system_tokens),
    read("tool_schemas", usage.tool_schema_tokens),
    read("visible_capabilities", null),
    read("deferred_capabilities", null),
    read("runtime_paths", usage.runtime_path_tokens),
    read("skills", usage.skill_tokens),
  ].some((value) => value !== null);
  const messageTokens = sumNullableNumbers(
    read("messages", usage.message_tokens),
    read("request_context", null),
    read("upload_context", null),
    read("approval_context", null),
    read("plan_context", null),
    read("memory_context", null),
    read("conversation_summary", null),
    read("todo_state", null),
    read("view_image_context", null),
  ) ?? (hasSpecificCategoryTokens ? 0 : contextTokens);
  const systemToolsTokens = sumNullableNumbers(
    read("system", usage.system_tokens),
    read("tool_schemas", usage.tool_schema_tokens),
    read("visible_capabilities", null),
    read("deferred_capabilities", null),
    read("runtime_paths", usage.runtime_path_tokens),
    read("promoted_capabilities", null),
  );
  const skillTokens = read("skills", usage.skill_tokens) ?? 0;
  const bufferTokens =
    normalizeNumber(usage.autocompact_buffer_tokens) ??
    (thresholdTokens !== null && windowTokens !== null && windowTokens >= thresholdTokens ? windowTokens - thresholdTokens : null);
  const knownWindowOccupancy = sumNullableNumbers(contextTokens, bufferTokens);
  const freeTokens =
    normalizeNumber(usage.free_space_tokens) ??
    (knownWindowOccupancy !== null && windowTokens !== null ? Math.max(windowTokens - knownWindowOccupancy, 0) : null);
  return [
    { key: "messages", label: labels.messages, tokens: messageTokens, percentage: ratioOrNull(messageTokens, windowTokens), color: "var(--context-messages)", group: "context" },
    { key: "system-tools", label: labels.tools, tokens: systemToolsTokens ?? 0, percentage: ratioOrNull(systemToolsTokens ?? 0, windowTokens), color: "var(--context-system)", group: "context" },
    { key: "skills", label: labels.skills, tokens: skillTokens, percentage: ratioOrNull(skillTokens, windowTokens), color: "var(--context-skills)", group: "context" },
    { key: "autocompact-buffer", label: labels.buffer, tokens: bufferTokens, percentage: ratioOrNull(bufferTokens, windowTokens), color: "var(--context-buffer)", group: "reserve" },
    { key: "free-space", label: labels.free, tokens: freeTokens, percentage: ratioOrNull(freeTokens, windowTokens), color: "var(--context-free)", group: "reserve" },
  ];
}

function sumNullableNumbers(...values: Array<number | null | undefined>): number | null {
  const present = values.filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  if (present.length === 0) {
    return null;
  }
  return present.reduce((total, value) => total + value, 0);
}

function ratioOrNull(value: number | null | undefined, total: number | null | undefined): number | null {
  if (typeof value !== "number" || !Number.isFinite(value) || typeof total !== "number" || !Number.isFinite(total) || total <= 0) {
    return null;
  }
  return clampRatio(value / total);
}

function ContextBreakdownRow({
  color,
  label,
  tokens,
  percentage,
  total,
}: {
  color: string;
  label: string;
  tokens: number;
  percentage: number | null;
  total: number | null | undefined;
}) {
  const ratio = percentage !== null ? clampRatio(percentage) : total && total > 0 ? clampRatio(tokens / total) : 0;
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 text-xs">
      <div className="min-w-0">
        <div className="mb-0.5 grid grid-cols-[auto_minmax(0,1fr)] items-center gap-1.5">
          <span className="size-2 rounded-sm" style={{ background: color }} />
          <HoverRevealText value={label} className="text-[var(--muted)]" />
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-[var(--panel-muted)]">
          <div className="h-full rounded-full" style={{ width: `${Math.round(ratio * 100)}%`, background: color }} />
        </div>
      </div>
      <span className="font-[var(--mono-font)] text-[var(--ink)]">
        {formatTokenCount(tokens)}
        {percentage !== null ? ` ${formatPercent(percentage)}` : ""}
      </span>
    </div>
  );
}

function ContextCompactBreakdownRow({
  color,
  label,
  tokens,
  percentage,
  total,
}: {
  color: string;
  label: string;
  tokens: number;
  percentage: number | null;
  total: number | null | undefined;
}) {
  const ratio = percentage !== null ? clampRatio(percentage) : total && total > 0 ? clampRatio(tokens / total) : 0;
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-2 text-[12px] leading-4">
      <div className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)] items-center gap-1.5">
        <CircleIcon className="size-2.5 shrink-0 fill-current" style={{ color }} aria-hidden="true" />
        <HoverRevealText value={label} className="text-[var(--ink)]" />
      </div>
      <span className="font-[var(--mono-font)] text-[var(--muted)]">{formatTokenCount(tokens)}</span>
      <span className="w-11 text-right font-[var(--mono-font)] text-[var(--ink)]">{formatPercent(ratio)}</span>
    </div>
  );
}

export function mergeIncomingDetailWindow(
  current: MessageView[],
  incoming: MessageView[],
  currentWindow: MessageWindowView | null,
  incomingWindow: MessageWindowView | null,
): MessageView[] {
  if (!incomingWindow || incomingWindow.offset === 0) {
    return [...incoming];
  }
  const incomingStart = incomingWindow.offset ?? 0;
  const incomingEnd = incomingStart + incoming.length;
  const retained = current.filter((_, index) => {
    const absoluteIndex = (currentWindow?.offset ?? 0) + index;
    return absoluteIndex < incomingStart || absoluteIndex >= incomingEnd;
  });
  return mergeMessageWindows(retained, incoming, currentWindow, incomingWindow);
}

function mergeMessageWindows(
  current: MessageView[],
  incoming: MessageView[],
  currentWindow: MessageWindowView | null,
  incomingWindow: MessageWindowView | null,
): MessageView[] {
  const byId = new Map<string, { message: MessageView; order: number }>();
  current.forEach((message, index) => {
    byId.set(message.message_id, {
      message,
      order: (currentWindow?.offset ?? 0) + index,
    });
  });
  incoming.forEach((message, index) => {
    byId.set(message.message_id, {
      message,
      order: (incomingWindow?.offset ?? 0) + index,
    });
  });
  return Array.from(byId.values())
    .sort((left, right) => {
      if (left.order !== right.order) {
        return left.order - right.order;
      }
      return left.message.message_id.localeCompare(right.message.message_id);
    })
    .map((entry) => entry.message);
}

export function shouldRequestFullThreadState(drawerDataVisible: boolean, drawerSection: DrawerSection): boolean {
  return drawerDataVisible && FULL_THREAD_STATE_DRAWER_SECTIONS.has(drawerSection);
}

export function selectPreferredModelName(
  selectedModelName: string | null | undefined,
  models: Array<Record<string, unknown>>,
): string {
  const options = Array.from(
    new Set(
      models
        .map((model) => String(model.name ?? "").trim())
        .filter(Boolean),
    ),
  );
  const selected = String(selectedModelName ?? "").trim();
  if (selected && options.includes(selected)) {
    return selected;
  }
  return options[0] ?? selected;
}

export function modelNameForRequest(selectedModelName: string | null | undefined): string | null {
  const selected = String(selectedModelName ?? "").trim();
  return selected || null;
}

export function shouldShowComposerRunning({
  activeThreadId,
  runStreamIsStreaming,
  isSubmittingRun,
  optimisticRunningThreadId,
  durableThreadStatus,
  streamTerminalSeen = false,
  streamMessageCompletedSeen = false,
}: {
  activeThreadId: string | null;
  runStreamIsStreaming: boolean;
  isSubmittingRun: boolean;
  optimisticRunningThreadId: string | null;
  durableThreadStatus?: string | null;
  streamTerminalSeen?: boolean;
  streamMessageCompletedSeen?: boolean;
}): boolean {
  if (streamTerminalSeen) {
    return false;
  }
  if (runStreamIsStreaming) {
    if (streamMessageCompletedSeen && isTerminalThreadStatus(durableThreadStatus)) {
      return false;
    }
    return true;
  }
  if (isActiveThreadStatus(durableThreadStatus)) {
    return true;
  }
  if (!isSubmittingRun) {
    return false;
  }
  if (!optimisticRunningThreadId) {
    return false;
  }
  const activeKey = activeThreadId ?? DRAFT_SESSION_KEY;
  return optimisticRunningThreadId === activeKey;
}

export function shouldUseNewSessionStart({
  activeThreadId,
  visibleMessageCount,
  hasOptimisticUserMessage,
  hasPendingApproval,
  hasPendingUserInteraction,
  queuedFollowupCount,
  isStreaming,
  isSubmitting,
}: {
  activeThreadId: string | null;
  visibleMessageCount: number;
  hasOptimisticUserMessage: boolean;
  hasPendingApproval: boolean;
  hasPendingUserInteraction: boolean;
  queuedFollowupCount: number;
  isStreaming: boolean;
  isSubmitting: boolean;
}) {
  return (
    !activeThreadId &&
    visibleMessageCount === 0 &&
    !hasOptimisticUserMessage &&
    !hasPendingApproval &&
    !hasPendingUserInteraction &&
    queuedFollowupCount === 0 &&
    !isStreaming &&
    !isSubmitting
  );
}

function NewSessionStart({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-3 pb-8 pt-[clamp(4rem,18vh,9rem)] sm:px-4">
      <section
        data-testid="new-session-start"
        className="mx-auto flex w-full max-w-[900px] flex-col items-center text-center"
      >
        <h1 className="text-[clamp(1.7rem,2.7vw,2.35rem)] font-semibold leading-tight tracking-normal text-[var(--ink)]">
          {title}
        </h1>
        <p className="mt-3 max-w-xl text-[13px] leading-6 text-[var(--muted)]">{subtitle}</p>
        <div data-testid="new-session-composer" className="mt-16 w-full sm:mt-20">
          {children}
        </div>
      </section>
    </div>
  );
}

function isTerminalThreadStatus(status: string | null | undefined): boolean {
  return Boolean(status && !["new", "running", "awaiting_approval", "awaiting_clarification"].includes(status));
}

function isActiveThreadStatus(status: string | null | undefined): boolean {
  return status === "running" || status === "awaiting_approval" || status === "awaiting_clarification";
}

type QueuedFollowupRunDefaults = {
  executionMode: ExecutionMode;
  selectedModelName: string;
  selectedProfile: string;
  selectedReasoningEffort: string;
  selectedPlanMode: boolean;
};

export function selectNextQueuedFollowup(items: QueuedFollowUpView[]): QueuedFollowUpView | null {
  return items.find((item) => (item.mode ?? "followup") === "guidance") ?? items[0] ?? null;
}

export function canDispatchQueuedFollowup(
  threadState: Pick<ThreadStateView, "status" | "has_pending_approval" | "active_followup_dispatch"> | null | undefined,
): boolean {
  if (!threadState) {
    return false;
  }
  if (threadState.has_pending_approval) {
    return false;
  }
  if ("active_followup_dispatch" in threadState && threadState.active_followup_dispatch) {
    return false;
  }
  return !["running", "awaiting_approval", "awaiting_clarification"].includes(threadState.status);
}

export function buildQueuedFollowupDispatchSignature(
  threadState: Pick<ThreadStateView, "status" | "has_pending_approval" | "active_followup_dispatch"> | null | undefined,
): string {
  if (!threadState) {
    return "none";
  }
  const activeDispatch = threadState.active_followup_dispatch;
  const dispatchPart = activeDispatch
    ? `${activeDispatch.dispatch_id ?? ""}:${activeDispatch.queue_id ?? ""}`
    : "";
  return `${threadState.status}:${Boolean(threadState.has_pending_approval)}:${dispatchPart}`;
}

export function buildQueuedFollowupRunBody(
  item: QueuedFollowUpView,
  defaults: QueuedFollowupRunDefaults,
): RunRequestBody {
  const executionMode = item.execution_mode ?? defaults.executionMode;
  const selectedModel = item.selected_model ?? modelNameForRequest(defaults.selectedModelName);
  const selectedProfile = item.profile ?? (defaults.selectedProfile || null);
  const reasoningEffort = item.selected_reasoning_effort ?? (defaults.selectedReasoningEffort || null);
  const body: RunRequestBody = {
    message: item.message,
    client_message_id: queuedFollowupClientMessageId(item.queue_id),
    execution_mode: executionMode,
    selected_model: selectedModel,
    profile: selectedProfile,
    selected_reasoning_effort: reasoningEffort,
    uploaded_filenames: item.uploaded_filenames ?? [],
    promoted_capabilities: item.promoted_capabilities ?? [],
    is_plan_mode: typeof item.is_plan_mode === "boolean" ? item.is_plan_mode : defaults.selectedPlanMode,
    followup_dispatch_id: item.dispatch_id ?? null,
  };
  if (item.upload_context) {
    body.upload_context = item.upload_context;
  }
  return body;
}

export function buildQueuedFollowupRestoreRequest(item: QueuedFollowUpView): QueuedFollowUpCreateRequest {
  return {
    message: item.message,
    mode: item.mode ?? "followup",
    execution_mode: item.execution_mode ?? null,
    selected_model: item.selected_model ?? null,
    selected_reasoning_effort: item.selected_reasoning_effort ?? null,
    profile: item.profile ?? null,
    upload_context: item.upload_context ?? null,
    uploaded_filenames: item.uploaded_filenames ?? [],
    uploaded_file_refs: item.uploaded_file_refs ?? [],
    promoted_capabilities: item.promoted_capabilities ?? [],
    is_plan_mode: typeof item.is_plan_mode === "boolean" ? item.is_plan_mode : null,
    insert_position: "front",
  };
}

export function deduplicateTranscriptMessages(messages: MessageView[]): MessageView[] {
  const result: MessageView[] = [];
  const seenByMessageId = new Set<string>();
  const durableUserTextByFingerprint = new Map<string, string>();
  for (const message of messages) {
    if (seenByMessageId.has(message.message_id)) {
      continue;
    }
    seenByMessageId.add(message.message_id);
    if (message.role === "human" || message.role === "user") {
      const fingerprint = normalizeOptimisticMessageText(message.content);
      const originalText = message.content.trim();
      const previousOriginalText = fingerprint ? durableUserTextByFingerprint.get(fingerprint) : undefined;
      if (fingerprint && previousOriginalText !== undefined && previousOriginalText !== originalText) {
        continue;
      }
      if (fingerprint && previousOriginalText === undefined) {
        durableUserTextByFingerprint.set(fingerprint, originalText);
      }
    }
    result.push(message);
  }
  return result;
}

function messageWindowStartOffset(window: MessageWindowView | null, messages: MessageView[]): number {
  if (typeof window?.offset === "number") {
    return window.offset;
  }
  return 0;
}

function mergeMessageWindow(
  current: MessageWindowView | null,
  incoming: MessageWindowView | null,
  messages: MessageView[],
): MessageWindowView | null {
  if (!incoming && !current) {
    return null;
  }
  const total = incoming?.total ?? current?.total ?? messages.length;
  const offset = Math.min(
    messageWindowStartOffset(current, messages),
    messageWindowStartOffset(incoming, messages),
  );
  const returned = messages.length;
  const end = offset + returned;
  return {
    total,
    offset,
    limit: null,
    returned,
    has_more_before: offset > 0,
    has_more_after: end < total,
    truncated: offset > 0 || end < total,
    start_message_id: messages[0]?.message_id ?? null,
    end_message_id: messages.length > 0 ? messages[messages.length - 1]!.message_id : null,
  };
}

function messageWindowCacheEquivalent(
  currentMessages: MessageView[],
  nextMessages: MessageView[],
  currentWindow: MessageWindowView | null,
  nextWindow: MessageWindowView | null,
): boolean {
  if (!messageWindowEquivalent(currentWindow, nextWindow)) {
    return false;
  }
  if (currentMessages.length !== nextMessages.length) {
    return false;
  }
  return currentMessages.every((message, index) => messageSignature(message) === messageSignature(nextMessages[index]!));
}

function messageWindowEquivalent(left: MessageWindowView | null, right: MessageWindowView | null): boolean {
  if (left === right) {
    return true;
  }
  if (!left || !right) {
    return false;
  }
  return (
    left.total === right.total &&
    left.offset === right.offset &&
    left.limit === right.limit &&
    left.returned === right.returned &&
    left.has_more_before === right.has_more_before &&
    left.has_more_after === right.has_more_after &&
    left.truncated === right.truncated &&
    left.start_message_id === right.start_message_id &&
    left.end_message_id === right.end_message_id
  );
}

function messageWindowSignature(window: MessageWindowView | null): string {
  if (!window) {
    return "none";
  }
  return JSON.stringify([
    window.total ?? null,
    window.offset ?? null,
    window.limit ?? null,
    window.returned ?? null,
    window.has_more_before ?? null,
    window.has_more_after ?? null,
    window.truncated ?? null,
    window.start_message_id ?? null,
    window.end_message_id ?? null,
  ]);
}

function messageSignature(message: MessageView): string {
  return JSON.stringify([
    message.message_id,
    message.role,
    message.content,
    message.status ?? null,
    message.stream_status ?? null,
    message.tool_call_id ?? null,
    message.name ?? null,
    message.steps.map((step) => [
      step.step_id,
      step.type,
      step.status,
      step.payload,
      step.order,
    ]),
    message.tool_calls.map((tool) => [
      tool.tool_call_id,
      tool.name,
      tool.status,
      tool.result_text ?? null,
      tool.duration_ms ?? null,
    ]),
    message.approval ? approvalIdentity(message.approval) : null,
    message.artifact_refs.map((artifact) => [artifact.kind, artifact.label, artifact.virtual_path ?? null, artifact.artifact_url ?? null]),
  ]);
}

export function deriveContextWindowUsageForDisplay(
  usage: ContextWindowUsageView | null,
  tokenUsageSummary: TokenUsageSummaryView | null | undefined,
  tokenUsage: Record<string, unknown> | null | undefined,
): ContextWindowUsageDisplay | null {
  if (!usage) {
    return null;
  }
  const cumulativeUsage = tokenUsageSummary?.total ?? readRecordField(tokenUsage, "total");
  const lastUsage = tokenUsageSummary?.last ?? readRecordField(tokenUsage, "last");
  const inputTokens = usage.input_tokens ?? readNumberField(cumulativeUsage, "input_tokens") ?? readNumberField(tokenUsage, "input_tokens");
  const outputTokens = usage.output_tokens ?? readNumberField(cumulativeUsage, "output_tokens") ?? readNumberField(tokenUsage, "output_tokens");
  let totalTokens = usage.total_tokens ?? readNumberField(cumulativeUsage, "total_tokens") ?? readNumberField(tokenUsage, "total_tokens");
  if (totalTokens === null && inputTokens !== null && outputTokens !== null) {
    totalTokens = inputTokens + outputTokens;
  }

  const cacheCreationTokens =
    usage.cache_write_tokens ??
    readNumberField(cumulativeUsage, "cache_write_tokens") ??
    tokenUsageSummary?.cache_write_tokens ??
    readNumberField(cumulativeUsage, "cache_creation_input_tokens") ??
    null;
  const cacheReadTokens =
    usage.cache_read_tokens ??
    readNumberField(cumulativeUsage, "cache_read_tokens") ??
    tokenUsageSummary?.cache_read_tokens ??
    readNumberField(cumulativeUsage, "cache_read_input_tokens") ??
    null;

  const rawContextTokens = usage.context_tokens ?? null;
  const contextTokens =
    rawContextTokens !== null
      ? rawContextTokens
      : lastUsage !== null
        ? readNumberField(lastUsage, "input_tokens") ?? readNumberField(lastUsage, "prompt_tokens")
        : null;
  const contextWindowTokens = usage.context_window_tokens;
  const compactThresholdTokens = usage.auto_compact_threshold_tokens;
  const usageRatio =
    usage.usage_ratio ??
    (contextTokens !== null && contextWindowTokens && contextWindowTokens > 0
      ? Math.min(1, contextTokens / contextWindowTokens)
      : null);
  const compactRatio =
    usage.compact_ratio ??
    (contextTokens !== null && compactThresholdTokens && compactThresholdTokens > 0
      ? Math.min(1, contextTokens / compactThresholdTokens)
      : null);
  const requestCount = usage.request_count ?? tokenUsageSummary?.request_count ?? readNumberField(tokenUsage, "request_count");
  const lastInputTokens = readNumberField(lastUsage, "input_tokens") ?? readNumberField(tokenUsage, "last_input_tokens");
  const lastOutputTokens = readNumberField(lastUsage, "output_tokens") ?? readNumberField(tokenUsage, "last_output_tokens");
  let lastTotalTokens = readNumberField(lastUsage, "total_tokens") ?? readNumberField(tokenUsage, "last_total_tokens");
  if (lastTotalTokens === null && lastInputTokens !== null && lastOutputTokens !== null) {
    lastTotalTokens = lastInputTokens + lastOutputTokens;
  }
  const lastCacheReadTokens =
    readNumberField(lastUsage, "cache_read_tokens") ??
    readNumberField(lastUsage, "cache_read_input_tokens") ??
    readNumberField(tokenUsage, "last_cache_read_tokens");
  const lastCacheWriteTokens =
    readNumberField(lastUsage, "cache_write_tokens") ??
    readNumberField(lastUsage, "cache_creation_input_tokens") ??
    readNumberField(tokenUsage, "last_cache_write_tokens");
  const cacheHitRatio =
    usage.cache_hit_ratio ??
    (cacheReadTokens !== null && cacheCreationTokens !== null && cacheReadTokens + cacheCreationTokens > 0
      ? cacheReadTokens / (cacheReadTokens + cacheCreationTokens)
      : null);
  const cacheSavingsTokens = usage.cache_savings_tokens ?? cacheReadTokens;
  const compactionLevel = normalizeInteger(usage.compaction_level) ?? 0;
  const compactStatus =
    usage.compact_status === "unknown" && compactThresholdTokens && contextTokens !== null
      ? contextTokens >= compactThresholdTokens
        ? "over_threshold"
        : "below_threshold"
      : usage.compact_status;

  let contextSource = usage.context_source;
  if (!contextSource) {
    if (rawContextTokens !== null) {
      contextSource = "backend";
    } else if (contextTokens !== null) {
      contextSource = "provider_last_input";
    } else {
      contextSource = null;
    }
  }

  return {
    ...usage,
    context_tokens: contextTokens,
    context_source: contextSource,
    input_tokens: inputTokens,
    output_tokens: outputTokens,
    total_tokens: totalTokens,
    request_count: requestCount,
    usage_ratio: usageRatio,
    compact_ratio: compactRatio,
    compact_status: compactStatus,
    compaction_level: compactionLevel,
    compaction_diagnostics: usage.compaction_diagnostics ?? {},
    cache_creation_tokens: cacheCreationTokens,
    cache_read_tokens: cacheReadTokens,
    cache_write_tokens: cacheCreationTokens,
    cache_hit_ratio: cacheHitRatio,
    cache_savings_tokens: cacheSavingsTokens,
    last_input_tokens: lastInputTokens,
    last_output_tokens: lastOutputTokens,
    last_total_tokens: lastTotalTokens,
    last_cache_read_tokens: lastCacheReadTokens,
    last_cache_write_tokens: lastCacheWriteTokens,
  };
}

function clampRatio(value: unknown): number {
  if (typeof value !== "number" || Number.isNaN(value) || value <= 0) {
    return 0;
  }
  return Math.min(value, 1);
}

function formatPercent(value: number): string {
  const ratio = clampRatio(value);
  if (ratio > 0 && ratio < 0.01) {
    return "<1%";
  }
  return `${Math.round(ratio * 100)}%`;
}

function formatTokenCount(value: number | null | undefined, emptyLabel = "n/a"): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return emptyLabel;
  }
  const absolute = Math.abs(value);
  if (absolute >= 1_000_000) {
    return `${trimFixed(value / 1_000_000)}M`;
  }
  if (absolute >= 1_000) {
    return `${trimFixed(value / 1_000)}k`;
  }
  return String(value);
}

function formatCost(value: number | null | undefined, currency: string, status: string | null): string | null {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return status === "unknown" ? "n/a" : null;
  }
  const precision = value > 0 && value < 0.01 ? 6 : 4;
  return `${currency} ${value.toFixed(precision)}`;
}

function trimFixed(value: number): string {
  return value.toFixed(value >= 10 ? 0 : 1).replace(/\.0$/, "");
}

function TranscriptTurnBlock({
  turn,
  latestUserMessageId,
  editingMessageId,
  editingMessageDraft,
  onEditingMessageDraftChange,
  onCopyMessage,
  onStartEditMessage,
  onCancelEditMessage,
  onSubmitEditMessage,
}: {
  turn: TranscriptTurn;
  latestUserMessageId: string | null;
  editingMessageId: string | null;
  editingMessageDraft: string;
  onEditingMessageDraftChange(value: string): void;
  onCopyMessage(content: string): Promise<void>;
  onStartEditMessage(message: MessageView): void;
  onCancelEditMessage(): void;
  onSubmitEditMessage(messageId: string): Promise<void>;
}) {
  return (
    <div className="space-y-2">
      {turn.user ? (
        <UserStepMessage
          message={turn.user}
          canEdit={turn.user.message_id === latestUserMessageId}
          isEditing={editingMessageId === turn.user.message_id}
          editDraft={editingMessageDraft}
          onEditDraftChange={onEditingMessageDraftChange}
          onCopy={() => void onCopyMessage(turn.user!.content)}
          onStartEdit={() => onStartEditMessage(turn.user!)}
          onCancelEdit={onCancelEditMessage}
          onSubmitEdit={() => void onSubmitEditMessage(turn.user!.message_id)}
          editor={
            <div className="w-[min(44rem,100%)] rounded-[1.5rem] bg-[var(--panel-muted)] p-4 text-left shadow-[0_18px_46px_rgba(15,23,42,0.08)]">
              {turn.user.artifact_refs.length > 0 ? (
                <ArtifactRefList artifactRefs={turn.user.artifact_refs} className="mb-3 mt-0" />
              ) : null}
              <Textarea
                value={editingMessageDraft}
                onChange={(event) => onEditingMessageDraftChange(event.target.value)}
                className="min-h-24 resize-none rounded-none border-0 bg-transparent px-0 py-0 text-[14px] leading-6 shadow-none focus:border-transparent focus:ring-0"
              />
              <div className="mt-3 flex items-center justify-end gap-2">
                <Button variant="secondary" onClick={onCancelEditMessage}>Cancel</Button>
                <Button variant="primary" onClick={() => void onSubmitEditMessage(turn.user!.message_id)}>Resend</Button>
              </div>
            </div>
          }
        />
      ) : null}
      {turn.assistantMessages.length > 0 ? (
        <StepChainMessage messages={turn.assistantMessages} onCopyMessage={onCopyMessage} />
      ) : null}
    </div>
  );
}

function ComposerCard({
  message,
  onMessageChange,
  selectedFiles,
  onFileSelection,
  onRemoveSelectedFile,
  queuedAttachments,
  onRemoveQueuedAttachment,
  queuedFollowups,
  onGuideFollowup,
  onDeleteFollowup,
  onEditFollowup,
  onRun,
  onStop,
  pendingApproval,
  pendingUserInteraction,
  onSubmitUserInteraction,
  approvalNote,
  onApprovalNoteChange,
  onApprove,
  onApproveSession,
  onCancelApproval,
  approvalBusy,
  interactionBusy,
  onExecutionModeChange,
  executionMode,
  isStreaming,
  isSubmitting,
  models,
  selectedModelName,
  onSelectedModelNameChange,
  selectedReasoningEffort,
  onSelectedReasoningEffortChange,
  selectedPlanMode,
  onSelectedPlanModeChange,
  planModeSupported,
  contextWindowUsage,
  promptCacheDiagnostics,
  promptSectionTokenLedger,
  contextCacheDiagnostics,
  capabilityAssemblyDiagnostics,
  memoryInjectionDiagnostics,
  compactionDiagnostics,
  activeModelName,
}: {
  message: string;
  onMessageChange(value: string): void;
  selectedFiles: File[];
  onFileSelection(files: File[]): void;
  onRemoveSelectedFile(fileIndex: number): void;
  queuedAttachments: ComposerQueuedAttachment[];
  onRemoveQueuedAttachment(fileIndex: number): void;
  queuedFollowups: QueuedFollowUpView[];
  onGuideFollowup(item: QueuedFollowUpView): void | Promise<void>;
  onDeleteFollowup(item: QueuedFollowUpView): void | Promise<void>;
  onEditFollowup(item: QueuedFollowUpView): void | Promise<void>;
  onRun(): void;
  onStop(): void;
  pendingApproval: ApprovalView | null;
  pendingUserInteraction: UserInteractionRequestView | null;
  onSubmitUserInteraction(body: UserInteractionSubmitDraft): void | Promise<void>;
  approvalNote: string;
  onApprovalNoteChange(value: string): void;
  onApprove(): void;
  onApproveSession(): void;
  onCancelApproval(): void;
  approvalBusy: boolean;
  interactionBusy: boolean;
  onExecutionModeChange(mode: ExecutionMode): void;
  executionMode: ExecutionMode;
  isStreaming: boolean;
  isSubmitting: boolean;
  models: Array<Record<string, unknown>>;
  selectedModelName: string;
  onSelectedModelNameChange(value: string): void;
  selectedReasoningEffort: string;
  onSelectedReasoningEffortChange(value: string): void;
  selectedPlanMode: boolean;
  onSelectedPlanModeChange(value: boolean): void;
  planModeSupported: boolean;
  contextWindowUsage: ContextWindowUsageView | null;
  promptCacheDiagnostics?: PromptCacheDiagnosticsDisplay | null;
  promptSectionTokenLedger?: PromptSectionTokenLedgerDisplay | null;
  contextCacheDiagnostics?: ContextCacheDiagnosticsDisplay | null;
  capabilityAssemblyDiagnostics?: CapabilityAssemblyDiagnosticsDisplay | null;
  memoryInjectionDiagnostics?: MemoryInjectionDiagnosticsDisplay | null;
  compactionDiagnostics?: CompactionDiagnosticsDisplay | null;
  activeModelName: string | null;
}) {
  const { t, locale } = useI18n();
  const ui = workspaceCopy(locale);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [manualApprovalOpen, setManualApprovalOpen] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [previewImage, setPreviewImage] = useState<{ name: string; url: string } | null>(null);
  const toolsMenuRef = useRef<HTMLDivElement | null>(null);
  const isPlanApproval = pendingApproval?.action_kind === "plan_confirmation";
  const activeModeLabel = ui.modeLabels[executionMode] ?? executionMode;
  const displayedAttachments = selectedFiles.length + queuedAttachments.length;
  const deepThinkingEnabled = selectedReasoningEffort === "high" || selectedReasoningEffort === "xhigh";
  useDismissablePopup(toolsOpen, toolsMenuRef, () => setToolsOpen(false));

  useEffect(() => {
    const element = composerRef.current;
    if (!element) {
      return;
    }
    const computed = window.getComputedStyle(element);
    const lineHeight = Number.parseFloat(computed.lineHeight || "24") || 24;
    const paddingTop = Number.parseFloat(computed.paddingTop || "0") || 0;
    const paddingBottom = Number.parseFloat(computed.paddingBottom || "0") || 0;
    const borderTop = Number.parseFloat(computed.borderTopWidth || "0") || 0;
    const borderBottom = Number.parseFloat(computed.borderBottomWidth || "0") || 0;
    const verticalChrome = paddingTop + paddingBottom + borderTop + borderBottom;
    const minHeight = lineHeight * 2 + verticalChrome;
    const maxHeight = lineHeight * 7 + verticalChrome;
    element.style.height = "auto";
    const nextHeight = Math.min(Math.max(element.scrollHeight, minHeight), maxHeight);
    element.style.height = `${nextHeight}px`;
    element.style.overflowY = element.scrollHeight > maxHeight ? "auto" : "hidden";
  }, [message]);

  return (
    <div className="mt-2 min-w-0">
      {pendingApproval ? (
        <div className="mb-2 rounded-[0.8rem] border border-[var(--warning)]/30 bg-[var(--warning-soft)] px-2.5 py-2">
          <div className="flex min-w-0 items-start gap-2">
            <ShieldAlertIcon className="mt-0.5 size-4 shrink-0 text-[var(--warning)]" />
            <div className="min-w-0 flex-1">
              <div className="text-[13px] font-semibold text-[var(--ink)]">{ui.misc.approvalRequired}</div>
              {pendingApproval.reason ? (
                <span className="group/approval-reason relative mt-1 block min-w-0">
                  <span
                    className="block overflow-hidden text-xs leading-5 text-[var(--muted)] [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:3]"
                    title={pendingApproval.reason}
                  >
                    {pendingApproval.reason}
                  </span>
                  <span className="pointer-events-none invisible absolute bottom-[calc(100%+0.35rem)] left-0 z-50 block max-w-[min(34rem,90vw)] whitespace-normal break-words rounded-lg border border-[var(--line)] bg-[var(--panel-strong)] px-3 py-2 text-xs leading-5 text-[var(--ink)] opacity-0 shadow-[var(--panel-shadow)] transition-opacity group-hover/approval-reason:visible group-hover/approval-reason:opacity-100">
                    {pendingApproval.reason}
                  </span>
                </span>
              ) : null}
            </div>
          </div>
          {manualApprovalOpen ? (
            <Textarea
              className="mt-2 min-h-16"
              placeholder={isPlanApproval ? ui.drawer.planApprovalNote : defaultApprovalContext(locale)}
              value={approvalNote}
              onChange={(event) => onApprovalNoteChange(event.target.value)}
            />
          ) : null}
          <div className="mt-2 flex flex-wrap gap-1.5">
            <Button variant="primary" disabled={approvalBusy} onClick={() => void onApprove()}>
              <CheckIcon className="size-4" />
              {isPlanApproval ? ui.drawer.confirmPlan : ui.composer.approveOnce}
            </Button>
            {!isPlanApproval && pendingApproval.scope_options.includes("session") ? (
              <Button variant="secondary" disabled={approvalBusy} onClick={() => void onApproveSession()}>
                <CheckIcon className="size-4" />
                {ui.composer.approveSession}
              </Button>
            ) : null}
            <Button variant="danger" disabled={approvalBusy} onClick={() => void onCancelApproval()}>
              <XIcon className="size-4" />
              {isPlanApproval ? ui.drawer.rejectPlan : ui.composer.denyApproval}
            </Button>
            <Button variant="ghost" disabled={approvalBusy} onClick={() => setManualApprovalOpen((current) => !current)}>
              <ChevronDownIcon className={cn("size-4 transition", manualApprovalOpen ? "rotate-180" : "")} />
              {ui.composer.manualApproval}
            </Button>
          </div>
        </div>
      ) : null}
      {queuedFollowups.length > 0 ? (
        <QueuedFollowupStack
          items={queuedFollowups}
          onGuideFollowup={onGuideFollowup}
          onDeleteFollowup={onDeleteFollowup}
          onEditFollowup={onEditFollowup}
        />
      ) : null}
      {pendingUserInteraction ? (
        <div className="mb-2">
          <UserInteractionCard
            interaction={pendingUserInteraction}
            busy={interactionBusy}
            onSubmit={onSubmitUserInteraction}
          />
        </div>
      ) : null}
      <div className="mx-auto w-full max-w-[900px] rounded-[1.2rem] border border-[var(--line)] bg-[var(--panel)] px-3 py-2 shadow-[0_14px_38px_rgba(0,0,0,0.075)]">
        {displayedAttachments > 0 ? (
          <div className="mb-2 flex flex-wrap gap-2">
            {queuedAttachments.map((file, index) => (
              <AttachmentPreviewChip
                key={`${file.filename}-${index}`}
                label={file.filename}
                description={ui.composer.attachmentFromQueue}
                onRemove={() => onRemoveQueuedAttachment(index)}
              />
            ))}
            {selectedFiles.map((file, index) => (
              <AttachmentPreviewChip
                key={`${file.name}-${file.size}-${index}`}
                label={file.name}
                description={ui.composer.pastedFiles}
                file={file}
                onPreviewImage={(url) => setPreviewImage({ name: file.name, url })}
                onRemove={() => onRemoveSelectedFile(index)}
              />
            ))}
          </div>
        ) : null}
        <Dialog open={Boolean(previewImage)} onOpenChange={(open) => !open && setPreviewImage(null)}>
          <DialogContent className="max-w-[min(92vw,70rem)] rounded-3xl border-0 bg-transparent p-0 shadow-none">
            {previewImage ? (
              <img
                src={previewImage.url}
                alt={previewImage.name}
                className="max-h-[86dvh] max-w-full rounded-3xl object-contain shadow-[0_28px_80px_rgba(0,0,0,0.28)]"
              />
            ) : null}
          </DialogContent>
        </Dialog>
        <Textarea
          ref={composerRef}
          rows={2}
          aria-label={t.composer.title}
          placeholder={t.composer.placeholder}
          value={message}
          className="max-h-[calc(1.25rem*7+1rem)] min-h-0 resize-none border-0 bg-transparent px-2 py-1 text-[14px] leading-6 shadow-none outline-none focus-visible:ring-0"
          onChange={(event) => onMessageChange(event.target.value)}
          onPaste={(event) => {
            const pastedFiles = filesFromClipboard(event.clipboardData);
            if (pastedFiles.length > 0) {
              event.preventDefault();
              onFileSelection(pastedFiles);
            }
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              if (!isSubmitting) {
                onRun();
              }
            }
          }}
        />
        <div className="mt-2 flex min-w-0 items-center justify-between gap-2">
          <div className="flex min-w-0 flex-wrap items-center gap-1.5">
            <div ref={toolsMenuRef} className="relative">
              <button
                type="button"
                aria-label={ui.composer.add}
                className="inline-flex size-8 items-center justify-center rounded-full text-[var(--muted)] transition hover:bg-[var(--panel-muted)] hover:text-[var(--ink)]"
                onClick={() => setToolsOpen((current) => !current)}
              >
                <PlusIcon className="size-4" />
              </button>
              {toolsOpen ? (
                <div className="absolute bottom-10 left-0 z-20 w-52 rounded-[0.8rem] border border-[var(--line)] bg-[var(--panel)] p-1.5 shadow-[var(--panel-shadow)]">
                  <input
                    ref={fileInputRef}
                    className="hidden"
                    type="file"
                    multiple
                    onChange={(event) => {
                      onFileSelection(Array.from(event.target.files ?? []));
                      event.currentTarget.value = "";
                      setToolsOpen(false);
                    }}
                  />
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 rounded-[0.65rem] px-2 py-2 text-left text-[13px] text-[var(--ink)] hover:bg-[var(--panel-muted)]"
                    onClick={() => fileInputRef.current?.click()}
                  >
                    <PaperclipIcon className="size-4 text-[var(--muted)]" />
                    {ui.composer.attach}
                  </button>
                  <button
                    type="button"
                    className="flex w-full items-center justify-between rounded-[0.65rem] px-2 py-2 text-left text-[13px] text-[var(--ink)] hover:bg-[var(--panel-muted)]"
                    onClick={() => onSelectedReasoningEffortChange(deepThinkingEnabled ? "medium" : "high")}
                  >
                    <span className="flex items-center gap-2">
                      <BrainCircuitIcon className="size-4 text-[var(--muted)]" />
                      {ui.composer.deepThinking}
                    </span>
                    <span className="text-[12px] text-[var(--muted)]">
                      {deepThinkingEnabled ? ui.composer.deepThinkingOn : ui.composer.deepThinkingOff}
                    </span>
                  </button>
                  {planModeSupported && executionMode !== "chat" ? (
                    <button
                      type="button"
                      className="flex w-full items-center justify-between rounded-[0.65rem] px-2 py-2 text-left text-[13px] text-[var(--ink)] hover:bg-[var(--panel-muted)]"
                      onClick={() => onSelectedPlanModeChange(!selectedPlanMode)}
                    >
                      <span>{ui.composer.planMode}</span>
                      <span className="text-[12px] text-[var(--muted)]">{selectedPlanMode ? "on" : "off"}</span>
                    </button>
                  ) : null}
                </div>
              ) : null}
            </div>
            <ModelPicker
              models={models}
              selectedModelName={selectedModelName}
              onSelectedModelNameChange={onSelectedModelNameChange}
              placeholder={ui.composer.autoModel}
              compact
              className="w-auto min-w-[8rem] max-w-[18rem] border-0 bg-transparent shadow-none"
            />
            <ExecutionModePicker
              executionMode={executionMode}
              onExecutionModeChange={onExecutionModeChange}
              compact
              labelOverride={activeModeLabel}
              quiet
            />
            {planModeSupported && executionMode !== "chat" && selectedPlanMode ? (
              <button
                type="button"
                className="text-[13px] text-[var(--primary)] hover:text-[var(--ink)]"
                onClick={() => onSelectedPlanModeChange(false)}
              >
                {ui.composer.planMode}
              </button>
            ) : null}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {contextWindowUsage ? (
              <ContextWindowUsageControl
                usage={contextWindowUsage}
                promptCacheDiagnostics={promptCacheDiagnostics}
                promptSectionTokenLedger={promptSectionTokenLedger}
                contextCacheDiagnostics={contextCacheDiagnostics}
                capabilityAssemblyDiagnostics={capabilityAssemblyDiagnostics}
                memoryInjectionDiagnostics={memoryInjectionDiagnostics}
                compactionDiagnostics={compactionDiagnostics}
                activeModelName={activeModelName}
              />
            ) : null}
            {isStreaming ? (
              <button
                type="button"
                aria-label={ui.composer.stop}
                className="inline-flex size-9 items-center justify-center rounded-full bg-[var(--danger)] text-white shadow-[0_10px_28px_rgba(239,68,68,0.24)] transition active:translate-y-px"
                onClick={onStop}
              >
                <SquareIcon className="size-4" />
              </button>
            ) : isSubmitting ? (
              <button
                type="button"
                className="inline-flex size-9 items-center justify-center rounded-full bg-[var(--primary)] text-white opacity-80"
                disabled
                aria-label={ui.composer.sending}
              >
                <Loader2Icon className="size-4 animate-spin" />
              </button>
            ) : (
              <button
                type="button"
                aria-label={t.composer.run}
                className="inline-flex size-9 items-center justify-center rounded-full bg-[var(--ink)] text-[var(--panel)] shadow-[0_10px_28px_rgba(15,23,42,0.20)] transition hover:scale-[1.02] active:translate-y-px disabled:cursor-not-allowed disabled:opacity-35"
                onClick={() => void onRun()}
                disabled={!message.trim() && selectedFiles.length === 0 && queuedAttachments.length === 0}
              >
                <ArrowUpIcon className="size-4" />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export function filesFromClipboard(clipboardData: DataTransfer | null) {
  const directFiles = Array.from(clipboardData?.files ?? []);
  if (directFiles.length > 0) {
    return directFiles;
  }
  return Array.from(clipboardData?.items ?? [])
    .filter((item) => item.kind === "file")
    .map((item) => item.getAsFile())
    .filter((file): file is File => Boolean(file));
}

function QueuedFollowupStack({
  items,
  onGuideFollowup,
  onDeleteFollowup,
  onEditFollowup,
}: {
  items: QueuedFollowUpView[];
  onGuideFollowup(item: QueuedFollowUpView): void | Promise<void>;
  onDeleteFollowup(item: QueuedFollowUpView): void | Promise<void>;
  onEditFollowup(item: QueuedFollowUpView): void | Promise<void>;
}) {
  const { locale } = useI18n();
  const ui = workspaceCopy(locale);
  const [activeQueuedMenu, setActiveQueuedMenu] = useState<{ queueId: string; top: number; left: number } | null>(null);
  const activeMenuRef = useRef<HTMLDivElement | null>(null);
  const visibleItems = items.slice(0, 5);
  const hiddenCount = Math.max(items.length - visibleItems.length, 0);
  useDismissablePopup(Boolean(activeQueuedMenu), activeMenuRef, () => setActiveQueuedMenu(null));
  if (visibleItems.length === 0) {
    return null;
  }
  function toggleQueuedMenu(event: React.MouseEvent<HTMLButtonElement>, item: QueuedFollowUpView) {
    event.stopPropagation();
    const rect = event.currentTarget.getBoundingClientRect();
    setActiveQueuedMenu((current) => {
      if (current?.queueId === item.queue_id) {
        return null;
      }
      return {
        queueId: item.queue_id,
        left: Math.min(window.innerWidth - 152, Math.max(8, rect.right - 144)),
        top: Math.max(8, rect.top - 44),
      };
    });
  }
  return (
    <div
      data-testid="queued-followup-stack"
      className="mb-2 mx-auto w-full max-w-[900px] overflow-hidden rounded-[0.95rem] border border-[var(--line)] bg-[color-mix(in_srgb,var(--panel)_94%,white_6%)] text-[13px] shadow-[0_14px_34px_rgba(15,23,42,0.08)]"
    >
      <div className="flex h-10 items-center justify-between gap-3 border-b border-[var(--line)] px-3">
        <div className="flex min-w-0 items-center gap-2">
          <CornerDownRightIcon className="size-4 shrink-0 text-[var(--muted)]" />
          <span className="shrink-0 font-medium text-[var(--ink)]">
            {items.length} {ui.composer.queuedFollowups}
          </span>
          <span className="min-w-0 truncate text-[var(--muted)]">{ui.composer.queuedDuringRun}</span>
        </div>
      </div>
      <div data-testid="queued-followup-list" className="max-h-44 overflow-y-auto py-1">
        {visibleItems.map((item) => {
          const isGuidance = (item.mode ?? "followup") === "guidance";
          const attachmentCount = item.uploaded_filenames.length || item.uploaded_file_refs.length;
          return (
            <div
              key={item.queue_id}
              className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2 px-3 py-1.5 text-[13px] hover:bg-[var(--panel-muted)]"
            >
              <CornerDownRightIcon
                className={cn("size-3.5 shrink-0", isGuidance ? "text-[var(--primary)]" : "text-[var(--muted)]")}
              />
              <div className="min-w-0">
                <div className="flex min-w-0 items-center gap-2">
                  <span className="min-w-0 truncate text-[var(--muted)]">{item.message}</span>
                  {isGuidance ? (
                    <span className="shrink-0 rounded-full bg-[var(--primary-soft)] px-1.5 py-0.5 text-[11px] text-[var(--primary)]">
                      {ui.composer.guiding}
                    </span>
                  ) : null}
                </div>
                {attachmentCount > 0 ? (
                  <div className="mt-0.5 text-[12px] text-[var(--muted)]">{attachmentCount}</div>
                ) : null}
              </div>
              <div className="flex shrink-0 items-center gap-1 text-[var(--muted)]">
                <button
                  type="button"
                  className="inline-flex h-7 items-center gap-1 rounded-[0.55rem] px-2 text-[12px] hover:bg-[var(--panel)] hover:text-[var(--ink)]"
                  onClick={() => void onGuideFollowup(item)}
                >
                  <CornerDownRightIcon className="size-3.5" />
                  {ui.composer.guide}
                </button>
                <button
                  type="button"
                  className="inline-flex size-7 items-center justify-center rounded-full hover:bg-[var(--danger-soft)] hover:text-[var(--danger)]"
                  aria-label={ui.composer.deleteQueued}
                  onClick={() => void onDeleteFollowup(item)}
                >
                  <Trash2Icon className="size-3.5" />
                </button>
                <div>
                  <button
                    type="button"
                    className="inline-flex size-7 items-center justify-center rounded-full hover:bg-[var(--panel)] hover:text-[var(--ink)]"
                    aria-label={ui.composer.moreQueuedActions}
                    aria-expanded={activeQueuedMenu?.queueId === item.queue_id}
                    onPointerDown={(event) => event.stopPropagation()}
                    onClick={(event) => toggleQueuedMenu(event, item)}
                  >
                    <EllipsisIcon className="size-3.5" />
                  </button>
                  {activeQueuedMenu?.queueId === item.queue_id ? (
                    <div
                      ref={activeMenuRef}
                      className="fixed z-[320] w-36 rounded-xl border border-[var(--line)] bg-[var(--panel)] p-1 shadow-[var(--panel-shadow)]"
                      style={{ left: activeQueuedMenu.left, top: activeQueuedMenu.top }}
                      onPointerDown={(event) => event.stopPropagation()}
                      onClick={(event) => event.stopPropagation()}
                    >
                      <button
                        type="button"
                        className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] text-[var(--ink)] hover:bg-[var(--panel-muted)]"
                        onClick={() => {
                          setActiveQueuedMenu(null);
                          void onEditFollowup(item);
                        }}
                      >
                        <PencilIcon className="size-4 text-[var(--muted)]" />
                        {ui.composer.editQueued}
                      </button>
                    </div>
                  ) : null}
                </div>
              </div>
            </div>
          );
        })}
        {hiddenCount > 0 ? (
          <div className="px-3 py-1.5 text-[12px] text-[var(--muted)]">
            {locale === "zh-CN" ? `再显示 ${hiddenCount} 个` : `${hiddenCount} more`}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function AttachmentPreviewChip({
  label,
  description,
  file,
  onPreviewImage,
  onRemove,
}: {
  label: string;
  description: string;
  file?: File;
  onPreviewImage?(url: string): void;
  onRemove(): void;
}) {
  const { locale } = useI18n();
  const ui = workspaceCopy(locale);
  const imageUrl = useObjectUrl(file && file.type.startsWith("image/") ? file : undefined);
  const isImage = Boolean(imageUrl);
  return (
    <div className="relative inline-flex max-w-full items-center gap-2 rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-2 text-xs text-[var(--ink)] shadow-[0_10px_22px_rgba(0,0,0,0.06)]">
      <button
        type="button"
        className={cn(
          "inline-flex size-12 shrink-0 items-center justify-center overflow-hidden rounded-xl bg-[var(--panel-muted)] text-[var(--primary)]",
          isImage ? "cursor-zoom-in" : "cursor-default",
        )}
        onClick={() => {
          if (imageUrl) {
            onPreviewImage?.(imageUrl);
          }
        }}
        aria-label={isImage ? ui.composer.previewImage : ui.composer.fileAttachment}
      >
        {imageUrl ? (
          <img src={imageUrl} alt={label} className="h-full w-full object-cover" />
        ) : (
          <FileTextIcon className="size-5" />
        )}
      </button>
      <span className="min-w-0 pr-4">
        <HoverRevealText value={label} className="max-w-[14rem] font-medium" />
        <span className="block text-[11px] text-[var(--muted)]">{description}</span>
      </span>
      <button
        type="button"
        className="absolute right-1.5 top-1.5 inline-flex size-5 shrink-0 items-center justify-center rounded-full bg-[var(--ink)] text-[var(--panel)] transition hover:opacity-80"
        onClick={onRemove}
        aria-label={`Remove ${label}`}
      >
        <XIcon className="size-3" />
      </button>
    </div>
  );
}

function useObjectUrl(file?: File) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!file || typeof URL.createObjectURL !== "function") {
      setUrl(null);
      return;
    }
    const nextUrl = URL.createObjectURL(file);
    setUrl(nextUrl);
    return () => {
      if (typeof URL.revokeObjectURL === "function") {
        URL.revokeObjectURL(nextUrl);
      }
    };
  }, [file]);
  return url;
}

function RightDrawer({
  open,
  pinned,
  mobile = false,
  section,
  onSectionChange,
  onClose,
  onTogglePinned,
  pendingApproval,
  onApprove,
  onCancelApproval,
  approvalNote,
  onApprovalNoteChange,
  approvalBusy,
  threadState,
  contextWindowUsage,
  promptCacheDiagnostics,
  promptSectionTokenLedger,
  contextCacheDiagnostics,
  capabilityAssemblyDiagnostics,
  memoryInjectionDiagnostics,
  compactionDiagnostics,
  threadSettings,
  timelineItems,
  recentTools,
  uploads,
  activeThread,
  models,
  skills,
  subagentTasks,
  processSessions,
  processCapabilities,
  healthStatus,
  gatewayUrl,
  onOpenOpsConsole,
  memoryLayers,
  memoryLayerId,
  onMemoryLayerChange,
  sessionMemory,
  memoryOverview,
  memoryAudit,
  memoryEntries,
  memoryProviders,
  memoryConflicts,
  memoryStaleness,
  memoryReviewItems,
  memoryTraceItems,
  onActivateProvider,
  onFlushMemory,
  onApproveMemoryReview,
  onRejectMemoryReview,
  onBatchMemoryReview,
  onResolveMemoryConflict,
  onReloadProviders,
  onTestProvider,
  onExportMemory,
  onImportMemory,
  entryDraft,
  onEntryDraftChange,
  entryCategory,
  onEntryCategoryChange,
  editingEntryId,
  onEditEntry,
  onSaveEntry,
  onDeleteEntry,
  sessionSearchQuery,
  onSessionSearchQueryChange,
  onSessionSearch,
  sessionSearchResult,
  reflectionJobs,
  onRunReflection,
  onPauseReflection,
  onResumeReflection,
  onRemoveReflection,
  selectedModelName,
  onSelectedModelNameChange,
  selectedProfile,
  onSelectedProfileChange,
  selectedReasoningEffort,
  onSelectedReasoningEffortChange,
  selectedWorkspaceRoot,
  onSelectedWorkspaceRootChange,
  selectedPlanMode,
  onSelectedPlanModeChange,
  planModeSupported,
  onSaveThreadSettings,
  selectedArtifactPreview,
  onSelectedArtifactPreviewChange,
  selectedProcessSessionId,
  onSelectedProcessSessionIdChange,
  processLog,
  processLogOutput,
  processLogFetching,
  processStdinDraft,
  onProcessStdinDraftChange,
  processColumns,
  processRows,
  onProcessColumnsChange,
  onProcessRowsChange,
  onWriteProcessInput,
  onCloseProcessInput,
  onInterruptProcess,
  onResizeProcess,
  onRefreshProcessLog,
  onWaitSubagent,
  onCancelSubagent,
  onWaitProcess,
  onKillProcess,
}: {
  open: boolean;
  pinned: boolean;
  mobile?: boolean;
  section: DrawerSection;
  onSectionChange(section: DrawerSection): void;
  onClose(): void;
  onTogglePinned(): void;
  pendingApproval: ApprovalView | null;
  onApprove(): void;
  onCancelApproval(): void;
  approvalNote: string;
  onApprovalNoteChange(value: string): void;
  approvalBusy: boolean;
  threadState: any;
  contextWindowUsage: ContextWindowUsageView | null;
  promptCacheDiagnostics?: PromptCacheDiagnosticsDisplay | null;
  promptSectionTokenLedger?: PromptSectionTokenLedgerDisplay | null;
  contextCacheDiagnostics?: ContextCacheDiagnosticsDisplay | null;
  capabilityAssemblyDiagnostics?: CapabilityAssemblyDiagnosticsDisplay | null;
  memoryInjectionDiagnostics?: MemoryInjectionDiagnosticsDisplay | null;
  compactionDiagnostics?: CompactionDiagnosticsDisplay | null;
  threadSettings: any;
  timelineItems: RuntimeTimelineItem[];
  recentTools: ToolActivityView[];
  uploads: UploadItemView[];
  activeThread: ThreadView | null;
  models: Array<Record<string, unknown>>;
  skills: Array<Record<string, unknown>>;
  subagentTasks: SubagentTaskView[];
  processSessions: any[];
  processCapabilities: any | null;
  healthStatus: string;
  gatewayUrl: string;
  onOpenOpsConsole(): void;
  memoryLayers: any[];
  memoryLayerId: MemoryLayerId;
  onMemoryLayerChange(layerId: MemoryLayerId): void;
  sessionMemory: SessionMemoryView | null;
  memoryOverview: any;
  memoryAudit: any;
  memoryEntries: MemoryEntryView[];
  memoryProviders: any[];
  memoryConflicts: MemoryConflictView[];
  memoryStaleness: MemoryStalenessEntryView[];
  memoryReviewItems: MemoryReviewItemView[];
  memoryTraceItems: any[];
  onActivateProvider(providerId: string): Promise<unknown>;
  onFlushMemory(): Promise<void>;
  onApproveMemoryReview(reviewId: string): Promise<void>;
  onRejectMemoryReview(reviewId: string): Promise<void>;
  onBatchMemoryReview(action: "approve" | "reject", reviewIds: string[]): Promise<void>;
  onResolveMemoryConflict(conflictId: string, action: string): Promise<void>;
  onReloadProviders(): Promise<unknown>;
  onTestProvider(providerId: string): Promise<unknown>;
  onExportMemory(): Promise<unknown>;
  onImportMemory(payload: Record<string, unknown>): Promise<unknown>;
  entryDraft: string;
  onEntryDraftChange(value: string): void;
  entryCategory: string;
  onEntryCategoryChange(value: string): void;
  editingEntryId: string | null;
  onEditEntry(entry: MemoryEntryView): void;
  onSaveEntry(): Promise<void>;
  onDeleteEntry(entryId: string): Promise<void>;
  sessionSearchQuery: string;
  onSessionSearchQueryChange(value: string): void;
  onSessionSearch(): Promise<unknown>;
  sessionSearchResult: SessionSearchResultView | null;
  reflectionJobs: any[];
  onRunReflection(jobId: string): Promise<unknown>;
  onPauseReflection(jobId: string): Promise<unknown>;
  onResumeReflection(jobId: string): Promise<unknown>;
  onRemoveReflection(jobId: string): Promise<unknown>;
  selectedModelName: string;
  onSelectedModelNameChange(value: string): void;
  selectedProfile: string;
  onSelectedProfileChange(value: string): void;
  selectedReasoningEffort: string;
  onSelectedReasoningEffortChange(value: string): void;
  selectedWorkspaceRoot: string;
  onSelectedWorkspaceRootChange(value: string): void;
  selectedPlanMode: boolean;
  onSelectedPlanModeChange(value: boolean): void;
  planModeSupported: boolean;
  onSaveThreadSettings(): Promise<void>;
  selectedArtifactPreview: { label: string; artifactUrl: string } | null;
  onSelectedArtifactPreviewChange(value: { label: string; artifactUrl: string } | null): void;
  selectedProcessSessionId: string | null;
  onSelectedProcessSessionIdChange(value: string | null): void;
  processLog: ProcessLogView | null;
  processLogOutput: string;
  processLogFetching: boolean;
  processStdinDraft: string;
  onProcessStdinDraftChange(value: string): void;
  processColumns: string;
  processRows: string;
  onProcessColumnsChange(value: string): void;
  onProcessRowsChange(value: string): void;
  onWriteProcessInput(sessionId: string, submit: boolean): Promise<unknown>;
  onCloseProcessInput(sessionId: string): Promise<unknown>;
  onInterruptProcess(sessionId: string): Promise<unknown>;
  onResizeProcess(sessionId: string): Promise<unknown>;
  onRefreshProcessLog(): void;
  onWaitSubagent(taskId: string): Promise<unknown>;
  onCancelSubagent(taskId: string): Promise<unknown>;
  onWaitProcess(sessionId: string): Promise<unknown>;
  onKillProcess(sessionId: string): Promise<unknown>;
}) {
  const { locale, t } = useI18n();
  const ui = workspaceCopy(locale);
  const opsUi = opsCopy(locale);
  const currentMemoryLayer = memoryLayers.find((layer) => layer.layer_id === memoryLayerId) ?? null;
  const currentMemoryStore = currentMemoryLayer?.store_id
    ? memoryOverview?.stores?.find((store: any) => store.store_id === currentMemoryLayer.store_id)
    : null;
  const [memoryImportDraft, setMemoryImportDraft] = useState("");
  const [memoryImportError, setMemoryImportError] = useState<string | null>(null);
  const [memoryExportPreview, setMemoryExportPreview] = useState<Record<string, unknown> | null>(null);
  const [providerTestResult, setProviderTestResult] = useState<Record<string, unknown> | null>(null);
  const runtimeOperatorStatus = threadState?.runtime_operator_status ?? null;
  const subagentTaskSignature = useMemo(
    () =>
      subagentTasks
        .map(
          (task) =>
            `${task.task_id}:${task.status}:${task.parent_run_id ?? ""}:${(task.depends_on_task_ids ?? []).join(",")}`,
        )
        .join("|"),
    [subagentTasks],
  );
  const subagentDependencyGraph = useMemo(() => buildSubagentDependencyGraph(subagentTasks), [subagentTaskSignature]);

  if (!mobile && !open && !pinned) {
    return null;
  }

  return (
    <aside
      aria-hidden={!mobile && !open && !pinned}
      className={cn(
        mobile
          ? "flex h-full min-h-0 flex-col"
          : "relative flex h-full min-h-0 shrink-0 flex-col overflow-hidden border-l border-[var(--line)] bg-[color-mix(in_srgb,var(--panel)_84%,white_16%)] backdrop-blur-xl transition-[width,opacity] duration-200",
        !mobile && open ? "w-[320px] opacity-100" : "",
        !mobile && !open ? "w-0 opacity-0 pointer-events-none" : "",
      )}
    >
      <PanelHeader
        title={sectionLabel(section, locale)}
        subtitle={activeThread?.title ?? activeThread?.last_user_message_preview ?? activeThread?.thread_id ?? ui.drawer.noThread}
        actions={
          !mobile ? (
            <Button variant={pinned ? "primary" : "ghost"} size="icon" onClick={onTogglePinned} aria-label={ui.drawer.togglePinnedDrawer}>
              <PinIcon className="size-4" />
            </Button>
          ) : undefined
        }
      />

      <div className="flex flex-wrap gap-1.5 border-b border-[var(--line)] px-3 py-2">
        {DRAWER_SECTIONS.map((item) => (
          <Button
            key={item}
            variant={item === section ? "primary" : "ghost"}
            size="sm"
            onClick={() => onSectionChange(item)}
          >
            {sectionLabel(item, locale)}
          </Button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden overscroll-contain px-2.5 py-2.5 pr-2">
        {section === "timeline" ? (
          <div className="space-y-2">
            {runtimeOperatorStatus ? <RuntimeOperatorSummary status={runtimeOperatorStatus} ui={ui} /> : null}
            {timelineItems.length === 0 ? <EmptyPanelText text={ui.drawer.noTimeline} /> : null}
            {timelineItems.map((item) => (
              <div key={item.id} className="rounded-[0.8rem] border border-[var(--line)] bg-[var(--panel)] p-2.5 shadow-[var(--shadow-card)]">
                <div className="flex items-center justify-between gap-3">
                  <div className="text-[13px] font-medium text-[var(--ink)]">{item.label}</div>
                  <Badge tone={timelineBadgeTone(item)}>
                    {item.status && item.status !== item.kind ? `${item.kind} · ${item.status}` : item.kind}
                  </Badge>
                </div>
                <div className="mt-1.5 text-[13px] leading-5 text-[var(--muted)]">{item.detail}</div>
                {item.timestamp ? (
                  <div className="mt-1.5 text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{item.timestamp}</div>
                ) : null}
              </div>
            ))}
          </div>
        ) : null}

        {section === "recent_tools" ? (
          <div className="space-y-2">
            {recentTools.length === 0 ? <EmptyPanelText text={ui.drawer.noRecentTools} /> : null}
            {recentTools.map((tool) => (
              <ToolBlock key={`${tool.tool_call_id ?? tool.name}-${tool.started_at ?? ""}`} tool={tool} />
            ))}
          </div>
        ) : null}

        {section === "approvals" ? (
          <div className="space-y-3">
            {pendingApproval ? (
              <>
                <ApprovalCard approval={pendingApproval} />
                <SectionCard title={ui.drawer.approvalNote}>
                  {(() => {
                    const isPlanApproval = pendingApproval.action_kind === "plan_confirmation";
                    return (
                      <>
                  <Textarea
                    className="min-h-24"
                    placeholder={isPlanApproval ? ui.drawer.planApprovalNote : defaultApprovalContext(locale)}
                    value={approvalNote}
                    onChange={(event) => onApprovalNoteChange(event.target.value)}
                  />
                  <div className="flex flex-wrap gap-2">
                    <Button className="flex-1" variant="primary" disabled={approvalBusy} onClick={() => void onApprove()}>
                      {approvalBusy ? ui.drawer.resumingApproval : isPlanApproval ? ui.drawer.confirmPlan : ui.drawer.resumeApproval}
                    </Button>
                    <Button className="flex-1" variant="danger" disabled={approvalBusy} onClick={() => void onCancelApproval()}>
                      {isPlanApproval ? ui.drawer.rejectPlan : ui.drawer.cancelApproval}
                    </Button>
                  </div>
                      </>
                    );
                  })()}
                </SectionCard>
              </>
            ) : (
              <EmptyPanelText text={t.rightRail.noPendingApproval} />
            )}
            {threadState?.approval_policy_summary ? (
              <SectionCard title={ui.drawer.approvalPolicy}>
                <div className="text-sm text-[var(--muted)]">{threadState.approval_policy_summary}</div>
                <InfoRow label={ui.drawer.needsApproval} value={(threadState.requires_approval_actions ?? []).join(", ") || ui.drawer.none} />
                <InfoRow label={ui.drawer.restricted} value={(threadState.restricted_actions ?? []).join(", ") || ui.drawer.none} />
              </SectionCard>
            ) : null}
            {threadState?.recent_approval_events?.length ? (
              <SectionCard title={ui.drawer.recentApprovals}>
                {threadState.recent_approval_events.map((event: any, index: number) => (
                  <DataCard key={`${event.request_id ?? index}`}>
                    <div className="flex items-center justify-between gap-2">
                      <div className="font-medium text-[var(--ink)]">{event.decision}</div>
                      <Badge tone={event.status === "resolved" ? "success" : "warning"}>{event.status}</Badge>
                    </div>
                    {event.reason ? <div className="mt-2 text-sm text-[var(--muted)]">{event.reason}</div> : null}
                    <div className="mt-2 text-xs uppercase tracking-[0.06em] text-[var(--muted)]">
                      {(event.requested_permissions ?? []).join(", ") || ui.drawer.noRequestedPermissions}
                    </div>
                  </DataCard>
                ))}
              </SectionCard>
            ) : null}
          </div>
        ) : null}

        {section === "files" ? (
          <div className="space-y-3">
            <SectionCard title={ui.drawer.uploadedFiles}>
              {uploads.length === 0 ? <EmptyPanelText text={ui.drawer.noUploadedFiles} /> : null}
              {uploads.map((file, index) => (
                <DataCard key={`${String(file.filename ?? index)}`}>
                  <div className="flex items-center justify-between gap-2">
                    <button
                      type="button"
                      onClick={() =>
                        onSelectedArtifactPreviewChange({
                          label: String(file.filename ?? file.virtual_path ?? ui.misc.toolFallback.toLowerCase()),
                          artifactUrl: resolveGatewayUrl(String(file.artifact_url ?? "")),
                        })
                      }
                      className="text-left text-sm font-medium text-[var(--ink)] transition hover:text-[var(--primary)]"
                    >
                      {String(file.filename ?? file.virtual_path ?? "file")}
                    </button>
                    <div className="flex flex-wrap items-center justify-end gap-2">
                      {file.source_scope ? <Badge tone="neutral">{String(file.source_scope)}</Badge> : null}
                      {file.extension ? <Badge tone="neutral">{String(file.extension)}</Badge> : null}
                    </div>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {file.converter_used ? <Badge tone="accent">{String(file.converter_used)}</Badge> : null}
                    {file.ocr_used ? <Badge tone="warning">OCR</Badge> : null}
                  </div>
                  <div className="mt-3 space-y-2">
                    <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2">
                      <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">
                        {locale === "zh-CN" ? "原始文件" : "Original"}
                      </div>
                      <button
                        type="button"
                        onClick={() =>
                          onSelectedArtifactPreviewChange({
                            label: String(file.filename ?? file.virtual_path ?? "file"),
                            artifactUrl: resolveGatewayUrl(String(file.artifact_url ?? "")),
                          })
                        }
                        className="mt-2 text-left text-sm text-[var(--ink)] transition hover:text-[var(--primary)]"
                      >
                        {String(file.virtual_path ?? file.filename ?? "file")}
                      </button>
                    </div>
                    {(file.companions?.filter((companion) => !companion.internal) ?? []).length > 0 ? (
                      <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2">
                        <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">
                          {locale === "zh-CN" ? "分析资产" : "Analysis"}
                        </div>
                        <div className="mt-2 space-y-2">
                          {file.companions
                            ?.filter((companion) => !companion.internal)
                            .map((companion) => (
                              <button
                                key={`${companion.kind}-${companion.virtual_path ?? companion.label}`}
                                type="button"
                                disabled={!companion.artifact_url}
                                onClick={() =>
                                  companion.artifact_url
                                    ? onSelectedArtifactPreviewChange({
                                        label: companion.label,
                                        artifactUrl: resolveGatewayUrl(companion.artifact_url),
                                      })
                                    : undefined
                                }
                                className="block text-left text-sm text-[var(--primary)] underline decoration-[color-mix(in_srgb,var(--primary)_38%,transparent)] underline-offset-4 disabled:cursor-not-allowed disabled:opacity-60"
                              >
                                {companion.label}
                                {companion.provider ? ` · ${companion.provider}` : ""}
                              </button>
                            ))}
                        </div>
                      </div>
                    ) : file.markdown_artifact_url ? (
                      <button
                        type="button"
                        onClick={() =>
                          onSelectedArtifactPreviewChange({
                            label: String(file.markdown_file ?? `${file.filename}.md`),
                            artifactUrl: resolveGatewayUrl(String(file.markdown_artifact_url)),
                          })
                        }
                        className="block text-sm text-[var(--primary)] underline decoration-[color-mix(in_srgb,var(--primary)_38%,transparent)] underline-offset-4"
                      >
                        {String(file.markdown_file ?? `${file.filename}.md`)}
                      </button>
                    ) : null}
                  </div>
                  {Array.isArray(file.outline) && file.outline.length > 0 ? (
                    <div className="mt-3 rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2 text-xs text-[var(--muted)]">
                      {file.outline
                        .filter((entry) => !entry.truncated)
                        .slice(0, 4)
                        .map((entry, outlineIndex) => (
                          <div key={`${entry.title ?? outlineIndex}`}>
                            {entry.line ? `L${entry.line}: ` : ""}
                            {entry.title}
                          </div>
                        ))}
                    </div>
                  ) : Array.isArray(file.outline_preview) && file.outline_preview.length > 0 ? (
                    <div className="mt-3 rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2 text-xs text-[var(--muted)]">
                      {file.outline_preview.slice(0, 3).map((line, previewIndex) => (
                        <div key={`${line}-${previewIndex}`}>{line}</div>
                      ))}
                    </div>
                  ) : null}
                  {file.extraction?.diagnostics?.length ? (
                    <div className="mt-3 rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2 text-xs text-[var(--muted)]">
                      {file.extraction.diagnostics.slice(0, 3).map((diagnostic, diagnosticIndex) => (
                        <div key={`${diagnostic}-${diagnosticIndex}`}>{diagnostic}</div>
                      ))}
                    </div>
                  ) : null}
                  {file.conversion_error ? (
                    <div className="mt-3 text-xs text-[var(--danger)]">{String(file.conversion_error)}</div>
                  ) : null}
                </DataCard>
              ))}
            </SectionCard>

            <SectionCard title={ui.drawer.outputArtifacts}>
              {threadState?.output_artifacts?.length ? (
                threadState.output_artifacts.map((artifact: { label: string; artifact_url: string | null; virtual_path: string | null }) => (
                  <button
                    key={`${artifact.virtual_path ?? artifact.artifact_url ?? artifact.label}`}
                    type="button"
                    disabled={!artifact.artifact_url}
                    onClick={() =>
                      artifact.artifact_url
                        ? onSelectedArtifactPreviewChange({
                            label: artifact.label,
                            artifactUrl: resolveGatewayUrl(artifact.artifact_url),
                          })
                        : undefined
                    }
                    className="block rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-2 text-left text-sm text-[var(--ink)] transition hover:bg-[var(--panel-strong)] disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {artifact.label}
                  </button>
                ))
              ) : (
                <EmptyPanelText text={ui.drawer.noOutputArtifacts} />
              )}
            </SectionCard>
            {selectedArtifactPreview ? <ArtifactPreviewPanel {...selectedArtifactPreview} /> : null}
          </div>
        ) : null}

        {section === "memory" ? (
          <div className="space-y-4">
            <SectionCard title={ui.drawer.memoryWorkspace}>
              {memoryOverview ? (
                <div className="mb-3 flex flex-wrap gap-2 text-xs text-[var(--muted)]">
                  <Badge tone={memoryOverview.runtime_mode === "memory_platform" ? "success" : "warning"}>
                    {String(memoryOverview.runtime_mode ?? "memory_platform")}
                  </Badge>
                  <Badge tone={memoryOverview.legacy_capture_enabled ? "warning" : "neutral"}>
                    {locale === "zh-CN" ? "Legacy capture" : "Legacy capture"} {memoryOverview.legacy_capture_enabled ? "on" : "off"}
                  </Badge>
                </div>
              ) : null}
              <div className="flex flex-wrap gap-2">
                {memoryLayers.map((layer) => (
                  <Button
                    key={layer.layer_id}
                    size="sm"
                    variant={layer.layer_id === memoryLayerId ? "primary" : "ghost"}
                    onClick={() => onMemoryLayerChange(layer.layer_id)}
                  >
                    {layer.display_name}
                  </Button>
                ))}
              </div>
              {currentMemoryLayer ? (
                <div className="mt-3 rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3 text-sm text-[var(--muted)]">
                  <div className="font-medium text-[var(--ink)]">{currentMemoryLayer.display_name}</div>
                  <div className="mt-1">{currentMemoryLayer.description}</div>
                  {currentMemoryStore ? (
                    <div className="mt-3 space-y-2">
                      <div className="flex items-center justify-between text-xs text-[var(--muted)]">
                        <span>{locale === "zh-CN" ? "Store usage" : "Store usage"}</span>
                        <span>
                          {Number(currentMemoryStore.usage_tokens ?? 0)} / {Number(currentMemoryStore.effective_max_tokens ?? currentMemoryStore.max_tokens ?? 0)}
                        </span>
                      </div>
                      <div className="h-2 overflow-hidden rounded-full bg-[var(--panel)]">
                        <div
                          className="h-full rounded-full bg-[var(--primary)]"
                          style={{
                            width: `${Math.min(100, Math.round(((currentMemoryStore.usage_tokens ?? 0) / Math.max(currentMemoryStore.effective_max_tokens ?? currentMemoryStore.max_tokens ?? 1, 1)) * 100))}%`,
                          }}
                        />
                      </div>
                      <div className="grid gap-1 text-xs text-[var(--muted)]">
                        <div>
                          {locale === "zh-CN" ? "Store budget" : "Store budget"} {Number(currentMemoryStore.effective_max_tokens ?? currentMemoryStore.max_tokens ?? 0)} tokens · {Number(currentMemoryStore.max_chars ?? 0)} chars
                        </div>
                        <div>
                          {locale === "zh-CN" ? "Injection budget" : "Injection budget"} {Number(currentMemoryStore.effective_injection_tokens ?? currentMemoryStore.injection_tokens ?? 0)} tokens · {Number(currentMemoryStore.injection_chars ?? 0)} chars
                        </div>
                        <div>
                          {locale === "zh-CN" ? "Snapshot payload" : "Snapshot payload"} {Number(currentMemoryStore.actual_injection_tokens ?? 0)} tokens · {Number(currentMemoryStore.actual_injection_chars ?? 0)} chars
                        </div>
                        <div>
                          {locale === "zh-CN" ? "Stored size" : "Stored size"} {Number(currentMemoryStore.usage_chars ?? 0)} chars
                        </div>
                      </div>
                      <div className="flex flex-wrap gap-2 pt-1 text-xs text-[var(--muted)]">
                        <Badge tone={currentMemoryStore.snapshot_status === "frozen" ? "success" : "neutral"}>
                          {locale === "zh-CN" ? "快照" : "Snapshot"} {currentMemoryStore.snapshot_status ?? "live"}
                        </Badge>
                        {currentMemoryStore.budget_source ? (
                          <Badge tone={currentMemoryStore.budget_source === "migrated" ? "warning" : "neutral"}>
                            {locale === "zh-CN" ? "预算" : "Budget"} {String(currentMemoryStore.budget_source)}
                          </Badge>
                        ) : null}
                        <span>{locale === "zh-CN" ? "更新时间" : "Updated"} {currentMemoryStore.updated_at ?? ui.drawer.none}</span>
                      </div>
                      <SummarySectionsPanel
                        sections={currentMemoryStore.summary_sections}
                        emptyLabel={locale === "zh-CN" ? "暂无结构化摘要。" : "No structured summary sections."}
                      />
                    </div>
                  ) : null}
                </div>
              ) : null}
            </SectionCard>

            {memoryLayerId === "session" ? (
              <>
                <SectionCard title={ui.drawer.sessionRecall}>
                  <InfoRow label={ui.drawer.archiveTurns} value={String(sessionMemory?.archive_turn_count ?? 0)} />
                  <InfoRow label="Namespace" value={sessionMemory?.memory_namespace ?? ui.drawer.none} mono />
                  <InfoRow label="Injected snapshot" value={sessionMemory?.injected_memory_snapshot_id ?? ui.drawer.none} mono />
                  {sessionMemory?.session_summary ? (
                    <div className="mt-3 rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3 text-sm text-[var(--muted)]">
                      {sessionMemory.session_summary}
                    </div>
                  ) : null}
                  {sessionMemory?.latest_prompt_snapshot ? (
                    <div className="mt-3 rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
                      <div className="text-xs uppercase tracking-[0.06em] text-[var(--muted)]">{ui.drawer.currentSnapshot}</div>
                      <div className="mt-2 text-sm font-medium text-[var(--ink)]">{sessionMemory.latest_prompt_snapshot.snapshot_id}</div>
                      <div className="mt-1 text-xs text-[var(--muted)]">{sessionMemory.latest_prompt_snapshot.prompt_hash}</div>
                    </div>
                  ) : null}
                  <div className="mt-4 space-y-3">
                    {(sessionMemory?.recent_turns ?? []).map((turn) => (
                      <div key={turn.archive_id} className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
                        <div className="text-xs uppercase tracking-[0.06em] text-[var(--muted)]">{ui.drawer.recentTurns}</div>
                        <div className="mt-2 text-sm text-[var(--ink)]">{turn.user_content}</div>
                        <div className="mt-2 text-sm text-[var(--muted)]">{turn.assistant_content}</div>
                      </div>
                    ))}
                  </div>
                </SectionCard>

                <SectionCard title={ui.drawer.sessionSearch}>
                  <Input
                    placeholder={ui.drawer.searchPriorSessions}
                    value={sessionSearchQuery}
                    onChange={(event) => onSessionSearchQueryChange(event.target.value)}
                  />
                  <Button className="mt-3" variant="secondary" onClick={() => void onSessionSearch()}>
                    <SearchIcon className="size-4" />
                    {ui.drawer.sessionSearch}
                  </Button>
                  {(sessionSearchResult?.groups ?? []).map((group) => (
                    <div key={group.thread_id} className="mt-3 rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3 [content-visibility:auto]">
                      <div className="flex items-start justify-between gap-3">
                        <div className="font-medium text-[var(--ink)]">{group.thread_id}</div>
                        <Badge tone="neutral">{group.hit_count} hits</Badge>
                      </div>
                      {group.summary ? (
                        <div className="mt-3 rounded-lg border border-[var(--line)] bg-[var(--panel)] p-3 text-sm text-[var(--ink)]">
                          {group.summary}
                        </div>
                      ) : null}
                      {(group.evidence ?? []).length ? (
                        <div className="mt-3">
                          <div className="text-xs uppercase tracking-[0.06em] text-[var(--muted)]">{ui.drawer.recallEvidence}</div>
                          {(group.evidence ?? []).map((evidence) => (
                            <div key={evidence.evidence_id} className="mt-2 rounded-lg border border-[var(--line)] bg-[var(--panel)] p-3">
                              <div className="text-sm font-medium text-[var(--ink)]">{evidence.reason}</div>
                              <div className="mt-1 text-xs uppercase tracking-[0.06em] text-[var(--muted)]">
                                {evidence.source_kind} · final {Number(evidence.final_score ?? evidence.score ?? 0).toFixed(3)} · rerank {Number(evidence.rerank_score ?? 0).toFixed(3)}
                              </div>
                              {evidence.dropped_reason ? <div className="mt-1 text-xs text-[var(--danger)]">{evidence.dropped_reason}</div> : null}
                              <div className="mt-2 text-sm text-[var(--muted)]">{evidence.excerpt}</div>
                            </div>
                          ))}
                        </div>
                      ) : null}
                      {(group.hits ?? []).length ? (
                        <div className="mt-3">
                          <div className="text-xs uppercase tracking-[0.06em] text-[var(--muted)]">{ui.drawer.matchedTurns}</div>
                          {(group.hits ?? []).map((hit) => (
                            <div key={hit.archive_id} className="mt-2 text-sm text-[var(--muted)]">
                              {hit.excerpt}
                            </div>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  ))}
                  {(sessionSearchResult?.provider_notes ?? []).length ? (
                    <div className="mt-3 rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3 text-sm text-[var(--muted)]">
                      <div className="font-medium text-[var(--ink)]">{ui.drawer.recallEngineNotes}</div>
                      {(sessionSearchResult?.provider_notes ?? []).map((note, index) => (
                        <div key={`${note}-${index}`} className="mt-2">
                          {note}
                        </div>
                      ))}
                    </div>
                  ) : null}
                </SectionCard>

                <SectionCard title={ui.drawer.recallInspector}>
                  {memoryTraceItems.length === 0 ? <EmptyPanelText text={ui.drawer.noRecentTools} /> : null}
                  {memoryTraceItems.map((trace) => (
                    <div key={trace.trace_id} className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
                      <div className="font-medium text-[var(--ink)]">{trace.trace_kind}</div>
                      {trace.query ? <div className="mt-1 text-sm text-[var(--muted)]">{trace.query}</div> : null}
                      {(trace.evidence ?? []).map((evidence: any) => (
                        <div key={evidence.evidence_id} className="mt-3 rounded-lg border border-[var(--line)] bg-[var(--panel)] p-3">
                          <div className="text-sm font-medium text-[var(--ink)]">{evidence.reason}</div>
                          <div className="mt-1 text-xs uppercase tracking-[0.06em] text-[var(--muted)]">
                            {evidence.source_kind} · {evidence.source_id} · final {Number(evidence.final_score ?? evidence.score ?? 0).toFixed(3)}
                          </div>
                          <div className="mt-1 text-xs text-[var(--muted)]">
                            match {Number(evidence.match_score ?? 0).toFixed(3)} · rerank {Number(evidence.rerank_score ?? 0).toFixed(3)} · recency {Number(evidence.recency_score ?? 0).toFixed(3)}
                          </div>
                          <div className="mt-2 text-sm text-[var(--muted)]">{evidence.excerpt}</div>
                        </div>
                      ))}
                    </div>
                  ))}
                </SectionCard>
              </>
            ) : (
              <SectionCard title={memoryLayerId === "user" ? ui.drawer.userMemory : ui.drawer.workspaceMemory}>
                <div className="flex flex-wrap gap-2">
                  <Input
                    placeholder={t.rightRail.entryCategory}
                    value={entryCategory}
                    onChange={(event) => onEntryCategoryChange(event.target.value)}
                  />
                </div>
                <Textarea
                  className="mt-3 min-h-24"
                  placeholder={t.rightRail.entryContent}
                  value={entryDraft}
                  onChange={(event) => onEntryDraftChange(event.target.value)}
                />
                <Button className="mt-3" onClick={() => void onSaveEntry()}>
                  {editingEntryId ? t.rightRail.updateEntry : t.rightRail.createEntry}
                </Button>
                <div className="mt-4 space-y-3">
                  {memoryEntries.map((entry) => (
                    <div key={entry.entry_id} className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
                      <div className="flex items-start justify-between gap-2">
                        <div className="font-medium text-[var(--ink)]">{entry.category}</div>
                        <Badge tone={entry.status === "active" ? "success" : "warning"}>{entry.status}</Badge>
                      </div>
                      <div className="mt-2 text-sm text-[var(--muted)]">{entry.content}</div>
                      <div className="mt-2 text-xs uppercase tracking-[0.06em] text-[var(--muted)]">
                        score {Number(entry.effective_score ?? entry.priority ?? 0).toFixed(2)} · confidence {Number(entry.confidence ?? 0).toFixed(2)} · salience {Number(entry.salience ?? 0).toFixed(2)}
                      </div>
                      {(entry.supersedes?.length ?? 0) > 0 || (entry.conflicts_with?.length ?? 0) > 0 ? (
                        <div className="mt-2 text-xs text-[var(--muted)]">
                          supersedes {(entry.supersedes ?? []).join(", ") || "none"} · conflicts {(entry.conflicts_with ?? []).join(", ") || "none"}
                        </div>
                      ) : null}
                      <div className="mt-3 flex gap-2">
                        <Button size="sm" variant="secondary" onClick={() => onEditEntry(entry)}>
                          {ui.drawer.edit}
                        </Button>
                        <Button size="sm" variant="danger" onClick={() => void onDeleteEntry(entry.entry_id)}>
                          {ui.drawer.delete}
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </SectionCard>
            )}

            <SectionCard title={ui.drawer.platformControls}>
              <InfoRow label={ui.drawer.activeProvider} value={memoryOverview?.active_provider_id ?? ui.drawer.none} />
              <InfoRow label={ui.drawer.stores} value={String(memoryOverview?.store_count ?? 0)} />
              <InfoRow label={ui.drawer.archiveTurns} value={String(memoryOverview?.archive_turn_count ?? 0)} />
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                <Button variant="secondary" onClick={() => void onFlushMemory()}>
                  <BrainCircuitIcon className="size-4" />
                  {locale === "zh-CN" ? "沉淀记忆" : "Flush memory"}
                </Button>
                <Button variant="secondary" onClick={() => void onReloadProviders()}>
                  <WrenchIcon className="size-4" />
                  {locale === "zh-CN" ? "重载 Provider" : "Reload providers"}
                </Button>
                <Button
                  variant="secondary"
                  onClick={async () => {
                    const payload = (await onExportMemory()) as Record<string, unknown>;
                    setMemoryExportPreview(payload);
                  }}
                >
                  <ExternalLinkIcon className="size-4" />
                  {locale === "zh-CN" ? "导出" : "Export"}
                </Button>
                <Button
                  variant="secondary"
                  onClick={async () => {
                    if (!memoryImportDraft.trim()) {
                      return;
                    }
                    try {
                      const payload = JSON.parse(memoryImportDraft) as Record<string, unknown>;
                      await onImportMemory(payload);
                      setMemoryImportError(null);
                    } catch {
                      setMemoryImportError(locale === "zh-CN" ? "导入 JSON 无效" : "Invalid import JSON");
                    }
                  }}
                >
                  <HardDriveUploadIcon className="size-4" />
                  {locale === "zh-CN" ? "导入" : "Import"}
                </Button>
              </div>
              <Textarea
                className="mt-3 min-h-20 font-mono text-xs"
                value={memoryImportDraft}
                onChange={(event) => {
                  setMemoryImportDraft(event.target.value);
                  if (memoryImportError) {
                    setMemoryImportError(null);
                  }
                }}
                placeholder={locale === "zh-CN" ? "粘贴 /memory/admin/export 产生的 JSON，导入会合并而不覆盖。" : "Paste JSON from /memory/admin/export. Imports merge instead of overwriting."}
              />
              {memoryImportError ? <div className="mt-2 text-sm text-[var(--danger)]">{memoryImportError}</div> : null}
              {memoryExportPreview ? (
                <div className="mt-3 rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
                  <div className="text-xs uppercase tracking-[0.06em] text-[var(--muted)]">
                    {locale === "zh-CN" ? "最近导出" : "Latest export"}
                  </div>
                  <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-[var(--panel)] p-3 text-xs text-[var(--muted)]">
                    {JSON.stringify(memoryExportPreview, null, 2)}
                  </pre>
                </div>
              ) : null}
              {memoryAudit ? (
                <div className="mt-4 rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
                  <div className="font-medium text-[var(--ink)]">{locale === "zh-CN" ? "审计快照" : "Audit Snapshot"}</div>
                  <div className="mt-2 grid gap-2 sm:grid-cols-3">
                    <MetricSmall label={locale === "zh-CN" ? "待审" : "Review"} value={String(memoryAudit.pending_review_count ?? 0)} />
                    <MetricSmall label={locale === "zh-CN" ? "冲突" : "Conflicts"} value={String(memoryAudit.conflict_count ?? 0)} />
                    <MetricSmall label={locale === "zh-CN" ? "过期" : "Stale"} value={String(memoryAudit.staleness_count ?? 0)} />
                  </div>
                  {(memoryAudit.providers ?? []).length ? (
                    <div className="mt-3 text-xs text-[var(--muted)]">
                      {(memoryAudit.providers ?? []).map((provider: any) => `${provider.provider_id}:${provider.health ?? "unknown"}`).join(" · ")}
                    </div>
                  ) : null}
                </div>
              ) : null}
              <div className="mt-4 rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
                <div className="font-medium text-[var(--ink)]">{locale === "zh-CN" ? "待审记忆" : "Review Queue"}</div>
                {memoryReviewItems.length === 0 ? <div className="mt-2 text-sm text-[var(--muted)]">{locale === "zh-CN" ? "没有待审候选。" : "No pending candidates."}</div> : null}
                {memoryReviewItems.length > 0 ? (
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Button size="sm" variant="primary" onClick={() => void onBatchMemoryReview("approve", memoryReviewItems.map((item) => item.review_id))}>
                      {locale === "zh-CN" ? "批量批准" : "Approve all"}
                    </Button>
                    <Button size="sm" variant="secondary" onClick={() => void onBatchMemoryReview("reject", memoryReviewItems.map((item) => item.review_id))}>
                      {locale === "zh-CN" ? "批量拒绝" : "Reject all"}
                    </Button>
                  </div>
                ) : null}
                {memoryReviewItems.map((item) => (
                  <div key={item.review_id} className="mt-3 rounded-lg border border-[var(--line)] bg-[var(--panel)] p-3">
                    <div className="flex items-start justify-between gap-2">
                      <div className="text-sm font-medium text-[var(--ink)]">{item.category}</div>
                      <Badge tone="warning">{item.layer_id}</Badge>
                    </div>
                    <div className="mt-2 text-sm text-[var(--muted)]">{item.content}</div>
                    <div className="mt-2 text-xs uppercase tracking-[0.06em] text-[var(--muted)]">
                      confidence {Number(item.confidence ?? 0).toFixed(2)} · salience {Number(item.salience ?? 0).toFixed(2)}
                    </div>
                    {item.rationale ? <div className="mt-2 text-xs text-[var(--muted)]">{item.rationale}</div> : null}
                    <div className="mt-3 flex flex-wrap gap-2">
                      <Button size="sm" variant="primary" onClick={() => void onApproveMemoryReview(item.review_id)}>
                        {locale === "zh-CN" ? "批准" : "Approve"}
                      </Button>
                      <Button size="sm" variant="secondary" onClick={() => void onRejectMemoryReview(item.review_id)}>
                        {locale === "zh-CN" ? "拒绝" : "Reject"}
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
              <div className="mt-4 rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
                <div className="font-medium text-[var(--ink)]">{ui.drawer.conflictQueue}</div>
                {memoryConflicts.length === 0 ? <div className="mt-2 text-sm text-[var(--muted)]">{ui.drawer.noConflicts}</div> : null}
                {memoryConflicts.map((conflict) => (
                  <div key={conflict.conflict_id} className="mt-3 rounded-lg border border-[var(--line)] bg-[var(--panel)] p-3">
                    <div className="text-sm font-medium text-[var(--ink)]">{conflict.reason}</div>
                    <div className="mt-1 text-xs text-[var(--muted)]">
                      {conflict.memory_id} {"<->"} {conflict.conflicting_memory_id}
                    </div>
                    {conflict.memory_content ? <div className="mt-2 text-sm text-[var(--ink)]">{conflict.memory_content}</div> : null}
                    {conflict.conflicting_content ? <div className="mt-2 text-sm text-[var(--muted)]">{conflict.conflicting_content}</div> : null}
                    <div className="mt-3 flex flex-wrap gap-2">
                      <Button size="sm" variant="secondary" onClick={() => void onResolveMemoryConflict(conflict.conflict_id, "keep_both")}>
                        {locale === "zh-CN" ? "都保留" : "Keep both"}
                      </Button>
                      <Button size="sm" variant="secondary" onClick={() => void onResolveMemoryConflict(conflict.conflict_id, "keep_memory")}>
                        {locale === "zh-CN" ? "保留左侧" : "Keep left"}
                      </Button>
                      <Button size="sm" variant="secondary" onClick={() => void onResolveMemoryConflict(conflict.conflict_id, "keep_conflicting")}>
                        {locale === "zh-CN" ? "保留右侧" : "Keep right"}
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
              <div className="mt-4 rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
                <div className="font-medium text-[var(--ink)]">{ui.drawer.stalenessQueue}</div>
                {memoryStaleness.length === 0 ? <div className="mt-2 text-sm text-[var(--muted)]">{ui.drawer.noStaleness}</div> : null}
                {memoryStaleness.map((item) => (
                  <div key={`${item.memory_id}-${item.layer_id}`} className="mt-3 rounded-lg border border-[var(--line)] bg-[var(--panel)] p-3">
                    <div className="text-sm font-medium text-[var(--ink)]">{item.memory_id}</div>
                    <div className="mt-1 text-xs uppercase tracking-[0.06em] text-[var(--muted)]">
                      {item.layer_id} · {Number(item.stale_score ?? 0).toFixed(2)}
                    </div>
                    <div className="mt-2 text-sm text-[var(--muted)]">{item.reason}</div>
                    <div className="mt-2 text-xs text-[var(--muted)]">
                      last accessed {item.last_accessed_at ?? ui.drawer.none} · expires {item.expires_at ?? ui.drawer.none}
                    </div>
                  </div>
                ))}
              </div>
              <div className="mt-4 space-y-3">
                {memoryProviders.map((provider) => (
                  <div key={provider.provider_id} className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="break-words font-medium text-[var(--ink)]">{provider.display_name}</div>
                        <div className="mt-1 break-words text-xs uppercase tracking-[0.06em] text-[var(--muted)]">
                          {provider.family} · {provider.kind ?? "local_curated"} · {provider.origin ?? "builtin"}
                        </div>
                      </div>
                      <Badge tone={provider.health === "ok" ? "success" : provider.health === "error" ? "danger" : "neutral"}>
                        {provider.active ? ui.drawer.active : provider.health ?? "unknown"}
                      </Badge>
                    </div>
                    {(provider.roles ?? []).length ? (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {(provider.roles ?? []).map((role: string) => (
                          <Badge key={`${provider.provider_id}-${role}`} tone="neutral">
                            {role}
                          </Badge>
                        ))}
                      </div>
                    ) : null}
                    <div className="mt-2 text-xs text-[var(--muted)]">
                      {locale === "zh-CN" ? "最近同步" : "Last sync"} {provider.last_sync_at ?? ui.drawer.none}
                    </div>
                    {(provider.diagnostics ?? []).length ? (
                      <div className="mt-2 rounded-lg border border-[var(--line)] bg-[var(--panel)] p-2 text-xs text-[var(--muted)]">
                        {(provider.diagnostics ?? []).slice(0, 4).map((line: string, index: number) => (
                          <div key={`${provider.provider_id}-diag-${index}`} className="break-words">
                            {line}
                          </div>
                        ))}
                      </div>
                    ) : null}
                    <div className="mt-3 flex flex-wrap gap-2">
                      <Button
                        size="sm"
                        variant={provider.active ? "secondary" : "primary"}
                        onClick={() => void onActivateProvider(provider.provider_id)}
                      >
                        {provider.active ? ui.drawer.active : ui.drawer.activateProvider(provider.display_name)}
                      </Button>
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={async () => {
                          const result = (await onTestProvider(provider.provider_id)) as Record<string, unknown>;
                          setProviderTestResult(result);
                        }}
                      >
                        {locale === "zh-CN" ? "测试" : "Test"}
                      </Button>
                    </div>
                  </div>
                ))}
                {providerTestResult ? (
                  <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
                    <div className="font-medium text-[var(--ink)]">{locale === "zh-CN" ? "最近 Provider 测试" : "Latest provider test"}</div>
                    <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-[var(--panel)] p-3 text-xs text-[var(--muted)]">
                      {JSON.stringify(providerTestResult, null, 2)}
                    </pre>
                  </div>
                ) : null}
              </div>
              <div className="mt-4 space-y-3">
                {reflectionJobs.map((job) => (
                  <div key={job.job_id} className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
                    <div className="font-medium text-[var(--ink)]">{job.name}</div>
                    <div className="mt-1 text-xs uppercase tracking-[0.06em] text-[var(--muted)]">{job.template}</div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <Button size="sm" variant="secondary" onClick={() => void onRunReflection(job.job_id)}>
                        {`${t.rightRail.runJob} ${job.name}`}
                      </Button>
                      <Button size="sm" variant="secondary" onClick={() => void onPauseReflection(job.job_id)}>
                        {t.rightRail.pause}
                      </Button>
                      <Button size="sm" variant="secondary" onClick={() => void onResumeReflection(job.job_id)}>
                        {t.rightRail.resume}
                      </Button>
                      <Button size="sm" variant="danger" onClick={() => void onRemoveReflection(job.job_id)}>
                        {t.rightRail.remove}
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            </SectionCard>
          </div>
        ) : null}

        {section === "skills" ? (
          <div className="space-y-2">
            {skills.length === 0 ? <EmptyPanelText text={ui.drawer.noSkills} /> : null}
            {skills.map((skill: any) => (
              <div key={skill.skill_id} className="rounded-[0.8rem] border border-[var(--line)] bg-[var(--panel)] p-2.5">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="text-[13px] font-medium text-[var(--ink)]">{skill.title}</div>
                    <div className="mt-0.5 text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{skill.skill_id}</div>
                  </div>
                  <Badge tone={skill.enabled ? "success" : "neutral"}>{skill.enabled ? ui.drawer.enabled : ui.drawer.disabled}</Badge>
                </div>
                <div className="mt-1.5 text-[13px] leading-5 text-[var(--muted)]">{skill.summary}</div>
                {skill.source_root ? <InfoRow label={ui.drawer.skillSource} value={String(skill.source_root)} mono /> : null}
                {skill.path ? <InfoRow label={ui.drawer.skillPath} value={String(skill.path)} mono /> : null}
              </div>
            ))}
          </div>
        ) : null}

        {section === "subagents" ? (
          <div className="space-y-4">
            {subagentTasks.length === 0 ? <EmptyPanelText text={ui.drawer.noSubagents} /> : null}
            {subagentTasks.length > 0 ? <SubagentDependencyGraphPanel graph={subagentDependencyGraph} ui={ui} /> : null}
            {subagentTasks.map((task) => (
              <SectionCard key={task.task_id} title={task.task_id}>
                <InfoRow label={ui.drawer.status} value={task.status} />
                <InfoRow label={ui.drawer.profile} value={task.assigned_profile} />
                {task.child_thread_id ? <InfoRow label="Child thread" value={task.child_thread_id} mono /> : null}
                {task.child_run_id ? <InfoRow label="Child run" value={task.child_run_id} mono /> : null}
                {task.workspace_mode ? <InfoRow label="Workspace" value={task.workspace_mode} /> : null}
                <InfoRow label={ui.drawer.allowedTools} value={task.allowed_tool_names.join(", ") || ui.drawer.none} />
                {task.summary ? <WorkspaceRichContent content={task.summary} /> : null}
                {task.error ? <div className="text-sm text-[var(--danger)]">{task.error}</div> : null}
                {task.recent_tool_activity?.length ? (
                  <div className="space-y-3">
                    {(task.recent_tool_activity ?? []).map((tool, index) => (
                      <SubagentToolEvidenceBlock key={`${task.task_id}-tool-${tool.tool_call_id ?? tool.name ?? index}`} tool={tool} />
                    ))}
                  </div>
                ) : null}
                {task.messages?.length ? (
                  <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3 text-xs text-[var(--muted)]">
                    {(task.messages ?? []).slice(-3).map((message, index) => (
                      <div key={`${task.task_id}-message-${index}`} className="mb-2 last:mb-0">
                        <span className="font-medium text-[var(--ink)]">{message.role}</span>
                        {message.content_preview ? <span className="ml-1">{message.content_preview}</span> : null}
                        {message.tool_call_count ? <span className="ml-1">tools:{message.tool_call_count}</span> : null}
                      </div>
                    ))}
                  </div>
                ) : null}
                {task.recent_events?.length ? (
                  <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3 text-xs text-[var(--muted)]">
                    {(task.recent_events ?? []).slice(-6).map((event, index) => (
                      <div key={`${task.task_id}-event-${index}`} className="mb-2 last:mb-0">
                        <div className="font-medium text-[var(--ink)]">{String(event.event ?? "event")}</div>
                        <div>{String(event.status ?? event.summary ?? event.error ?? "")}</div>
                      </div>
                    ))}
                  </div>
                ) : null}
                <div className="flex gap-2">
                  <Button size="sm" variant="secondary" onClick={() => void onWaitSubagent(task.task_id)}>
                    {ui.drawer.wait}
                  </Button>
                  {task.status === "running" || task.status === "queued" ? (
                    <Button size="sm" variant="danger" onClick={() => void onCancelSubagent(task.task_id)}>
                      {ui.drawer.cancel}
                    </Button>
                  ) : null}
                </div>
              </SectionCard>
            ))}
          </div>
        ) : null}

        {section === "processes" ? (
          <ProcessTerminalPanel
            ui={ui}
            processCapabilities={processCapabilities}
            processSessions={processSessions}
            selectedProcessSessionId={selectedProcessSessionId}
            onSelectedProcessSessionIdChange={onSelectedProcessSessionIdChange}
            processLog={processLog}
            processLogOutput={processLogOutput}
            processLogFetching={processLogFetching}
            processStdinDraft={processStdinDraft}
            onProcessStdinDraftChange={onProcessStdinDraftChange}
            processColumns={processColumns}
            processRows={processRows}
            onProcessColumnsChange={onProcessColumnsChange}
            onProcessRowsChange={onProcessRowsChange}
            onWriteProcessInput={onWriteProcessInput}
            onCloseProcessInput={onCloseProcessInput}
            onInterruptProcess={onInterruptProcess}
            onResizeProcess={onResizeProcess}
            onRefreshProcessLog={onRefreshProcessLog}
            onWaitProcess={onWaitProcess}
            onKillProcess={onKillProcess}
          />
        ) : null}

        {section === "ops" ? (
          <div className="space-y-4">
            <SectionCard title={opsUi.title}>
              <InfoRow label={t.shell.health} value={healthStatus} />
              <InfoRow label={ui.drawer.url} value={gatewayUrl} mono />
              <div className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-2 text-sm leading-6 text-[var(--muted)]">
                {opsUi.description}
              </div>
              <Button size="sm" variant="secondary" onClick={onOpenOpsConsole}>
                {opsUi.summary.openConsole}
              </Button>
            </SectionCard>
            <SectionCard title={t.rightRail.runtimeCapabilities}>
              <InfoRow
                label={t.rightRail.runtimeSummary}
                value={[
                  threadState?.runtime_capabilities?.summarization_enabled ? "summarization" : null,
                  threadState?.runtime_capabilities?.plan_mode_enabled ? "plan" : null,
                  threadState?.runtime_capabilities?.view_image_enabled ? "vision" : null,
                  threadState?.runtime_capabilities?.memory_enabled ? "memory" : null,
                  threadState?.runtime_capabilities?.guardrails_enabled ? "guardrails" : null,
                ].filter(Boolean).join(", ") || ui.drawer.none}
              />
              <InfoRow
                label={opsUi.summary.runtimeSummary}
                value={[
                  threadState?.execution_mode ?? null,
                  threadState?.runtime_capabilities?.sandbox_mode ?? null,
                ].filter(Boolean).join(", ") || ui.drawer.none}
              />
              <InfoRow label={opsUi.summary.toolsVisible} value={String(threadState?.visible_tool_names?.length ?? 0)} />
              <InfoRow label={opsUi.summary.toolsDeferred} value={String(threadState?.deferred_tool_names?.length ?? 0)} />
              <InfoRow label={opsUi.summary.enabledSkills} value={String(threadState?.enabled_skill_ids?.length ?? 0)} />
              <InfoRow label={opsUi.summary.connectedMcp} value={String(threadState?.runtime_capabilities?.mcp_servers_connected ?? 0)} />
              <InfoRow
                label={t.rightRail.sandboxMode}
                value={String(threadState?.runtime_capabilities?.sandbox_mode ?? "unsupported")}
              />
              <InfoRow
                label={t.rightRail.isolatedSupport}
                value={threadState?.runtime_capabilities?.isolated_sandbox_supported ? "docker-ready" : "unavailable"}
              />
              <InfoRow
                label={t.rightRail.connectedMcp}
                value={String(threadState?.runtime_capabilities?.mcp_servers_connected ?? 0)}
              />
            </SectionCard>
          </div>
        ) : null}

        {section === "settings" ? (
          <div className="space-y-4">
            <SectionCard title={ui.drawer.threadSettings}>
              <InfoRow label={ui.drawer.currentMode} value={String(threadState?.execution_mode ?? "agent")} />
              <InfoRow label={ui.drawer.effectiveModel} value={String(threadState?.effective_model ?? threadState?.active_model ?? ui.drawer.none)} />
              <InfoRow label={ui.drawer.workspaceMode} value={String(threadSettings?.workspace_mode ?? threadState?.workspace_mode ?? "thread")} />
              <InfoRow label={ui.drawer.workspaceRoot} value={String(threadSettings?.workspace_root ?? threadState?.workspace_root ?? ui.drawer.none)} mono />
              <InfoRow label={ui.drawer.resolvedWorkspacePath} value={String(threadSettings?.resolved_workspace_path ?? threadState?.resolved_workspace_path ?? ui.drawer.none)} mono />
              <InfoRow label={ui.drawer.anvilHome} value={String(threadSettings?.anvil_home ?? ui.drawer.none)} mono />
              <InfoRow label={locale === "zh-CN" ? "Anvil Profile" : "Anvil Profile"} value={String(threadSettings?.anvil_profile ?? "default")} mono />
              <InfoRow
                label={locale === "zh-CN" ? "Profile Home" : "Profile Home"}
                value={String(threadSettings?.anvil_profile_home ?? ui.drawer.none)}
                mono
              />
              <ContextWindowSummary
                contextWindowUsage={contextWindowUsage}
                promptCacheDiagnostics={promptCacheDiagnostics}
                promptSectionTokenLedger={promptSectionTokenLedger}
                contextCacheDiagnostics={contextCacheDiagnostics}
                capabilityAssemblyDiagnostics={capabilityAssemblyDiagnostics}
                memoryInjectionDiagnostics={memoryInjectionDiagnostics}
                compactionDiagnostics={compactionDiagnostics}
              />
              <InfoRow
                label={t.rightRail.recentInterruption}
                value={
                  threadState?.last_message_interrupted
                    ? String(threadState?.last_message_interrupted_reason ?? t.transcript.interrupted)
                    : ui.drawer.none
                }
              />
              <div className="space-y-2">
                <label className="grid gap-1.5 text-[13px] text-[var(--muted)]">
                  <span>{ui.drawer.workspaceRoot}</span>
                  <Input
                    value={selectedWorkspaceRoot}
                    onChange={(event) => onSelectedWorkspaceRootChange(event.target.value)}
                    placeholder={locale === "zh-CN" ? "留空表示使用线程独立工作区" : "Leave blank to use per-thread workspace"}
                  />
                </label>
                <label className="grid gap-1.5 text-[13px] text-[var(--muted)]">
                  <span>{ui.drawer.threadDefaultModel}</span>
                  <ModelPicker
                    models={models}
                    selectedModelName={selectedModelName}
                    onSelectedModelNameChange={onSelectedModelNameChange}
                    placeholder={ui.composer.autoModel}
                  />
                </label>
                <label className="grid gap-1.5 text-[13px] text-[var(--muted)]">
                  <span>{ui.drawer.threadProfile}</span>
                  <Input
                    value={selectedProfile}
                    onChange={(event) => onSelectedProfileChange(event.target.value)}
                    placeholder="default"
                  />
                </label>
                <label className="grid gap-1.5 text-[13px] text-[var(--muted)]">
                  <span>{ui.drawer.reasoningEffort}</span>
                  <NativeSelect
                    value={selectedReasoningEffort}
                    onChange={(event) => onSelectedReasoningEffortChange(event.target.value)}
                  >
                    <option value="low">low</option>
                    <option value="medium">medium</option>
                    <option value="high">high</option>
                    <option value="xhigh">xhigh</option>
                  </NativeSelect>
                </label>
                {planModeSupported && threadState?.execution_mode !== "chat" ? (
                  <div className="grid gap-1.5 text-[13px] text-[var(--muted)]">
                    <span>{ui.drawer.planMode}</span>
                    <Button
                      variant={selectedPlanMode ? "primary" : "secondary"}
                      onClick={() => onSelectedPlanModeChange(!selectedPlanMode)}
                      className="justify-start"
                      aria-pressed={selectedPlanMode}
                    >
                      {selectedPlanMode ? ui.drawer.planModeEnabled : ui.drawer.planModeDisabled}
                      <span className="ml-2">{ui.drawer.planMode}</span>
                    </Button>
                  </div>
                ) : null}
                <Button onClick={() => void onSaveThreadSettings()}>{ui.drawer.saveThreadSettings}</Button>
              </div>
            </SectionCard>
          </div>
        ) : null}
      </div>
    </aside>
  );
}

function ApprovalCard({ approval }: { approval: ApprovalView }) {
  return (
    <div className="rounded-[1rem] border border-[var(--warning)]/25 bg-[var(--warning-soft)] px-4 py-4 text-[var(--ink)]">
      <div className="flex items-center gap-2 text-sm font-semibold">
        <ShieldAlertIcon className="size-4" />
        <span>Approval required</span>
      </div>
      {approval.reason ? <div className="mt-2 text-sm text-[var(--muted)]">{approval.reason}</div> : null}
    </div>
  );
}

export function UserInteractionCard({
  interaction,
  busy = false,
  readonly = false,
  compact = false,
  onSubmit,
}: {
  interaction: UserInteractionRequestView;
  busy?: boolean;
  readonly?: boolean;
  compact?: boolean;
  onSubmit?(body: UserInteractionSubmitDraft): void | Promise<void>;
}) {
  const { locale } = useI18n();
  const ui = workspaceCopy(locale);
  const fields = useMemo(() => normalizeInteractionFields(interaction), [interaction]);
  const fieldResetKey = useMemo(
    () => fields.map((field) => `${field.field_id}:${field.selection_mode}:${field.options.map((option) => option.id).join(",")}`).join("|"),
    [fields],
  );
  const [fieldDrafts, setFieldDrafts] = useState<Record<string, UserInteractionFieldDraft>>(() =>
    initialInteractionFieldDrafts(fields),
  );
  const [validationError, setValidationError] = useState<string | null>(null);
  const canSubmit = fields.every((field) => fieldCanSubmit(field, fieldDrafts[field.field_id]));

  useEffect(() => {
    setFieldDrafts(initialInteractionFieldDrafts(fields));
    setValidationError(null);
  }, [interaction.request_id, fieldResetKey]);

  function toggleOption(field: NormalizedInteractionField, optionId: string) {
    setValidationError(null);
    if (readonly) {
      return;
    }
    setFieldDrafts((current) => {
      const draft = current[field.field_id] ?? emptyInteractionFieldDraft();
      let selectedOptionIds: string[];
      if (field.selection_mode === "single") {
        selectedOptionIds = [optionId];
      } else if (draft.selectedOptionIds.includes(optionId)) {
        selectedOptionIds = draft.selectedOptionIds.filter((id) => id !== optionId);
      } else if (typeof field.max_selections === "number" && draft.selectedOptionIds.length >= field.max_selections) {
        selectedOptionIds = draft.selectedOptionIds;
      } else {
        selectedOptionIds = [...draft.selectedOptionIds, optionId];
      }
      return {
        ...current,
        [field.field_id]: { ...draft, selectedOptionIds },
      };
    });
  }

  async function handleSubmit() {
    if (!onSubmit) {
      return;
    }
    if (!canSubmit) {
      setValidationError(ui.composer.interactionRequired);
      return;
    }
    setValidationError(null);
    const fieldResponses = fields.map((field) => {
      const draft = fieldDrafts[field.field_id] ?? emptyInteractionFieldDraft();
      const normalizedCustomResponse = draft.customResponse.trim();
      const normalizedFreeText = draft.freeText.trim();
      const normalizedSelectionIds =
        field.selection_mode === "single" && normalizedCustomResponse.length > 0 ? [] : draft.selectedOptionIds;
      return {
        fieldId: field.field_id,
        selectedOptionIds: normalizedSelectionIds,
        customResponse: normalizedCustomResponse || null,
        freeText: normalizedFreeText || null,
      };
    });
    const first = fieldResponses[0] ?? {
      selectedOptionIds: [],
      customResponse: null,
      freeText: null,
    };
    await onSubmit({
      selectedOptionIds: first.selectedOptionIds,
      customResponse: first.customResponse,
      freeText: first.freeText,
      fieldResponses: interaction.fields?.length ? fieldResponses : [],
    });
  }

  return (
    <div
      className={cn(
        "rounded-[1rem] border border-[color-mix(in_srgb,var(--primary)_24%,var(--line))] bg-[color-mix(in_srgb,var(--panel)_88%,var(--primary-soft)_12%)] text-[var(--ink)] shadow-[0_14px_34px_rgba(15,23,42,0.06)]",
        compact ? "px-3 py-3" : "px-4 py-4",
      )}
      data-testid="user-interaction-card"
    >
      <div className="flex min-w-0 items-start gap-2.5">
        <span className="mt-0.5 inline-flex size-7 shrink-0 items-center justify-center rounded-full bg-[var(--primary-soft)] text-[var(--primary)]">
          <CheckIcon className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <span className="text-[13px] font-semibold text-[var(--ink)]">
              {interaction.title || ui.transcript.userInteraction}
            </span>
            <span className="rounded-full bg-[var(--panel-muted)] px-2 py-0.5 text-[11px] text-[var(--muted)]">
              {fields.length > 1
                ? locale === "zh-CN"
                  ? `${fields.length} 项`
                  : `${fields.length} fields`
                : fields[0]?.selection_mode === "multiple"
                  ? "multi-select"
                  : fields[0]?.selection_mode === "text"
                    ? "text"
                    : "single-select"}
            </span>
          </div>
          <div className="mt-1 text-[13px] leading-5 text-[var(--ink)]">{interaction.question}</div>
          {interaction.description ? (
            <div className="mt-1 text-[12px] leading-5 text-[var(--muted)]">{interaction.description}</div>
          ) : (
            <div className="mt-1 text-[12px] leading-5 text-[var(--muted)]">{ui.transcript.userInteractionDetail}</div>
          )}
        </div>
      </div>
      <div className="mt-3 grid gap-3">
        {fields.map((field) => {
          const draft = fieldDrafts[field.field_id] ?? emptyInteractionFieldDraft();
          const hasOptions = field.options.length > 0;
          const hasTextResponse = field.selection_mode === "text";
          return (
            <div
              key={field.field_id}
              className={cn(fields.length > 1 ? "rounded-[0.9rem] border border-[var(--line)] bg-[var(--panel)] p-3" : "")}
            >
              {fields.length > 1 ? (
                <div className="mb-2">
                  <div className="text-[13px] font-semibold text-[var(--ink)]">{field.label}</div>
                  {field.description ? <div className="mt-0.5 text-[12px] leading-5 text-[var(--muted)]">{field.description}</div> : null}
                </div>
              ) : null}
              {hasOptions ? (
                <div className={cn("grid gap-2", readonly ? "opacity-80" : "")}>
                  {field.options.map((option) => {
                    const checked = draft.selectedOptionIds.includes(option.id);
                    return (
                      <button
                        key={option.id}
                        type="button"
                        disabled={readonly || option.disabled}
                        onClick={() => toggleOption(field, option.id)}
                        className={cn(
                          "group grid min-w-0 grid-cols-[auto_minmax(0,1fr)] gap-2 rounded-[0.85rem] border px-3 py-2 text-left transition",
                          checked
                            ? "border-[color-mix(in_srgb,var(--primary)_45%,var(--line))] bg-[var(--primary-soft)]"
                            : "border-[var(--line)] bg-[var(--panel)] hover:bg-[var(--panel-muted)]",
                          option.disabled ? "cursor-not-allowed opacity-55" : "active:translate-y-px",
                        )}
                        aria-pressed={checked}
                      >
                        <span
                          className={cn(
                            "mt-0.5 inline-flex size-4 items-center justify-center rounded-full border",
                            field.selection_mode === "multiple" ? "rounded-[0.3rem]" : "",
                            checked
                              ? "border-[var(--primary)] bg-[var(--primary)] text-white"
                              : "border-[var(--line)] bg-[var(--panel)]",
                          )}
                        >
                          {checked ? <CheckIcon className="size-3" /> : null}
                        </span>
                        <span className="min-w-0">
                          <span className="flex min-w-0 items-center gap-2">
                            <span className="min-w-0 break-words text-[13px] font-medium text-[var(--ink)] [overflow-wrap:anywhere]">
                              {option.label}
                            </span>
                            {option.recommended ? (
                              <span className="shrink-0 rounded-full bg-[var(--panel-muted)] px-1.5 py-0.5 text-[11px] text-[var(--primary)]">
                                {locale === "zh-CN" ? "推荐" : "Recommended"}
                              </span>
                            ) : null}
                          </span>
                          {option.description ? (
                            <span className="mt-1 block break-words text-[12px] leading-5 text-[var(--muted)] [overflow-wrap:anywhere]">
                              {option.description}
                            </span>
                          ) : null}
                        </span>
                      </button>
                    );
                  })}
                </div>
              ) : null}
              {!readonly && field.allow_custom && !hasTextResponse ? (
                <label className="mt-3 grid gap-1.5 text-[12px] text-[var(--muted)]">
                  <span>{field.custom_label || ui.composer.interactionCustom}</span>
                  <Input
                    value={draft.customResponse}
                    onChange={(event) => {
                      setValidationError(null);
                      const value = event.target.value;
                      setFieldDrafts((current) => {
                        const nextDraft = current[field.field_id] ?? emptyInteractionFieldDraft();
                        return {
                          ...current,
                          [field.field_id]: {
                            ...nextDraft,
                            selectedOptionIds:
                              field.selection_mode === "single" && value.trim() ? [] : nextDraft.selectedOptionIds,
                            customResponse: value,
                          },
                        };
                      });
                    }}
                    placeholder={field.placeholder || ui.composer.interactionFreeText}
                  />
                </label>
              ) : null}
              {!readonly && hasTextResponse ? (
                <label className="mt-3 grid gap-1.5 text-[12px] text-[var(--muted)]">
                  <span>{field.custom_label || ui.composer.interactionFreeText}</span>
                  <Textarea
                    value={draft.freeText}
                    onChange={(event) => {
                      setValidationError(null);
                      const value = event.target.value;
                      setFieldDrafts((current) => ({
                        ...current,
                        [field.field_id]: {
                          ...(current[field.field_id] ?? emptyInteractionFieldDraft()),
                          freeText: value,
                        },
                      }));
                    }}
                    className="min-h-20 resize-y"
                    placeholder={field.placeholder || ui.composer.interactionFreeText}
                  />
                </label>
              ) : null}
            </div>
          );
        })}
      </div>
      {!readonly ? (
        <div className="mt-3 flex min-w-0 items-center justify-between gap-2">
          <span className="min-h-5 text-[12px] text-[var(--danger)]">{validationError}</span>
          <Button size="sm" variant="primary" disabled={busy || !canSubmit} onClick={() => void handleSubmit()}>
            {busy ? <Loader2Icon className="size-3.5 animate-spin" /> : <CheckIcon className="size-3.5" />}
            {ui.composer.submitInteraction}
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function normalizeInteractionFields(interaction: UserInteractionRequestView): NormalizedInteractionField[] {
  if (interaction.fields?.length) {
    return interaction.fields.map((field) => ({
      field_id: field.field_id,
      label: field.label,
      description: field.description,
      selection_mode: field.selection_mode ?? "single",
      options: field.options ?? [],
      min_selections: field.min_selections ?? (field.required === false ? 0 : field.selection_mode === "text" ? 1 : 1),
      max_selections: field.selection_mode === "single" ? 1 : field.max_selections,
      allow_custom: Boolean(field.allow_custom),
      custom_label: field.custom_label,
      placeholder: field.placeholder,
      required: field.required !== false,
    }));
  }
  return [
    {
      field_id: "response",
      label: interaction.question,
      description: interaction.description,
      selection_mode: interaction.selection_mode ?? "single",
      options: interaction.options ?? [],
      min_selections:
        interaction.min_selections ??
        (interaction.required === false ? 0 : interaction.selection_mode === "text" ? 1 : interaction.options?.length ? 1 : 0),
      max_selections: interaction.selection_mode === "single" ? 1 : interaction.max_selections,
      allow_custom: Boolean(interaction.allow_custom),
      custom_label: interaction.custom_label,
      placeholder: interaction.placeholder,
      required: interaction.required !== false,
    },
  ];
}

function emptyInteractionFieldDraft(): UserInteractionFieldDraft {
  return { selectedOptionIds: [], customResponse: "", freeText: "" };
}

function initialInteractionFieldDrafts(fields: NormalizedInteractionField[]): Record<string, UserInteractionFieldDraft> {
  return Object.fromEntries(
    fields.map((field) => {
      const recommended = field.options.find((option) => option.recommended && !option.disabled);
      const firstEnabled = field.options.find((option) => !option.disabled);
      const selectedOptionIds =
        field.selection_mode === "single" && (recommended ?? firstEnabled) ? [(recommended ?? firstEnabled)!.id] : [];
      return [field.field_id, { selectedOptionIds, customResponse: "", freeText: "" }];
    }),
  );
}

function fieldCanSubmit(field: NormalizedInteractionField, draft: UserInteractionFieldDraft | undefined): boolean {
  const current = draft ?? emptyInteractionFieldDraft();
  if (field.selection_mode === "text") {
    return !field.required || current.freeText.trim().length > 0;
  }
  const customFilled = field.allow_custom && current.customResponse.trim().length > 0;
  const selectionCount = current.selectedOptionIds.length + (customFilled ? 1 : 0);
  if (selectionCount < field.min_selections) {
    return false;
  }
  if (typeof field.max_selections === "number" && selectionCount > field.max_selections) {
    return false;
  }
  return selectionCount > 0 || (!field.required && field.min_selections === 0);
}

function ProcessTerminalPanel({
  ui,
  processCapabilities,
  processSessions,
  selectedProcessSessionId,
  onSelectedProcessSessionIdChange,
  processLog,
  processLogOutput,
  processLogFetching,
  processStdinDraft,
  onProcessStdinDraftChange,
  processColumns,
  processRows,
  onProcessColumnsChange,
  onProcessRowsChange,
  onWriteProcessInput,
  onCloseProcessInput,
  onInterruptProcess,
  onResizeProcess,
  onRefreshProcessLog,
  onWaitProcess,
  onKillProcess,
}: {
  ui: ReturnType<typeof workspaceCopy>;
  processCapabilities: TerminalBackendCapabilitiesView | null;
  processSessions: ProcessSessionView[];
  selectedProcessSessionId: string | null;
  onSelectedProcessSessionIdChange(value: string | null): void;
  processLog: ProcessLogView | null;
  processLogOutput: string;
  processLogFetching: boolean;
  processStdinDraft: string;
  onProcessStdinDraftChange(value: string): void;
  processColumns: string;
  processRows: string;
  onProcessColumnsChange(value: string): void;
  onProcessRowsChange(value: string): void;
  onWriteProcessInput(sessionId: string, submit: boolean): Promise<unknown>;
  onCloseProcessInput(sessionId: string): Promise<unknown>;
  onInterruptProcess(sessionId: string): Promise<unknown>;
  onResizeProcess(sessionId: string): Promise<unknown>;
  onRefreshProcessLog(): void;
  onWaitProcess(sessionId: string): Promise<unknown>;
  onKillProcess(sessionId: string): Promise<unknown>;
}) {
  const selectedSession =
    processSessions.find((session) => session.session_id === selectedProcessSessionId) ?? processSessions[0] ?? null;
  const selectedSessionId = selectedSession?.session_id ?? null;
  const canSendInput = Boolean(selectedSessionId && selectedSession?.status === "running" && !selectedSession?.stdin_closed);
  const logText = processLogOutput || processLog?.output || selectedSession?.last_output || "";

  return (
    <div className="space-y-4">
      {processCapabilities ? (
        <SectionCard title={`${String(processCapabilities.label ?? "Terminal")} · ${String(processCapabilities.backend_id ?? "local")}`}>
          <InfoRow label={ui.drawer.backend} value={String(processCapabilities.kind ?? "local")} />
          <InfoRow label={ui.drawer.executable} value={processCapabilities.executable ? "yes" : "no"} />
          <InfoRow
            label={ui.drawer.status}
            value={[
              processCapabilities.interactive ? "interactive" : null,
              processCapabilities.stdin ? "stdin" : null,
              processCapabilities.interrupt ? "interrupt" : null,
              processCapabilities.incremental_log ? "cursor-log" : null,
            ].filter(Boolean).join(", ") || ui.drawer.none}
          />
          {Array.isArray(processCapabilities.notes) && processCapabilities.notes.length ? (
            <pre className="max-h-[140px] overflow-auto whitespace-pre-wrap break-all rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3 text-xs text-[var(--muted)]">
              {processCapabilities.notes.join("\n")}
            </pre>
          ) : null}
        </SectionCard>
      ) : null}

      {processSessions.length === 0 ? <EmptyPanelText text={ui.drawer.noProcesses} /> : null}

      {processSessions.length > 0 ? (
        <SectionCard title="Sessions">
          <div className="grid gap-2">
            {processSessions.map((session) => {
              const isSelected = selectedSessionId === session.session_id;
              const isRunning = session.status === "running";
              return (
                <button
                  key={session.session_id}
                  type="button"
                  className={cn(
                    "grid w-full gap-2 rounded-xl border p-3 text-left transition-colors",
                    isSelected
                      ? "border-[var(--accent)] bg-[color-mix(in_srgb,var(--accent)_12%,var(--panel))]"
                      : "border-[var(--line)] bg-[var(--panel-muted)] hover:border-[var(--accent)]",
                  )}
                  onClick={() => onSelectedProcessSessionIdChange(session.session_id)}
                >
                  <div className="flex min-w-0 items-center justify-between gap-2">
                    <div className="flex min-w-0 items-center gap-2">
                      <TerminalIcon className="size-4 shrink-0 text-[var(--muted)]" />
                      <HoverRevealText value={session.session_id} className="font-medium text-[var(--ink)]" />
                    </div>
                    <Badge tone={isRunning ? "accent" : session.status === "completed" ? "success" : "neutral"}>
                      {session.status}
                    </Badge>
                  </div>
                  <HoverRevealText value={session.command} className="font-mono text-xs text-[var(--muted)]" />
                </button>
              );
            })}
          </div>
        </SectionCard>
      ) : null}

      {selectedSession ? (
        <SectionCard title={`Session · ${selectedSession.session_id}`}>
          <InfoRow label={ui.drawer.command} value={selectedSession.command} mono />
          <InfoRow label={ui.drawer.cwd} value={selectedSession.cwd} mono />
          <InfoRow label={ui.drawer.backend} value={`${selectedSession.backend_label ?? selectedSession.backend ?? "local"} · ${selectedSession.backend_id ?? "local"}`} />
          <InfoRow label={ui.drawer.pid} value={selectedSession.pid == null ? ui.drawer.none : String(selectedSession.pid)} />
          <InfoRow label={ui.drawer.exitCode} value={selectedSession.exit_code == null ? ui.drawer.none : String(selectedSession.exit_code)} />
          <InfoRow label={ui.drawer.dimensions} value={`${selectedSession.columns ?? "-"} x ${selectedSession.rows ?? "-"}`} />
          <div className="mt-3 grid grid-cols-2 gap-2">
            <Button size="sm" variant="secondary" onClick={() => void onWaitProcess(selectedSession.session_id)}>
              {ui.drawer.wait}
            </Button>
            {selectedSession.status === "running" ? (
              <Button size="sm" variant="danger" onClick={() => void onKillProcess(selectedSession.session_id)}>
                {ui.drawer.kill}
              </Button>
            ) : null}
          </div>
        </SectionCard>
      ) : null}

      {selectedSession ? (
        <SectionCard title={ui.drawer.stdin}>
          <Textarea
            className="min-h-24 rounded-xl font-mono text-xs"
            value={processStdinDraft}
            disabled={!canSendInput}
            onChange={(event) => onProcessStdinDraftChange(event.target.value)}
            placeholder={selectedSession.stdin_closed ? ui.drawer.stdinClosed : ui.drawer.stdinPlaceholder}
          />
          <div className="mt-3 grid grid-cols-2 gap-2">
            <Button size="sm" variant="secondary" disabled={!canSendInput} onClick={() => void onWriteProcessInput(selectedSession.session_id, false)}>
              <KeyboardIcon className="size-4" />
              {ui.drawer.sendInput}
            </Button>
            <Button size="sm" variant="secondary" disabled={!canSendInput} onClick={() => void onWriteProcessInput(selectedSession.session_id, true)}>
              {ui.drawer.submitInput}
            </Button>
            <Button size="sm" variant="secondary" disabled={!canSendInput} onClick={() => void onInterruptProcess(selectedSession.session_id)}>
              {ui.drawer.interrupt}
            </Button>
            <Button size="sm" variant="danger" disabled={!canSendInput} onClick={() => void onCloseProcessInput(selectedSession.session_id)}>
              {ui.drawer.closeInput}
            </Button>
          </div>
          {selectedSession.input_history?.length ? (
            <div className="mt-3 rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3 text-xs text-[var(--muted)]">
              <div className="mb-2 font-medium text-[var(--ink)]">{ui.drawer.inputHistory}</div>
              {selectedSession.input_history.slice(-4).map((event, index) => (
                <HoverRevealText
                  key={`${event.created_at}-${index}`}
                  value={`${event.submitted ? "↵ " : ""}${event.text_preview}`}
                  className="font-mono"
                />
              ))}
            </div>
          ) : null}
        </SectionCard>
      ) : null}

      {selectedSession ? (
        <SectionCard title={ui.drawer.resize}>
          <div className="grid grid-cols-2 gap-2">
            <Input aria-label="terminal columns" inputMode="numeric" value={processColumns} onChange={(event) => onProcessColumnsChange(event.target.value)} />
            <Input aria-label="terminal rows" inputMode="numeric" value={processRows} onChange={(event) => onProcessRowsChange(event.target.value)} />
          </div>
          <Button className="mt-3 w-full" size="sm" variant="secondary" onClick={() => void onResizeProcess(selectedSession.session_id)}>
            {ui.drawer.resize}
          </Button>
        </SectionCard>
      ) : null}

      {selectedSession ? (
        <SectionCard title={`Log · ${selectedSession.session_id}`}>
          <div className="mb-3 flex items-center justify-between gap-2 text-xs text-[var(--muted)]">
            <span>
              {ui.drawer.logCursor}: {processLog?.start_offset ?? 0} → {processLog?.next_offset ?? selectedSession.log_cursor ?? 0}
            </span>
            <Button size="sm" variant="ghost" onClick={onRefreshProcessLog} aria-label={ui.drawer.refreshLog}>
              <RotateCcwIcon className={cn("size-4", processLogFetching ? "animate-spin" : "")} />
              {ui.drawer.refreshLog}
            </Button>
          </div>
          <pre className="max-h-[420px] min-h-32 overflow-auto whitespace-pre-wrap break-all rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3 text-xs text-[var(--muted)]">
            {logText || " "}
          </pre>
        </SectionCard>
      ) : null}
    </div>
  );
}

function SummarySectionsPanel({
  sections,
  emptyLabel,
}: {
  sections?: Record<string, Record<string, string>> | null;
  emptyLabel: string;
}) {
  const entries = Object.entries(sections ?? {}).flatMap(([group, values]) =>
    Object.entries(values ?? {})
      .map(([key, value]) => ({ group, key, value: String(value ?? "") }))
      .filter((entry) => entry.value.trim().length > 0),
  );
  if (!entries.length) {
    return <div className="rounded-lg border border-[var(--line)] bg-[var(--panel)] p-2 text-xs text-[var(--muted)]">{emptyLabel}</div>;
  }
  return (
    <div className="space-y-2 rounded-lg border border-[var(--line)] bg-[var(--panel)] p-2">
      {entries.map((entry) => (
        <div key={`${entry.group}-${entry.key}`} className="min-w-0">
          <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">
            {entry.group} / {entry.key}
          </div>
          <div className="mt-1 break-words text-xs text-[var(--ink)]">{entry.value}</div>
        </div>
      ))}
    </div>
  );
}

function MetricSmall({ label, value, tone }: { label: string; value: string; tone?: "primary" | "success" | "warning" | "neutral" }) {
  const toneClass = tone === "primary"
    ? "text-[var(--primary)]"
    : tone === "success"
      ? "text-[var(--success)]"
      : tone === "warning"
        ? "text-[var(--warning)]"
        : "text-[var(--foreground)]";

  return (
    <div className="flex flex-col gap-0.5 rounded-md bg-[var(--panel)] p-1.5">
      <HoverRevealText
        value={label}
        className="text-[10px] uppercase tracking-wider text-[var(--muted)]"
      />
      <HoverRevealText
        value={value}
        className={cn("font-mono text-sm font-medium", toneClass)}
      />
    </div>
  );
}

// Local helper components have been moved to @/src/components/ui
// ModelPicker has been moved to ./model-picker.tsx

function ExecutionModePicker({
  executionMode,
  onExecutionModeChange,
  compact = false,
  labelOverride,
  quiet = false,
}: {
  executionMode: ExecutionMode;
  onExecutionModeChange(mode: ExecutionMode): void;
  compact?: boolean;
  labelOverride?: string;
  quiet?: boolean;
}) {
  const { locale } = useI18n();
  const ui = workspaceCopy(locale);
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  useDismissablePopup(open, containerRef, () => setOpen(false));

  const options = useMemo(
    () =>
      EXECUTION_MODES.map((mode) => ({
        value: mode.id,
        label: ui.modeLabels[mode.id],
        summary: ui.modeSummaries[mode.id],
      })),
    [ui.modeLabels, ui.modeSummaries],
  );
  const current = options.find((option) => option.value === executionMode) ?? options[0]!;

  return (
    <div ref={containerRef} className={cn("relative min-w-0", compact ? (quiet ? "min-w-[5.75rem] shrink-0" : "w-[10.5rem]") : "w-full")}>
      <button
        type="button"
        aria-label={ui.composer.executionMode}
        aria-expanded={open}
        onClick={() => setOpen((currentOpen) => !currentOpen)}
        className={cn(
          "flex w-full items-center justify-between gap-2 rounded-[0.7rem] text-left text-[13px] text-[var(--ink)] transition-[background,border-color,box-shadow,transform] duration-200 ease-[var(--ease-smooth)] active:translate-y-px",
          quiet
            ? "border border-transparent bg-transparent px-1 shadow-none hover:bg-[var(--panel-muted)]"
            : "border border-[var(--line)] bg-[var(--panel)] px-2.5 shadow-[var(--shadow-card)] hover:border-[color-mix(in_srgb,var(--primary)_26%,var(--line))] hover:bg-[var(--panel-strong)]",
          compact ? "h-8" : "h-9",
        )}
      >
        <span className={cn("min-w-0", quiet ? "whitespace-normal break-words leading-4" : "truncate")}>{labelOverride ?? current.label}</span>
        <ChevronsUpDownIcon className="size-4 shrink-0 text-[var(--muted)]" />
      </button>
      {open ? (
        <div
          role="listbox"
          aria-label={ui.composer.executionMode}
          className={cn(
            "absolute bottom-[calc(100%+0.4rem)] left-0 z-20 max-h-72 overflow-hidden rounded-[0.8rem] border border-[var(--line)] bg-[var(--panel-strong)] p-1 shadow-[var(--panel-shadow)]",
            quiet ? "w-[13.5rem]" : "w-full",
          )}
        >
          <div className="max-h-64 overflow-y-auto pr-1">
            <div className="space-y-1">
              {options.map((option) => {
                const active = option.value === executionMode;
                return (
                  <button
                    key={option.value}
                    type="button"
                    role="option"
                    aria-selected={active}
                    onClick={() => {
                      onExecutionModeChange(option.value);
                      setOpen(false);
                    }}
                    className={cn(
                      "flex w-full items-start justify-between gap-2 rounded-[0.65rem] px-2 py-1.5 text-left text-[13px] transition",
                      active
                        ? "bg-[var(--accent-soft)] text-[var(--ink)]"
                        : "text-[var(--ink)] hover:bg-[var(--panel-muted)]",
                    )}
                  >
                    <div className="min-w-0">
                      <div className={quiet ? "whitespace-nowrap" : "truncate"}>{option.label}</div>
                      <div className={cn("mt-1 text-xs text-[var(--muted)]", quiet ? "whitespace-normal leading-4" : "truncate")}>{option.summary}</div>
                    </div>
                    {active ? <CheckIcon className="mt-0.5 size-4 shrink-0 text-[var(--primary)]" /> : null}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function displayToolLabel(tool: Pick<ToolActivityView, "display_name" | "name" | "source_kind" | "capability_group">) {
  return tool.display_name ?? tool.name ?? tool.capability_group ?? tool.source_kind ?? "Tool";
}

type RuntimeTimelineItem = {
  id: string;
  label: string;
  detail: string;
  timestamp: string | null;
  kind: string;
  status: string;
  durationMs: number | null;
};

type RuntimePhaseMarkSummary = {
  phase: string;
  label: string;
  elapsedMs: number;
  durationSincePreviousMs: number;
};

type RuntimePhaseDiagnostics = {
  status: string;
  totalElapsedMs: number | null;
  firstModelEventElapsedMs: number | null;
  firstContentDeltaElapsedMs: number | null;
  completedElapsedMs: number | null;
  phaseCount: number;
  slowestPhase: RuntimePhaseMarkSummary | null;
  marks: RuntimePhaseMarkSummary[];
};

function RuntimeOperatorSummary({ status, ui }: { status: RuntimeOperatorStatusView; ui: ReturnType<typeof workspaceCopy> }) {
  const diagnostics = buildRuntimePhaseDiagnostics(status.runtime_phase_timings ?? null);
  const summary = [
    { label: ui.drawer.activeTools, value: status.active_tool_count ?? 0 },
    { label: ui.drawer.completedTools, value: status.completed_tool_count ?? 0 },
    { label: ui.drawer.failedTools, value: status.failed_tool_count ?? 0 },
    { label: ui.drawer.pendingApprovals, value: status.pending_approval_count ?? 0 },
    { label: ui.drawer.runningProcesses, value: status.running_process_count ?? 0 },
    { label: ui.drawer.activeSubagents, value: status.active_subagent_count ?? 0 },
  ];
  return (
    <SectionCard title={ui.drawer.operatorStatus}>
      <div className="flex items-center justify-between gap-3">
        <Badge tone={operatorStatusTone(String(status.status ?? "idle"))}>{String(status.status ?? "idle")}</Badge>
        {status.latest_activity_at ? <span className="text-xs text-[var(--muted)]">{String(status.latest_activity_at)}</span> : null}
      </div>
      {status.latest_activity ? (
        <div className="mt-2 min-w-0 break-words text-sm text-[var(--ink)] [overflow-wrap:anywhere]">
          <span className="text-[var(--muted)]">{ui.drawer.latestActivity}: </span>
          {String(status.latest_activity)}
        </div>
      ) : null}
      {diagnostics ? <RuntimePhaseDiagnosticsPanel diagnostics={diagnostics} ui={ui} /> : null}
      <div className="mt-3 grid grid-cols-2 gap-2">
        {summary.map((item) => (
          <div key={item.label} className="rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-2.5 py-2">
            <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">{item.label}</div>
            <div className="mt-1 text-sm font-semibold text-[var(--ink)]">{String(item.value)}</div>
          </div>
        ))}
      </div>
    </SectionCard>
  );
}

function RuntimePhaseDiagnosticsPanel({
  diagnostics,
  ui,
}: {
  diagnostics: RuntimePhaseDiagnostics;
  ui: ReturnType<typeof workspaceCopy>;
}) {
  const metrics = [
    { label: ui.drawer.runtimeTotalElapsed, value: diagnostics.totalElapsedMs },
    { label: ui.drawer.runtimeFirstModel, value: diagnostics.firstModelEventElapsedMs },
    { label: ui.drawer.runtimeFirstContent, value: diagnostics.firstContentDeltaElapsedMs },
    { label: ui.drawer.runtimeCompleted, value: diagnostics.completedElapsedMs },
  ];
  return (
    <div className="mt-3 rounded-[0.75rem] border border-[var(--line)] bg-[var(--panel-muted)] p-2.5">
      <div className="flex min-w-0 items-center justify-between gap-2">
        <div className="min-w-0 text-[12px] font-semibold text-[var(--ink)]">{ui.drawer.runtimeDiagnostics}</div>
        <Badge tone={operatorStatusTone(diagnostics.status)}>{diagnostics.status}</Badge>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-2">
        {metrics.map((metric) => (
          <div key={metric.label} className="min-w-0 rounded-lg border border-[var(--line)] bg-[var(--panel)] px-2 py-1.5">
            <HoverRevealText value={metric.label} className="text-[10px] uppercase tracking-[0.06em] text-[var(--muted)]" />
            <HoverRevealText
              value={formatRuntimeDuration(metric.value)}
              className="mt-0.5 font-mono text-[12px] font-semibold text-[var(--ink)]"
            />
          </div>
        ))}
      </div>
      {diagnostics.slowestPhase ? (
        <div className="mt-2 rounded-lg border border-[var(--line)] bg-[var(--panel)] px-2 py-1.5">
          <div className="flex min-w-0 items-center justify-between gap-2">
            <HoverRevealText value={ui.drawer.runtimeSlowestPhase} className="text-[11px] text-[var(--muted)]" />
            <div className="font-mono text-[11px] font-semibold text-[var(--ink)]">
              {formatRuntimeDuration(diagnostics.slowestPhase.durationSincePreviousMs)}
            </div>
          </div>
          <HoverRevealText value={diagnostics.slowestPhase.label} className="mt-1 text-[12px] font-medium text-[var(--ink)]" />
          <div className="mt-0.5 text-[11px] text-[var(--muted)]">
            {ui.drawer.runtimePhaseCount(diagnostics.phaseCount)}
          </div>
        </div>
      ) : null}
    </div>
  );
}

type SubagentDependencyEdge = {
  source_task_id: string;
  target_task_id: string;
  status: string;
  source_status: string | null;
};

type SubagentDependencyGraphModel = {
  tasks: SubagentTaskView[];
  edges: SubagentDependencyEdge[];
  readyTaskIds: string[];
  waitingTaskIds: string[];
  blockedTaskIds: string[];
  missingDependencyTaskIds: string[];
  nodes: SubagentDependencyNode[];
  layers: SubagentDependencyNode[][];
  criticalBlockers: SubagentDependencyBlocker[];
};

type SubagentDependencyNode = {
  task: SubagentTaskView;
  dependencyState: string;
  layer: number;
  waitingForTaskIds: string[];
  blockedByTaskIds: string[];
  missingDependencyTaskIds: string[];
  downstreamTaskIds: string[];
};

type SubagentDependencyBlocker = {
  taskId: string;
  status: string;
  affectedTaskIds: string[];
};

function SubagentDependencyGraphPanel({
  graph,
  ui,
}: {
  graph: SubagentDependencyGraphModel;
  ui: ReturnType<typeof workspaceCopy>;
}) {
  const taskById = new Map(graph.tasks.map((task) => [task.task_id, task]));
  return (
    <SectionCard title={ui.drawer.subagentGraph}>
      <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
        <SubagentGraphMetric label={ui.drawer.subagentGraphTotal} value={graph.tasks.length} tone="neutral" />
        <SubagentGraphMetric label={ui.drawer.readySubagents} value={graph.readyTaskIds.length} tone="success" />
        <SubagentGraphMetric label={ui.drawer.waitingSubagents} value={graph.waitingTaskIds.length} tone="accent" />
        <SubagentGraphMetric label={ui.drawer.blockedSubagents} value={graph.blockedTaskIds.length} tone="danger" />
      </div>
      <div className="mt-3 space-y-2">
        <div className="text-xs font-medium uppercase tracking-[0.06em] text-[var(--muted)]">{ui.drawer.subagentExecutionPath}</div>
        {graph.layers.map((layer, layerIndex) => (
          <div key={`subagent-layer-${layerIndex}`} className="rounded-[0.75rem] border border-[var(--line)] bg-[var(--panel-muted)] p-2.5">
            <div className="text-[11px] uppercase tracking-[0.06em] text-[var(--muted)]">L{layerIndex}</div>
            <div className="mt-2 flex flex-wrap gap-2">
              {layer.map((node) => (
                <div
                  key={node.task.task_id}
                  className="min-w-0 max-w-full rounded-full border border-[var(--line)] bg-[var(--panel)] px-2.5 py-1 text-[12px]"
                  title={subagentNodeTitle(node, ui)}
                >
                  <span className="font-medium text-[var(--ink)]">{shortRuntimeId(node.task.task_id)}</span>
                  <span className="ml-1 text-[var(--muted)]">{node.task.status}</span>
                  {node.downstreamTaskIds.length > 0 ? (
                    <span className="ml-1 text-[var(--muted)]">→{node.downstreamTaskIds.length}</span>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
      {graph.criticalBlockers.length > 0 ? (
        <div className="mt-3 space-y-2">
          <div className="text-xs font-medium uppercase tracking-[0.06em] text-[var(--muted)]">{ui.drawer.subagentCriticalBlockers}</div>
          {graph.criticalBlockers.map((blocker) => (
            <div
              key={`blocker-${blocker.taskId}`}
              className="rounded-[0.75rem] border border-[color-mix(in_srgb,var(--danger)_28%,var(--line))] bg-[color-mix(in_srgb,var(--danger)_7%,var(--panel))] p-2.5 text-xs"
            >
              <div className="flex min-w-0 items-center justify-between gap-2">
                <HoverRevealText
                  value={shortRuntimeId(blocker.taskId)}
                  className="font-medium text-[var(--ink)]"
                  tooltipClassName="max-w-[min(30rem,82vw)]"
                />
                <Badge tone="danger">{blocker.status}</Badge>
              </div>
              <div className="mt-1 text-[var(--muted)]">
                {ui.drawer.downstreamTasks}: {blocker.affectedTaskIds.map(shortRuntimeId).join(", ")}
              </div>
            </div>
          ))}
        </div>
      ) : null}
      <div className="mt-3 space-y-2">
        <div className="text-xs font-medium uppercase tracking-[0.06em] text-[var(--muted)]">{ui.drawer.dependencyEdges}</div>
        {graph.edges.length === 0 ? <EmptyPanelText text={ui.drawer.noDependencyEdges} /> : null}
        {graph.edges.map((edge) => {
          const source = taskById.get(edge.source_task_id);
          const target = taskById.get(edge.target_task_id);
          const targetDependencyState = target ? dependencyStateForTask(target, taskById) : "missing";
          const targetDependencyReason = target ? dependencyReasonForTask(target, taskById, ui) : "";
          return (
            <div key={`${edge.source_task_id}->${edge.target_task_id}`} className="rounded-[0.75rem] border border-[var(--line)] bg-[var(--panel-muted)] p-2.5">
              <div className="flex min-w-0 items-center justify-between gap-2">
                <div className="flex min-w-0 items-center text-[12px] font-medium text-[var(--ink)]">
                  <HoverRevealText
                    value={shortRuntimeId(edge.source_task_id)}
                    className="inline-block max-w-[8rem] align-bottom"
                    tooltipClassName="max-w-[min(30rem,82vw)]"
                  />
                  <span className="mx-1 text-[var(--muted)]">→</span>
                  <HoverRevealText
                    value={shortRuntimeId(edge.target_task_id)}
                    className="inline-block max-w-[8rem] align-bottom"
                    tooltipClassName="max-w-[min(30rem,82vw)]"
                  />
                </div>
                <Badge tone={dependencyBadgeTone(edge.status)}>{edge.status}</Badge>
              </div>
              <div className="mt-1 grid gap-1 text-[11px] leading-4 text-[var(--muted)]">
                <HoverRevealText value={`${ui.drawer.dependsOn}: ${source?.status ?? edge.source_status ?? "missing"}`} />
                <HoverRevealText value={`${ui.drawer.blockedBy}: ${targetDependencyState}`} />
                {targetDependencyReason ? (
                  <HoverRevealText value={targetDependencyReason} />
                ) : null}
              </div>
            </div>
          );
        })}
        {graph.missingDependencyTaskIds.length ? (
          <div className="rounded-[0.75rem] border border-[color-mix(in_srgb,var(--danger)_28%,var(--line))] bg-[color-mix(in_srgb,var(--danger)_8%,var(--panel))] p-2.5 text-xs text-[var(--danger)]">
            {ui.drawer.blockedBy}: {graph.missingDependencyTaskIds.map(shortRuntimeId).join(", ")}
          </div>
        ) : null}
      </div>
    </SectionCard>
  );
}

function SubagentToolEvidenceBlock({ tool }: { tool: SubagentToolEvidenceView }) {
  const title = tool.display_name || tool.name || "Tool";
  const argSummary = tool.args_keys.length ? tool.args_keys.join(", ") : "no args";
  const resultSummary = tool.has_result ? `${tool.result_char_count ?? 0} chars` : "no result";
  return (
    <div className="rounded-[0.75rem] border border-[var(--line)] bg-[var(--panel-muted)] p-2.5 text-xs">
      <div className="flex min-w-0 items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <WrenchIcon className="size-3.5 shrink-0 text-[var(--muted)]" />
          <HoverRevealText value={title} className="font-medium text-[var(--ink)]" tooltipClassName="max-w-[min(30rem,82vw)]" />
        </div>
        {tool.status ? <Badge tone={operatorStatusTone(tool.status)}>{tool.status}</Badge> : null}
      </div>
      <div className="mt-2 grid gap-1 text-[11px] leading-4 text-[var(--muted)]">
        <HoverRevealText value={`args: ${argSummary}`} tooltipClassName="max-w-[min(30rem,82vw)]" />
        <HoverRevealText value={`result: ${resultSummary}`} />
        {tool.duration_ms !== null && tool.duration_ms !== undefined ? (
          <HoverRevealText value={`duration: ${formatRuntimeDuration(tool.duration_ms)}`} />
        ) : null}
      </div>
    </div>
  );
}

function subagentNodeTitle(node: SubagentDependencyNode, ui: ReturnType<typeof workspaceCopy>) {
  const parts = [
    `${node.task.task_id} · ${node.task.status}`,
    `${ui.drawer.downstreamTasks}: ${node.downstreamTaskIds.length}`,
  ];
  const reason = dependencyReasonForNode(node, ui);
  if (reason) {
    parts.push(reason);
  }
  return parts.join("\n");
}

function SubagentGraphMetric({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "neutral" | "accent" | "success" | "warning" | "danger";
}) {
  return (
    <div className="rounded-[0.75rem] border border-[var(--line)] bg-[var(--panel-muted)] p-2">
      <div className="text-[11px] text-[var(--muted)]">{label}</div>
      <div className="mt-1 flex items-center justify-between gap-2">
        <span className="text-base font-semibold text-[var(--ink)]">{value}</span>
        <Badge tone={tone}>{value}</Badge>
      </div>
    </div>
  );
}

function operatorStatusTone(status: string): "neutral" | "accent" | "success" | "warning" | "danger" {
  if (status === "running") {
    return "accent";
  }
  if (status === "completed" || status === "ready") {
    return "success";
  }
  if (status === "awaiting_approval" || status === "awaiting_clarification") {
    return "warning";
  }
  if (status === "failed" || status === "timed_out" || status === "cancelled" || status === "interrupted") {
    return "danger";
  }
  return "neutral";
}

function timelineBadgeTone(item: RuntimeTimelineItem): "neutral" | "accent" | "success" | "warning" | "danger" {
  if (item.status === "error" || item.status === "failed" || item.status === "timed_out") {
    return "danger";
  }
  if (item.status === "running" || item.status === "queued") {
    return "accent";
  }
  if (item.status === "requested" || item.status === "needs_approval" || item.kind === "approval") {
    return "warning";
  }
  if (item.status === "completed" || item.status === "success") {
    return "success";
  }
  if (item.kind === "artifact") {
    return "accent";
  }
  return "neutral";
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function runtimeDurationValue(value: unknown): number | null {
  if (!isFiniteNumber(value)) {
    return null;
  }
  return Math.max(0, Math.round(value));
}

function formatRuntimeDuration(value: number | null) {
  if (value === null) {
    return "-";
  }
  if (value < 1000) {
    return `${value}ms`;
  }
  if (value < 60_000) {
    return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)}s`;
  }
  const minutes = Math.floor(value / 60_000);
  const seconds = Math.round((value % 60_000) / 1000);
  return `${minutes}m ${seconds}s`;
}

function useThreadActivityClock() {
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    const interval = window.setInterval(() => setNowMs(Date.now()), 60_000);
    return () => window.clearInterval(interval);
  }, []);
  return nowMs;
}

export function buildRuntimePhaseDiagnostics(timings: RuntimePhaseTimingsView | null | undefined): RuntimePhaseDiagnostics | null {
  if (!timings || !Array.isArray(timings.marks)) {
    return null;
  }
  const totalElapsedMs = runtimeDurationValue(timings.total_elapsed_ms);
  const firstModelEventElapsedMs = runtimeDurationValue(timings.first_model_event_elapsed_ms);
  const firstContentDeltaElapsedMs = runtimeDurationValue(timings.first_content_delta_elapsed_ms);
  const completedElapsedMs = runtimeDurationValue(timings.completed_elapsed_ms);
  const marks = timings.marks
    .map((mark) => {
      const elapsedMs = runtimeDurationValue(mark.elapsed_ms);
      const durationSincePreviousMs = runtimeDurationValue(mark.duration_since_previous_ms);
      if (elapsedMs === null || durationSincePreviousMs === null) {
        return null;
      }
      return {
        phase: String(mark.phase ?? ""),
        label: String(mark.label ?? mark.phase ?? "Runtime phase"),
        elapsedMs,
        durationSincePreviousMs,
      } satisfies RuntimePhaseMarkSummary;
    })
    .filter((mark): mark is RuntimePhaseMarkSummary => mark !== null);
  if (
    marks.length === 0 &&
    totalElapsedMs === null &&
    firstModelEventElapsedMs === null &&
    firstContentDeltaElapsedMs === null &&
    completedElapsedMs === null
  ) {
    return null;
  }
  const slowestPhase = marks.reduce<RuntimePhaseMarkSummary | null>((current, mark) => {
    if (!current || mark.durationSincePreviousMs > current.durationSincePreviousMs) {
      return mark;
    }
    return current;
  }, null);
  return {
    status: String(timings.status ?? "unknown"),
    totalElapsedMs,
    firstModelEventElapsedMs,
    firstContentDeltaElapsedMs,
    completedElapsedMs,
    phaseCount: marks.length,
    slowestPhase,
    marks,
  };
}

export function buildSubagentDependencyGraph(tasks: SubagentTaskView[]): SubagentDependencyGraphModel {
  const taskById = new Map(tasks.map((task) => [task.task_id, task]));
  const edges: SubagentDependencyEdge[] = [];
  const missingDependencyTaskIds = new Set<string>();
  const readyTaskIds: string[] = [];
  const waitingTaskIds: string[] = [];
  const blockedTaskIds: string[] = [];
  const downstreamByTaskId = new Map<string, Set<string>>();
  const directDownstreamByTaskId = new Map<string, Set<string>>();

  for (const task of tasks) {
    const edgeStatuses: string[] = [];
    for (const dependencyTaskId of task.depends_on_task_ids ?? []) {
      const dependency = taskById.get(dependencyTaskId);
      const status = dependencyEdgeStatus(dependency);
      edgeStatuses.push(status);
      if (status === "missing") {
        missingDependencyTaskIds.add(dependencyTaskId);
      }
      if (!directDownstreamByTaskId.has(dependencyTaskId)) {
        directDownstreamByTaskId.set(dependencyTaskId, new Set());
      }
      directDownstreamByTaskId.get(dependencyTaskId)!.add(task.task_id);
      edges.push({
        source_task_id: dependencyTaskId,
        target_task_id: task.task_id,
        status,
        source_status: dependency?.status ?? null,
      });
    }
    const dependencyState = task.dependency_state ?? dependencyStateFromEdgeStatuses(edgeStatuses);
    if (dependencyState === "blocked") {
      blockedTaskIds.push(task.task_id);
    } else if (dependencyState === "waiting") {
      waitingTaskIds.push(task.task_id);
    } else {
      readyTaskIds.push(task.task_id);
    }
  }

  for (const task of tasks) {
    downstreamByTaskId.set(task.task_id, collectDownstreamTaskIds(task.task_id, directDownstreamByTaskId));
  }

  const nodes = tasks.map((task) => {
    const edgeDetails = (task.depends_on_task_ids ?? []).map((dependencyTaskId) => {
      const dependency = taskById.get(dependencyTaskId);
      return {
        taskId: dependencyTaskId,
        status: dependencyEdgeStatus(dependency),
      };
    });
    return {
      task,
      dependencyState: task.dependency_state ?? dependencyStateFromEdgeStatuses(edgeDetails.map((item) => item.status)),
      layer: dependencyLayerForTask(task, taskById, new Map()),
      waitingForTaskIds: edgeDetails.filter((item) => item.status === "waiting").map((item) => item.taskId),
      blockedByTaskIds: edgeDetails.filter((item) => item.status === "terminal_unsatisfied").map((item) => item.taskId),
      missingDependencyTaskIds: edgeDetails.filter((item) => item.status === "missing").map((item) => item.taskId),
      downstreamTaskIds: Array.from(downstreamByTaskId.get(task.task_id) ?? []).sort(),
    } satisfies SubagentDependencyNode;
  });

  const layers = Array.from(
    nodes.reduce((acc, node) => {
      const blockedOffset = node.dependencyState === "blocked" ? 1 : 0;
      const layer = Math.max(node.layer + blockedOffset, 0);
      if (!acc.has(layer)) {
        acc.set(layer, []);
      }
      acc.get(layer)!.push(node);
      return acc;
    }, new Map<number, SubagentDependencyNode[]>()).entries(),
  )
    .sort(([left], [right]) => left - right)
    .map(([, layerNodes]) => layerNodes.sort((left, right) => left.task.task_id.localeCompare(right.task.task_id)));

  const criticalBlockers = nodes
    .filter((node) => node.dependencyState === "blocked" || ["failed", "cancelled", "timed_out", "interrupted", "failed_recovery"].includes(node.task.status))
    .map((node) => ({
      taskId: node.task.task_id,
      status: node.task.status,
      affectedTaskIds: node.downstreamTaskIds.filter((taskId) => taskId !== node.task.task_id),
    }))
    .filter((item) => item.affectedTaskIds.length > 0)
    .sort((left, right) => right.affectedTaskIds.length - left.affectedTaskIds.length || left.taskId.localeCompare(right.taskId))
    .slice(0, 5);

  return {
    tasks,
    edges,
    readyTaskIds,
    waitingTaskIds,
    blockedTaskIds,
    missingDependencyTaskIds: Array.from(missingDependencyTaskIds).sort(),
    nodes,
    layers,
    criticalBlockers,
  };
}

function collectDownstreamTaskIds(taskId: string, directDownstreamByTaskId: Map<string, Set<string>>, seen = new Set<string>()) {
  const downstream = directDownstreamByTaskId.get(taskId);
  if (!downstream) {
    return new Set<string>();
  }
  const collected = new Set<string>();
  for (const childTaskId of downstream) {
    if (seen.has(childTaskId)) {
      continue;
    }
    seen.add(childTaskId);
    collected.add(childTaskId);
    for (const nestedTaskId of collectDownstreamTaskIds(childTaskId, directDownstreamByTaskId, seen)) {
      collected.add(nestedTaskId);
    }
  }
  return collected;
}

function dependencyLayerForTask(
  task: SubagentTaskView,
  taskById: Map<string, SubagentTaskView>,
  memo: Map<string, number>,
  visiting = new Set<string>(),
): number {
  const cached = memo.get(task.task_id);
  if (cached !== undefined) {
    return cached;
  }
  if (visiting.has(task.task_id)) {
    return 0;
  }
  visiting.add(task.task_id);
  const dependencyLayers = (task.depends_on_task_ids ?? [])
    .map((dependencyTaskId) => {
      const dependency = taskById.get(dependencyTaskId);
      return dependency ? dependencyLayerForTask(dependency, taskById, memo, visiting) : 0;
    });
  visiting.delete(task.task_id);
  const layer = dependencyLayers.length > 0 ? Math.max(...dependencyLayers) + 1 : 0;
  memo.set(task.task_id, layer);
  return layer;
}

function dependencyReasonForNode(node: SubagentDependencyNode, ui: ReturnType<typeof workspaceCopy>) {
  const pieces: string[] = [];
  if (node.waitingForTaskIds.length > 0) {
    pieces.push(`${ui.drawer.waitingFor}: ${node.waitingForTaskIds.map(shortRuntimeId).join(", ")}`);
  }
  if (node.blockedByTaskIds.length > 0) {
    pieces.push(`${ui.drawer.blockedBy}: ${node.blockedByTaskIds.map(shortRuntimeId).join(", ")}`);
  }
  if (node.missingDependencyTaskIds.length > 0) {
    pieces.push(`${ui.drawer.missingDependencies}: ${node.missingDependencyTaskIds.map(shortRuntimeId).join(", ")}`);
  }
  return pieces.join(" · ");
}

function dependencyReasonForTask(
  task: SubagentTaskView,
  taskById: Map<string, SubagentTaskView>,
  ui: ReturnType<typeof workspaceCopy>,
) {
  const edgeDetails = (task.depends_on_task_ids ?? []).map((dependencyTaskId) => {
    const dependency = taskById.get(dependencyTaskId);
    return {
      taskId: dependencyTaskId,
      status: dependencyEdgeStatus(dependency),
    };
  });
  return dependencyReasonForNode(
    {
      task,
      dependencyState: task.dependency_state ?? dependencyStateFromEdgeStatuses(edgeDetails.map((item) => item.status)),
      layer: 0,
      waitingForTaskIds: edgeDetails.filter((item) => item.status === "waiting").map((item) => item.taskId),
      blockedByTaskIds: edgeDetails.filter((item) => item.status === "terminal_unsatisfied").map((item) => item.taskId),
      missingDependencyTaskIds: edgeDetails.filter((item) => item.status === "missing").map((item) => item.taskId),
      downstreamTaskIds: [],
    },
    ui,
  );
}

function dependencyStateForTask(task: SubagentTaskView, taskById: Map<string, SubagentTaskView>) {
  if (task.dependency_state) {
    return task.dependency_state;
  }
  const statuses = (task.depends_on_task_ids ?? []).map((dependencyTaskId) => dependencyEdgeStatus(taskById.get(dependencyTaskId)));
  return dependencyStateFromEdgeStatuses(statuses);
}

function dependencyEdgeStatus(task: SubagentTaskView | undefined) {
  if (!task) {
    return "missing";
  }
  if (task.status === "completed") {
    return "satisfied";
  }
  if (["failed", "cancelled", "timed_out", "interrupted", "failed_recovery"].includes(task.status)) {
    return "terminal_unsatisfied";
  }
  return "waiting";
}

function dependencyStateFromEdgeStatuses(statuses: string[]) {
  if (statuses.length === 0) {
    return "ready";
  }
  if (statuses.every((status) => status === "satisfied")) {
    return "ready";
  }
  if (statuses.some((status) => status === "missing" || status === "terminal_unsatisfied")) {
    return "blocked";
  }
  return "waiting";
}

function dependencyBadgeTone(status: string): "neutral" | "accent" | "success" | "warning" | "danger" {
  if (status === "satisfied") {
    return "success";
  }
  if (status === "waiting") {
    return "accent";
  }
  if (status === "missing" || status === "terminal_unsatisfied") {
    return "danger";
  }
  return "neutral";
}

function shortRuntimeId(value: string) {
  if (value.length <= 18) {
    return value;
  }
  return `${value.slice(0, 10)}…${value.slice(-6)}`;
}

function toolEventKey(data: Record<string, unknown>, fallbackIndex: number) {
  if (data.activity_key) {
    return String(data.activity_key);
  }
  if (data.tool_call_id) {
    return String(data.tool_call_id);
  }
  if (data.message_id && data.name) {
    return `${String(data.message_id)}:${String(data.name)}`;
  }
  if (data.name) {
    return `tool:${String(data.name)}:${fallbackIndex}`;
  }
  return `tool-${fallbackIndex}`;
}

function findRecentToolKey(
  activity: Map<string, ToolActivityView>,
  data: Record<string, unknown>,
  fallbackIndex: number,
) {
  const directKey = toolEventKey(data, fallbackIndex);
  if (activity.has(directKey)) {
    return directKey;
  }
  if (data.name) {
    const normalizedName = String(data.name);
    const entries = Array.from(activity.entries()).reverse();
    const match = entries.find(([, item]) => item.name === normalizedName && item.completed_at === null);
    if (match) {
      return match[0];
    }
  }
  return directKey;
}

export function buildRecentTools(existing: ToolActivityView[], events: RunStreamEvent[]): ToolActivityView[] {
  const activity = new Map<string, ToolActivityView>();
  const order: string[] = [];

  for (const item of existing) {
    if (!isRenderableToolActivity(item)) {
      continue;
    }
    const id = item.tool_call_id ?? `${item.name}-${item.started_at ?? item.completed_at ?? "existing"}`;
    activity.set(id, item);
    order.push(id);
  }

  for (const event of events) {
    if (event.event === "step_started" || event.event === "step_updated") {
      const rawStep = event.data.step;
      if (!rawStep || typeof rawStep !== "object") {
        continue;
      }
      const step = rawStep as Record<string, unknown>;
      if (step.type !== "call") {
        continue;
      }
      if (!isChatVisibleRunStep(step)) {
        continue;
      }
      const activityKey = String(step.step_id ?? step.tool_call_id ?? `tool-${order.length}`);
      if (!activity.has(activityKey)) {
        order.push(activityKey);
      }
      activity.set(activityKey, {
        tool_call_id: step.tool_call_id ? String(step.tool_call_id) : activityKey,
        message_id: step.message_id ? String(step.message_id) : null,
        name: step.tool_name ? String(step.tool_name) : null,
        display_name: null,
        source_kind: null,
        source_id: null,
        capability_group: null,
        tool_execution_mode: null,
        status: step.status === "success" ? "completed" : step.status === "error" ? "error" : "running",
        args: toolArgsFromStep(step),
        result_text: step.payload ? String(step.payload) : null,
        started_at: step.started_at ? String(step.started_at) : null,
        completed_at: step.completed_at ? String(step.completed_at) : null,
        duration_ms: typeof step.duration_ms === "number" ? step.duration_ms : null,
      });
      continue;
    }

    if (event.event === "step_delta") {
      const stepId = event.data.step_id ? String(event.data.step_id) : null;
      if (!stepId) {
        continue;
      }
      const current = activity.get(stepId);
      if (!current) {
        continue;
      }
      activity.set(stepId, {
        ...current,
        status: current.status ?? "running",
        result_text: `${current.result_text ?? ""}${String(event.data.payload_delta ?? "")}`,
      });
      continue;
    }

    if (event.event === "tool_call_started") {
      if (!hasMeaningfulToolEventData(event.data)) {
        continue;
      }
      const toolCallId = toolEventKey(event.data, order.length);
      if (!activity.has(toolCallId)) {
        order.push(toolCallId);
      }
      activity.set(toolCallId, {
        tool_call_id: event.data.tool_call_id ? String(event.data.tool_call_id) : null,
        message_id: event.data.message_id ? String(event.data.message_id) : null,
        name: event.data.name ? String(event.data.name) : null,
        display_name: event.data.display_name ? String(event.data.display_name) : null,
        source_kind: event.data.source_kind ? String(event.data.source_kind) : null,
        source_id: event.data.source_id ? String(event.data.source_id) : null,
        capability_group: event.data.capability_group ? String(event.data.capability_group) : null,
        tool_execution_mode: event.data.tool_execution_mode ? String(event.data.tool_execution_mode) : null,
        status: "running",
        args: typeof event.data.args === "object" && event.data.args ? (event.data.args as Record<string, unknown>) : {},
        result_text: null,
        started_at: event.data.started_at ? String(event.data.started_at) : new Date().toISOString(),
        completed_at: event.data.completed_at ? String(event.data.completed_at) : null,
        duration_ms: typeof event.data.duration_ms === "number" ? event.data.duration_ms : null,
      });
    }

    if (event.event === "tool_call_progress") {
      const toolCallId = findRecentToolKey(activity, event.data, order.length);
      const current =
        activity.get(toolCallId) ??
        ({
          tool_call_id: event.data.tool_call_id ? String(event.data.tool_call_id) : null,
          message_id: event.data.message_id ? String(event.data.message_id) : null,
          name: event.data.name ? String(event.data.name) : null,
          display_name: event.data.display_name ? String(event.data.display_name) : null,
          source_kind: event.data.source_kind ? String(event.data.source_kind) : null,
          source_id: event.data.source_id ? String(event.data.source_id) : null,
          capability_group: event.data.capability_group ? String(event.data.capability_group) : null,
          tool_execution_mode: event.data.tool_execution_mode ? String(event.data.tool_execution_mode) : null,
          status: "running",
          args: typeof event.data.args === "object" && event.data.args ? (event.data.args as Record<string, unknown>) : {},
          result_text: null,
          started_at: event.data.started_at ? String(event.data.started_at) : new Date().toISOString(),
          completed_at: null,
          duration_ms: null,
        } satisfies ToolActivityView);
      if (!current.name && !current.display_name && !hasMeaningfulToolEventData(event.data)) {
        continue;
      }
      activity.set(toolCallId, {
        ...current,
        status: String(event.data.status ?? current.status ?? "running"),
        result_text: `${current.result_text ?? ""}${String(event.data.delta ?? "")}`,
      });
      if (!order.includes(toolCallId)) {
        order.push(toolCallId);
      }
    }

    if (event.event === "tool_call_completed") {
      const toolCallId = findRecentToolKey(activity, event.data, order.length);
      const current =
        activity.get(toolCallId) ??
        ({
          tool_call_id: event.data.tool_call_id ? String(event.data.tool_call_id) : null,
          message_id: event.data.message_id ? String(event.data.message_id) : null,
          name: event.data.name ? String(event.data.name) : null,
          display_name: event.data.display_name ? String(event.data.display_name) : null,
          source_kind: event.data.source_kind ? String(event.data.source_kind) : null,
          source_id: event.data.source_id ? String(event.data.source_id) : null,
          capability_group: event.data.capability_group ? String(event.data.capability_group) : null,
          tool_execution_mode: event.data.tool_execution_mode ? String(event.data.tool_execution_mode) : null,
          status: "completed",
          args: {},
          result_text: null,
          started_at: null,
          completed_at: null,
          duration_ms: null,
        } satisfies ToolActivityView);
      if (!current.name && !current.display_name && !hasMeaningfulToolEventData(event.data)) {
        continue;
      }

      const completedAt = new Date().toISOString();
      activity.set(toolCallId, {
        ...current,
        name: event.data.name ? String(event.data.name) : current.name,
        display_name: event.data.display_name ? String(event.data.display_name) : current.display_name,
        source_kind: event.data.source_kind ? String(event.data.source_kind) : current.source_kind,
        source_id: event.data.source_id ? String(event.data.source_id) : current.source_id,
        capability_group: event.data.capability_group ? String(event.data.capability_group) : current.capability_group,
        tool_execution_mode: event.data.tool_execution_mode ? String(event.data.tool_execution_mode) : current.tool_execution_mode,
        status: String(event.data.status ?? "completed"),
        result_text: event.data.result_text ? String(event.data.result_text) : current.result_text,
        completed_at: event.data.completed_at ? String(event.data.completed_at) : completedAt,
        duration_ms:
          typeof event.data.duration_ms === "number"
            ? event.data.duration_ms
            : current.started_at
              ? Date.parse(completedAt) - Date.parse(current.started_at)
              : current.duration_ms,
      });
      if (!order.includes(toolCallId)) {
        order.push(toolCallId);
      }
    }
  }

  return order
    .map((id) => activity.get(id))
    .filter((item): item is ToolActivityView => {
      if (!item) {
        return false;
      }
      return isRenderableToolActivity(item);
    })
    .slice(-5)
    .reverse();
}

function toolArgsFromStep(step: Record<string, unknown>) {
  const action = typeof step.action === "string" ? step.action : "";
  const language = typeof step.language === "string" ? step.language : "";
  if (!action) {
    return {};
  }
  if (language === "json") {
    try {
      const parsed = JSON.parse(action);
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : { input: action };
    } catch {
      return { input: action };
    }
  }
  return { command: action };
}

function hasMeaningfulToolEventData(data: Record<string, unknown>) {
  const name = typeof data.name === "string" ? data.name.trim() : "";
  const displayName = typeof data.display_name === "string" ? data.display_name.trim() : "";
  const args = typeof data.args === "object" && data.args ? Object.keys(data.args as Record<string, unknown>).length > 0 : false;
  const resultText = typeof data.result_text === "string" ? data.result_text.trim() : "";
  return Boolean(name || displayName || args || resultText);
}

function toolActivitySignature(tool: ToolActivityView): string {
  return JSON.stringify([
    tool.tool_call_id ?? null,
    tool.message_id ?? null,
    tool.name ?? null,
    tool.display_name ?? null,
    tool.source_kind ?? null,
    tool.source_id ?? null,
    tool.capability_group ?? null,
    tool.tool_execution_mode ?? null,
    tool.status ?? null,
    tool.started_at ?? null,
    tool.completed_at ?? null,
    tool.duration_ms ?? null,
    tool.args ?? {},
    tool.result_text ?? null,
  ]);
}

function streamEventSignature(event: RunStreamEvent, index: number): string {
  return JSON.stringify([
    index,
    event.event,
    event.data ?? {},
    (event as RunStreamEvent & { receivedAt?: number }).receivedAt ?? null,
  ]);
}

function isChatVisibleRunStep(step: Record<string, unknown>) {
  const visibility = typeof step.visibility === "string" ? step.visibility : "chat";
  return visibility === "chat";
}

const HIDDEN_RUNTIME_TOOL_NAMES = new Set([
  "delegate_batch",
  "delegate_cancel",
  "delegate_status",
  "delegated_task",
  "memory",
  "memory_trace",
  "session_search",
  "subagent",
]);
const HIDDEN_RUNTIME_TOOL_GROUPS = new Set(["memory"]);

function isHiddenRuntimeToolActivity(tool: Pick<ToolActivityView, "name" | "capability_group">) {
  const name = typeof tool.name === "string" ? tool.name.trim() : "";
  const capabilityGroup = typeof tool.capability_group === "string" ? tool.capability_group.trim() : "";
  return HIDDEN_RUNTIME_TOOL_NAMES.has(name) || HIDDEN_RUNTIME_TOOL_GROUPS.has(capabilityGroup);
}

function isRenderableToolActivity(tool: ToolActivityView) {
  if (isHiddenRuntimeToolActivity(tool)) {
    return false;
  }
  const name = typeof tool.name === "string" ? tool.name.trim() : "";
  const displayName = typeof tool.display_name === "string" ? tool.display_name.trim() : "";
  const args = tool.args ? Object.keys(tool.args).length > 0 : false;
  const resultText = typeof tool.result_text === "string" ? tool.result_text.trim() : "";
  return Boolean(name || displayName || args || resultText);
}

export function buildTimelineItems(
  operatorStatus: any,
  toolActivity: ToolActivityView[],
  approvalEvents: Array<{
    request_id?: string | null;
    decision?: string;
    reason?: string | null;
    status?: string;
    created_at?: string;
    resolved_at?: string | null;
  }>,
  subagentTasks: SubagentTaskView[],
  processSessions: Array<{ session_id: string; status: string; command: string; started_at?: string | null; completed_at?: string | null }>,
  events: RunStreamEvent[],
) {
  const items: RuntimeTimelineItem[] = [];
  const operatorTimeline = Array.isArray(operatorStatus?.timeline) ? operatorStatus.timeline : [];
  for (const item of operatorTimeline) {
    if (!item || typeof item !== "object") {
      continue;
    }
    const payload = item as Record<string, unknown>;
    if (payload.hidden === true) {
      continue;
    }
    items.push({
      id: String(payload.item_id ?? `operator-${items.length}`),
      label: String(payload.title ?? payload.kind ?? "Event"),
      detail: String(payload.detail ?? ""),
      timestamp: typeof payload.timestamp === "string" ? payload.timestamp : null,
      kind: String(payload.kind ?? "runtime"),
      status: String(payload.status ?? "unknown"),
      durationMs: typeof payload.duration_ms === "number" ? payload.duration_ms : null,
    });
  }

  if (items.length > 0 && events.length === 0) {
    return compactTimelineItems(items);
  }

  for (const tool of toolActivity) {
    if (!isRenderableToolActivity(tool)) {
      continue;
    }
    items.push({
      id: tool.tool_call_id ?? `${tool.name}-${tool.started_at ?? tool.completed_at ?? "tool"}`,
      label: String(tool.display_name ?? tool.name ?? "Tool"),
      detail: tool.result_text ?? JSON.stringify(tool.args ?? {}),
      timestamp: tool.completed_at ?? tool.started_at ?? null,
      kind: "tool",
      status: tool.status ?? "unknown",
      durationMs: tool.duration_ms ?? null,
    });
  }

  for (const approval of approvalEvents) {
    items.push({
      id: approval.request_id ?? `approval-${approval.created_at ?? items.length}`,
      label: String(approval.decision ?? "approval"),
      detail: approval.reason ?? "approval event",
      timestamp: approval.resolved_at ?? approval.created_at ?? null,
      kind: "approval",
      status: approval.status ?? "requested",
      durationMs: null,
    });
  }

  for (const task of subagentTasks) {
    items.push({
      id: task.task_id,
      label: `Subagent ${task.task_id}`,
      detail: task.summary ?? task.status,
      timestamp: task.completed_at ?? task.started_at ?? null,
      kind: "subagent",
      status: task.status,
      durationMs: null,
    });
  }

  for (const session of processSessions) {
    items.push({
      id: session.session_id,
      label: `Process ${session.session_id}`,
      detail: session.command,
      timestamp: session.completed_at ?? session.started_at ?? null,
      kind: "process",
      status: session.status,
      durationMs: null,
    });
  }

  for (const event of events) {
    const item = timelineItemFromStreamEvent(event, items.length);
    if (item) {
      items.push(item);
    }
  }

  return compactTimelineItems(items);
}

function timelineItemFromStreamEvent(event: RunStreamEvent, index: number): RuntimeTimelineItem | null {
  if (
    event.event === "document_ingestion_started" ||
    event.event === "document_ingestion_completed" ||
    event.event === "document_export_started" ||
    event.event === "document_export_completed" ||
    event.event === "cleanup_scratch" ||
    event.event === "run_warning" ||
    event.event === "artifact_registered"
  ) {
    return {
      id: `${event.event}-${index}`,
      label: String(event.event),
      detail: String(event.data.output_path ?? event.data.path ?? event.data.message ?? event.data.label ?? event.data.provider ?? event.event),
      timestamp: null,
      kind: "document",
      status: event.event.endsWith("_completed") ? "completed" : event.event.endsWith("_started") ? "running" : "event",
      durationMs: null,
    };
  }
  if (event.event === "artifact_emitted") {
    return {
      id: `artifact-${String(event.data.artifact_url ?? event.data.label ?? index)}`,
      label: "Artifact emitted",
      detail: String(event.data.label ?? event.data.virtual_path ?? "artifact"),
      timestamp: null,
      kind: "artifact",
      status: "completed",
      durationMs: null,
    };
  }
  if (event.event === "subagent_submitted") {
    return {
      id: String(event.data.subagent_job_id ?? event.data.task_id ?? `subagent-${index}`),
      label: `Subagent ${String(event.data.subagent_job_id ?? event.data.task_id ?? "")}`,
      detail: String(event.data.status ?? "queued"),
      timestamp: null,
      kind: "subagent",
      status: String(event.data.status ?? "queued"),
      durationMs: null,
    };
  }
  if (
    event.event === "subagent_started" ||
    event.event === "subagent_completed" ||
    event.event === "subagent_failed" ||
    event.event === "subagent_cancelled" ||
    event.event === "subagent_timed_out" ||
    event.event === "subagent_interrupted"
  ) {
    return {
      id: String(event.data.subagent_job_id ?? `subagent-${index}`),
      label: `Subagent ${String(event.data.subagent_job_id ?? "")}`,
      detail: String(event.data.summary ?? event.data.error ?? event.data.status ?? event.event),
      timestamp: typeof event.data.timestamp === "string" ? event.data.timestamp : null,
      kind: "subagent",
      status: statusFromRuntimeEvent(event.event),
      durationMs: null,
    };
  }
  if (event.event === "process_started" || event.event === "process_completed") {
    return {
      id: String(event.data.session_id ?? `process-${index}`),
      label: `Process ${String(event.data.session_id ?? "")}`,
      detail: String(event.data.command ?? event.data.status ?? "process event"),
      timestamp: null,
      kind: "process",
      status: event.event === "process_completed" ? String(event.data.status ?? "completed") : "running",
      durationMs: typeof event.data.duration_ms === "number" ? event.data.duration_ms : null,
    };
  }
  return null;
}

function compactTimelineItems(items: RuntimeTimelineItem[]) {
  const byId = new Map<string, RuntimeTimelineItem>();
  for (const item of items) {
    byId.set(`${item.kind}:${item.id}`, item);
  }
  return Array.from(byId.values())
    .sort((a, b) => String(b.timestamp ?? "").localeCompare(String(a.timestamp ?? "")))
    .slice(0, 20);
}

function statusFromRuntimeEvent(event: string) {
  if (event.endsWith("_started") || event.endsWith("_submitted")) {
    return "running";
  }
  if (event.endsWith("_completed")) {
    return "completed";
  }
  if (event.endsWith("_failed")) {
    return "failed";
  }
  if (event.endsWith("_cancelled")) {
    return "cancelled";
  }
  if (event.endsWith("_timed_out")) {
    return "timed_out";
  }
  if (event.endsWith("_interrupted")) {
    return "interrupted";
  }
  return "event";
}

function approvalIdentity(approval: ApprovalView | null) {
  if (!approval) {
    return null;
  }
  return approval.request_id ?? approval.reason ?? approval.decision;
}

export function buildOptimisticUserMessage(
  content: string,
  artifactRefs: ArtifactRefView[] = [],
  clientMessageId?: string | null,
): MessageView {
  const attachmentSignature = artifactRefs
    .map((artifact) => artifact.virtual_path ?? artifact.artifact_url ?? artifact.label ?? artifact.kind)
    .filter(Boolean)
    .join("|");
  const messageKey = attachmentSignature ? `${content}:${attachmentSignature}` : content;
  const stableId = clientMessageId || `optimistic-user:${stableClientMessageKey(messageKey)}`;
  return {
    message_id: stableId,
    client_message_id: clientMessageId ?? null,
    role: "human",
    content,
    steps: [],
    content_blocks: [],
    reasoning: null,
    tool_calls: [],
    tool_call_id: null,
    name: null,
    status: "pending",
    artifact_refs: artifactRefs,
    approval: null,
  };
}

function createClientMessageId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `client:${crypto.randomUUID()}`;
  }
  return `client:${Date.now().toString(36)}:${Math.random().toString(36).slice(2, 10)}`;
}

function queuedFollowupClientMessageId(queueId: string) {
  return `queued:${queueId}`;
}

function stableClientMessageKey(value: string) {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = ((hash << 5) - hash + value.charCodeAt(index)) | 0;
  }
  return `${Math.abs(hash).toString(36)}:${value.length}`;
}

function uploadItemToArtifactRef(file: UploadItemView): ArtifactRefView {
  return {
    kind: "upload",
    label: file.filename,
    artifact_url: file.artifact_url,
    virtual_path: file.virtual_path,
    source_scope: file.source_scope,
    internal: file.internal,
    extension: file.extension,
    markdown_file: file.markdown_file,
    markdown_virtual_path: file.markdown_virtual_path,
    markdown_artifact_url: file.markdown_artifact_url,
    companions: file.companions,
    extraction: file.extraction,
    outline: file.outline,
    outline_preview: file.outline_preview,
    converter_used: file.converter_used,
    ocr_used: file.ocr_used,
    conversion_error: file.conversion_error,
  };
}

function mergeSelectedFiles(existing: File[], nextFiles: File[]) {
  const seen = new Set(existing.map((file) => `${file.name}:${file.size}:${file.lastModified}`));
  const merged = [...existing];
  for (const file of nextFiles) {
    const key = `${file.name}:${file.size}:${file.lastModified}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    merged.push(file);
  }
  return merged;
}

function defaultAttachmentPrompt(locale: Locale) {
  return locale === "zh-CN" ? "请处理我刚上传的附件。" : "Please process the files I just uploaded.";
}

function queuedFollowupAttachments(item: QueuedFollowUpView): ComposerQueuedAttachment[] {
  if (item.uploaded_file_refs.length > 0) {
    return item.uploaded_file_refs.map((artifactRef, index) => ({
      filename: item.uploaded_filenames[index] ?? artifactRef.label,
      artifactRef,
    }));
  }
  return item.uploaded_filenames.map((filename) => ({
    filename,
    artifactRef: {
      kind: "upload",
      label: filename,
      artifact_url: null,
      virtual_path: null,
      source_scope: "upload",
      internal: false,
      extension: filename.includes(".") ? filename.split(".").pop() ?? null : null,
      companions: [],
      extraction: null,
      outline: [],
      outline_preview: [],
      converter_used: null,
      ocr_used: false,
      conversion_error: null,
    },
  }));
}

function mergeQueuedAttachments(
  existing: ComposerQueuedAttachment[],
  nextFiles: ComposerQueuedAttachment[],
): ComposerQueuedAttachment[] {
  const seen = new Set(existing.map((file) => `${file.filename}:${file.artifactRef.virtual_path ?? file.artifactRef.artifact_url ?? ""}`));
  const merged = [...existing];
  for (const file of nextFiles) {
    const key = `${file.filename}:${file.artifactRef.virtual_path ?? file.artifactRef.artifact_url ?? ""}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    merged.push(file);
  }
  return merged;
}

function messageMatchesOptimisticTurn(message: MessageView, optimisticTurn: OptimisticTurn) {
  if (message.client_message_id && message.client_message_id === optimisticTurn.clientMessageId) {
    return true;
  }
  if (optimisticTurn.clientMessageId) {
    return false;
  }
  return messagesEquivalentForOptimistic(message.content, optimisticTurn.message);
}

function messagesEquivalentForOptimistic(left: string, right: string) {
  const normalizedLeft = normalizeOptimisticMessageText(left);
  const normalizedRight = normalizeOptimisticMessageText(right);
  return Boolean(normalizedLeft) && normalizedLeft === normalizedRight;
}

function normalizeOptimisticMessageText(value: string) {
  return value
    .trim()
    .replace(/\s+/g, " ")
    .replace(/\\/g, "/")
    .replace(/\b([A-Za-z]):\/([^"'`“”‘’\s，。；、,;）)]*)/g, (_match, drive: string, rest: string) => {
      return `drive:${drive.toLowerCase()}:/${rest}`;
    })
    .replace(/\/mnt\/user-data\/workspace\/_host\/([A-Za-z])_drive\/([^"'`“”‘’\s，。；、,;）)]*)/g, (_match, drive: string, rest: string) => {
      return `drive:${drive.toLowerCase()}:/${rest}`;
    });
}

function sectionLabel(section: DrawerSection, locale: Locale) {
  const ui = workspaceCopy(locale);
  return ui.drawerSections[section];
}

function syncWorkspaceUrl(threadId: string | null, opsState: OpsUrlState, replace = false) {
  if (typeof window === "undefined") {
    return;
  }
  const pathname = threadId ? `/threads/${encodeURIComponent(threadId)}` : "/";
  const search = applyOpsStateToSearch(window.location.search, opsState);
  const target = `${pathname}${search}`;
  const current = `${window.location.pathname}${window.location.search}`;
  if (current === target) {
    return;
  }
  if (replace) {
    window.history.replaceState(null, "", target);
  } else {
    window.history.pushState(null, "", target);
  }
}

function useStoredBoolean(key: string, initialValue: boolean) {
  const [value, setValue] = useState<boolean>(() => {
    if (typeof window === "undefined") {
      return initialValue;
    }
    const raw = window.localStorage.getItem(key);
    if (raw === "true") {
      return true;
    }
    if (raw === "false") {
      return false;
    }
    return initialValue;
  });

  useEffect(() => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(key, String(value));
    }
  }, [key, value]);

  return [value, setValue] as const;
}

function useStoredStringArray(key: string, initialValue: string[]) {
  const [value, setValue] = useState<string[]>(() => {
    if (typeof window === "undefined") {
      return initialValue;
    }
    try {
      const parsed = JSON.parse(window.localStorage.getItem(key) ?? "null");
      if (Array.isArray(parsed)) {
        return parsed.filter((item): item is string => typeof item === "string");
      }
    } catch {
      return initialValue;
    }
    return initialValue;
  });

  useEffect(() => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(key, JSON.stringify(value));
    }
  }, [key, value]);

  return [value, setValue] as const;
}

function useStoredRecord(key: string, initialValue: Record<string, string>) {
  const [value, setValue] = useState<Record<string, string>>(() => {
    if (typeof window === "undefined") {
      return initialValue;
    }
    try {
      const parsed = JSON.parse(window.localStorage.getItem(key) ?? "null");
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return Object.fromEntries(
          Object.entries(parsed).filter((entry): entry is [string, string] => typeof entry[0] === "string" && typeof entry[1] === "string"),
        );
      }
    } catch {
      return initialValue;
    }
    return initialValue;
  });

  useEffect(() => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(key, JSON.stringify(value));
    }
  }, [key, value]);

  return [value, setValue] as const;
}
