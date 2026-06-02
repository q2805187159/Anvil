"use client";

import React from "react";

export type Locale = "en-US" | "zh-CN";

type Translations = {
  shell: {
    appTitle: string;
    home: string;
    gateway: string;
    ops: string;
    createThread: string;
    noThreadSelected: string;
    local: string;
    health: string;
    language: string;
    collapseLeft: string;
    expandLeft: string;
    collapseRight: string;
    expandRight: string;
  };
  tabs: {
    thread: string;
    approvals: string;
    memory: string;
    skills: string;
    ops: string;
  };
  threadList: {
    title: string;
    searchPlaceholder: string;
    empty: string;
  };
  transcript: {
    title: string;
    emptyTitle: string;
    emptyBody: string;
    live: string;
    interrupted: string;
    user: string;
    assistant: string;
    tool: string;
    system: string;
    reasoning: string;
    show: string;
    hide: string;
    approvalRequired: string;
    toolStarted: string;
    toolCompleted: string;
    contentLabel: string;
  };
  composer: {
    title: string;
    placeholder: string;
    upload: string;
    run: string;
    running: string;
  };
  rightRail: {
    threadOverview: string;
    pendingApproval: string;
    noPendingApproval: string;
    approve: string;
    memoryOverview: string;
    memoryStores: string;
    memoryEntries: string;
    archiveSearch: string;
    reflectionJobs: string;
    providers: string;
    entryCategory: string;
    entryContent: string;
    createEntry: string;
    updateEntry: string;
    searchArchivePlaceholder: string;
    searchArchive: string;
    runJob: string;
    pause: string;
    resume: string;
    remove: string;
    skillsOverview: string;
    streamCapabilities: string;
    models: string;
    extensions: string;
    localDocker: string;
    activeThread: string;
    activeModel: string;
    runtimeCapabilities: string;
    runtimeSummary: string;
    sandboxMode: string;
    isolatedSupport: string;
    connectedMcp: string;
    recentInterruption: string;
    visibleTools: string;
    deferredTools: string;
    uploadedFiles: string;
    enabledSkills: string;
    noSkills: string;
  };
};

