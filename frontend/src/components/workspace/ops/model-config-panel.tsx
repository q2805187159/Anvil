"use client";

import React, { useEffect, useMemo, useState } from "react";
import { ActivityIcon, HelpCircleIcon, Loader2, PencilIcon, PlusIcon, Trash2Icon, XIcon } from "lucide-react";

import { Badge, Button, Input, NativeSelect, ScrollArea, Tooltip } from "@/src/components/ui";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/src/components/ui/dialog";
import {
  useDeleteModelProvider,
  useModelProviderPresets,
  useModels,
  useTestModelProvider,
  useUpdateModelSelection,
  useUpsertModelProvider,
} from "@/src/core/models/hooks";
import type { ModelHealthCheckView, ModelProviderPresetView, ModelProviderUpsertRequest, ModelView } from "@/src/core/contracts";

import { OpsEmptyState, OpsJsonBlock, OpsPanelCard } from "./shared";
import { HealthStatusIndicator } from "./HealthStatusIndicator";
import type { OpsCopy } from "./types";

type ModelConfigPanelProps = {
  copy: OpsCopy;
};

type Draft = {
  provider: string;
  name: string;
  baseUrl: string;
  apiKey: string;
  apiKeyEnv: string;
  models: string[];
  newModel: string;
  defaultModel: string;
  advancedOpen: boolean;
  defaultReasoningEffort: string;
  contextWindowTokens: string;
  autoCompactThresholdTokens: string;
  maxTokens: string;
  temperature: string;
  topP: string;
  timeout: string;
  requestTimeout: string;
  defaultRequestTimeout: string;
  maxRetries: string;
  useResponsesApi: boolean;
  supportsToolCalling: boolean;
  supportsThinking: boolean;
  supportsReasoningEffort: boolean;
  supportsVision: boolean;
  supportsImageGeneration: boolean;
  outputVersion: string;
  defaultHeaders: string;
  extraBody: string;
  providerSettings: string;
  whenThinkingEnabled: string;
  whenThinkingDisabled: string;
  thinking: string;
  imageGenerationEndpoint: string;
  imageGeneration: string;
};

type InternalTaskOption = {
  value: string;
  providerName: string;
  modelName: string;
  label: string;
  active: boolean;
};

function formatNumber(value: number | null | undefined, emptyLabel: string) {
  if (!value) {
    return emptyLabel;
  }
  return new Intl.NumberFormat().format(value);
}

