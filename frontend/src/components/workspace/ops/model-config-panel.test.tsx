import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { opsCopy } from "./types";
import { ModelConfigPanel } from "./model-config-panel";
import { TooltipProvider } from "@/src/components/ui";

const mutateMock = vi.fn();
const upsertMock = vi.fn().mockResolvedValue({});
const deleteMock = vi.fn().mockResolvedValue({});
const testModelMock = vi.fn().mockResolvedValue({
  name: "minimax",
  model_name: "MiniMax-M2.7",
  subsystem: "background_tasks",
  ok: true,
  status: "ready",
  message: "OK",
  checked_at: "2026-05-21T00:00:00Z",
  latency_ms: 12,
  config_fingerprint: "cfg",
});

vi.mock("@/src/core/models/hooks", () => ({
  useModels: () => ({
    data: [
      {
        name: "openai",
        display_name: "OpenAI",
        description: "OpenAI compatible provider",
        available: true,
        source: "config",
        use: null,
        provider: "openai",
        provider_kind: "openai",
        model_name: "gpt-5.4",
        default_model: "gpt-5.4",
        selected_model: "gpt-5.4",
        model_catalog: ["gpt-5.4", "gpt-5.5"],
        context_window_tokens: 1000,
        auto_compact_threshold_tokens: 800,
        max_tokens: null,
        temperature: null,
        top_p: null,
        model_context_windows: {},
        model_auto_compact_thresholds: {},
        base_url: null,
        api_key_env: "OPENAI_API_KEY",
        default_reasoning_effort: null,
        supports_tool_calling: true,
        supports_thinking: true,
        supports_reasoning_effort: undefined,
        supports_vision: false,
        supports_image_generation: false,
        timeout: 30,
        request_timeout: null,
        default_request_timeout: null,
        max_retries: 2,
        use_responses_api: null,
        output_version: null,
        image_generation: null,
        diagnostics: [],
        capabilities: {},
        internal_task_default: true,
        internal_task_selected_model: "gpt-5.4",
      },
      {
        name: "minimax",
        display_name: "MiniMax",
        description: "MiniMax provider",
        available: true,
        source: "config",
        use: null,
        provider: "minimax",
        provider_kind: "openai_compatible",
        model_name: "MiniMax-M2.7",
        default_model: "MiniMax-M2.7",
        selected_model: "MiniMax-M2.7",
        model_catalog: ["mimo-v2-flash", "MiniMax-M2.7"],
        context_window_tokens: 1000,
        auto_compact_threshold_tokens: 800,
        max_tokens: null,
        temperature: null,
        top_p: null,
        model_context_windows: {},
        model_auto_compact_thresholds: {},
        base_url: null,
        api_key_env: "MINIMAX_API_KEY",
        default_reasoning_effort: null,
        supports_tool_calling: true,
        supports_thinking: false,
        supports_reasoning_effort: undefined,
        supports_vision: false,
        supports_image_generation: false,
        timeout: 30,
        request_timeout: null,
        default_request_timeout: null,
        max_retries: 2,
        use_responses_api: null,
        output_version: null,
        image_generation: null,
        diagnostics: [],
        capabilities: {},
        internal_task_default: false,
        internal_task_selected_model: null,
      },
    ],
  }),
  useModelProviderPresets: () => ({
    data: [
      {
        provider: "openrouter",
        display_name: "OpenRouter",
        base_url: "https://openrouter.ai/api/v1",
        api_key_env: "OPENROUTER_API_KEY",
        model_catalog: ["openai/gpt-5.4"],
        default_model: "openai/gpt-5.4",
        supports_reasoning_effort: true,
        context_window_tokens: 200000,
        auto_compact_threshold_tokens: 150000,
        defaults: { timeout: 600, max_retries: 2 },
      },
      {
        provider: "openai",
        display_name: "OpenAI",
        base_url: "https://api.openai.com/v1",
        api_key_env: "OPENAI_API_KEY",
        model_catalog: ["gpt-image-1"],
        default_model: "gpt-image-1",
        supports_image_generation: false,
        defaults: {},
      },
      {
        provider: "minimax_cn",
        display_name: "MiniMax CN",
        base_url: "https://api.minimaxi.com/v1",
        api_key_env: "MINIMAX_API_KEY",
        model_catalog: ["MiniMax-M2.7"],
        default_model: "MiniMax-M2.7",
        supports_image_generation: false,
        defaults: {},
      },
    ],
  }),
  useUpdateModelSelection: () => ({
    isPending: false,
    variables: null,
    mutate: mutateMock,
    mutateAsync: mutateMock,
  }),
  useUpsertModelProvider: () => ({
    isPending: false,
    mutateAsync: upsertMock,
  }),
  useDeleteModelProvider: () => ({
    isPending: false,
    mutateAsync: deleteMock,
  }),
  useTestModelProvider: () => ({
    isPending: false,
    mutateAsync: testModelMock,
  }),
}));

