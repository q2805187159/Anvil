import React from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider, useI18n } from "./index";


function wrapper({ children }: Readonly<{ children: React.ReactNode }>) {
  return <I18nProvider>{children}</I18nProvider>;
}


describe("i18n hooks", () => {
  it("defaults from browser language and can switch manually", async () => {
    vi.stubGlobal("navigator", { language: "zh-CN" });

    function LocaleProbe() {
      const { locale, t } = useI18n();
      return <span>{`${locale}:${t.shell.appTitle}`}</span>;
    }

    expect(renderToString(<I18nProvider><LocaleProbe /></I18nProvider>)).toContain("en-US:Anvil");

    const { result } = renderHook(() => useI18n(), { wrapper });

    await waitFor(() => {
      expect(result.current.locale).toBe("zh-CN");
    });
    expect(result.current.t.shell.appTitle).toBe("Anvil");

    await act(async () => {
      result.current.changeLocale("en-US");
    });

    expect(result.current.locale).toBe("en-US");
    expect(result.current.t.shell.appTitle).toBe("Anvil");
  });
});
