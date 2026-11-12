// AI contribution: 50% or more AI-generated
import type { SectorShare } from "../../services/api";

export function formatShare(value: number): string {
  return `${value.toFixed(1)}%`;
}

// The axis ends at the next 10% above the largest value, so bars use the width well.
export function scaleMax(sectors: SectorShare[]): number {
  const values = sectors.flatMap((s) => [
    s.organization_share,
    s.people_share ?? 0,
    s.target ?? 0,
    s.region_share ?? 0,
  ]);
  return Math.min(100, Math.max(10, Math.ceil(Math.max(0, ...values) / 10) * 10));
}
