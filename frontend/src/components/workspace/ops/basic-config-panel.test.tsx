import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BasicConfigPanel } from "./basic-config-panel";
import { opsCopy } from "./types";

const saveBasicConfigMock = vi.fn().mockResolvedValue({});
const testBasicConfigMock = vi.fn().mockResolvedValue({
  item_id: "git_token",
  ok: true,
  status: "ready",
  message: "Git token is configured.",
  checked_at: "2026-06-05T00:00:00Z",
  config_fingerprint: "cfg",
});

vi.mock("@/src/core/config/hooks", () => ({
  useBasicConfig: () => ({
    data: {
      config_path: "/tmp/config.yaml",
      dotenv_path: "/tmp/.env",
      config_fingerprint: "cfg",
      required_count: 1,
      configured_required_count: 0,
      missing_required_count: 1,
      required_items: [
        {
          item_id: "git_token",
          label: "Git token",
          description: "Required by HCMS Git-like version control.",
          category: "required",
          required: true,
          configured: false,
          testable: true,
          token_env: "GITHUB_TOKEN",
          value: null,
          secret: true,
          status: "missing",
          message: "Missing GITHUB_TOKEN.",
        },
      ],
      extension_items: [
        {
          item_id: "git_author",
          label: "Git author",
          description: "Optional commit author metadata.",
          category: "extension",
          required: false,
          configured: true,
          testable: true,
          token_env: null,
          value: "Anvil Operator <operator@example.test>",
          secret: false,
          status: "ready",
          message: "Author metadata configured.",
        },
        {
          item_id: "git_remote",
          label: "Git remote",
          description: "Optional remote repository URL for HCMS version metadata.",
          category: "extension",
          required: false,
          configured: true,
          testable: true,
          token_env: null,
          value: "https://github.com/example/anvil-memory.git",
          secret: false,
          status: "ready",
          message: "Git remote URL configured.",
        },
      ],
    },
    isLoading: false,
    isFetching: false,
    isError: false,
  }),
  useSaveBasicConfig: () => ({
    isPending: false,
    mutateAsync: saveBasicConfigMock,
  }),
  useTestBasicConfig: () => ({
    isPending: false,
    mutateAsync: testBasicConfigMock,
  }),
}));

describe("BasicConfigPanel", () => {
  beforeEach(() => {
    saveBasicConfigMock.mockClear();
    testBasicConfigMock.mockClear();
  });

  it("separates required and extension config and saves the Git token", async () => {
    render(<BasicConfigPanel copy={opsCopy("en-US")} />);

    expect(screen.getByText("Required configuration")).toBeInTheDocument();
    expect(screen.getByText("Extension configuration")).toBeInTheDocument();
    expect(screen.getByText("Git token")).toBeInTheDocument();
    expect(screen.getByText("Git author")).toBeInTheDocument();
    expect(screen.getByText("Git remote")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Git token env"), { target: { value: "ANVIL_GIT_TOKEN" } });
    fireEvent.change(screen.getByLabelText("Git token value"), { target: { value: "test-git-token" } });
    fireEvent.change(screen.getByLabelText("Git user name"), { target: { value: "Anvil Operator" } });
    fireEvent.change(screen.getByLabelText("Git user email"), { target: { value: "operator@example.test" } });
    fireEvent.click(screen.getByRole("button", { name: "Save basic configuration" }));

    await waitFor(() => {
      expect(saveBasicConfigMock).toHaveBeenCalledWith({
        git_token_env: "ANVIL_GIT_TOKEN",
        git_token: "test-git-token",
        git_user_name: "Anvil Operator",
        git_user_email: "operator@example.test",
        git_remote_url: "https://github.com/example/anvil-memory.git",
      });
    });
  });

  it("runs a per-item basic config test", async () => {
    render(<BasicConfigPanel copy={opsCopy("en-US")} />);

    fireEvent.click(screen.getByRole("button", { name: "Test Git token" }));

    await waitFor(() => {
      expect(testBasicConfigMock).toHaveBeenCalledWith({ item_id: "git_token" });
    });
    expect(await screen.findByText("Git token is configured.")).toBeInTheDocument();
  });

  it("prefills configured extension values in the Git editor", async () => {
    render(<BasicConfigPanel copy={opsCopy("en-US")} />);

    await waitFor(() => {
      expect(screen.getByLabelText("Git remote URL")).toHaveValue("https://github.com/example/anvil-memory.git");
    });
  });
});
