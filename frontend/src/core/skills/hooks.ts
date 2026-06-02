"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { SkillFileReadRequest, SkillManageRequest } from "@/src/core/contracts";

import {
  getSkill,
  getSkillContent,
  getSkillCuratorAutomation,
  listSkillFiles,
  listSkills,
  manageSkill,
  manageSkillCurator,
  readSkillFile,
  reloadSkills,
  runSkillCuratorAutomation,
  runSkillCuratorMaintenance,
} from "./api";

export function useSkills() {
  return useQuery({
    queryKey: ["skills"],
    queryFn: listSkills,
    staleTime: 30000,
  });
}

export function useSkill(skillId: string | null) {
  return useQuery({
    queryKey: ["skills", skillId],
    queryFn: () => getSkill(skillId!),
    enabled: Boolean(skillId),
    staleTime: 60000,
  });
}

export function useSkillContent(skillId: string | null) {
  return useQuery({
    queryKey: ["skill-content", skillId],
    queryFn: () => getSkillContent(skillId!),
    enabled: Boolean(skillId),
    staleTime: 60000,
  });
}

export function useSkillFiles(skillId: string | null) {
  return useQuery({
    queryKey: ["skill-files", skillId],
    queryFn: () => listSkillFiles(skillId!),
    enabled: Boolean(skillId),
    staleTime: 60000,
  });
}

export function useSkillFile(skillId: string | null, relativePath: string | null, maxBytes = 64_000) {
  return useQuery({
    queryKey: ["skill-file", skillId, relativePath, maxBytes],
    queryFn: () => readSkillFile(skillId!, { relative_path: relativePath!, max_bytes: maxBytes } as SkillFileReadRequest),
    enabled: Boolean(skillId && relativePath),
    staleTime: 60000,
  });
}

export function useReloadSkills() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: reloadSkills,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["skills"] });
      await queryClient.invalidateQueries({ queryKey: ["skill-content"] });
      await queryClient.invalidateQueries({ queryKey: ["skill-files"] });
      await queryClient.invalidateQueries({ queryKey: ["skill-file"] });
      await queryClient.invalidateQueries({ queryKey: ["config-overview"] });
      await queryClient.invalidateQueries({ queryKey: ["threads"] });
      await queryClient.invalidateQueries({ queryKey: ["catalog-tools"] });
      await queryClient.invalidateQueries({ queryKey: ["tool-catalog"] });
    },
  });
}

export function useManageSkill() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: SkillManageRequest) => manageSkill(body),
    onSuccess: async (_result, variables) => {
      await queryClient.invalidateQueries({ queryKey: ["skills"] });
      await queryClient.invalidateQueries({ queryKey: ["skill-content"] });
      await queryClient.invalidateQueries({ queryKey: ["skill-files"] });
      await queryClient.invalidateQueries({ queryKey: ["skill-file"] });
      await queryClient.invalidateQueries({ queryKey: ["config-overview"] });
      await queryClient.invalidateQueries({ queryKey: ["threads"] });
      await queryClient.invalidateQueries({ queryKey: ["catalog-tools"] });
      await queryClient.invalidateQueries({ queryKey: ["tool-catalog"] });
      if (variables.skill_id) {
        await queryClient.invalidateQueries({ queryKey: ["skills", variables.skill_id] });
      }
    },
  });
}

export function useSkillProcedures(status?: string | null) {
  return useQuery({
    queryKey: ["skill-procedures", status ?? ""],
    queryFn: () => manageSkillCurator({ action: "procedures", outcome: status || null }),
    staleTime: 30000,
  });
}

export function usePromoteSkillProcedure() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ procedureId, skillId, force = false }: { procedureId: string; skillId?: string | null; force?: boolean }) =>
      manageSkillCurator({
        action: "promote_procedure",
        procedure_id: procedureId,
        skill_id: skillId ?? null,
        force,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["skill-procedures"] });
      await queryClient.invalidateQueries({ queryKey: ["skills"] });
      await queryClient.invalidateQueries({ queryKey: ["skill-content"] });
      await queryClient.invalidateQueries({ queryKey: ["skill-files"] });
      await queryClient.invalidateQueries({ queryKey: ["config-overview"] });
      await queryClient.invalidateQueries({ queryKey: ["threads"] });
      await queryClient.invalidateQueries({ queryKey: ["catalog-tools"] });
      await queryClient.invalidateQueries({ queryKey: ["tool-catalog"] });
    },
  });
}

export function useRejectSkillProcedure() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ procedureId, rationale = null, force = false }: { procedureId: string; rationale?: string | null; force?: boolean }) =>
      manageSkillCurator({
        action: "reject_procedure",
        procedure_id: procedureId,
        rationale,
        force,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["skill-procedures"] });
      await queryClient.invalidateQueries({ queryKey: ["skills"] });
      await queryClient.invalidateQueries({ queryKey: ["config-overview"] });
      await queryClient.invalidateQueries({ queryKey: ["threads"] });
      await queryClient.invalidateQueries({ queryKey: ["catalog-tools"] });
      await queryClient.invalidateQueries({ queryKey: ["tool-catalog"] });
    },
  });
}

export function useRestoreSkillProcedure() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ procedureId, rationale = null, force = false }: { procedureId: string; rationale?: string | null; force?: boolean }) =>
      manageSkillCurator({
        action: "restore_procedure",
        procedure_id: procedureId,
        rationale,
        force,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["skill-procedures"] });
      await queryClient.invalidateQueries({ queryKey: ["skills"] });
      await queryClient.invalidateQueries({ queryKey: ["config-overview"] });
      await queryClient.invalidateQueries({ queryKey: ["threads"] });
      await queryClient.invalidateQueries({ queryKey: ["catalog-tools"] });
      await queryClient.invalidateQueries({ queryKey: ["tool-catalog"] });
    },
  });
}

export function useRunSkillCuratorMaintenance() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: runSkillCuratorMaintenance,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["skill-curator-automation"] });
      await queryClient.invalidateQueries({ queryKey: ["skill-procedures"] });
      await queryClient.invalidateQueries({ queryKey: ["skills"] });
      await queryClient.invalidateQueries({ queryKey: ["skill-content"] });
      await queryClient.invalidateQueries({ queryKey: ["skill-files"] });
      await queryClient.invalidateQueries({ queryKey: ["config-overview"] });
      await queryClient.invalidateQueries({ queryKey: ["threads"] });
      await queryClient.invalidateQueries({ queryKey: ["catalog-tools"] });
      await queryClient.invalidateQueries({ queryKey: ["tool-catalog"] });
    },
  });
}

export function useSkillCuratorAutomation() {
  return useQuery({
    queryKey: ["skill-curator-automation"],
    queryFn: getSkillCuratorAutomation,
    staleTime: 30000,
  });
}

export function useRunSkillCuratorAutomation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: runSkillCuratorAutomation,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["skill-curator-automation"] });
      await queryClient.invalidateQueries({ queryKey: ["skill-procedures"] });
      await queryClient.invalidateQueries({ queryKey: ["skills"] });
      await queryClient.invalidateQueries({ queryKey: ["skill-content"] });
      await queryClient.invalidateQueries({ queryKey: ["skill-files"] });
      await queryClient.invalidateQueries({ queryKey: ["config-overview"] });
      await queryClient.invalidateQueries({ queryKey: ["threads"] });
      await queryClient.invalidateQueries({ queryKey: ["catalog-tools"] });
      await queryClient.invalidateQueries({ queryKey: ["tool-catalog"] });
    },
  });
}
