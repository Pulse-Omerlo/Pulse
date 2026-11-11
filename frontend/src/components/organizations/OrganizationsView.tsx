// AI contribution: 50% or more AI-generated
import { useEffect, useState } from "react";
import {
  getOrganization,
  getOrganizations,
  getOrganizationTimeline,
  type Organization,
  type OrganizationSort,
  type OrganizationSummary,
  type Page,
  type RecordRef,
  type TimelineEntry,
} from "../../services/api";
import { ErrorMessage } from "../ErrorMessage";
import { FieldChanges } from "../syncRuns/FieldChanges";
import { formatTime } from "../syncRuns/format";

interface OrganizationsViewProps {
  tenantId: string;
  onOpenRecord: (record: RecordRef) => void;
}

export function OrganizationsView({ tenantId, onOpenRecord }: OrganizationsViewProps) {
  const [sort, setSort] = useState<OrganizationSort>("name");
  const [page, setPage] = useState(1);
  const [list, setList] = useState<Page<OrganizationSummary> | null>(null);
  const [selected, setSelected] = useState<Organization | null>(null);
  const [timeline, setTimeline] = useState<TimelineEntry[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let current = true;
    getOrganizations(tenantId, sort, page)
      .then((result) => current && setList(result))
      .catch((err: unknown) => current && setError(err));
    return () => {
      current = false;
    };
  }, [tenantId, sort, page]);

  const open = (organizationId: string) => {
    setTimeline(null);
    getOrganization(tenantId, organizationId).then(setSelected).catch(setError);
    getOrganizationTimeline(tenantId, organizationId).then(setTimeline).catch(setError);
  };

  if (error !== null) return <ErrorMessage error={error} />;

  if (selected) {
    return (
      <section aria-label="Organization">
        <div className="history-heading">
          <h2>{selected.display_name}</h2>
          <button type="button" onClick={() => setSelected(null)}>
            Back
          </button>
        </div>
        {selected.primary_domain && <p className="muted">{selected.primary_domain}</p>}

        <h3>Source records</h3>
        <ul className="change-list">
          {selected.records.map(({ record, link, versions }) => (
            <li key={record.version_id} className="change">
              <button type="button" className="link" onClick={() => onOpenRecord(record)}>
                {record.label}
              </button>{" "}
              <span className="muted">
                matched by {link.method}, {versions.length} version
                {versions.length === 1 ? "" : "s"}
              </span>
            </li>
          ))}
        </ul>

        <h3>Timeline</h3>
        {timeline === null ? (
          <p className="muted">Loading timeline...</p>
        ) : timeline.length === 0 ? (
          <p className="empty">No changes yet.</p>
        ) : (
          <ol className="change-list">
            {timeline.map((entry) => (
              <li key={`${entry.sync_run_id}-${entry.record.version_id}`} className="change">
                <div className="change-heading">
                  <span className={`badge badge-${entry.change_type}`}>{entry.change_type}</span>
                  <span className="muted">{formatTime(entry.started_at)}</span>
                  <button type="button" className="link" onClick={() => onOpenRecord(entry.record)}>
                    {entry.record.label}
                  </button>
                </div>
                <FieldChanges fields={entry.changed_fields} />
              </li>
            ))}
          </ol>
        )}
      </section>
    );
  }

  if (list === null) return <p className="muted">Loading organizations...</p>;

  const pages = Math.max(1, Math.ceil(list.total / list.page_size));

  return (
    <section aria-label="Organizations">
      <div className="toolbar">
        <h2>Organizations ({list.total})</h2>
        <label>
          Sort by{" "}
          <select
            value={sort}
            onChange={(event) => {
              setSort(event.target.value as OrganizationSort);
              setPage(1);
            }}
          >
            <option value="name">Name</option>
            <option value="last_changed">Last change</option>
          </select>
        </label>
      </div>
      {list.items.length === 0 ? (
        <p className="empty">No organizations yet. Run a sync for this tenant.</p>
      ) : (
        <table className="field-changes">
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">Source records</th>
              <th scope="col">Last change</th>
            </tr>
          </thead>
          <tbody>
            {list.items.map((item) => (
              <tr key={item.organization_id ?? item.record?.version_id}>
                <td>
                  <button
                    type="button"
                    className="link"
                    onClick={() =>
                      item.organization_id
                        ? open(item.organization_id)
                        : item.record && onOpenRecord(item.record)
                    }
                  >
                    {item.name}
                  </button>
                </td>
                <td>{item.records}</td>
                <td>{formatTime(item.last_changed)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="filters">
        <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>
          Previous
        </button>
        <span className="muted">
          Page {page} of {pages}
        </span>
        <button type="button" disabled={page >= pages} onClick={() => setPage(page + 1)}>
          Next
        </button>
      </div>
    </section>
  );
}
