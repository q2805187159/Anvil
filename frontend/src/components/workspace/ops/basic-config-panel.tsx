"use client";

import React, { useEffect, useState } from "react";
import { CheckCircle2Icon, KeyRoundIcon, Loader2Icon, TestTube2Icon, XCircleIcon } from "lucide-react";

import { Badge, Button, Input, ScrollArea } from "@/src/components/ui";
import type { BasicConfigItemView, BasicConfigTestView, BasicConfigUpdateRequest } from "@/src/core/contracts";
import { useBasicConfig, useSaveBasicConfig, useTestBasicConfig } from "@/src/core/config/hooks";

import { OpsEmptyState, OpsPanelCard } from "./shared";
import type { OpsCopy } from "./types";

type BasicConfigPanelProps = {
  copy: OpsCopy;
};

type Draft = {
  gitTokenEnv: string;
  gitToken: string;
  gitUserName: string;
  gitUserEmail: string;
  gitRemoteUrl: string;
};

export function BasicConfigPanel({ copy }: BasicConfigPanelProps) {
  const basics = useBasicConfig();
  const saveBasicConfig = useSaveBasicConfig();
  const testBasicConfig = useTestBasicConfig();
  const overview = basics.data ?? null;
  const gitTokenItem = overview?.required_items.find((item) => item.item_id === "git_token") ?? null;
  const authorItem = overview?.extension_items.find((item) => item.item_id === "git_author") ?? null;
  const remoteItem = overview?.extension_items.find((item) => item.item_id === "git_remote") ?? null;
  const [draft, setDraft] = useState<Draft>({
    gitTokenEnv: "GITHUB_TOKEN",
    gitToken: "",
    gitUserName: "",
    gitUserEmail: "",
    gitRemoteUrl: "",
  });
  const [testResults, setTestResults] = useState<Record<string, BasicConfigTestView>>({});
  const [testingItem, setTestingItem] = useState<string | null>(null);
  const configuredGitTokenEnv = gitTokenItem?.token_env || "";
  const configuredAuthorValue = authorItem?.value || "";
  const configuredRemoteValue = remoteItem?.value || "";

  useEffect(() => {
    if (!configuredGitTokenEnv && !configuredAuthorValue && !configuredRemoteValue) {
      return;
    }
    setDraft((current) => {
      const next = {
        ...current,
        gitTokenEnv: configuredGitTokenEnv || current.gitTokenEnv || "GITHUB_TOKEN",
        gitUserName: authorName(configuredAuthorValue) || current.gitUserName,
        gitUserEmail: authorEmail(configuredAuthorValue) || current.gitUserEmail,
        gitRemoteUrl: configuredRemoteValue || current.gitRemoteUrl,
      };
      if (
        next.gitTokenEnv === current.gitTokenEnv &&
        next.gitUserName === current.gitUserName &&
        next.gitUserEmail === current.gitUserEmail &&
        next.gitRemoteUrl === current.gitRemoteUrl
      ) {
        return current;
      }
      return next;
    });
  }, [configuredAuthorValue, configuredGitTokenEnv, configuredRemoteValue]);

  const missingRequired = overview?.missing_required_count ?? 0;
  const requiredItems = overview?.required_items ?? [];
  const extensionItems = overview?.extension_items ?? [];
  const pending = saveBasicConfig.isPending || basics.isFetching;

  async function saveDraft() {
    const body: BasicConfigUpdateRequest = {
      git_token_env: emptyToNull(draft.gitTokenEnv),
      git_token: emptyToNull(draft.gitToken),
      git_user_name: emptyToNull(draft.gitUserName),
      git_user_email: emptyToNull(draft.gitUserEmail),
      git_remote_url: emptyToNull(draft.gitRemoteUrl),
    };
    await saveBasicConfig.mutateAsync(body);
    setDraft((current) => ({ ...current, gitToken: "" }));
  }

  async function runItemTest(itemId: string) {
    setTestingItem(itemId);
    try {
      const result = await testBasicConfig.mutateAsync({ item_id: itemId });
      setTestResults((current) => ({ ...current, [itemId]: result }));
    } finally {
      setTestingItem(null);
    }
  }

  return (
    <ScrollArea className="h-full min-h-0">
      <div className="space-y-4 pr-2">
        <section className="rounded-xl border border-[var(--line)] bg-[var(--panel)] p-5 shadow-[var(--shadow-card)]">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <KeyRoundIcon className="size-4 text-[var(--primary)]" />
                <h2 className="text-base font-semibold text-[var(--ink)]">{copy.basic.title}</h2>
              </div>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--muted)]">{copy.basic.description}</p>
            </div>
            <div className="flex shrink-0 flex-wrap gap-2">
              <Badge tone={missingRequired > 0 ? "warning" : "success"}>
                {copy.basic.required}: {overview?.configured_required_count ?? 0}/{overview?.required_count ?? 0}
              </Badge>
              <Badge tone="neutral">
                {copy.basic.configPath}: {overview?.config_path ?? copy.common.loading}
              </Badge>
            </div>
          </div>
        </section>

        <div className="grid gap-4 lg:grid-cols-[minmax(0,1.15fr)_minmax(320px,0.85fr)]">
          <OpsPanelCard title={copy.basic.required}>
            {requiredItems.length === 0 ? <OpsEmptyState text={copy.common.none} /> : null}
            <ItemList items={requiredItems} copy={copy} testResults={testResults} testingItem={testingItem} onTest={runItemTest} />
          </OpsPanelCard>

          <OpsPanelCard title={copy.basic.extension}>
            {extensionItems.length === 0 ? <OpsEmptyState text={copy.common.none} /> : null}
            <ItemList items={extensionItems} copy={copy} testResults={testResults} testingItem={testingItem} onTest={runItemTest} />
          </OpsPanelCard>
        </div>

        <OpsPanelCard title="Git">
          <div className="grid gap-3 md:grid-cols-2">
            <Field label={copy.basic.gitTokenEnv}>
              <Input aria-label={copy.basic.gitTokenEnv} value={draft.gitTokenEnv} onChange={(event) => setDraft({ ...draft, gitTokenEnv: event.target.value })} placeholder="GITHUB_TOKEN" />
            </Field>
            <Field label={copy.basic.gitTokenValue}>
              <Input aria-label={copy.basic.gitTokenValue} type="password" value={draft.gitToken} onChange={(event) => setDraft({ ...draft, gitToken: event.target.value })} placeholder={draft.gitTokenEnv || "GITHUB_TOKEN"} />
            </Field>
            <Field label={copy.basic.gitUserName}>
              <Input aria-label={copy.basic.gitUserName} value={draft.gitUserName} onChange={(event) => setDraft({ ...draft, gitUserName: event.target.value })} placeholder="Anvil Operator" />
            </Field>
            <Field label={copy.basic.gitUserEmail}>
              <Input aria-label={copy.basic.gitUserEmail} value={draft.gitUserEmail} onChange={(event) => setDraft({ ...draft, gitUserEmail: event.target.value })} placeholder="operator@example.com" />
            </Field>
            <Field label={copy.basic.gitRemoteUrl}>
              <Input aria-label={copy.basic.gitRemoteUrl} value={draft.gitRemoteUrl} onChange={(event) => setDraft({ ...draft, gitRemoteUrl: event.target.value })} placeholder="https://github.com/org/repo.git" />
            </Field>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Button type="button" variant="primary" onClick={saveDraft} disabled={pending}>
              {pending ? <Loader2Icon className="size-4 animate-spin" /> : null}
              {copy.basic.save}
            </Button>
          </div>
          <div className="mt-3 min-w-0 truncate rounded-lg border border-[var(--line)] bg-[var(--panel-muted)] px-3 py-2 text-xs text-[var(--muted)]">
            {copy.basic.dotenvPath}: {overview?.dotenv_path ?? copy.common.none}
          </div>
        </OpsPanelCard>
      </div>
    </ScrollArea>
  );
}

