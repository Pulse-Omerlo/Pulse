// AI contribution: 50% or more AI-generated
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  decideCandidate,
  getCandidates,
  type MatchCandidate,
  type Page,
} from "../../services/api";
import { ReviewView } from "./ReviewView";

vi.mock("../../services/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../services/api")>()),
  getCandidates: vi.fn(),
  decideCandidate: vi.fn(),
}));

const LEFT = { source_id: "s1", external_id: "o1", version_id: "v1", label: "Acme" };
const RIGHT = { source_id: "s1", external_id: "o2", version_id: "v2", label: "ACME inc" };

function candidate(overrides: Partial<MatchCandidate> = {}): MatchCandidate {
  return {
    id: "c1",
    score: 0.72,
    status: "pending",
    left: LEFT,
    right: RIGHT,
    matched_fields: [{ rule: "fuzzy", field: "name" }],
    left_values: [
      { field: "name", value: "Acme", normalized: "acme" },
      { field: "website", value: "https://www.acme.ca/", normalized: "acme.ca" },
    ],
    right_values: [
      { field: "name", value: "ACME inc", normalized: "acme" },
      { field: "website", value: null, normalized: null },
    ],
    decided_by: null,
    decided_at: null,
    ...overrides,
  };
}

function page(items: MatchCandidate[]): Page<MatchCandidate> {
  return { items, total: items.length, page: 1, page_size: 10 };
}

function renderView() {
  const onOpenRecord = vi.fn();
  render(<ReviewView tenantId="t1" onOpenRecord={onOpenRecord} />);
  return onOpenRecord;
}

describe("ReviewView", () => {
  beforeEach(() => {
    vi.mocked(getCandidates).mockReset();
    vi.mocked(decideCandidate).mockReset();
    vi.mocked(getCandidates).mockResolvedValue(page([candidate()]));
  });

  it("shows both records' raw and normalized values side by side with the score", async () => {
    // Act
    renderView();

    // Assert
    const card = await screen.findByRole("article", { name: "Acme and ACME inc" });
    expect(within(card).getByText("72% likely")).toBeVisible();
    expect(within(card).getByText("matched on fuzzy")).toBeVisible();
    expect(within(card).getByText("https://www.acme.ca/")).toBeVisible();
    expect(within(card).getByText("normalized: acme.ca")).toBeVisible();
    expect(within(card).getAllByText("(empty)")).toHaveLength(1);
    expect(getCandidates).toHaveBeenCalledWith("t1", "pending", 1);
  });

  it("opens a record from its column", async () => {
    // Arrange
    const onOpenRecord = renderView();

    // Act
    fireEvent.click(await screen.findByRole("button", { name: "ACME inc" }));

    // Assert
    expect(onOpenRecord).toHaveBeenCalledWith(RIGHT);
  });

  it.each([
    ["Same organization", "accept"],
    ["Different organizations", "reject"],
  ])("sends %s as %s and reloads the queue", async (label, action) => {
    // Arrange
    vi.mocked(decideCandidate).mockResolvedValue(candidate({ status: "accepted" }));
    renderView();

    // Act
    fireEvent.click(await screen.findByRole("button", { name: label }));

    // Assert
    expect(decideCandidate).toHaveBeenCalledWith("t1", "c1", action);
    await waitFor(() => expect(getCandidates).toHaveBeenCalledTimes(2));
  });

  it("shows who decided a pair and lets them undo it", async () => {
    // Arrange
    vi.mocked(getCandidates).mockImplementation((_tenant, status) =>
      Promise.resolve(
        page(
          status === "accepted"
            ? [candidate({ status: "accepted", decided_by: "ana@pulse.test" })]
            : [],
        ),
      ),
    );
    vi.mocked(decideCandidate).mockResolvedValue(candidate());
    renderView();
    fireEvent.click(await screen.findByRole("button", { name: "Accepted" }));

    // Act
    fireEvent.click(await screen.findByRole("button", { name: "Undo" }));

    // Assert
    expect(screen.getByText(/Accepted by ana@pulse.test/)).toBeVisible();
    expect(decideCandidate).toHaveBeenCalledWith("t1", "c1", "undo");
  });

  it("says when there is nothing to review", async () => {
    // Arrange
    vi.mocked(getCandidates).mockResolvedValue(page([]));

    // Act
    renderView();

    // Assert
    expect(await screen.findByText(/No possible matches to review/)).toBeVisible();
  });

  it("shows a loading message first", () => {
    // Arrange
    vi.mocked(getCandidates).mockReturnValue(new Promise(() => {}));

    // Act
    renderView();

    // Assert
    expect(screen.getByText("Loading possible matches...")).toBeVisible();
  });

  it("explains a pair someone else already decided", async () => {
    // Arrange
    vi.mocked(decideCandidate).mockRejectedValue(
      new ApiError("This pair was already rejected", { status: 409, code: "conflict" }),
    );
    renderView();

    // Act
    fireEvent.click(await screen.findByRole("button", { name: "Same organization" }));

    // Assert
    expect(await screen.findByRole("alert")).toHaveTextContent("This pair was already rejected");
  });

  it("shows an error when the queue cannot load", async () => {
    // Arrange
    vi.mocked(getCandidates).mockRejectedValue(
      new ApiError("Cannot reach the PULSE server.", { status: null, code: "network_error" }),
    );

    // Act
    renderView();

    // Assert
    expect(await screen.findByRole("alert")).toHaveTextContent("We can't reach the PULSE server");
  });
});