describe("ModelConfigPanel", () => {
  beforeEach(() => {
    mutateMock.mockClear();
    upsertMock.mockClear();
    deleteMock.mockClear();
    testModelMock.mockClear();
  });

  it("lets operators select the default concrete model for a provider", () => {
    renderModelConfigPanel();

    const select = screen.getByLabelText("openai Default model");
    fireEvent.change(select, { target: { value: "gpt-5.5" } });

    expect(mutateMock).toHaveBeenCalledWith({ name: "openai", modelName: "gpt-5.5" });
  });

  it("lets operators select the global default reasoning effort for a provider", () => {
    renderModelConfigPanel();

    const select = screen.getByLabelText("openai Default reasoning effort");
    expect(select).not.toBeDisabled();
    fireEvent.change(select, { target: { value: "high" } });

    expect(mutateMock).toHaveBeenCalledWith({
      name: "openai",
      modelName: "gpt-5.4",
      defaultReasoningEffort: "high",
    });
  });

  it("lets operators choose the unique internal task model from the global selector", async () => {
    renderModelConfigPanel();

    fireEvent.change(screen.getByLabelText("Background tasks"), {
      target: { value: "minimax::MiniMax-M2.7" },
    });

    await waitFor(() =>
      expect(mutateMock).toHaveBeenCalledWith({
        name: "minimax",
        modelName: "MiniMax-M2.7",
        internalTaskDefault: true,
      }),
    );
  });

  it("tests the selected background task model without changing provider defaults", async () => {
    renderModelConfigPanel();

    fireEvent.change(screen.getByLabelText("Background tasks"), {
      target: { value: "minimax::mimo-v2-flash" },
    });
    expect(mutateMock).toHaveBeenCalledWith({
      name: "minimax",
      modelName: "mimo-v2-flash",
      internalTaskDefault: true,
    });

    fireEvent.click(screen.getByRole("button", { name: "Test background model" }));

    await waitFor(() =>
      expect(testModelMock).toHaveBeenCalledWith({
        name: "minimax",
        body: { model_name: "mimo-v2-flash", subsystem: "background_tasks" },
      }),
    );
  });

  it("lets operators add and delete model providers", async () => {
    renderModelConfigPanel();

    fireEvent.click(screen.getByRole("button", { name: /Add model/i }));
    expect(screen.getByRole("dialog", { name: /Add model provider/i })).toBeInTheDocument();
    expect(screen.queryByText("Use as global default provider")).not.toBeInTheDocument();
    fireEvent.change(getApiKeyInput(), { target: { value: "test-key" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(upsertMock).toHaveBeenCalledWith({
        name: "openrouter",
        body: expect.objectContaining({
          provider: "openrouter",
          api_key: "test-key",
          api_key_env: "OPENROUTER_API_KEY",
          models: ["openai/gpt-5.4"],
          default_model: "openai/gpt-5.4",
          use_responses_api: false,
          supports_reasoning_effort: false,
        }),
      }),
    );
    expect(upsertMock.mock.calls[0]?.[0].body).not.toHaveProperty("set_default");

    fireEvent.click(screen.getByRole("button", { name: "Delete OpenAI" }));
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("openai"));
  });

  it("lets operators edit an existing model provider", async () => {
    renderModelConfigPanel();

    fireEvent.click(screen.getByRole("button", { name: "Edit OpenAI" }));
    expect(screen.getByRole("dialog", { name: /Edit model provider/i })).toBeInTheDocument();
    const dialog = screen.getByRole("dialog", { name: /Edit model provider/i });
    const urlInput = Array.from(dialog.querySelectorAll("input")).find((input) => input.value === "") as HTMLInputElement | undefined;
    expect(urlInput).toBeDefined();
    fireEvent.change(urlInput!, { target: { value: "https://api.example.test/v1" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(upsertMock).toHaveBeenCalledWith({
        name: "openai",
        body: expect.objectContaining({
          provider: "openai",
          base_url: "https://api.example.test/v1",
          models: ["gpt-5.4", "gpt-5.5"],
          default_model: "gpt-5.4",
        }),
      }),
    );
  });

  it("keeps Responses API and reasoning effort disabled on an unchanged edit by default", async () => {
    renderModelConfigPanel();

    fireEvent.click(screen.getByRole("button", { name: "Edit MiniMax" }));
    expect(screen.getByRole("dialog", { name: /Edit model provider/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(upsertMock).toHaveBeenCalledWith({
        name: "minimax",
        body: expect.objectContaining({
          provider: "minimax",
          models: ["mimo-v2-flash", "MiniMax-M2.7"],
          default_model: "MiniMax-M2.7",
          use_responses_api: false,
          supports_reasoning_effort: false,
        }),
      }),
    );
  });

  it("requires and saves the image generation endpoint when image generation is enabled", async () => {
    renderModelConfigPanel();

    fireEvent.click(screen.getByRole("button", { name: /Add model/i }));
    fireEvent.change(getProviderPresetSelect(), { target: { value: "minimax_cn" } });
    fireEvent.change(getApiKeyInput(), { target: { value: "test-key" } });
    fireEvent.click(screen.getByRole("button", { name: "Advanced fields" }));
    fireEvent.click(screen.getByRole("switch", { name: /supports_image_generation/i }));

    const endpoint = screen.getByRole("textbox", { name: "image_generation.endpoint" });
    expect(endpoint).toHaveAttribute("placeholder", "/image_generation");
    fireEvent.change(endpoint, { target: { value: "/image_generation" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(upsertMock).toHaveBeenCalledWith({
        name: "minimax_cn",
        body: expect.objectContaining({
          provider: "minimax_cn",
          base_url: null,
          supports_image_generation: true,
          image_generation: { endpoint: "/image_generation" },
        }),
      }),
    );
  });

  it("blocks saving when image generation is enabled without an endpoint", async () => {
    renderModelConfigPanel();

    fireEvent.click(screen.getByRole("button", { name: /Add model/i }));
    fireEvent.change(getApiKeyInput(), { target: { value: "test-key" } });
    fireEvent.click(screen.getByRole("button", { name: "Advanced fields" }));
    fireEvent.click(screen.getByRole("switch", { name: /supports_image_generation/i }));
    fireEvent.change(screen.getByRole("textbox", { name: "image_generation.endpoint" }), { target: { value: "" } });

    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("resets the add-model draft after saving and reopening", async () => {
    renderModelConfigPanel();

    fireEvent.click(screen.getByRole("button", { name: /Add model/i }));
    fireEvent.change(getApiKeyInput(), { target: { value: "first-key" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(upsertMock).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: /Add model/i }));

    expect(getApiKeyInput()).toHaveValue("");
  });
});

function renderModelConfigPanel() {
  return render(
    <TooltipProvider>
      <ModelConfigPanel copy={opsCopy("en-US")} />
    </TooltipProvider>,
  );
}

function getApiKeyInput() {
  const dialog = screen.getByRole("dialog", { name: /Add model provider/i });
  const input = dialog.querySelector('input[type="password"]');
  expect(input).not.toBeNull();
  return input as HTMLInputElement;
}

function getProviderPresetSelect() {
  const dialog = screen.getByRole("dialog", { name: /Add model provider/i });
  const select = within(dialog).getAllByRole("combobox")[0];
  expect(select).toBeDefined();
  return select as HTMLSelectElement;
}
