import type {
  SkillContentView,
  SkillCuratorAutomationRequest,
  SkillCuratorAutomationRunResponse,
  SkillCuratorAutomationStatusResponse,
  SkillFileIndexView,
  SkillFileReadRequest,
  SkillFileReadView,
  SkillCuratorRequest,
  SkillCuratorMaintenanceRequest,
  SkillListItemView,
  SkillManageRequest,
  SkillManageResultView,
  SkillView,
} from "@/src/core/contracts";
import { apiRequest } from "@/src/core/api/client";

export function listSkills() {
  return apiRequest<SkillListItemView[]>("/skills");
}

export function getSkill(skillId: string) {
  return apiRequest<SkillView>(`/skills/${encodeURIComponent(skillId)}`);
}

export function getSkillContent(skillId: string) {
  return apiRequest<SkillContentView>(`/skills/${encodeURIComponent(skillId)}/content`);
}

export function listSkillFiles(skillId: string) {
  return apiRequest<SkillFileIndexView>(`/skills/${encodeURIComponent(skillId)}/files`);
}

export function readSkillFile(skillId: string, body: SkillFileReadRequest) {
  return apiRequest<SkillFileReadView>(`/skills/${encodeURIComponent(skillId)}/files/read`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function reloadSkills() {
  return apiRequest<Record<string, unknown>>("/skills/reload", {
    method: "POST",
  });
}

export function manageSkill(body: SkillManageRequest) {
  return apiRequest<SkillManageResultView>("/skills/manage", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function manageSkillCurator(body: SkillCuratorRequest) {
  return apiRequest<Record<string, unknown>>("/skills/curator", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getSkillCuratorAutomation() {
  return apiRequest<SkillCuratorAutomationStatusResponse>("/skills/curator/automation");
}

export function runSkillCuratorAutomation(body: SkillCuratorAutomationRequest = {}) {
  return apiRequest<SkillCuratorAutomationRunResponse>("/skills/curator/automation/run", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function runSkillCuratorMaintenance(body: SkillCuratorMaintenanceRequest) {
  return apiRequest<Record<string, unknown>>("/skills/curator/maintenance", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