export function ModelConfigPanel({ copy }: ModelConfigPanelProps) {
  const models = useModels();
  const presets = useModelProviderPresets();
  const updateSelection = useUpdateModelSelection();
  const upsertModel = useUpsertModelProvider();
  const deleteModel = useDeleteModelProvider();
  const testModel = useTestModelProvider();
  const items = models.data ?? [];
  const presetItems = presets.data ?? [];
  const [formOpen, setFormOpen] = useState(false);
  const [editingModelName, setEditingModelName] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(() => draftFromPreset(presetItems[0]));
  const [internalTaskHealth, setInternalTaskHealth] = useState<ModelHealthCheckView | null>(null);
  const [pendingInternalTaskValue, setPendingInternalTaskValue] = useState<string | null>(null);
  const [modelHealthStatus, setModelHealthStatus] = useState<Record<string, ModelHealthCheckView | null>>({});
  const [testingModel, setTestingModel] = useState<string | null>(null);

  useEffect(() => {
    if (presetItems.length > 0 && formOpen && !draft.provider) {
      setDraft(draftFromPreset(presetItems[0]));
    }
  }, [draft.provider, formOpen, presetItems]);

  const selectedPreset = useMemo(
    () => presetItems.find((item) => item.provider === draft.provider) ?? presetItems[0],
    [draft.provider, presetItems],
  );

  function selectPreset(provider: string) {
    const preset = presetItems.find((item) => item.provider === provider);
    setDraft(draftFromPreset(preset));
  }

  function toggleImageGeneration(value: boolean) {
    setDraft((current) => ({
      ...current,
      supportsImageGeneration: value,
      imageGenerationEndpoint: value && !current.imageGenerationEndpoint.trim()
        ? suggestedImageGenerationEndpoint(current.provider)
        : current.imageGenerationEndpoint,
    }));
  }

  function openAddDialog() {
    setEditingModelName(null);
    setDraft(draftFromPreset(presetItems[0]));
    setFormOpen(true);
  }

  function openEditDialog(model: ModelView) {
    setEditingModelName(model.name);
    setDraft(draftFromModel(model));
    setFormOpen(true);
  }

  function closeAddDialog() {
    setFormOpen(false);
    setEditingModelName(null);
    setDraft(draftFromPreset(presetItems[0]));
  }

  async function submitDraft() {
    const name = draft.name.trim() || draft.provider;
    const body: ModelProviderUpsertRequest = {
      provider: draft.provider,
      name,
      base_url: emptyToNull(draft.baseUrl),
      api_key: emptyToNull(draft.apiKey),
      api_key_env: emptyToNull(draft.apiKeyEnv),
      models: dedupeStrings(draft.models),
      default_model: emptyToNull(draft.defaultModel),
      default_reasoning_effort: emptyToNull(draft.defaultReasoningEffort),
      context_window_tokens: numberOrNull(draft.contextWindowTokens),
      auto_compact_threshold_tokens: numberOrNull(draft.autoCompactThresholdTokens),
      max_tokens: numberOrNull(draft.maxTokens),
      temperature: numberOrNull(draft.temperature),
      top_p: numberOrNull(draft.topP),
      timeout: numberOrNull(draft.timeout),
      request_timeout: numberOrNull(draft.requestTimeout),
      default_request_timeout: numberOrNull(draft.defaultRequestTimeout),
      max_retries: numberOrNull(draft.maxRetries),
      use_responses_api: draft.useResponsesApi,
      supports_tool_calling: draft.supportsToolCalling,
      supports_thinking: draft.supportsThinking,
      supports_reasoning_effort: draft.supportsReasoningEffort,
      supports_vision: draft.supportsVision,
      supports_image_generation: draft.supportsImageGeneration,
      output_version: emptyToNull(draft.outputVersion),
      default_headers: jsonStringObjectOrUndefined(draft.defaultHeaders),
      extra_body: jsonObjectOrUndefined(draft.extraBody),
      provider_settings: jsonObjectOrUndefined(draft.providerSettings),
      when_thinking_enabled: jsonObjectOrUndefined(draft.whenThinkingEnabled),
      when_thinking_disabled: jsonObjectOrUndefined(draft.whenThinkingDisabled),
      thinking: jsonObjectOrUndefined(draft.thinking),
      image_generation: imageGenerationPayload(draft),
    };
    await upsertModel.mutateAsync({ name, body });
    closeAddDialog();
  }

  function addDraftModel() {
    const value = draft.newModel.trim();
    if (!value) {
      return;
    }
    setDraft((current) => ({
      ...current,
      models: dedupeStrings([...current.models, value]),
      defaultModel: current.defaultModel || value,
      newModel: "",
    }));
  }

  const internalTaskOptions = useMemo(
    () =>
      items.flatMap((model) =>
        dedupeStrings([model.internal_task_selected_model, ...modelOptions(model)]).map((modelName) => ({
          value: `${model.name}::${modelName}`,
          providerName: model.name,
          modelName,
          label: `${model.display_name || model.name} / ${modelName}`,
          active: Boolean(model.internal_task_default) && (model.internal_task_selected_model || modelOptions(model)[0] || "") === modelName,
        })),
      ),
    [items],
  );
  const activeInternalTaskValue =
    pendingInternalTaskValue ||
    internalTaskOptions.find((option) => option.active)?.value ||
    internalTaskOptions.find((option) => items.find((model) => model.name === option.providerName)?.internal_task_default)?.value ||
    "";
  const activeInternalTaskOption = internalTaskOptions.find((option) => option.value === activeInternalTaskValue);

  useEffect(() => {
    if (!pendingInternalTaskValue) {
      return;
    }
    if (internalTaskOptions.some((option) => option.active && option.value === pendingInternalTaskValue)) {
      setPendingInternalTaskValue(null);
    }
  }, [internalTaskOptions, pendingInternalTaskValue]);

  async function updateInternalTaskModel(value: string) {
    const option = internalTaskOptions.find((item) => item.value === value);
    if (!option) {
      return;
    }
    setPendingInternalTaskValue(option.value);
    setInternalTaskHealth(null);
    await updateSelection.mutateAsync({
      name: option.providerName,
      modelName: option.modelName,
      internalTaskDefault: true,
    });
    await runInternalTaskHealthCheck(option);
  }

  async function runInternalTaskHealthCheck(option = activeInternalTaskOption) {
    if (!option) {
      return;
    }
    const result = await testModel.mutateAsync({
      name: option.providerName,
      body: { model_name: option.modelName, subsystem: "background_tasks" },
    });
    setInternalTaskHealth(result);
  }

  async function testModelProvider(model: ModelView) {
    setTestingModel(model.name);
    try {
      const modelName = model.selected_model || model.model_name || model.default_model || modelOptions(model)[0] || "";
      const result = await testModel.mutateAsync({
        name: model.name,
        body: { model_name: modelName, subsystem: "user_threads" },
      });
      setModelHealthStatus((prev) => ({ ...prev, [model.name]: result }));
    } catch (error) {
      setModelHealthStatus((prev) => ({
        ...prev,
        [model.name]: makeLocalFailedHealthCheck(model, String(error), "user_threads"),
      }));
    } finally {
      setTestingModel(null);
    }
  }

  return (
    <ScrollArea className="h-full min-h-0 rounded-xl border border-[var(--line)] bg-[var(--panel)] px-4 py-4 shadow-[var(--shadow-card)]">
      <div className="space-y-4 pr-2">
        <div className="flex min-w-0 items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-[var(--ink)]">{copy.models.title}</h2>
            <p className="mt-1 text-xs text-[var(--muted)]">{items.length} {copy.overview.total}</p>
          </div>
          <Button size="sm" variant="primary" onClick={openAddDialog} className="text-[11px] whitespace-nowrap">
            <PlusIcon className="size-4" aria-hidden="true" />
            {copy.models.add}
          </Button>
        </div>

        {internalTaskOptions.length > 0 ? (
          <OpsPanelCard title={copy.models.internalTaskDefault}>
            <div className="grid gap-2 md:grid-cols-[minmax(0,22rem)_1fr] md:items-center">
              <NativeSelect
                className="w-full"
                aria-label={copy.models.internalTaskDefault}
                value={activeInternalTaskValue}
                disabled={updateSelection.isPending}
                onChange={(event) => void updateInternalTaskModel(event.target.value)}
              >
                {internalTaskOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </NativeSelect>
              <p className="text-xs leading-5 text-[var(--muted)]">{copy.models.internalTaskDefaultHint}</p>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Badge tone={internalTaskHealth ? (internalTaskHealth.ok ? "success" : "danger") : "neutral"}>
                {copy.models.internalTaskStatus}: {internalTaskHealth ? (internalTaskHealth.ok ? copy.models.modelReady : copy.models.modelUnavailable) : copy.models.modelUntested}
              </Badge>
              {internalTaskHealth?.message ? <span className="max-w-xl truncate text-xs text-[var(--muted)]">{internalTaskHealth.message}</span> : null}
              <Button type="button" size="sm" variant="secondary" disabled={!activeInternalTaskOption || testModel.isPending} onClick={() => void runInternalTaskHealthCheck()}>
                {testModel.isPending ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : <HelpCircleIcon className="size-4" aria-hidden="true" />}
                {copy.models.testInternalTaskModel}
              </Button>
            </div>
          </OpsPanelCard>
        ) : null}

        <Dialog open={formOpen} onOpenChange={(open) => (!open ? closeAddDialog() : undefined)}>
          <DialogContent className="w-[min(94vw,48rem)] p-0">
            <DialogHeader>
              <DialogTitle>{editingModelName ? copy.models.editFormTitle : copy.models.formTitle}</DialogTitle>
              <DialogDescription>{copy.models.title}</DialogDescription>
            </DialogHeader>
            <div className="max-h-[min(72vh,42rem)] overflow-auto px-5 py-4">
              <div className="grid gap-3 md:grid-cols-2">
                <Field label={copy.models.providerPreset} required description={copy.models.fieldHelp.providerPreset}>
                  <NativeSelect value={draft.provider} onChange={(event) => selectPreset(event.target.value)}>
                    {presetItems.map((preset) => (
                      <option key={preset.provider} value={preset.provider}>
                        {preset.display_name || preset.provider}
                      </option>
                    ))}
                  </NativeSelect>
                </Field>
                <Field label={copy.models.name}>
                  <Input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder={draft.provider || "openai"} />
                </Field>
                <Field label={copy.models.url}>
                  <Input value={draft.baseUrl} onChange={(event) => setDraft({ ...draft, baseUrl: event.target.value })} placeholder={selectedPreset?.base_url ?? ""} />
                </Field>
                <Field label={copy.models.apiKey} required description={copy.models.fieldHelp.apiKey}>
                  <Input type="password" value={draft.apiKey} onChange={(event) => setDraft({ ...draft, apiKey: event.target.value })} placeholder={editingModelName ? copy.models.providerDefault : draft.apiKeyEnv || selectedPreset?.api_key_env || ""} />
                </Field>
                <Field label={copy.models.apiKeyEnv} description={copy.models.fieldHelp.apiKeyEnv}>
                  <Input value={draft.apiKeyEnv} onChange={(event) => setDraft({ ...draft, apiKeyEnv: event.target.value })} placeholder={selectedPreset?.api_key_env ?? ""} />
                </Field>
              </div>

              <div className="mt-4 rounded-xl border border-[var(--line)] bg-[var(--panel)] p-3">
                <div className="mb-3 inline-flex items-center gap-1 text-xs font-semibold text-[var(--muted)]">
                  <span>{copy.models.models}<span className="ml-0.5 text-[var(--danger)]">*</span></span>
                  <HelpTooltip content={copy.models.fieldHelp.models} />
                </div>
                <div className="flex gap-2">
                  <Input
                    value={draft.newModel}
                    onChange={(event) => setDraft({ ...draft, newModel: event.target.value })}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        addDraftModel();
                      }
                    }}
                    placeholder={copy.models.modelPlaceholder}
                  />
                  <Button type="button" onClick={addDraftModel}>{copy.models.addModel}</Button>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  {draft.models.map((modelName) => (
                    <label key={modelName} className="inline-flex max-w-full items-center gap-2 rounded-[0.7rem] border border-[var(--line)] bg-[var(--panel-muted)] px-2.5 py-1.5 text-xs text-[var(--ink)]">
                      <input
                        type="radio"
                        name="default-model"
                        checked={draft.defaultModel === modelName}
                        onChange={() => setDraft({ ...draft, defaultModel: modelName })}
                      />
                      <span className="max-w-[18rem] truncate">{modelName}</span>
                      <button
                        type="button"
                        className="text-[var(--muted)] hover:text-[var(--danger)]"
                        onClick={() =>
                          setDraft((current) => {
                            const nextModels = current.models.filter((item) => item !== modelName);
                            return {
                              ...current,
                              models: nextModels,
                              defaultModel: current.defaultModel === modelName ? nextModels[0] ?? "" : current.defaultModel,
                            };
                          })
                        }
                      >
                        <XIcon className="size-3" aria-hidden="true" />
                      </button>
                    </label>
                  ))}
                </div>
              </div>

              <button
                type="button"
                className="mt-4 text-xs font-semibold text-[var(--muted)] hover:text-[var(--ink)]"
                onClick={() => setDraft((current) => ({ ...current, advancedOpen: !current.advancedOpen }))}
              >
                {copy.models.advanced}
              </button>
              {draft.advancedOpen ? (
                <div className="mt-3 grid gap-3 md:grid-cols-3">
                  <Field label={copy.models.defaultReasoningEffort} description={copy.models.fieldHelp.defaultReasoningEffort}>
                    <NativeSelect value={draft.defaultReasoningEffort} onChange={(event) => setDraft({ ...draft, defaultReasoningEffort: event.target.value })}>
                      <option value="">{selectedPreset?.default_reasoning_effort || copy.models.providerDefault}</option>
                      {REASONING_EFFORT_OPTIONS.map((option) => <option key={option} value={option}>{option}</option>)}
                    </NativeSelect>
                  </Field>
                  <NumberField label={copy.models.contextWindow} description={copy.models.fieldHelp.contextWindow} value={draft.contextWindowTokens} placeholder="1000000" onChange={(value) => setDraft({ ...draft, contextWindowTokens: value })} />
                  <NumberField label={copy.models.compactThreshold} description={copy.models.fieldHelp.compactThreshold} value={draft.autoCompactThresholdTokens} placeholder="900000" onChange={(value) => setDraft({ ...draft, autoCompactThresholdTokens: value })} />
                  <NumberField label="max_tokens" description={copy.models.fieldHelp.maxTokens} value={draft.maxTokens} placeholder={stringDefault(defaultValue(selectedPreset, "max_tokens"))} onChange={(value) => setDraft({ ...draft, maxTokens: value })} />
                  <NumberField label="temperature" description={copy.models.fieldHelp.temperature} value={draft.temperature} placeholder={stringDefault(defaultValue(selectedPreset, "temperature"))} onChange={(value) => setDraft({ ...draft, temperature: value })} />
                  <NumberField label="top_p" description={copy.models.fieldHelp.topP} value={draft.topP} placeholder={stringDefault(defaultValue(selectedPreset, "top_p"))} onChange={(value) => setDraft({ ...draft, topP: value })} />
                  <NumberField label="timeout" description={copy.models.fieldHelp.timeout} value={draft.timeout} placeholder={stringDefault(defaultValue(selectedPreset, "timeout"))} onChange={(value) => setDraft({ ...draft, timeout: value })} />
                  <NumberField label="request_timeout" description={copy.models.fieldHelp.requestTimeout} value={draft.requestTimeout} placeholder={stringDefault(defaultValue(selectedPreset, "request_timeout"))} onChange={(value) => setDraft({ ...draft, requestTimeout: value })} />
                  <NumberField label="default_request_timeout" description={copy.models.fieldHelp.defaultRequestTimeout} value={draft.defaultRequestTimeout} placeholder={stringDefault(defaultValue(selectedPreset, "default_request_timeout"))} onChange={(value) => setDraft({ ...draft, defaultRequestTimeout: value })} />
                  <NumberField label="max_retries" description={copy.models.fieldHelp.maxRetries} value={draft.maxRetries} placeholder={stringDefault(defaultValue(selectedPreset, "max_retries"))} onChange={(value) => setDraft({ ...draft, maxRetries: value })} />
                  <TextField label="output_version" description={copy.models.fieldHelp.outputVersion} value={draft.outputVersion} placeholder={stringDefault(defaultValue(selectedPreset, "output_version"))} onChange={(value) => setDraft({ ...draft, outputVersion: value })} />
                  <ToggleField label="use_responses_api" description={copy.models.fieldHelp.useResponsesApi} checked={draft.useResponsesApi} onChange={(value) => setDraft({ ...draft, useResponsesApi: value })} />
                  <ToggleField label="supports_tool_calling" description={copy.models.fieldHelp.supportsToolCalling} checked={draft.supportsToolCalling} onChange={(value) => setDraft({ ...draft, supportsToolCalling: value })} />
                  <ToggleField label="supports_thinking" description={copy.models.fieldHelp.supportsThinking} checked={draft.supportsThinking} onChange={(value) => setDraft({ ...draft, supportsThinking: value })} />
                  <ToggleField label="supports_reasoning_effort" description={copy.models.fieldHelp.supportsReasoningEffort} checked={draft.supportsReasoningEffort} onChange={(value) => setDraft({ ...draft, supportsReasoningEffort: value })} />
                  <ToggleField label="supports_vision" description={copy.models.fieldHelp.supportsVision} checked={draft.supportsVision} onChange={(value) => setDraft({ ...draft, supportsVision: value })} />
                  <ToggleField label="supports_image_generation" description={copy.models.fieldHelp.supportsImageGeneration} checked={draft.supportsImageGeneration} onChange={toggleImageGeneration} />
                  {draft.supportsImageGeneration ? (
                    <TextField label="image_generation.endpoint" required description={copy.models.fieldHelp.imageGenerationEndpoint} value={draft.imageGenerationEndpoint} placeholder={suggestedImageGenerationEndpoint(draft.provider)} onChange={(value) => setDraft({ ...draft, imageGenerationEndpoint: value })} />
                  ) : null}
                  <JsonField label="default_headers" description={copy.models.fieldHelp.defaultHeaders} value={draft.defaultHeaders} onChange={(value) => setDraft({ ...draft, defaultHeaders: value })} />
                  <JsonField label="extra_body" description={copy.models.fieldHelp.extraBody} value={draft.extraBody} onChange={(value) => setDraft({ ...draft, extraBody: value })} />
                  <JsonField label="provider_settings" description={copy.models.fieldHelp.providerSettings} value={draft.providerSettings} onChange={(value) => setDraft({ ...draft, providerSettings: value })} />
                  <JsonField label="when_thinking_enabled" description={copy.models.fieldHelp.whenThinkingEnabled} value={draft.whenThinkingEnabled} onChange={(value) => setDraft({ ...draft, whenThinkingEnabled: value })} />
                  <JsonField label="when_thinking_disabled" description={copy.models.fieldHelp.whenThinkingDisabled} value={draft.whenThinkingDisabled} onChange={(value) => setDraft({ ...draft, whenThinkingDisabled: value })} />
                  <JsonField label="thinking" description={copy.models.fieldHelp.thinking} value={draft.thinking} onChange={(value) => setDraft({ ...draft, thinking: value })} />
                  <JsonField label="image_generation" description={copy.models.fieldHelp.imageGeneration} value={draft.imageGeneration} onChange={(value) => setDraft({ ...draft, imageGeneration: value })} />
                </div>
              ) : null}
            </div>

            <div className="flex justify-end gap-2 border-t border-[var(--line)] px-5 py-4">
              {upsertModel.isPending ? <Loader2 className="mr-auto size-4 animate-spin self-center text-[var(--muted)]" aria-hidden="true" /> : null}
              <Button type="button" variant="ghost" onClick={closeAddDialog} disabled={upsertModel.isPending}>{copy.models.cancel}</Button>
              <Button type="button" variant="primary" onClick={() => void submitDraft()} disabled={upsertModel.isPending || !draft.provider || (!editingModelName && !draft.apiKey.trim()) || draft.models.length === 0 || (draft.supportsImageGeneration && !draft.imageGenerationEndpoint.trim())}>
                {copy.models.save}
              </Button>
            </div>
          </DialogContent>
        </Dialog>

        {items.length === 0 ? <OpsEmptyState text={copy.models.noResults} /> : null}
        {items.map((model) => (
          <article
            key={model.name}
            className="relative rounded-xl border border-[var(--line)] bg-[var(--panel-muted)] p-4 transition-all duration-300 hover:border-[var(--border-active)] hover:shadow-md group"
          >
            <Button
              size="icon"
              variant="ghost"
              className="absolute right-12 top-3"
              aria-label={`${copy.models.edit} ${model.display_name || model.name}`}
              onClick={() => openEditDialog(model)}
              disabled={upsertModel.isPending}
            >
              <PencilIcon className="size-4" aria-hidden="true" />
            </Button>
            <Button
              size="icon"
              variant="ghost"
              className="absolute right-3 top-3 text-[var(--danger)] hover:text-[var(--danger)]"
              aria-label={`${copy.models.delete} ${model.display_name || model.name}`}
              onClick={() => void deleteModel.mutateAsync(model.name)}
              disabled={deleteModel.isPending}
            >
              <Trash2Icon className="size-4" aria-hidden="true" />
            </Button>
            <div className="flex min-w-0 flex-col gap-3 pr-10 md:flex-row md:items-start md:justify-between">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <h3 className="truncate text-base font-semibold text-[var(--ink)]">{model.display_name || model.name}</h3>
                  <Button
                    size="sm"
                    variant="secondary"
                    className="text-[11px] transition-transform hover:scale-105"
                    disabled={testingModel === model.name}
                    onClick={() => void testModelProvider(model)}
                  >
                    {testingModel === model.name ? (
                      <Loader2 className="size-3 animate-spin" aria-hidden="true" />
                    ) : (
                      <ActivityIcon className="size-3" aria-hidden="true" />
                    )}
                    {copy.models.testModel}
                  </Button>
                  {modelHealthStatus[model.name] ? (
                    <HealthStatusIndicator
                      status={modelHealthStatus[model.name]?.ok ? "operational" : "failed"}
                      message={modelHealthStatus[model.name]?.message ?? undefined}
                      labels={{
                        operational: copy.models.modelReady,
                        degraded: copy.models.modelUntested,
                        failed: copy.models.modelUnavailable,
                      }}
                    />
                  ) : null}
                </div>
                <p className="mt-1 text-sm leading-6 text-[var(--muted)]">{model.description || model.provider}</p>
              </div>
              <div className="flex min-w-0 shrink-0 flex-col gap-2 md:items-end">
                <ModelSelectionControl
                  model={model}
                  copy={copy}
                  pending={updateSelection.isPending && updateSelection.variables?.name === model.name}
                  onSelectModel={(modelName) =>
                    updateSelection.mutate({
                      name: model.name,
                      modelName,
                    })
                  }
                  onSelectReasoningEffort={(defaultReasoningEffort) => {
                    const options = modelOptions(model);
                    updateSelection.mutate({
                      name: model.name,
                      modelName: model.selected_model || model.model_name || model.default_model || options[0] || "",
                      defaultReasoningEffort,
                    });
                  }}
                />
              </div>
            </div>

            <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
              <OpsPanelCard title={copy.models.provider}>
                <OpsJsonBlock value={{ provider: model.provider, source: model.source ?? null, use: model.use ?? null }} emptyLabel={copy.common.none} />
              </OpsPanelCard>
              <OpsPanelCard title={copy.models.selected}>
                <OpsJsonBlock value={{ selected_model: model.selected_model ?? null, model_name: model.model_name ?? null, default_model: model.default_model ?? null }} emptyLabel={copy.common.none} />
              </OpsPanelCard>
              <OpsPanelCard title={copy.models.contextWindow}>
                <OpsJsonBlock value={{ context_window_tokens: formatNumber(model.context_window_tokens, copy.common.none), auto_compact_threshold_tokens: formatNumber(model.auto_compact_threshold_tokens, copy.common.none) }} emptyLabel={copy.common.none} />
              </OpsPanelCard>
              <OpsPanelCard title={copy.models.endpoint}>
                <OpsJsonBlock value={{ base_url: model.base_url ?? null, timeout: model.timeout ?? null, max_retries: model.max_retries ?? null }} emptyLabel={copy.common.none} />
              </OpsPanelCard>
            </div>

            <div className="mt-3 grid gap-3 lg:grid-cols-2">
              <OpsPanelCard title={copy.models.capabilities}>
                <OpsJsonBlock
                  value={{
                    tool_calling: model.supports_tool_calling,
                    thinking: model.supports_thinking,
                    reasoning_effort: model.supports_reasoning_effort,
                    vision: model.supports_vision,
                    image_generation: model.supports_image_generation,
                    default_reasoning_effort: model.default_reasoning_effort ?? null,
                    output_version: model.output_version ?? null,
                  }}
                  emptyLabel={copy.common.none}
                />
              </OpsPanelCard>
              <OpsPanelCard title={copy.models.diagnostics}>
                <OpsJsonBlock value={model.diagnostics} emptyLabel={copy.common.none} />
              </OpsPanelCard>
            </div>
          </article>
        ))}
      </div>
    </ScrollArea>
  );
}

