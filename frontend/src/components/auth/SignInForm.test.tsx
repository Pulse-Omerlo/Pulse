// AI contribution: 50% or more AI-generated
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, signIn } from "../../services/api";
import { SignInForm } from "./SignInForm";

vi.mock("../../services/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../services/api")>()),
  signIn: vi.fn(),
}));

const USER = { email: "ana@example.ca", tenant_id: "t1", tenant_name: "Capitale" };

function fill(email: string, password: string) {
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: email } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("SignInForm", () => {
  beforeEach(() => {
    vi.mocked(signIn).mockReset();
  });

  it("signs the user in and hands them to the app", async () => {
    // Arrange
    vi.mocked(signIn).mockResolvedValue(USER);
    const onSignedIn = vi.fn();
    render(<SignInForm onSignedIn={onSignedIn} />);

    // Act
    fill("ana@example.ca", "a long password");

    // Assert
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeEnabled();
    expect(signIn).toHaveBeenCalledWith("ana@example.ca", "a long password");
    expect(onSignedIn).toHaveBeenCalledWith(USER);
  });

  it("shows why signing in failed and clears the password", async () => {
    // Arrange
    vi.mocked(signIn).mockRejectedValue(
      new ApiError("Email or password is incorrect", { status: 401, code: "not_authenticated" }),
    );
    render(<SignInForm onSignedIn={vi.fn()} />);

    // Act
    fill("ana@example.ca", "wrong password");

    // Assert
    expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is incorrect");
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });

  it("says it is working while the request runs", () => {
    // Arrange
    vi.mocked(signIn).mockReturnValue(new Promise(() => {}));
    render(<SignInForm onSignedIn={vi.fn()} />);

    // Act
    fill("ana@example.ca", "a long password");

    // Assert
    expect(screen.getByRole("button", { name: "Signing in..." })).toBeDisabled();
  });
});
