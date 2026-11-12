// AI contribution: 50% or more AI-generated
import type { SectorShare } from "../../services/api";
import { formatShare, scaleMax } from "./format";

interface SectorChartProps {
  sectors: SectorShare[];
  peopleLinked: boolean;
  selectedCode: string | null;
  onSelect: (code: string) => void;
}

interface BarProps {
  value: number;
  max: number;
  series: "organizations" | "people";
  target?: number | null;
}

function Bar({ value, max, series, target = null }: BarProps) {
  return (
    <span className="bar-line">
      <span className="bar-track">
        <span
          className={`bar bar-${series}`}
          style={{ width: `${(value / max) * 100}%` }}
          aria-hidden="true"
        />
        {target !== null && (
          <span
            className="target"
            style={{ left: `${(target / max) * 100}%` }}
            aria-hidden="true"
          />
        )}
      </span>
      <span className="bar-value">{formatShare(value)}</span>
    </span>
  );
}

export function SectorChart({ sectors, peopleLinked, selectedCode, onSelect }: SectorChartProps) {
  const max = scaleMax(sectors);

  return (
    <figure className="sector-chart">
      <div className="legend" aria-hidden="true">
        <span>
          <span className="swatch bar-organizations" /> Organizations
        </span>
        {peopleLinked && (
          <span>
            <span className="swatch bar-people" /> People
          </span>
        )}
        {sectors.some((s) => s.target !== null) && (
          <span>
            <span className="swatch target-swatch" /> Target
          </span>
        )}
      </div>
      <ul className="sector-rows">
        {sectors.map((sector) => {
          const people =
            peopleLinked && sector.people_share !== null
              ? `, ${formatShare(sector.people_share)} of people`
              : "";
          const target = sector.target !== null ? `, target ${formatShare(sector.target)}` : "";
          const label = `${sector.name}: ${formatShare(sector.organization_share)} of organizations${people}${target}`;
          return (
            <li key={sector.code}>
              <button
                type="button"
                className={sector.code === selectedCode ? "sector-row selected" : "sector-row"}
                aria-pressed={sector.code === selectedCode}
                aria-label={label}
                title={label}
                onClick={() => onSelect(sector.code)}
              >
                <span className="sector-name">{sector.name}</span>
                <span className="sector-bars">
                  <Bar
                    value={sector.organization_share}
                    max={max}
                    series="organizations"
                    target={sector.target}
                  />
                  {peopleLinked && sector.people_share !== null && (
                    <Bar value={sector.people_share} max={max} series="people" />
                  )}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      <figcaption className="muted">
        Share of the network's classified organizations{peopleLinked ? " and people" : ""} in each
        sector. Select a sector to see who is behind it.
      </figcaption>
      <details>
        <summary>Table view</summary>
        <table className="field-changes">
          <thead>
            <tr>
              <th scope="col">Sector</th>
              <th scope="col">Organizations</th>
              {peopleLinked && <th scope="col">People</th>}
              <th scope="col">Target</th>
            </tr>
          </thead>
          <tbody>
            {sectors.map((sector) => (
              <tr key={sector.code}>
                <th scope="row">{sector.name}</th>
                <td>
                  {sector.organization_count} ({formatShare(sector.organization_share)})
                </td>
                {peopleLinked && (
                  <td>
                    {sector.people_count ?? 0} ({formatShare(sector.people_share ?? 0)})
                  </td>
                )}
                <td>{sector.target !== null ? formatShare(sector.target) : "None"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}