function Field({ label, children, required = false, description }: React.PropsWithChildren<{ label: string; required?: boolean; description?: string }>) {
  return (
    <label className="grid min-w-0 gap-1.5 text-[11px] font-medium text-[var(--muted)]">
      <span className="inline-flex items-center gap-1">
        <span>{label}{required ? <span className="ml-0.5 text-[var(--danger)]">*</span> : null}</span>
        {description ? <HelpTooltip content={description} /> : null}
      </span>
      {children}
    </label>
  );
}

function HelpTooltip({ content }: { content: string }) {
  return (
    <Tooltip content={<span className="block max-w-[22rem] leading-5">{content}</span>}>
      <button type="button" className="inline-flex size-4 items-center justify-center rounded-full text-[var(--muted)] transition hover:text-[var(--ink)]" aria-label={content}>
        <HelpCircleIcon className="size-3.5" aria-hidden="true" />
      </button>
    </Tooltip>
  );
}

function NumberField({ label, value, placeholder, description, onChange }: { label: string; value: string; placeholder?: string; description?: string; onChange(value: string): void }) {
  return (
    <Field label={label} description={description}>
      <Input inputMode="numeric" value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} />
    </Field>
  );
}

function TextField({ label, value, placeholder, description, required = false, onChange }: { label: string; value: string; placeholder?: string; description?: string; required?: boolean; onChange(value: string): void }) {
  return (
    <Field label={label} required={required} description={description}>
      <Input aria-label={label} value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} />
    </Field>
  );
}

