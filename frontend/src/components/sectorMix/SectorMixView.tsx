// AI contribution: 50% or more AI-generated
import { useEffect, useState } from "react";
import {
  ApiError,
  getSectorMix,
  type RecordRef,
  type SectorMix,
  type SyncRun,
} from "../../services/api";
import { ErrorMessage } from "../ErrorMessage";
import { formatTime } from "../syncRuns/format";
import { GapList } from "./GapList";
import { SectorChart } from "./SectorChart";
import { OrganizationLinks, PeopleNames } from "./SectorDetail";
import "./SectorMixView.css";

interface SectorMixViewProps {
  tenantId: string;
  runs: SyncRun[];
  onOpenOrganization: (organizationId: string) => void;
  onOpenRecord: (record: RecordRef) => void;
}

type Loaded = { mix: SectorMix } | { missing: true } | { error: unknown };

export function SectorMixView({
  tenantId,
  runs,
  onOpenOrganization,
  onOpenRecord,
}: SectorMixViewProps) {
  // Empty means the latest run that has a sector mix.
  const [runId, setRunId] = useState("");
  const [loaded, setLoaded] = useState<{ key: string; result: Loaded } | null>(null);
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const key = `${tenantId}/${runId}`;

  useEffect(() => {
    let current = true;
    getSectorMix(tenantId, runId || undefined)
      .then((mix) => current && setLoaded({ key, result: { mix } }))
      .catch((error: unknown) => {
        if (!current) return;
        const missing = error instanceof ApiError && error.status === 404;
        setLoaded({ key, result: missing ? { missing: true } : { error } });
      });
    return () => {
      current = false;
    };
  }, [tenantId, runId, key]);

  const finished = runs.filter((run) => run.status === "succeeded");
  const result = loaded?.key === key ? loaded.result : null;
  const mix = result && "mix" in result ? result.mix : null;
  const shownRun = mix ? runs.find((run) => run.id === mix.sync_run_id) : undefined;

  const picker = (
    <label>
      Sync run{" "}
      <select
        value={runId}
        onChange={(event) => {
          setRunId(event.target.value);
          setSelectedCode(null);
        }}
      >
        <option value="">Latest</option>
        {finished.map((run) => (
          <option key={run.id} value={run.id}>
            {formatTime(run.started_at)}
          </option>
        ))}
      </select>
    </label>
  );

  const heading = (
    <div className="toolbar">
      <h2>Sector mix</h2>
      {picker}
    </div>
  );

  if (result === null) {
    return (
      <section aria-label="Sector mix">
        {heading}
        <p className="muted">Loading the sector mix...</p>
      </section>
    );
  }
  if ("error" in result) {
    return (
      <section aria-label="Sector mix">
        {heading}
        <ErrorMessage error={result.error} />
      </section>
    );
  }
  if ("missing" in result) {
    return (
      <section aria-label="Sector mix">
        {heading}
        <p className="empty">
          {runId
            ? "This sync run has no sector mix. Pick another run."
            : "No sector mix yet. It is computed after each successful sync of this network."}
        </p>
      </section>
    );
  }

  const { mix: shown } = result;
  const peopleLinked = shown.people_status === "linked";
  const baseline = shown.settings.baseline ?? null;
  const sectors = [...shown.sectors].sort(
    (a, b) => b.organization_share - a.organization_share || a.name.localeCompare(b.name),
  );
  const selected = sectors.find((sector) => sector.code === selectedCode) ?? null;

  return (
    <section aria-label="Sector mix" className="sector-mix">
      {heading}
      <p className="muted">
        From the sync run of {shownRun ? formatTime(shownRun.started_at) : "an earlier date"}:{" "}
        {shown.classified} of {shown.organizations} organizations classified
        {peopleLinked
          ? `, ${shown.people - shown.people_unplaced.length} of ${shown.people} people placed.`
          : ". People are not linked to organizations yet."}
      </p>

      {sectors.length === 0 ? (
        <p className="empty">No organization is classified into a sector yet.</p>
      ) : (
        <div className="mix-columns">
          <div>
            <SectorChart
              sectors={sectors}
              peopleLinked={peopleLinked}
              regionName={baseline?.region_name ?? null}
              selectedCode={selectedCode}
              onSelect={setSelectedCode}
            />
          </div>
          <div>
            <h3>Gaps, largest first</h3>
            <GapList gaps={shown.gaps} selectedCode={selectedCode} onSelect={setSelectedCode} />
          </div>
        </div>
      )}

      {baseline && (
        <p className="muted baseline-source">
          {baseline.region_name} area shares are of the{" "}
          {baseline.classified_businesses.toLocaleString("en-CA")} employer businesses Statistics
          Canada classified on {baseline.reference_period}; the{" "}
          {baseline.unclassified_businesses.toLocaleString("en-CA")} it could not classify are left
          out. {baseline.attribution} ({baseline.licence})
        </p>
      )}

      {selected && (
        <section aria-label={`${selected.name} details`} className="sector-detail">
          <div className="history-heading">
            <h3>{selected.name}</h3>
            <button type="button" onClick={() => setSelectedCode(null)}>
              Close
            </button>
          </div>
          <h4>Organizations ({selected.organization_count})</h4>
          <OrganizationLinks
            organizations={selected.organizations}
            onOpenOrganization={onOpenOrganization}
            onOpenRecord={onOpenRecord}
          />
          {selected.people !== null && (
            <>
              <h4>People ({selected.people.length})</h4>
              <PeopleNames people={selected.people} />
            </>
          )}
        </section>
      )}

      {shown.unclassified.length > 0 && (
        <details className="unclassified">
          <summary>
            {shown.unclassified.length} organization
            {shown.unclassified.length === 1 ? "" : "s"} without a sector
          </summary>
          <OrganizationLinks
            organizations={shown.unclassified}
            onOpenOrganization={onOpenOrganization}
            onOpenRecord={onOpenRecord}
          />
        </details>
      )}
    </section>
  );
}
