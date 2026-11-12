// AI contribution: 50% or more AI-generated
import type { CandidateAction, MatchCandidate, RecordRef } from "../../services/api";
import { formatTime } from "../syncRuns/format";

interface CandidateCardProps {
  candidate: MatchCandidate;
  busy: boolean;
  onDecide: (action: CandidateAction) => void;
  onOpenRecord: (record: RecordRef) => void;
}

function rules(candidate: MatchCandidate): string {
  const names = candidate.matched_fields
    .map((entry) => entry.rule ?? entry.field)
    .filter((name): name is string => typeof name === "string");
  return [...new Set(names)].join(", ") || "none";
}

function Value({ value }: { value: { value: string | null; normalized: string | null } }) {
  if (!value.value && !value.normalized) return <span className="muted">(empty)</span>;
  return (
    <>
      {value.value ?? <span className="muted">(empty)</span>}
      {value.normalized && value.normalized !== value.value && (
        <div className="muted">normalized: {value.normalized}</div>
      )}
    </>
  );
}

export function CandidateCard({ candidate, busy, onDecide, onOpenRecord }: CandidateCardProps) {
  const right = new Map(candidate.right_values.map((value) => [value.field, value]));
  const fields = candidate.left_values.map((left) => ({
    field: left.field,
    left,
    right: right.get(left.field) ?? { field: left.field, value: null, normalized: null },
  }));

  return (
    <article
      className="candidate"
      aria-label={`${candidate.left.label} and ${candidate.right.label}`}
    >
      <div className="candidate-heading">
        <span className="candidate-score">{Math.round(candidate.score * 100)}% likely</span>
        <span className="muted">matched on {rules(candidate)}</span>
      </div>
      <table className="field-changes">
        <thead>
          <tr>
            <th scope="col">Field</th>
            <th scope="col">
              <button type="button" className="link" onClick={() => onOpenRecord(candidate.left)}>
                {candidate.left.label}
              </button>
            </th>
            <th scope="col">
              <button type="button" className="link" onClick={() => onOpenRecord(candidate.right)}>
                {candidate.right.label}
              </button>
            </th>
          </tr>
        </thead>
        <tbody>
          {fields.map(({ field, left, right: other }) => (
            <tr
              key={field}
              className={left.normalized && left.normalized === other.normalized ? "same" : ""}
            >
              <th scope="row">{field}</th>
              <td>
                <Value value={left} />
              </td>
              <td>
                <Value value={other} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {candidate.status === "pending" ? (
        <div className="filters">
          <button type="button" disabled={busy} onClick={() => onDecide("accept")}>
            Same organization
          </button>
          <button type="button" disabled={busy} onClick={() => onDecide("reject")}>
            Different organizations
          </button>
        </div>
      ) : (
        <div className="filters">
          <span className="muted">
            {candidate.status === "accepted" ? "Accepted" : "Rejected"}
            {candidate.decided_by ? ` by ${candidate.decided_by}` : ""}
            {candidate.decided_at ? `, ${formatTime(candidate.decided_at)}` : ""}
          </span>
          <button type="button" disabled={busy} onClick={() => onDecide("undo")}>
            Undo
          </button>
        </div>
      )}
    </article>
  );
}