function ToggleField({ label, checked, description, onChange }: { label: string; checked: boolean; description?: string; onChange(value: boolean): void }) {
  return (
    <Field label={label} description={description}>
      <button
        type="button"
        role="switch"
        aria-label={label}
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className="flex h-9 items-center justify-between rounded-[0.7rem] border border-[var(--line)] bg-[var(--panel-muted)] px-2.5 text-xs text-[var(--ink)] transition hover:bg-[var(--panel)]"
      >
        <span>{checked ? "true" : "false"}</span>
        <span className={`h-4 w-7 rounded-full p-0.5 transition ${checked ? "bg-[var(--primary)]" : "bg-[var(--line)]"}`}>
          <span className={`block size-3 rounded-full bg-white transition ${checked ? "translate-x-3" : ""}`} />
        </span>
      </button>
    </Field>
  );
}

function JsonField({ label, value, description, onChange }: { label: string; value: string; description?: string; onChange(value: string): void }) {
  return (
    <Field label={label} description={description}>
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="{}"
        rows={3}
        className="min-h-20 rounded-[0.7rem] border border-[var(--line)] bg-[var(--panel-muted)] px-2.5 py-2 font-[var(--mono-font)] text-xs text-[var(--ink)] outline-none transition focus:border-[var(--primary)] focus:ring-2 focus:ring-[var(--primary)]/15"
      />
    </Field>
  );
}

