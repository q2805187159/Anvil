"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { deleteModelProvider, listModelProviderPresets, listModels, testModelProvider, updateModelSelection, upsertModelProvider } from "./api";
import type { ModelHealthCheckRequest, ModelProviderUpsertRequest } from "@/src/core/contracts";

export function useModels() {
  return useQuery({
    queryKey: ["models"],
    queryFn: listModels,
    staleTime: 15000,
  });
}

export function useModelProviderPresets() {
  return useQuery({
    queryKey: ["model-provider-presets"],
    queryFn: listModelProviderPresets,
    staleTime: 60000,
  });
}

function invalidateModelConfig(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ["models"] });
  void queryClient.invalidateQueries({ queryKey: ["config-overview"] });
  void queryClient.invalidateQueries({ queryKey: ["gateway-health"] });
  void queryClient.invalidateQueries({ queryKey: ["threads"] });
  void queryClient.invalidateQueries({ queryKey: ["thread-state"] });
  void queryClient.invalidateQueries({ queryKey: ["tool-catalog"] });
  void queryClient.invalidateQueries({ queryKey: ["catalog-tools"] });
  void queryClient.invalidateQueries({ queryKey: ["extensions"] });
  void queryClient.invalidateQueries({ queryKey: ["mcp-servers"] });
  void queryClient.invalidateQueries({ queryKey: ["plugins"] });
}

export function useUpsertModelProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, body }: { name: string; body: ModelProviderUpsertRequest }) => upsertModelProvider(name, body),
    onSuccess: () => invalidateModelConfig(queryClient),
  });
}

export function useDeleteModelProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => deleteModelProvider(name),
    onSuccess: () => invalidateModelConfig(queryClient),
  });
}

export function useUpdateModelSelection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      name,
      modelName,
      defaultReasoningEffort,
      internalTaskDefault,
    }: {
      name: string;
      modelName: string;
      defaultReasoningEffort?: string | null;
      internalTaskDefault?: boolean;
    }) =>
      updateModelSelection(name, {
        model_name: modelName,
        ...(defaultReasoningEffort === undefined ? {} : { default_reasoning_effort: defaultReasoningEffort }),
        ...(internalTaskDefault === undefined ? {} : { internal_task_default: internalTaskDefault }),
      }),
    onSuccess: () => invalidateModelConfig(queryClient),
  });
}

export function useTestModelProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, body }: { name: string; body: ModelHealthCheckRequest }) => testModelProvider(name, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["models"] });
    },
  });
}
