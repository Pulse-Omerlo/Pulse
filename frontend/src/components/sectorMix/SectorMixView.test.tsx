// AI contribution: 50% or more AI-generated
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, getSectorMix, type SectorMix, type SyncRun } from "../../services/api";
import { scaleMax } from "./format";
import { SectorMixView } from "./SectorMixView";

vi.mock("../../services/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../services/api")>()),
  getSectorMix: vi.fn(),
}));

const BOREAL_RECORD = {
  source_id: "s1",
  external_id: "o2",
  version_id: "v2",
  label: "Atelier Boréal",
};

function run(id: string, startedAt: string): SyncRun {
  return {
    id,
    source_id: "s1",
    mode: "full",
    status: "succeeded",
    started_at: startedAt,
    finished_at: startedAt,
    counts: {},
    error: null,
  };
}

const RUNS = [run("r2", "2026-11-02T12:00:00Z"), run("r1", "2026-11-01T12:00:00Z")];

function mix(overrides: Partial<SectorMix> = {}): SectorMix {
  return {
    sync_run_id: "r2",
    computed_at: "2026-11-02T12:01:00Z",
    settings: { gap_points: 10, targets: { "22": 25 }, version: "v" },
    organizations: 4,
    classified: 3,
    people_status: "linked",
    people: 5,
    sectors: [
      {
        code: "54",
        name: "Professional services",
        organization_count: 1,
        organization_share: 33.3,
        people_count: 4,
        people_share: 100,
        target: null,
        organizations: [
          {
            key: "org1",
            name: "Groupe Tremblay",
            records: ["v1"],
            organization_id: "org1",
            record: null,
          },
        ],
        people: [
          { id: "p1", name: "Ana Roy", organization: "org1" },
          { id: "p2", name: "Bob Roy", organization: "org1" },
          { id: "p3", name: "Cy Roy", organization: "org1" },
          { id: "p4", name: "Di Roy", organization: "org1" },
        ],
      },
      {
        code: "72",
        name: "Accommodation and food",
        organization_count: 2,
        organization_share: 66.7,
        people_count: 0,
        people_share: 0,
        target: null,
        organizations: [
          {
            key: "v2",
            name: "Atelier Boréal",
            records: ["v2"],
            organization_id: null,
            record: BOREAL_RECORD,
          },
          {
            key: "org3",
            name: "Café Nord",
            records: ["v3"],
            organization_id: "org3",
            record: null,
          },
        ],
        people: [],
      },
      {
        code: "22",
        name: "Utilities",
        organization_count: 0,
        organization_share: 0,
        people_count: 0,
        people_share: 0,
        target: 25,
        organizations: [],
        people: [],
      },
    ],
    gaps: [
      {
        code: "72",
        kind: "too_few_people",
        size: 66.7,
        explanation: "Accommodation and food makes up 66.7% of organizations but 0.0% of people.",
      },
      {
        code: "22",
        kind: "below_target",
        size: 25,
        explanation: "Utilities makes up 0.0% of organizations, below its target of 25.0%.",
      },
    ],
    unclassified: [
      { key: "org9", name: "Mystery Co", records: ["v9"], organization_id: "org9", record: null },
    ],
    people_unplaced: [{ id: "p5", name: "Eve Roy", organization: null }],
    ...overrides,
  };
}

function renderView() {
  const onOpenOrganization = vi.fn();
  const onOpenRecord = vi.fn();
  render(
    <SectorMixView
      tenantId="t1"
      runs={RUNS}
      onOpenOrganization={onOpenOrganization}
      onOpenRecord={onOpenRecord}
    />,
  );
  return { onOpenOrganization, onOpenRecord };
}