function draftFromPreset(preset?: ModelProviderPresetView): Draft {
  const provider = preset?.provider ?? "";
  const models = dedupeStrings([...(preset?.model_catalog ?? []), preset?.default_model ?? ""]);
  return {
    provider,
    name: provider,
    baseUrl: "",
    apiKey: "",
    apiKeyEnv: preset?.api_key_env ?? "",
    models,
    newModel: "",
    defaultModel: preset?.default_model || models[0] || "",
    advancedOpen: false,
    defaultReasoningEffort: "",
    contextWindowTokens: "",
    autoCompactThresholdTokens: "",
    maxTokens: "",
    temperature: "",
    topP: "",
    timeout: "",
    requestTimeout: "",
    defaultRequestTimeout: "",
    maxRetries: "",
    useResponsesApi: false,
    supportsToolCalling: preset?.supports_tool_calling ?? true,
    supportsThinking: preset?.supports_thinking ?? false,
    supportsReasoningEffort: false,
    supportsVision: preset?.supports_vision ?? false,
    supportsImageGeneration: preset?.supports_image_generation ?? false,
    outputVersion: stringDefault(defaultValue(preset, "output_version")),
    defaultHeaders: "{}",
    extraBody: "{}",
    providerSettings: "{}",
    whenThinkingEnabled: "{}",
    whenThinkingDisabled: "{}",
    thinking: "{}",
    imageGenerationEndpoint: preset?.supports_image_generation ? suggestedImageGenerationEndpoint(provider) : "",
    imageGeneration: "{}",
  };
}

