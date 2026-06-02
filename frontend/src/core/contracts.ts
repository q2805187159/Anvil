import type { ThreadDeleteView, ThreadSettingsView } from "./contracts.generated";

export * from "./contracts.generated";

export type ExecutionMode = Exclude<ThreadSettingsView["execution_mode"], null | undefined>;

export type SkillGovernanceHistoryItemView = {
  skill_id: string;
  action: string;
  created_at: string;
  detail: Record<string, unknown>;
};

export type SkillManageResultView = Record<string, unknown> & {
  skill_id?: string | null;
  items?: SkillGovernanceHistoryItemView[];
};

export type ThreadDeleteResult = ThreadDeleteView;