const translations: Record<Locale, Translations> = {
  "en-US": {
    shell: {
      appTitle: "Anvil",
      home: "Home",
      gateway: "LangSmith",
      ops: "Ops",
      createThread: "Create thread",
      noThreadSelected: "Select a thread to open the operator stage.",
      local: "local",
      health: "Health",
      language: "Language",
      collapseLeft: "Collapse threads",
      expandLeft: "Expand threads",
      collapseRight: "Collapse inspector",
      expandRight: "Expand inspector",
    },
    tabs: {
      thread: "Thread",
      approvals: "Approvals",
      memory: "Memory",
      skills: "Skills",
      ops: "Ops",
    },
    threadList: {
      title: "Threads",
      searchPlaceholder: "Search threads",
      empty: "No threads yet.",
    },
    transcript: {
      title: "Transcript",
      emptyTitle: "No transcript yet",
      emptyBody: "Create or open a thread and start a run to build the durable transcript.",
      live: "Live tail",
      interrupted: "Interrupted",
      user: "User",
      assistant: "Assistant",
      tool: "Tool",
      system: "System",
      reasoning: "Reasoning",
      show: "Show",
      hide: "Hide",
      approvalRequired: "Approval required",
      toolStarted: "Tool started",
      toolCompleted: "Tool completed",
      contentLabel: "content",
    },
    composer: {
      title: "Composer",
      placeholder: "Describe the next task…",
      upload: "Upload",
      run: "Send",
      running: "Running",
    },
    rightRail: {
      threadOverview: "Thread overview",
      pendingApproval: "Pending approval",
      noPendingApproval: "No pending approval on this thread.",
      approve: "Approve pending turn",
      memoryOverview: "Memory overview",
      memoryStores: "Stores",
      memoryEntries: "Entries",
      archiveSearch: "Archive search",
      reflectionJobs: "Reflection jobs",
      providers: "Providers",
      entryCategory: "Entry category",
      entryContent: "Write a durable memory note",
      createEntry: "Create entry",
      updateEntry: "Update entry",
      searchArchivePlaceholder: "Search archived memory",
      searchArchive: "Search archive",
      runJob: "Run",
      pause: "Pause",
      resume: "Resume",
      remove: "Remove",
      skillsOverview: "Discovered skills",
      streamCapabilities: "Stream capabilities",
      models: "Models",
      extensions: "Extensions",
      localDocker: "Local Docker",
      activeThread: "Active thread",
      activeModel: "Active model",
      runtimeCapabilities: "Runtime capabilities",
      runtimeSummary: "Runtime summary",
      sandboxMode: "Sandbox mode",
      isolatedSupport: "Isolated sandbox support",
      connectedMcp: "Connected MCP servers",
      recentInterruption: "Recent interruption",
      visibleTools: "Visible tools",
      deferredTools: "Deferred tools",
      uploadedFiles: "Uploaded files",
      enabledSkills: "Enabled skills",
      noSkills: "No skills available.",
    },
  },
  "zh-CN": {
    shell: {
      appTitle: "Anvil",
      home: "首页",
      gateway: "LangSmith",
      ops: "配置",
      createThread: "新建线程",
      noThreadSelected: "请选择一个线程，打开主操作舞台。",
      local: "本地",
      health: "健康",
      language: "语言",
      collapseLeft: "折叠线程栏",
      expandLeft: "展开线程栏",
      collapseRight: "折叠检查栏",
      expandRight: "展开检查栏",
    },
    tabs: {
      thread: "线程",
      approvals: "审批",
      memory: "记忆",
      skills: "技能",
      ops: "配置",
    },
    threadList: {
      title: "线程",
      searchPlaceholder: "搜索线程",
      empty: "还没有线程。",
    },
    transcript: {
      title: "对话舞台",
      emptyTitle: "还没有对话记录",
      emptyBody: "新建或打开线程后运行一次任务，这里会显示 durable transcript。",
      live: "实时尾流",
      interrupted: "已中断",
      user: "用户",
      assistant: "助手",
      tool: "工具",
      system: "系统",
      reasoning: "思考过程",
      show: "展开",
      hide: "收起",
      approvalRequired: "需要审批",
      toolStarted: "工具开始执行",
      toolCompleted: "工具执行完成",
      contentLabel: "内容",
    },
    composer: {
      title: "输入区",
      placeholder: "描述下一步任务…",
      upload: "上传",
      run: "发送",
      running: "运行中",
    },
    rightRail: {
      threadOverview: "线程概览",
      pendingApproval: "待审批",
      noPendingApproval: "当前线程没有待审批操作。",
      approve: "批准当前操作",
      memoryOverview: "记忆概览",
      memoryStores: "存储区",
      memoryEntries: "条目",
      archiveSearch: "归档搜索",
      reflectionJobs: "反思任务",
      providers: "提供方",
      entryCategory: "条目分类",
      entryContent: "写入一条持久记忆",
      createEntry: "创建条目",
      updateEntry: "更新条目",
      searchArchivePlaceholder: "搜索归档记忆",
      searchArchive: "搜索归档",
      runJob: "运行",
      pause: "暂停",
      resume: "继续",
      remove: "移除",
      skillsOverview: "已发现技能",
      streamCapabilities: "流式能力",
      models: "模型",
      extensions: "扩展",
      localDocker: "本地 Docker",
      activeThread: "当前线程",
      activeModel: "当前模型",
      runtimeCapabilities: "运行时能力",
      runtimeSummary: "运行时摘要",
      sandboxMode: "沙箱模式",
      isolatedSupport: "隔离沙箱支持",
      connectedMcp: "已连接 MCP 服务",
      recentInterruption: "最近一次中断",
      visibleTools: "可见工具",
      deferredTools: "延迟工具",
      uploadedFiles: "已上传文件",
      enabledSkills: "启用技能",
      noSkills: "没有可用技能。",
    },
  },
};

type I18nContextValue = {
  locale: Locale;
  changeLocale(locale: Locale): void;
  t: Translations;
};

const DEFAULT_LOCALE: Locale = "en-US";
const LOCALE_STORAGE_KEY = "forge.locale";
const I18nContext = React.createContext<I18nContextValue | null>(null);

function detectLocale(): Locale {
  if (typeof window === "undefined") {
    return DEFAULT_LOCALE;
  }
  const saved = window.localStorage.getItem(LOCALE_STORAGE_KEY);
  if (saved === "en-US" || saved === "zh-CN") {
    return saved;
  }
  const browserLocale = window.navigator.language.toLowerCase();
  return browserLocale.startsWith("zh") ? "zh-CN" : "en-US";
}

export function I18nProvider({ children }: Readonly<{ children: React.ReactNode }>) {
  const [locale, setLocale] = React.useState<Locale>(DEFAULT_LOCALE);
  const [clientLocaleReady, setClientLocaleReady] = React.useState(false);

  React.useEffect(() => {
    setLocale(detectLocale());
    setClientLocaleReady(true);
  }, []);

  React.useEffect(() => {
    if (!clientLocaleReady) {
      return;
    }
    if (typeof window !== "undefined") {
      window.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
    }
    if (typeof document !== "undefined") {
      document.documentElement.lang = locale;
      document.documentElement.setAttribute("translate", "no");
      document.documentElement.classList.add("notranslate");
      document.body.setAttribute("translate", "no");
      document.body.classList.add("notranslate");
    }
  }, [clientLocaleReady, locale]);

  const value = React.useMemo<I18nContextValue>(
    () => ({
      locale,
      changeLocale: (nextLocale: Locale) => {
        setClientLocaleReady(true);
        setLocale(nextLocale);
      },
      t: translations[locale],
    }),
    [locale],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  const context = React.useContext(I18nContext);
  if (!context) {
    throw new Error("useI18n must be used within I18nProvider");
  }
  return context;
}