function draftFromModel(model: ModelView): Draft {
  const models = modelOptions(model);
  const defaultModel = model.default_model || model.selected_model || model.model_name || models[0] || "";
  return {
    provider: model.provider,
    name: model.name,
    baseUrl: model.base_url ?? "",
    apiKey: "",
    apiKeyEnv: model.api_key_env ?? "",
    models,
    newModel: "",
    defaultModel,
    advancedOpen: true,
    defaultReasoningEffort: model.default_reasoning_effort ?? "",
    contextWindowTokens: stringDefault(model.context_window_tokens),
    autoCompactThresholdTokens: stringDefault(model.auto_compact_threshold_tokens),
    maxTokens: stringDefault(model.max_tokens),
    temperature: stringDefault(model.temperature),
    topP: stringDefault(model.top_p),
    timeout: stringDefault(model.timeout),
    requestTimeout: stringDefault(model.request_timeout),
    defaultRequestTimeout: stringDefault(model.default_request_timeout),
    maxRetries: stringDefault(model.max_retries),
    useResponsesApi: model.use_responses_api ?? false,
    supportsToolCalling: model.supports_tool_calling ?? true,
    supportsThinking: model.supports_thinking ?? false,
    supportsReasoningEffort: model.supports_reasoning_effort ?? false,
    supportsVision: model.supports_vision ?? false,
    supportsImageGeneration: model.supports_image_generation ?? false,
    outputVersion: model.output_version ?? "",
    defaultHeaders: "{}",
    extraBody: "{}",
    providerSettings: "{}",
    whenThinkingEnabled: "{}",
    whenThinkingDisabled: "{}",
    thinking: "{}",
    imageGenerationEndpoint: imageGenerationEndpointFromConfig(model.image_generation),
    imageGeneration: JSON.stringify(imageGenerationConfigWithoutEndpoint(model.image_generation), null, 2),
  };
}

