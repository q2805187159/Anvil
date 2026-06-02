import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Tooltip } from "./tooltip";

describe("Tooltip", () => {
  it("renders help text with a high layer and stable readable width", () => {
    render(
      <Tooltip content="Single request timeout in seconds.">
        <button type="button">?</button>
      </Tooltip>,
    );

    const tooltip = screen.getByRole("tooltip");
    expect(tooltip).toHaveClass("z-[140]");
    expect(tooltip).toHaveClass("min-w-[12rem]");
    expect(tooltip).toHaveClass("w-max");
    expect(tooltip).toHaveClass("[overflow-wrap:break-word]");
  });
});
