import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { ApiError, getHealth, getMe, getRuns, getTenants, signOut } from "./services/api";

vi.mock("./services/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./services/api")>()),
  getHealth: vi.fn(),
  getMe: vi.fn(),
  signOut: vi.fn(),
  getTenants: vi.fn(),
  getRuns: vi.fn(),
}));

const USER = { email: "ana@example.ca", tenant_id: "t1", tenant_name: "Capitale" };
const NO_SESSION = new ApiError("Sign in to continue", { status: 401, code: "not_authenticated" });

describe("App", () => {
  beforeEach(() => {
    vi.mocked(getHealth).mockResolvedValue({ status: "ok", service: "pulse-backend" });
    vi.mocked(getMe).mockResolvedValue(USER);
    vi.mocked(getTenants).mockResolvedValue([]);
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(signOut).mockResolvedValue(undefined);
  });

  it("shows the backend status once it loads", async () => {
    render(<App />);

    expect(screen.getByText("Backend status: Connecting...")).toBeInTheDocument();
    expect(await screen.findByText("Backend status: ok")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("tells the user when the backend is unavailable", async () => {
    const unreachable = new ApiError("Cannot reach the PULSE server.", {
      status: null,
      code: "network_error",
    });
    vi.mocked(getHealth).mockRejectedValue(unreachable);
    vi.mocked(getMe).mockRejectedValue(unreachable);

    render(<App />);

    expect(await screen.findByText("Backend status: Backend unavailable")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("We can't reach the PULSE server");
  });

  it("asks a visitor without a session to sign in", async () => {
    // Arrange
    vi.mocked(getMe).mockRejectedValue(NO_SESSION);

    // Act
    render(<App />);

    // Assert
    expect(await screen.findByRole("form", { name: "Sign in" })).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows who is signed in and signs them out", async () => {
    // Arrange
    render(<App />);

    // Act
    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));

    // Assert
    expect(await screen.findByRole("form", { name: "Sign in" })).toBeVisible();
    expect(signOut).toHaveBeenCalled();
  });

  it("names the signed-in user and their network", async () => {
    // Act
    render(<App />);

    // Assert
    expect(await screen.findByText(/Signed in as ana@example.ca \(Capitale\)/)).toBeVisible();
  });
});
