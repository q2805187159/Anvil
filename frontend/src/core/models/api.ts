import type {
  ModelProviderDeleteView,
  ModelHealthCheckRequest,
  ModelHealthCheckView,
  ModelProviderPresetView,
  ModelProviderUpsertRequest,
  ModelProviderUpsertView,
  ModelSelectionUpdateRequest,
  ModelSelectionUpdateView,
  ModelView,
} from "@/src/core/contracts";
import { apiRequest } from "@/src/core/api/client";

export function listModels() {
  return apiRequest<ModelView[]>("/models");
}

export function listModelProviderPresets() {
  return apiRequest<ModelProviderPresetView[]>("/models/presets");
}

export function upsertModelProvider(name: string, body: ModelProviderUpsertRequest) {
  return apiRequest<ModelProviderUpsertView>(`/models/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function deleteModelProvider(name: string) {
  return apiRequest<ModelProviderDeleteView>(`/models/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
}

export function updateModelSelection(name: string, body: ModelSelectionUpdateRequest) {
  return apiRequest<ModelSelectionUpdateView>(`/models/${encodeURIComponent(name)}/selection`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function testModelProvider(name: string, body: ModelHealthCheckRequest) {
  return apiRequest<ModelHealthCheckView>(`/models/${encodeURIComponent(name)}/test`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
