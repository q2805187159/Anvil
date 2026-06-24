import type { Locale } from "@/src/core/i18n";

export type OpsSurface = "overview" | "basics" | "models" | "tools" | "skills" | "memory" | "selfUpgrade" | "mcp" | "plugins" | "scheduled";

export type OpsUrlState = {
  open: boolean;
  surface: OpsSurface;
  item: string | null;
  action: string | null;
  server: string | null;
};

export type OpsCopy = {
  title: string;
  description: string;
  open: string;
  close: string;
  surfaces: Record<OpsSurface, string>;
  overview: {
    title: string;
    description: string;
    globalStatus: string;
    currentThread: string;
    configSnapshot: string;
    noThread: string;
    runtimeDrawerNote: string;
    basics: string;
    basicsDescription: string;
    models: string;
    modelsDescription: string;
    tools: string;
    toolsDescription: string;
    skills: string;
    skillsDescription: string;
    memory: string;
    memoryDescription: string;
    selfUpgrade: string;
    selfUpgradeDescription: string;
    mcp: string;
    mcpDescription: string;
    plugins: string;
    pluginsDescription: string;
    scheduled: string;
    scheduledDescription: string;
    openSurface: string;
    enabled: string;
    ready: string;
    total: string;
  };
  summary: {
    health: string;
    runtime: string;
    runtimeSummary: string;
    toolsVisible: string;
    toolsDeferred: string;
    enabledSkills: string;
    connectedMcp: string;
    plugins: string;
    openConsole: string;
    inspectThreadContext: string;
  };
  filters: {
    search: string;
    sourceKind: string;
    capabilityGroup: string;
    all: string;
  };
  toolPanel: {
    title: string;
    catalogPath: string;
    approval: string;
    dependencies: string;
    provenance: string;
    health: string;
    resources: string;
    prompts: string;
    noResults: string;
    noDetail: string;
  };
    models: {
      title: string;
      noResults: string;
      add: string;
      delete: string;
      edit: string;
      formTitle: string;
      editFormTitle: string;
      providerPreset: string;
      name: string;
      url: string;
      apiKey: string;
      apiKeyEnv: string;
      models: string;
      addModel: string;
      modelPlaceholder: string;
      useAsDefault: string;
      advanced: string;
      save: string;
      cancel: string;
      provider: string;
      selected: string;
      defaultModel: string;
      defaultReasoningEffort: string;
      internalTaskDefault: string;
      internalTaskDefaultHint: string;
      internalTaskStatus: string;
      testInternalTaskModel: string;
      testModel: string;
      modelReady: string;
      modelUnavailable: string;
      modelUntested: string;
      providerDefault: string;
      contextWindow: string;
      compactThreshold: string;
      capabilities: string;
      diagnostics: string;
      endpoint: string;
      fieldHelp: Record<string, string>;
    };
  basic: {
    title: string;
    description: string;
    required: string;
    extension: string;
    missingRequired: string;
    gitTokenEnv: string;
    gitTokenValue: string;
    gitUserName: string;
    gitUserEmail: string;
    gitRemoteUrl: string;
    save: string;
    test: string;
    status: string;
    configured: string;
    missing: string;
    configPath: string;
    dotenvPath: string;
  };
  skills: {
    title: string;
    noResults: string;
    noDetail: string;
    trust: string;
    version: string;
    dependencies: string;
    readiness: string;
    config: string;
    platforms: string;
    package: string;
    validation: string;
    allowedTools: string;
    tags: string;
    content: string;
    files: string;
    selectedFile: string;
    assets: string;
    templates: string;
    scripts: string;
    references: string;
    procedures: string;
    procedureCandidates: string;
    procedureCandidatesDescription: string;
    procedureId: string;
    trigger: string;
    strength: string;
    qualityScore: string;
    frequency: string;
    confidence: string;
    success: string;
    failure: string;
    promotionReadiness: string;
    blockers: string;
    expectedOutcome: string;
    maintenance: string;
    maintenanceHint: string;
    automationStatus: string;
    backgroundDryRun: string;
    forceRun: string;
    nextRun: string;
    lastRun: string;
    lastStatus: string;
    lastRunId: string;
    interval: string;
    autoMerge: string;
    pinProtection: string;
    recommendations: string;
    planMaintenance: string;
    runMaintenance: string;
    selectedActions: string;
    candidateActions: string;
    executed: string;
    skipped: string;
    runId: string;
    status: string;
    promote: string;
    reject: string;
    restoreProcedure: string;
    promotable: string;
    promoted: string;
    rejected: string;
    enable: string;
    disable: string;
    uninstall: string;
    reload: string;
    refresh: string;
  };
    memory: {
      title: string;
      description: string;
      health: string;
      qualityScore: string;
      candidateAudit: string;
      decision: string;
      reason: string;
      blockers: string;
      stores: string;
    issues: string;
    recommendations: string;
    providers: string;
    pendingReview: string;
    userMemories: string;
    provisional: string;
    candidate: string;
    forgotten: string;
    polluted: string;
    forget: string;
    confidence: string;
    evidence: string;
    score: string;
    salience: string;
    retention: string;
    retentionScore: string;
    staleScore: string;
    reinforcement: string;
    temporalDecay: string;
    accessCount: string;
    accessed: string;
    lastAccessed: string;
    expiresAt: string;
    hot: string;
    warm: string;
    cold: string;
    recallBenchmark: string;
    benchmarkSuites: string;
    runBenchmark: string;
    runSuite: string;
    latestRun: string;
    benchmarkPassed: string;
    benchmarkFailed: string;
    hitRate: string;
    falsePositiveRate: string;
    averageEvidence: string;
    falsePositives: string;
    missingExpectations: string;
    topEvidence: string;
    noBenchmarkCases: string;
    noBenchmarkSuites: string;
    conflicts: string;
    conflictHelp: string;
    noConflicts: string;
    keepBoth: string;
    keepLeft: string;
    keepRight: string;
    leftMemory: string;
    rightMemory: string;
    resolved: string;
    stale: string;
    noStaleItems: string;
    archiveTurns: string;
    entries: string;
    active: string;
    inactive: string;
    lowConfidence: string;
    lowSalience: string;
    missingEvidence: string;
    duplicates: string;
    tokenPressure: string;
    flush: string;
    refresh: string;
    refreshMemory: string;
    reinforceMemory: string;
    archiveMemory: string;
    reviewMemory: string;
    governancePlan: string;
    maintenance: string;
    maintenanceHint: string;
    automationStatus: string;
    backgroundDryRun: string;
    forceRun: string;
    nextRun: string;
    lastRun: string;
    lastStatus: string;
    lastRunId: string;
    interval: string;
    errors: string;
    planMaintenance: string;
    runMaintenance: string;
    dryRun: string;
    executed: string;
    skipped: string;
    pendingUpdates: string;
    drainedUpdates: string;
    reflectionDue: string;
    reflectionJobs: string;
    planGovernance: string;
    executeGovernance: string;
    governancePolicy: string;
    governanceCandidates: string;
    approve: string;
    reject: string;
    approveAll: string;
    rejectAll: string;
      noReviewItems: string;
      noCandidateAudit: string;
      noIssues: string;
    noRecommendations: string;
    noStores: string;
    generatedAt: string;
  };
  selfUpgrade: {
    title: string;
    score: string;
    domains: string;
    backlog: string;
    recommendations: string;
    issues: string;
    refresh: string;
    generatedAt: string;
    noBacklog: string;
    noIssues: string;
    noRecommendations: string;
  };
  mcp: {
    title: string;
    status: string;
    tools: string;
    diagnostics: string;
    provenance: string;
    resources: string;
    prompts: string;
    refresh: string;
    reconnect: string;
    add: string;
    reload: string;
    delete: string;
    configJson: string;
    read: string;
    render: string;
    configPath: string;
    overview: string;
    ready: string;
    enabled: string;
    disabled: string;
    authRequired: string;
    failed: string;
    hiddenFromModel: string;
    visibleToModel: string;
    notVisibleToModel: string;
    requiredEnv: string;
    requiresPath: string;
    counts: string;
    connection: string;
    noDiagnostics: string;
    noServers: string;
    noSelection: string;
  };
  plugins: {
    title: string;
    noResults: string;
    noDetail: string;
    catalog: string;
    installed: string;
    sources: string;
    available: string;
    catalogEmpty: string;
    installedEmpty: string;
    sourcesEmpty: string;
    search: string;
    advancedInstall: string;
    addRegistry: string;
    refreshRegistry: string;
    deleteRegistry: string;
    installSelected: string;
    reinstall: string;
    registry: string;
    registrySource: string;
    registryKind: string;
    readonly: string;
    cached: string;
    lastChecked: string;
    diagnostics: string;
    sourceKind: string;
    version: string;
    author: string;
    homepage: string;
    trust: string;
    tags: string;
    counts: string;
    mcpServers: string;
    permissions: string;
    catalogMetadata: string;
    skillRoots: string;
    tools: string;
    discoverySource: string;
    resources: string;
    prompts: string;
    install: string;
  };
  scheduled: {
    title: string;
    noResults: string;
    refresh: string;
    run: string;
    pause: string;
    resume: string;
    nextRun: string;
    lastRun: string;
    lastStatus: string;
    schedule: string;
    thread: string;
    prompt: string;
    automation: string;
    enabled: string;
    due: string;
    running: string;
    failed: string;
    forceDue: string;
    recent: string;
  };
  actions: {
    title: string;
    description: string;
    submit: string;
    cancel: string;
    source: string;
    skillId: string;
    pluginId: string;
    registryId: string;
    registryName: string;
    trustLevel: string;
    enableOnInstall: string;
    force: string;
    revision: string;
    destination: string;
    arguments: string;
    configJson: string;
    invalidJson: string;
    result: string;
    noResult: string;
    latestResult: string;
  };
  common: {
    status: string;
    enabled: string;
    disabled: string;
    none: string;
    loading: string;
    metadata: string;
    path: string;
    server: string;
    source: string;
    copied: string;
    error: string;
    yes: string;
    no: string;
  };
};

