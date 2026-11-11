// AI contribution: 50% or more AI-generated
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  getOrganization,
  getOrganizations,
  getOrganizationTimeline,
  type Organization,
  type OrganizationSummary,
  type Page,
} from "../../services/api";
import { OrganizationsView } from "./OrganizationsView";

vi.mock("../../services/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../services/api")>()),
  getOrganizations: vi.fn(),
  getOrganization: vi.fn(),
  getOrganizationTimeline: vi.fn(),
}));

const RECORD = { source_id: "s1", external_id: "o2", version_id: "v2", label: "Atelier Boréal" };

function page(items: OrganizationSummary[], total = items.length): Page<OrganizationSummary> {
  return { items, total, page: 1, page_size: 25 };
}

const MATCHED: OrganizationSummary = {
  organization_id: "org1",
  record: null,
  name: "Groupe Tremblay inc.",
  records: 2,
  last_changed: "2026-11-01T12:00:00Z",
};

const UNMATCHED: OrganizationSummary = {
  organization_id: null,
  record: RECORD,
  name: "Atelier Boréal",
  records: 1,
  last_changed: "2026-11-01T12:00:00Z",
};

const DETAIL: Organization = {
  id: "org1",
  display_name: "Groupe Tremblay inc.",
  primary_domain: "tremblay.ca",
  records: [
    {
      record: { source_id: "s1", external_id: "o1", version_id: "v1", label: "Groupe Tremblay" },
      link: { method: "domain", confidence: 1, decided_by: "tier1:tier1-v1" },
      versions: [{ id: "v1", label: "Groupe Tremblay", domain: "tremblay.ca", valid_from: "" }],
    },
  ],
};

describe("OrganizationsView", () => {
  beforeEach(() => {
    vi.mocked(getOrganizations).mockResolvedValue(page([MATCHED, UNMATCHED]));
    vi.mocked(getOrganization).mockResolvedValue(DETAIL);
    vi.mocked(getOrganizationTimeline).mockResolvedValue([
      {
        sync_run_id: "r1",
        started_at: "2026-11-01T12:00:00Z",
        change_type: "modified",
        record: DETAIL.records[0].record,
        changed_fields: { domain: { before: "old.ca", after: "tremblay.ca" } },
      },
    ]);
  });

  it("lists organizations with their record counts", async () => {
    // Arrange
    render(<OrganizationsView tenantId="t1" onOpenRecord={vi.fn()} />);

    // Act / Assert
    expect(await screen.findByText("Groupe Tremblay inc.")).toBeInTheDocument();
    expect(screen.getByText("Organizations (2)")).toBeInTheDocument();
    expect(getOrganizations).toHaveBeenCalledWith("t1", "name", 1);
  });

  it("sorts by last change from the first page", async () => {
    // Arrange
    render(<OrganizationsView tenantId="t1" onOpenRecord={vi.fn()} />);
    await screen.findByText("Groupe Tremblay inc.");

    // Act
    fireEvent.change(screen.getByLabelText("Sort by"), { target: { value: "last_changed" } });

    // Assert
    expect(getOrganizations).toHaveBeenLastCalledWith("t1", "last_changed", 1);
  });

  it("pages through the list", async () => {
    // Arrange
    vi.mocked(getOrganizations).mockResolvedValue(page([MATCHED], 60));
    render(<OrganizationsView tenantId="t1" onOpenRecord={vi.fn()} />);
    await screen.findByText("Page 1 of 3");

    // Act
    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    // Assert
    expect(getOrganizations).toHaveBeenLastCalledWith("t1", "name", 2);
  });

  it("opens an organization with its records and timeline", async () => {
    // Arrange
    render(<OrganizationsView tenantId="t1" onOpenRecord={vi.fn()} />);

    // Act
    fireEvent.click(await screen.findByRole("button", { name: "Groupe Tremblay inc." }));

    // Assert
    expect(await screen.findByText(/matched by domain/)).toBeInTheDocument();
    expect(await screen.findByText("old.ca")).toBeInTheDocument();
    expect(getOrganization).toHaveBeenCalledWith("t1", "org1");
  });

  it("opens the source record of an organization that matched no other", async () => {
    // Arrange
    const onOpenRecord = vi.fn();
    render(<OrganizationsView tenantId="t1" onOpenRecord={onOpenRecord} />);

    // Act
    fireEvent.click(await screen.findByRole("button", { name: "Atelier Boréal" }));

    // Assert
    expect(onOpenRecord).toHaveBeenCalledWith(RECORD);
  });

  it("says when there are no organizations", async () => {
    // Arrange
    vi.mocked(getOrganizations).mockResolvedValue(page([]));

    // Act
    render(<OrganizationsView tenantId="t1" onOpenRecord={vi.fn()} />);

    // Assert
    expect(await screen.findByText(/No organizations yet/)).toBeInTheDocument();
  });

  it("shows an error when the list cannot load", async () => {
    // Arrange
    vi.mocked(getOrganizations).mockRejectedValue(
      new ApiError("Server error", { status: 500, code: "http_error" }),
    );

    // Act
    render(<OrganizationsView tenantId="t1" onOpenRecord={vi.fn()} />);

    // Assert
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});