function modelOptions(model: { model_catalog?: string[]; selected_model?: string | null; model_name?: string | null; default_model?: string | null; internal_task_selected_model?: string | null }) {
  const ordered = [...(model.model_catalog ?? []), model.selected_model, model.model_name, model.default_model].filter((value): value is string => Boolean(value));
  return Array.from(new Set(ordered));
}

function makeLocalFailedHealthCheck(model: ModelView, message: string, subsystem: string): ModelHealthCheckView {
  return {
    name: model.name,
    model_name: model.selected_model || model.model_name || model.default_model || modelOptions(model)[0] || "",
    subsystem,
    ok: false,
    status: "failed",
    message,
    checked_at: new Date().toISOString(),
    config_fingerprint: "",
  };
}

const REASONING_EFFORT_OPTIONS = ["minimal", "low", "medium", "high", "xhigh"];

function ModelSelectionControl({
  model,
  copy,
  pending,
  onSelectModel,
  onSelectReasoningEffort,
}: {
  model: {
    name: string;
    display_name?: string | null;
    available?: boolean;
    provider_kind?: string | null;
    selected_model?: string | null;
    model_name?: string | null;
    default_model?: string | null;
    model_catalog?: string[];
    internal_task_selected_model?: string | null;
    supports_reasoning_effort?: boolean;
    default_reasoning_effort?: string | null;
    internal_task_default?: boolean;
  };
  copy: OpsCopy;
  pending: boolean;
  onSelectModel: (modelName: string) => void;
  onSelectReasoningEffort: (value: string | null) => void;
}) {
  const options = modelOptions(model);
  const current = model.selected_model || model.model_name || model.default_model || options[0] || "";
  return (
    <div className="flex min-w-0 flex-col items-start gap-2 md:items-end">
      <div className="flex min-w-0 flex-wrap items-end gap-2 md:justify-end">
        <label className="grid min-w-0 gap-1.5 text-[11px] font-medium text-[var(--muted)]">
          <span>{copy.models.defaultModel}</span>
          <NativeSelect className="w-[12rem]" aria-label={`${model.name} ${copy.models.defaultModel}`} compact value={current} disabled={pending || options.length === 0} onChange={(event) => onSelectModel(event.target.value)}>
            {options.map((option) => <option key={option} value={option}>{option}</option>)}
          </NativeSelect>
        </label>
        <label className="grid min-w-0 gap-1.5 text-[11px] font-medium text-[var(--muted)]">
          <span>{copy.models.defaultReasoningEffort}</span>
          <NativeSelect aria-label={`${model.name} ${copy.models.defaultReasoningEffort}`} className="w-[9rem]" compact value={model.default_reasoning_effort ?? ""} disabled={pending} onChange={(event) => onSelectReasoningEffort(event.target.value || null)}>
            <option value="">{copy.models.providerDefault}</option>
            {REASONING_EFFORT_OPTIONS.map((option) => <option key={option} value={option}>{option}</option>)}
          </NativeSelect>
        </label>
        <div className="flex flex-wrap gap-2 pb-1 md:justify-end">
          <Badge tone={model.available === false ? "warning" : "success"}>
            {model.available === false ? copy.common.disabled : copy.common.enabled}
          </Badge>
          {model.provider_kind ? <Badge>{model.provider_kind}</Badge> : null}
        </div>
      </div>
      {pending ? (
        <div className="flex items-center gap-1 text-[11px] text-[var(--muted)]">
          <Loader2 className="size-3 animate-spin" aria-hidden="true" />
          {copy.common.loading}
        </div>
      ) : null}
    </div>
  );
}