export function opsCopy(locale: Locale): OpsCopy {
  if (locale === "zh-CN") {
    return {
      title: "配置中心",
      description: "统一管理模型、工具、Skills、MCP、插件和计划自动化。当前线程的运行时能力继续保留在聊天窗口右侧抽屉中。",
      open: "打开配置中心",
      close: "关闭配置中心",
      surfaces: {
        overview: "总览",
        basics: "基础配置",
        models: "模型配置",
        tools: "工具配置",
        skills: "技能配置",
        memory: "记忆治理",
        selfUpgrade: "自升级",
        mcp: "MCP 配置",
        plugins: "插件配置",
        scheduled: "计划自动化",
      },
      overview: {
        title: "全局配置总览",
        description: "这里是系统级配置入口，不展示当前线程的 visible/deferred 运行时细节。",
        globalStatus: "全局状态",
        currentThread: "当前线程",
        configSnapshot: "配置快照",
        noThread: "未选择线程",
        runtimeDrawerNote: "当前线程运行时能力、最近工具和审批状态仍在右侧抽屉查看。",
        basics: "基础配置",
        basicsDescription: "配置 HCMS 必需的 Git token，并区分可选扩展项与本地诊断。",
        models: "模型",
        modelsDescription: "配置 provider、默认模型、上下文窗口和推理能力。",
        tools: "工具",
        toolsDescription: "查看和筛选全局工具目录、来源、审批策略和健康信息。",
        skills: "Skills",
        skillsDescription: "总览显示仓库技能，详情页可治理仓库、用户和插件来源。",
        memory: "HCMS 记忆",
        memoryDescription: "查看 HCMS 四流召回、因果链、证据、置信度和版本历史。",
        selfUpgrade: "自升级",
        selfUpgradeDescription: "查看 memory、skills 和自动化健康聚合、backlog 和治理建议。",
        mcp: "MCP",
        mcpDescription: "配置 MCP server、资源、prompt 和连接状态。",
        plugins: "插件",
        pluginsDescription: "安装插件、配置来源目录和查看插件能力。",
        scheduled: "计划自动化",
        scheduledDescription: "查看计划任务、运行、暂停或恢复后台自动化。",
        openSurface: "进入",
        enabled: "启用",
        ready: "就绪",
        total: "总数",
      },
      summary: {
        health: "网关健康",
        runtime: "线程能力摘要",
        runtimeSummary: "运行时摘要",
        toolsVisible: "当前可见工具",
        toolsDeferred: "延迟工具",
        enabledSkills: "已启用技能",
        connectedMcp: "已连接 MCP",
        plugins: "插件数",
        openConsole: "打开全局配置",
        inspectThreadContext: "按当前线程上下文检查能力",
      },
      filters: {
        search: "搜索",
        sourceKind: "来源",
        capabilityGroup: "能力组",
        all: "全部",
      },
      toolPanel: {
        title: "Tools Catalog",
        catalogPath: "来源路径",
        approval: "审批",
        dependencies: "依赖",
        provenance: "来源证明",
        health: "健康信息",
        resources: "资源",
        prompts: "提示模板",
        noResults: "没有匹配的能力。",
        noDetail: "选择左侧能力查看详情。",
      },
      basic: {
        title: "基础配置",
        description: "管理 HCMS 启动前必须具备的配置，以及不会阻塞启动但可提升诊断和版本记录质量的扩展项。",
        required: "必须配置",
        extension: "扩展配置",
        missingRequired: "缺少必须配置",
        gitTokenEnv: "Git token 环境变量",
        gitTokenValue: "Git token",
        gitUserName: "Git 用户名",
        gitUserEmail: "Git 邮箱",
        gitRemoteUrl: "Git 远端 URL",
        save: "保存基础配置",
        test: "测试",
        status: "状态",
        configured: "已配置",
        missing: "未配置",
        configPath: "配置文件",
        dotenvPath: ".env 文件",
      },
      models: {
        title: "模型配置",
        noResults: "当前没有模型配置。",
        add: "添加模型",
        delete: "删除",
        edit: "编辑",
        formTitle: "添加模型 Provider",
        editFormTitle: "编辑模型 Provider",
        providerPreset: "Provider",
        name: "配置名称",
        url: "URL",
        apiKey: "KEY",
        apiKeyEnv: "KEY 环境变量",
        models: "模型列表",
        addModel: "添加模型",
        modelPlaceholder: "例如 gpt-5.4",
        useAsDefault: "设为默认模型",
        advanced: "扩展字段",
        save: "确定",
        cancel: "取消",
        provider: "Provider",
        selected: "当前选择",
        defaultModel: "默认模型",
        defaultReasoningEffort: "默认推理强度",
        internalTaskDefault: "后台任务模型",
        internalTaskDefaultHint: "用于标题生成、会话摘要、记忆沉淀、记忆检索重排、自动维护、Skills 提取、流程沉淀、计划自动化和轨迹压缩等非主对话任务。此选择独立于 Provider 默认模型。",
        internalTaskStatus: "后台模型状态",
        testInternalTaskModel: "测试后台模型",
        testModel: "测试模型",
        modelReady: "可用",
        modelUnavailable: "不可用",
        modelUntested: "未测试",
        providerDefault: "Provider 默认",
        contextWindow: "上下文窗口",
        compactThreshold: "压缩阈值",
        capabilities: "能力",
        diagnostics: "诊断",
        endpoint: "Endpoint",
        fieldHelp: {
          providerPreset: "选择一个内置 Provider 模板。URL、环境变量和能力开关会按该模板预填推荐值。",
          apiKey: "Provider 的访问密钥，会写入本地 .env 并在 config 中用环境变量引用。",
          apiKeyEnv: "保存 KEY 的环境变量名。留空时使用系统推荐名称。",
          models: "该 Provider 可用的模型名列表，至少需要一个，并且需要选择其中一个作为默认模型。",
          defaultReasoningEffort: "该 Provider 新线程默认使用的推理强度，线程右侧抽屉仍可临时覆盖。",
          contextWindow: "模型最大上下文窗口。留空时采用系统推荐值，默认建议 1000000。",
          compactThreshold: "达到该 token 数附近时触发压缩。留空时默认建议 900000。",
          maxTokens: "单次响应的最大输出 token 数。留空表示不覆盖 Provider 默认值。",
          temperature: "采样温度。值越高越发散，留空表示使用 Provider 默认值。",
          topP: "核采样概率阈值。留空表示使用 Provider 默认值。",
          timeout: "请求超时时间，单位秒。",
          requestTimeout: "单次底层请求超时时间，单位秒。",
          defaultRequestTimeout: "未单独指定请求超时时的默认请求超时，单位秒。",
          maxRetries: "同一模型请求失败后的重试次数。",
          outputVersion: "Provider 需要的输出协议版本，例如 responses/v1。",
          useResponsesApi: "OpenAI 兼容模型是否优先使用 Responses API。",
          supportsToolCalling: "模型是否支持工具调用。",
          supportsThinking: "模型是否支持结构化 thinking/reasoning block。",
          supportsReasoningEffort: "模型是否支持 reasoning_effort 参数。",
          supportsVision: "模型是否支持图像输入。",
          supportsImageGeneration: "模型是否支持图像生成。",
          imageGenerationEndpoint: "图像生成接口路径后缀，会拼接在 Provider URL 后面，例如 /image_generation 或 /images/generations。",
          defaultHeaders: "附加到请求的默认 HTTP headers，JSON object。",
          extraBody: "附加到模型请求体的固定字段，JSON object。",
          providerSettings: "Provider 专属配置，JSON object。",
          whenThinkingEnabled: "启用 thinking 时叠加到请求的配置，JSON object。",
          whenThinkingDisabled: "禁用 thinking 时叠加到请求的配置，JSON object。",
          thinking: "模型 thinking 配置，JSON object。",
          imageGeneration: "图像生成配置，JSON object。",
        },
      },
      skills: {
        title: "Skills",
        noResults: "没有匹配的技能。",
        noDetail: "选择左侧技能查看详情和治理动作。",
        trust: "信任级别",
        version: "版本",
        dependencies: "依赖",
        readiness: "就绪条件",
        config: "配置",
        platforms: "平台",
        package: "包信息",
        validation: "校验结果",
        allowedTools: "允许工具",
        tags: "标签",
        content: "技能正文",
        files: "关联文件",
        selectedFile: "文件内容",
        assets: "资源文件",
        templates: "模板",
        scripts: "脚本",
        references: "参考资料",
        procedures: "流程候选",
        procedureCandidates: "自动整理的流程候选",
        procedureCandidatesDescription: "这些候选来自已成功完成的可见工具流程，达到阈值后可以提升为默认启用的 workspace skill。",
        procedureId: "候选 ID",
        trigger: "触发条件",
        strength: "强度",
        qualityScore: "质量分",
        frequency: "次数",
        confidence: "置信度",
        success: "成功",
        failure: "失败",
        promotionReadiness: "准备度",
        blockers: "阻塞项",
        expectedOutcome: "预期结果",
        maintenance: "自动维护",
        maintenanceHint: "预演或执行 Skills 自治理：质量 review 计划、重复合并计划、过期归档、模板沉淀和流程候选提升都会按上限执行。",
        automationStatus: "后台自动维护",
        backgroundDryRun: "后台预演",
        forceRun: "立即检查",
        nextRun: "下次运行",
        lastRun: "上次运行",
        lastStatus: "上次状态",
        lastRunId: "上次 Run",
        interval: "间隔",
        autoMerge: "自动合并",
        pinProtection: "Pin 保护",
        recommendations: "推荐",
        planMaintenance: "预演维护",
        runMaintenance: "执行维护",
        selectedActions: "选中动作",
        candidateActions: "候选动作",
        executed: "已执行",
        skipped: "已跳过",
        runId: "运行 ID",
        status: "状态",
        promote: "提升为 Skill",
        reject: "拒绝",
        restoreProcedure: "恢复候选",
        promotable: "可提升",
        promoted: "已提升",
        rejected: "已拒绝",
        enable: "启用",
        disable: "禁用",
        uninstall: "卸载",
        reload: "重载技能",
        refresh: "刷新列表",
      },
      memory: {
        title: "HCMS 控制台",
        description: "面向 HCMS 的四流召回、因果链、证据、置信度和版本历史控制台。",
        health: "健康状态",
        qualityScore: "质量分",
        candidateAudit: "候选审计",
        decision: "决策",
        reason: "原因",
        blockers: "阻塞项",
        stores: "记忆库",
        issues: "问题",
        recommendations: "建议",
        providers: "Provider",
        pendingReview: "待审",
        userMemories: "用户记忆",
        provisional: "暂存",
        candidate: "候选",
        forgotten: "已遗忘",
        polluted: "污染源",
        forget: "遗忘",
        confidence: "置信度",
        evidence: "证据",
        score: "分数",
        salience: "价值",
        retention: "保留分层",
        retentionScore: "保留分",
        staleScore: "过期分",
        reinforcement: "强化",
        temporalDecay: "时间衰减",
        accessCount: "访问次数",
        accessed: "已访问",
        lastAccessed: "最近访问",
        expiresAt: "到期",
        hot: "高热",
        warm: "温热",
        cold: "冷却",
        recallBenchmark: "召回回归",
        benchmarkSuites: "评估集",
        runBenchmark: "运行评测",
        runSuite: "运行评估集",
        latestRun: "最近运行",
        benchmarkPassed: "通过",
        benchmarkFailed: "未通过",
        hitRate: "命中率",
        falsePositiveRate: "误召率",
        averageEvidence: "平均证据",
        falsePositives: "误召项",
        missingExpectations: "缺失期望",
        topEvidence: "主要证据",
        noBenchmarkCases: "没有可运行的召回评测用例。",
        noBenchmarkSuites: "还没有持久化召回评估集。",
        conflicts: "冲突",
        conflictHelp: "选择保留策略会更新冲突关系，保留审计证据但避免重复注入。",
        noConflicts: "当前没有冲突记忆。",
        keepBoth: "都保留",
        keepLeft: "保留左侧",
        keepRight: "保留右侧",
        leftMemory: "左侧记忆",
        rightMemory: "右侧记忆",
        resolved: "已处理",
        stale: "过期",
        noStaleItems: "当前没有过期或低保留分记忆。",
        archiveTurns: "归档轮次",
        entries: "条目",
        active: "活跃",
        inactive: "非活跃",
        lowConfidence: "低置信",
        lowSalience: "低价值",
        missingEvidence: "缺少证据",
        duplicates: "重复簇",
        tokenPressure: "注入压力",
        flush: "沉淀记忆",
        refresh: "刷新",
        refreshMemory: "刷新访问",
        reinforceMemory: "强化",
        archiveMemory: "归档",
        reviewMemory: "送审",
        governancePlan: "治理计划",
        maintenance: "自动维护",
        maintenanceHint: "先沉淀待处理更新，再运行到期反思任务，并用有界策略治理低保留分记忆。",
        automationStatus: "后台自动维护",
        backgroundDryRun: "后台预演",
        forceRun: "立即检查",
        nextRun: "下次运行",
        lastRun: "上次运行",
        lastStatus: "上次状态",
        lastRunId: "上次 Run",
        interval: "间隔",
        errors: "错误",
        planMaintenance: "预演维护",
        runMaintenance: "执行维护",
        dryRun: "预演",
        executed: "已执行",
        skipped: "已跳过",
        pendingUpdates: "待沉淀",
        drainedUpdates: "沉淀更新",
        reflectionDue: "到期反思",
        reflectionJobs: "反思任务",
        planGovernance: "生成计划",
        executeGovernance: "执行计划",
        governancePolicy: "策略",
        governanceCandidates: "候选",
        approve: "批准",
        reject: "拒绝",
        approveAll: "全部批准",
        rejectAll: "全部拒绝",
        noReviewItems: "当前没有 HCMS 质量候选。",
        noCandidateAudit: "还没有候选沉淀审计记录。",
        noIssues: "当前没有需要处理的记忆质量问题。",
        noRecommendations: "当前没有额外建议。",
        noStores: "当前没有记忆库。",
        generatedAt: "生成时间",
      },
      selfUpgrade: {
        title: "自升级健康",
        score: "健康分",
        domains: "领域",
        backlog: "Backlog",
        recommendations: "建议",
        issues: "问题",
        refresh: "刷新",
        generatedAt: "生成时间",
        noBacklog: "当前没有自升级 backlog。",
        noIssues: "当前没有问题。",
        noRecommendations: "当前没有建议。",
      },
      mcp: {
        title: "MCP",
        status: "状态",
        tools: "工具",
        diagnostics: "诊断",
        provenance: "来源证明",
        resources: "资源",
        prompts: "提示模板",
        refresh: "刷新",
        reconnect: "重连",
        add: "添加 MCP",
        reload: "刷新列表",
        delete: "删除 MCP",
        configJson: "MCP JSON 配置",
        read: "读取资源",
        render: "渲染 Prompt",
        configPath: "配置文件",
        overview: "配置概览",
        ready: "可用",
        enabled: "已启用",
        disabled: "未启用",
        authRequired: "缺少密钥",
        failed: "失败",
        hiddenFromModel: "对模型隐藏",
        visibleToModel: "模型可见",
        notVisibleToModel: "模型不可见",
        requiredEnv: "需要环境变量",
        requiresPath: "需要路径",
        counts: "数量",
        connection: "连接",
        noDiagnostics: "当前没有诊断信息。",
        noServers: "当前没有 MCP server。",
        noSelection: "选择左侧 server 查看资源、prompt 和来源。",
      },
      plugins: {
        title: "Plugins",
        noResults: "当前没有插件。",
        noDetail: "选择左侧插件查看详情。",
        catalog: "可安装",
        installed: "已安装",
        sources: "来源",
        available: "可安装",
        catalogEmpty: "当前没有可展示的插件源。你仍然可以使用高级安装输入本地路径、zip 或 Git 地址。",
        installedEmpty: "还没有安装插件。",
        sourcesEmpty: "还没有配置插件来源。",
        search: "搜索插件",
        advancedInstall: "高级安装",
        addRegistry: "添加来源",
        refreshRegistry: "刷新来源",
        deleteRegistry: "删除来源",
        installSelected: "安装",
        reinstall: "重新安装",
        registry: "来源",
        registrySource: "来源地址",
        registryKind: "来源类型",
        readonly: "只读",
        cached: "使用缓存",
        lastChecked: "最近检查",
        diagnostics: "诊断",
        sourceKind: "来源类型",
        version: "版本",
        author: "作者",
        homepage: "主页",
        trust: "信任级别",
        tags: "标签",
        counts: "能力概览",
        mcpServers: "Bundled MCP",
        permissions: "权限提示",
        catalogMetadata: "目录元数据",
        skillRoots: "技能根目录",
        tools: "工具",
        discoverySource: "发现来源",
        resources: "资源",
        prompts: "提示模板",
        install: "安装插件",
      },
      scheduled: {
        title: "计划自动化",
        noResults: "还没有计划任务。",
        refresh: "刷新",
        run: "运行",
        pause: "暂停",
        resume: "恢复",
        nextRun: "下次运行",
        lastRun: "上次运行",
        lastStatus: "最近状态",
        schedule: "调度",
        thread: "线程",
        prompt: "任务提示",
        automation: "自动化状态",
        enabled: "启用任务",
        due: "到期任务",
        running: "运行中",
        failed: "失败",
        forceDue: "执行到期任务",
        recent: "最近执行",
      },
      actions: {
        title: "动作确认",
        description: "危险或高影响动作统一通过确认对话框执行。",
        submit: "执行",
        cancel: "取消",
        source: "来源包或路径",
        skillId: "技能 ID",
        pluginId: "插件 ID",
        registryId: "来源 ID",
        registryName: "来源名称",
        trustLevel: "信任级别",
        enableOnInstall: "安装后启用",
        force: "如果已存在则覆盖安装",
        revision: "回滚版本",
        destination: "发布目标",
        arguments: "JSON 参数",
        configJson: "JSON 配置",
        invalidJson: "JSON 解析失败，请检查格式。",
        result: "结果",
        noResult: "尚未执行动作。",
        latestResult: "最近结果",
      },
      common: {
        status: "状态",
        enabled: "已启用",
        disabled: "已禁用",
        none: "无",
        loading: "加载中…",
        metadata: "元数据",
        path: "路径",
        server: "服务",
        source: "来源",
        copied: "已复制",
        error: "错误",
        yes: "是",
        no: "否",
      },
    };
  }

  return {
    title: "Configuration Center",
    description: "Manage global models, tools, skills, MCP servers, plugins, and scheduled automations. Thread runtime capabilities stay in the right drawer.",
    open: "Open Configuration Center",
    close: "Close Configuration Center",
    surfaces: {
      overview: "Overview",
      basics: "Basic Configuration",
      models: "Models",
      tools: "Tools",
      skills: "Skills",
      memory: "Memory",
      selfUpgrade: "Self-upgrade",
      mcp: "MCP",
      plugins: "Plugins",
      scheduled: "Scheduled",
    },
    overview: {
      title: "Global Configuration Overview",
      description: "This is the system-level configuration entry point, not the current thread runtime capability view.",
        globalStatus: "Global status",
        currentThread: "Current thread",
        configSnapshot: "Config snapshot",
        noThread: "No thread selected",
      runtimeDrawerNote: "Current thread runtime tools, approvals, and recent activity remain in the right drawer.",
      basics: "Basic Configuration",
      basicsDescription: "Configure the Git token required by HCMS and separate optional diagnostics from required setup.",
      models: "Models",
      modelsDescription: "Configure providers, default models, context windows, and reasoning capabilities.",
      tools: "Tools",
      toolsDescription: "Inspect the global tool catalog, sources, approval policy, and health.",
      skills: "Skills",
      skillsDescription: "Overview counts repo skills; detail pages govern repo, user, and plugin sources.",
      memory: "HCMS Memory",
      memoryDescription: "Inspect HCMS recall, causal chains, evidence, confidence, and version history.",
      selfUpgrade: "Self-upgrade",
      selfUpgradeDescription: "Inspect memory, skills, and automation health, backlog, and governance recommendations.",
      mcp: "MCP",
      mcpDescription: "Configure MCP servers, resources, prompts, and connection state.",
      plugins: "Plugins",
      pluginsDescription: "Install plugins, configure registries, and inspect bundled capabilities.",
      scheduled: "Scheduled Automations",
      scheduledDescription: "Review, run, pause, or resume background automations.",
      openSurface: "Open",
      enabled: "enabled",
      ready: "ready",
      total: "total",
    },
    summary: {
      health: "Gateway health",
      runtime: "Thread runtime summary",
      runtimeSummary: "Runtime summary",
      toolsVisible: "Visible tools",
      toolsDeferred: "Deferred tools",
      enabledSkills: "Enabled skills",
      connectedMcp: "Connected MCP",
      plugins: "Plugins",
      openConsole: "Open global config",
      inspectThreadContext: "Inspect current thread context",
    },
    filters: {
      search: "Search",
      sourceKind: "Source",
      capabilityGroup: "Group",
      all: "All",
    },
    toolPanel: {
      title: "Tools Catalog",
      catalogPath: "Catalog path",
      approval: "Approval",
      dependencies: "Dependencies",
      provenance: "Provenance",
      health: "Health",
      resources: "Resources",
      prompts: "Prompts",
      noResults: "No matching capabilities.",
      noDetail: "Select a capability to inspect details.",
    },
    basic: {
      title: "Basic Configuration",
      description: "Manage configuration required before HCMS starts, plus optional extension settings that improve diagnostics and version metadata.",
      required: "Required configuration",
      extension: "Extension configuration",
      missingRequired: "Missing required configuration",
      gitTokenEnv: "Git token env",
      gitTokenValue: "Git token value",
      gitUserName: "Git user name",
      gitUserEmail: "Git user email",
      gitRemoteUrl: "Git remote URL",
      save: "Save basic configuration",
      test: "Test",
      status: "Status",
      configured: "Configured",
      missing: "Missing",
      configPath: "Config file",
      dotenvPath: ".env file",
    },
    models: {
      title: "Model Configuration",
      noResults: "No model configurations are available.",
      add: "Add model",
      delete: "Delete",
      edit: "Edit",
      formTitle: "Add model provider",
      editFormTitle: "Edit model provider",
      providerPreset: "Provider",
      name: "Config name",
      url: "URL",
      apiKey: "KEY",
      apiKeyEnv: "KEY environment variable",
      models: "Models",
      addModel: "Add model",
      modelPlaceholder: "For example gpt-5.4",
      useAsDefault: "Use as default model",
      advanced: "Advanced fields",
      save: "Save",
      cancel: "Cancel",
      provider: "Provider",
      selected: "Selected",
      defaultModel: "Default model",
      defaultReasoningEffort: "Default reasoning effort",
      internalTaskDefault: "Background tasks",
      internalTaskDefaultHint: "Used for title generation, summarization, memory capture, memory reranking, automatic maintenance, skill extraction, procedure learning, scheduled automations, and trajectory compression. This selection is independent from each provider's default model.",
      internalTaskStatus: "Background model status",
      testInternalTaskModel: "Test background model",
      testModel: "Test model",
      modelReady: "Ready",
      modelUnavailable: "Unavailable",
      modelUntested: "Untested",
      providerDefault: "Provider default",
      contextWindow: "Context window",
      compactThreshold: "Compact threshold",
      capabilities: "Capabilities",
      diagnostics: "Diagnostics",
      endpoint: "Endpoint",
      fieldHelp: {
        providerPreset: "Choose a built-in provider template. URL, environment variable, and capability defaults are prefilled from it.",
        apiKey: "Provider credential. It is stored in local .env and referenced from config via an environment variable.",
        apiKeyEnv: "Environment variable name used for the key. Leave blank to use the recommended name.",
        models: "Available model names for this provider. At least one model is required, and one must be selected as default.",
        defaultReasoningEffort: "Default reasoning effort for new threads using this provider. The thread drawer can still override it per thread.",
        contextWindow: "Maximum model context window. Leave blank to use the recommended default, currently 1000000.",
        compactThreshold: "Token threshold that triggers compaction. Leave blank to use the recommended default, currently 900000.",
        maxTokens: "Maximum output tokens per response. Leave blank to keep the provider default.",
        temperature: "Sampling temperature. Higher values are more diverse. Leave blank to keep the provider default.",
        topP: "Nucleus sampling threshold. Leave blank to keep the provider default.",
        timeout: "Overall request timeout in seconds.",
        requestTimeout: "Low-level request timeout in seconds.",
        defaultRequestTimeout: "Fallback request timeout when no request-specific timeout is set.",
        maxRetries: "Retry count for the same model after transient failures.",
        outputVersion: "Provider output protocol version, such as responses/v1.",
        useResponsesApi: "Prefer the OpenAI Responses API for compatible providers.",
        supportsToolCalling: "Whether the model supports tool calls.",
        supportsThinking: "Whether the model supports structured thinking/reasoning blocks.",
        supportsReasoningEffort: "Whether the model accepts reasoning_effort.",
        supportsVision: "Whether the model supports image input.",
        supportsImageGeneration: "Whether the model supports image generation.",
        imageGenerationEndpoint: "Image generation API path suffix appended to the provider URL, such as /image_generation or /images/generations.",
        defaultHeaders: "Default HTTP headers added to requests, as a JSON object.",
        extraBody: "Fixed fields added to each model request body, as a JSON object.",
        providerSettings: "Provider-specific settings, as a JSON object.",
        whenThinkingEnabled: "Request overlays applied when thinking is enabled, as a JSON object.",
        whenThinkingDisabled: "Request overlays applied when thinking is disabled, as a JSON object.",
        thinking: "Model thinking configuration, as a JSON object.",
        imageGeneration: "Image generation configuration, as a JSON object.",
      },
    },
    skills: {
      title: "Skills",
      noResults: "No matching skills.",
      noDetail: "Select a skill to inspect and manage it.",
      trust: "Trust",
      version: "Version",
      dependencies: "Dependencies",
      readiness: "Readiness",
      config: "Config",
      platforms: "Platforms",
      package: "Package",
      validation: "Validation",
      allowedTools: "Allowed tools",
      tags: "Tags",
      content: "Skill content",
      files: "Files",
      selectedFile: "File content",
      assets: "Assets",
      templates: "Templates",
      scripts: "Scripts",
      references: "References",
      procedures: "Procedures",
      procedureCandidates: "Agent-curated procedure candidates",
      procedureCandidatesDescription: "These candidates come from successful visible tool workflows and can be promoted into enabled workspace skills after they reach the threshold.",
      procedureId: "Candidate ID",
      trigger: "Trigger",
      strength: "Strength",
      qualityScore: "Quality score",
      frequency: "Frequency",
      confidence: "Confidence",
      success: "Success",
      failure: "Failure",
      promotionReadiness: "Readiness",
      blockers: "Blockers",
      expectedOutcome: "Expected outcome",
      maintenance: "Automatic maintenance",
      maintenanceHint: "Plan or run skill self-governance with bounded review plans, duplicate merge plans, stale archival, template extraction, and procedure promotion.",
      automationStatus: "Background automation",
      backgroundDryRun: "Background dry run",
      forceRun: "Run due check",
      nextRun: "Next run",
      lastRun: "Last run",
      lastStatus: "Last status",
      lastRunId: "Last run",
      interval: "Interval",
      autoMerge: "Auto merge",
      pinProtection: "Pin protection",
      recommendations: "Recommendations",
      planMaintenance: "Plan maintenance",
      runMaintenance: "Run maintenance",
      selectedActions: "Selected actions",
      candidateActions: "Candidate actions",
      executed: "Executed",
      skipped: "Skipped",
      runId: "Run ID",
      status: "Status",
      promote: "Promote to Skill",
      reject: "Reject",
      restoreProcedure: "Restore candidate",
      promotable: "Promotable",
      promoted: "Promoted",
      rejected: "Rejected",
      enable: "Enable",
      disable: "Disable",
      uninstall: "Uninstall",
      reload: "Reload skill",
      refresh: "Refresh list",
    },
    memory: {
      title: "HCMS Console",
      description: "Inspect HCMS four-stream recall, causal chains, evidence, confidence, and version history.",
      health: "Health",
      qualityScore: "Quality score",
      candidateAudit: "Candidate audit",
      decision: "Decision",
      reason: "Reason",
      blockers: "Blockers",
      stores: "Stores",
      issues: "Issues",
      recommendations: "Recommendations",
      providers: "Providers",
      pendingReview: "Review",
      userMemories: "User memories",
      provisional: "Provisional",
      candidate: "Candidate",
      forgotten: "Forgotten",
      polluted: "Polluted source",
      forget: "Forget",
      confidence: "Confidence",
      evidence: "Evidence",
      score: "Score",
      salience: "Salience",
      retention: "Retention tiers",
      retentionScore: "Retention score",
      staleScore: "Stale score",
      reinforcement: "Reinforcement",
      temporalDecay: "Temporal decay",
      accessCount: "Access count",
      accessed: "Accessed",
      lastAccessed: "Last accessed",
      expiresAt: "Expires",
      hot: "Hot",
      warm: "Warm",
      cold: "Cold",
      recallBenchmark: "Recall benchmark",
      benchmarkSuites: "Eval suites",
      runBenchmark: "Run benchmark",
      runSuite: "Run suite",
      latestRun: "Latest run",
      benchmarkPassed: "Passed",
      benchmarkFailed: "Failed",
      hitRate: "Hit rate",
      falsePositiveRate: "False positive rate",
      averageEvidence: "Average evidence",
      falsePositives: "False positives",
      missingExpectations: "Missing expectations",
      topEvidence: "Top evidence",
      noBenchmarkCases: "No recall benchmark cases are available.",
      noBenchmarkSuites: "No persistent recall evaluation suites are configured.",
      conflicts: "Conflicts",
      conflictHelp: "Choose a retention strategy to update conflict relationships while keeping the audit trail intact.",
      noConflicts: "No memory conflicts are pending.",
      keepBoth: "Keep both",
      keepLeft: "Keep left",
      keepRight: "Keep right",
      leftMemory: "Left memory",
      rightMemory: "Right memory",
      resolved: "Resolved",
      stale: "Stale",
      noStaleItems: "No stale or low-retention memories need attention.",
      archiveTurns: "Archived turns",
      entries: "Entries",
      active: "Active",
      inactive: "Inactive",
      lowConfidence: "Low confidence",
      lowSalience: "Low salience",
      missingEvidence: "Missing evidence",
      duplicates: "Duplicate clusters",
      tokenPressure: "Token pressure",
      flush: "Flush memory",
      refresh: "Refresh",
      refreshMemory: "Refresh access",
      reinforceMemory: "Reinforce",
      archiveMemory: "Archive",
      reviewMemory: "Review",
      governancePlan: "Governance plan",
      maintenance: "Automatic maintenance",
      maintenanceHint: "Drain pending updates, run due reflection jobs, and govern low-retention memories through bounded policy actions.",
      automationStatus: "Background automation",
      backgroundDryRun: "Background dry run",
      forceRun: "Run due check",
      nextRun: "Next run",
      lastRun: "Last run",
      lastStatus: "Last status",
      lastRunId: "Last run",
      interval: "Interval",
      errors: "Errors",
      planMaintenance: "Plan maintenance",
      runMaintenance: "Run maintenance",
      dryRun: "Dry run",
      executed: "Executed",
      skipped: "Skipped",
      pendingUpdates: "Pending updates",
      drainedUpdates: "Drained updates",
      reflectionDue: "Due reflections",
      reflectionJobs: "Reflection jobs",
      planGovernance: "Plan",
      executeGovernance: "Execute plan",
      governancePolicy: "Policy",
      governanceCandidates: "Candidates",
      approve: "Approve",
      reject: "Reject",
      approveAll: "Approve all",
      rejectAll: "Reject all",
      noReviewItems: "No HCMS quality candidates are pending.",
      noCandidateAudit: "No memory candidate audit entries yet.",
      noIssues: "No memory quality issues need attention.",
      noRecommendations: "No additional recommendations.",
      noStores: "No memory stores are available.",
      generatedAt: "Generated",
    },
    selfUpgrade: {
      title: "Self-upgrade Health",
      score: "Score",
      domains: "Domains",
      backlog: "Backlog",
      recommendations: "Recommendations",
      issues: "Issues",
      refresh: "Refresh",
      generatedAt: "Generated",
      noBacklog: "No self-upgrade backlog.",
      noIssues: "No issues.",
      noRecommendations: "No recommendations.",
    },
    mcp: {
      title: "MCP",
      status: "Status",
      tools: "Tools",
      diagnostics: "Diagnostics",
      provenance: "Provenance",
      resources: "Resources",
      prompts: "Prompts",
      refresh: "Refresh",
      reconnect: "Reconnect",
      add: "Add MCP",
      reload: "Refresh",
      delete: "Delete MCP",
      configJson: "MCP JSON config",
      read: "Read resource",
      render: "Render prompt",
      configPath: "Config file",
      overview: "Configuration",
      ready: "Ready",
      enabled: "Enabled",
      disabled: "Disabled",
      authRequired: "Needs key",
      failed: "Failed",
      hiddenFromModel: "Hidden from model",
      visibleToModel: "Visible to model",
      notVisibleToModel: "Not visible to model",
      requiredEnv: "Required env",
      requiresPath: "Requires path",
      counts: "Counts",
      connection: "Connection",
      noDiagnostics: "No diagnostics.",
      noServers: "No MCP servers are configured.",
      noSelection: "Select a server to inspect its runtime surfaces.",
    },
    plugins: {
      title: "Plugins",
      noResults: "No plugins available.",
      noDetail: "Select a plugin to inspect it.",
      catalog: "Available",
      installed: "Installed",
      sources: "Sources",
      available: "Available",
      catalogEmpty: "No catalog plugins are available. Advanced install still accepts a local path, zip, or Git URL.",
      installedEmpty: "No plugins are installed.",
      sourcesEmpty: "No plugin sources are configured.",
      search: "Search plugins",
      advancedInstall: "Advanced install",
      addRegistry: "Add source",
      refreshRegistry: "Refresh source",
      deleteRegistry: "Delete source",
      installSelected: "Install",
      reinstall: "Reinstall",
      registry: "Source",
      registrySource: "Source URL/path",
      registryKind: "Source kind",
      readonly: "Read only",
      cached: "Using cache",
      lastChecked: "Last checked",
      diagnostics: "Diagnostics",
      sourceKind: "Source kind",
      version: "Version",
      author: "Author",
      homepage: "Homepage",
      trust: "Trust",
      tags: "Tags",
      counts: "Capability overview",
      mcpServers: "Bundled MCP",
      permissions: "Permission notes",
      catalogMetadata: "Catalog metadata",
      skillRoots: "Skill roots",
      tools: "Tools",
      discoverySource: "Discovery source",
      resources: "Resources",
      prompts: "Prompts",
      install: "Install plugin",
    },
    scheduled: {
      title: "Scheduled Automations",
      noResults: "No scheduled automations yet.",
      refresh: "Refresh",
      run: "Run",
      pause: "Pause",
      resume: "Resume",
      nextRun: "Next run",
      lastRun: "Last run",
      lastStatus: "Last status",
      schedule: "Schedule",
      thread: "Thread",
      prompt: "Prompt",
      automation: "Automation status",
      enabled: "Enabled tasks",
      due: "Due tasks",
      running: "Running",
      failed: "Failed",
      forceDue: "Run due tasks",
      recent: "Recent executions",
    },
    actions: {
      title: "Confirm action",
      description: "High-impact actions run through a typed confirmation dialog.",
      submit: "Run action",
      cancel: "Cancel",
      source: "Source archive or path",
      skillId: "Skill ID",
      pluginId: "Plugin ID",
      registryId: "Source ID",
      registryName: "Source name",
      trustLevel: "Trust level",
      enableOnInstall: "Enable after install",
      force: "Overwrite if it already exists",
      revision: "Revision",
      destination: "Destination",
      arguments: "JSON arguments",
      configJson: "JSON config",
      invalidJson: "JSON parsing failed. Fix the payload and try again.",
      result: "Result",
      noResult: "No action has run yet.",
      latestResult: "Latest result",
    },
    common: {
      status: "Status",
      enabled: "Enabled",
      disabled: "Disabled",
      none: "none",
      loading: "Loading…",
      metadata: "Metadata",
      path: "Path",
      server: "Server",
      source: "Source",
      copied: "Copied",
      error: "Error",
      yes: "Yes",
      no: "No",
    },
  };
}