describe("SectorMixView", () => {
  beforeEach(() => {
    vi.mocked(getSectorMix).mockReset();
    vi.mocked(getSectorMix).mockResolvedValue(mix());
  });

  it("shows each sector's share of organizations and people, largest first", async () => {
    // Act
    renderView();

    // Assert
    const chart = await screen.findByRole("figure");
    const rows = within(chart).getAllByRole("button");
    expect(rows.map((row) => row.getAttribute("aria-label"))).toEqual([
      "Accommodation and food: 66.7% of organizations, 0.0% of people",
      "Professional services: 33.3% of organizations, 100.0% of people",
      "Utilities: 0.0% of organizations, 0.0% of people, target 25.0%",
    ]);
    expect(screen.getByText(/3 of 4 organizations classified, 4 of 5 people placed/)).toBeVisible();
    expect(getSectorMix).toHaveBeenCalledWith("t1", undefined);
  });

  it("lists the gaps by size with their explanation", async () => {
    // Act
    renderView();

    // Assert
    const items = (await screen.findAllByText(/pts$/)).map((size) => size.textContent);
    expect(items).toEqual(["66.7 pts", "25.0 pts"]);
    expect(screen.getByText(/below its target of 25.0%/)).toBeVisible();
    expect(screen.getByText("Few people")).toBeVisible();
  });

  it("opens the organizations behind a gap, each linking to its page or record", async () => {
    // Arrange
    const { onOpenOrganization, onOpenRecord } = renderView();

    // Act
    fireEvent.click(await screen.findByText(/66.7% of organizations but 0.0% of people/));
    const detail = screen.getByRole("region", { name: "Accommodation and food details" });
    fireEvent.click(within(detail).getByRole("button", { name: "Café Nord" }));
    fireEvent.click(within(detail).getByRole("button", { name: "Atelier Boréal" }));

    // Assert
    expect(onOpenOrganization).toHaveBeenCalledWith("org3");
    expect(onOpenRecord).toHaveBeenCalledWith(BOREAL_RECORD);
    expect(within(detail).getByText("People (0)")).toBeVisible();
  });

  it("lists the people behind a sector when people are linked", async () => {
    // Arrange
    renderView();

    // Act
    fireEvent.click(await screen.findByRole("button", { name: /^Professional services/ }));

    // Assert
    const detail = screen.getByRole("region", { name: "Professional services details" });
    expect(within(detail).getByText("Ana Roy")).toBeVisible();
    expect(within(detail).getByText("People (4)")).toBeVisible();
  });

  it("leaves people out while they are not linked to organizations", async () => {
    // Arrange
    vi.mocked(getSectorMix).mockResolvedValue(
      mix({
        people_status: "not_linked",
        sectors: mix().sectors.map((s) => ({
          ...s,
          people: null,
          people_share: null,
          people_count: null,
        })),
      }),
    );

    // Act
    renderView();

    // Assert
    expect(await screen.findByText(/People are not linked to organizations yet/)).toBeVisible();
    const chart = screen.getByRole("figure");
    const labels = within(chart)
      .getAllByRole("button")
      .map((row) => row.getAttribute("aria-label"));
    expect(labels.some((label) => label?.includes("of people"))).toBe(false);
    expect(within(chart).queryByText("People")).toBeNull();
  });

  it("shows the run the results come from and loads an earlier one", async () => {
    // Arrange
    renderView();
    await screen.findByText(/From the sync run of/);

    // Act
    fireEvent.change(screen.getByLabelText("Sync run"), { target: { value: "r1" } });

    // Assert
    expect(getSectorMix).toHaveBeenLastCalledWith("t1", "r1");
  });

  it("explains when a network has no sector mix yet", async () => {
    // Arrange
    vi.mocked(getSectorMix).mockRejectedValue(
      new ApiError("No sector mix yet", { status: 404, code: "not_found" }),
    );

    // Act
    renderView();

    // Assert
    expect(await screen.findByText(/No sector mix yet/)).toBeVisible();
  });

  it("shows an error the user can act on when the server fails", async () => {
    // Arrange
    vi.mocked(getSectorMix).mockRejectedValue(
      new ApiError("Cannot reach the PULSE server.", { status: null, code: "network_error" }),
    );

    // Act
    renderView();

    // Assert
    expect(await screen.findByRole("alert")).toBeVisible();
  });

  it("shows a loading message first", () => {
    // Arrange
    vi.mocked(getSectorMix).mockReturnValue(new Promise(() => {}));

    // Act
    renderView();

    // Assert
    expect(screen.getByText("Loading the sector mix...")).toBeVisible();
  });

  it("ends the axis at the next ten percent above the largest value", () => {
    // Act / Assert
    expect(scaleMax(mix().sectors)).toBe(100);
    expect(scaleMax([{ ...mix().sectors[0], organization_share: 23, people_share: 4 }])).toBe(30);
  });
});