function ItemList({
  items,
  copy,
  testResults,
  testingItem,
  onTest,
}: {
  items: BasicConfigItemView[];
  copy: OpsCopy;
  testResults: Record<string, BasicConfigTestView>;
  testingItem: string | null;
  onTest(itemId: string): void;
}) {
  return (
    <div className="space-y-3">
      {items.map((item) => {
        const result = testResults[item.item_id];
        const ok = result ? result.ok : item.configured;
        return (
          <div key={item.item_id} className="rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  {ok ? <CheckCircle2Icon className="size-4 text-emerald-600" /> : <XCircleIcon className="size-4 text-rose-600" />}
                  <div className="text-sm font-semibold text-[var(--ink)]">{item.label}</div>
                </div>
                <div className="mt-1 text-sm leading-5 text-[var(--muted)]">{item.description}</div>
                <div className="mt-2 text-xs text-[var(--muted)]">
                  {copy.basic.status}: {result?.status ?? item.status}
                  {item.token_env ? ` · ${item.token_env}` : ""}
                  {item.value ? ` · ${item.value}` : ""}
                </div>
              </div>
              {item.testable ? (
                <Button type="button" size="sm" variant="secondary" onClick={() => onTest(item.item_id)} disabled={testingItem === item.item_id}>
                  {testingItem === item.item_id ? <Loader2Icon className="size-4 animate-spin" /> : <TestTube2Icon className="size-4" />}
                  {copy.basic.test} {item.label}
                </Button>
              ) : null}
            </div>
            {result?.message || item.message ? (
              <div className="mt-2 text-sm text-[var(--muted)]">{result?.message ?? item.message}</div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function Field({ label, children }: React.PropsWithChildren<{ label: string }>) {
  return (
    <label className="block min-w-0 space-y-1 text-sm">
      <span className="text-xs font-semibold uppercase tracking-[0.06em] text-[var(--muted)]">{label}</span>
      {children}
    </label>
  );
}

function emptyToNull(value: string) {
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function authorName(value: string | null | undefined) {
  if (!value) {
    return "";
  }
  const match = value.match(/^(.*?)\s*<[^>]+>$/);
  return (match?.[1] ?? value).trim();
}

function authorEmail(value: string | null | undefined) {
  if (!value) {
    return "";
  }
  const match = value.match(/<([^>]+)>/);
  return (match?.[1] ?? "").trim();
}
