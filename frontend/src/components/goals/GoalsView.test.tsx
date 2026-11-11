// AI contribution: 50% or more AI-generated
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { GoalsView } from "./GoalsView";

describe("GoalsView", () => {
  it("shows the goals and daily recommendations layout", () => {
    // Arrange
    render(<GoalsView />);

    // Act / Assert
    expect(screen.getByRole("heading", { name: "Goals" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Daily recommendations" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add goal" })).toBeDisabled();
  });
});