function emptyToNull(value: string) {
  const stripped = value.trim();
  return stripped ? stripped : null;
}

function numberOrNull(value: string) {
  const stripped = value.trim();
  if (!stripped) {
    return null;
  }
  const number = Number(stripped);
  return Number.isFinite(number) ? number : null;
}

function jsonObjectOrUndefined(value: string) {
  const stripped = value.trim();
  if (!stripped || stripped === "{}") {
    return undefined;
  }
  try {
    const parsed = JSON.parse(stripped) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : undefined;
  } catch {
    return undefined;
  }
}

function jsonStringObjectOrUndefined(value: string) {
  const parsed = jsonObjectOrUndefined(value);
  if (!parsed) {
    return undefined;
  }
  return Object.fromEntries(
    Object.entries(parsed).map(([key, entry]) => [key, String(entry)]),
  );
}

function imageGenerationPayload(draft: Draft) {
  const parsed = jsonObjectOrUndefined(draft.imageGeneration) ?? {};
  if (!draft.supportsImageGeneration) {
    return Object.keys(parsed).length > 0 ? parsed : undefined;
  }
  const endpoint = draft.imageGenerationEndpoint.trim();
  if (!endpoint) {
    return Object.keys(parsed).length > 0 ? parsed : undefined;
  }
  return { ...parsed, endpoint };
}

function imageGenerationEndpointFromConfig(value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return "";
  }
  const endpoint = (value as Record<string, unknown>).endpoint;
  return typeof endpoint === "string" ? endpoint : "";
}

function imageGenerationConfigWithoutEndpoint(value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }
  const { endpoint, ...rest } = value as Record<string, unknown>;
  return rest;
}

function suggestedImageGenerationEndpoint(provider: string) {
  const normalized = provider.trim().toLowerCase().replace(/-/g, "_");
  if (normalized.includes("minimax")) {
    return "/image_generation";
  }
  if (normalized.includes("openai")) {
    return "/images/generations";
  }
  return "";
}

function defaultValue(preset: ModelProviderPresetView | undefined, key: string) {
  return preset?.defaults?.[key];
}

function dedupeStrings(values: Array<string | null | undefined>) {
  return Array.from(new Set(values.map((value) => (value ?? "").trim()).filter(Boolean)));
}

function stringDefault(value: unknown) {
  return value === null || value === undefined ? "" : String(value);
}
