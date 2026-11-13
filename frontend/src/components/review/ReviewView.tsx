// AI contribution: 50% or more AI-generated
import { useEffect, useState } from "react";
import {
  type CandidateAction,
  type CandidateStatus,
  decideCandidate,
  getCandidates,
  type MatchCandidate,
  type Page,
  type RecordRef,
} from "../../services/api";
import { ErrorMessage } from "../ErrorMessage";
import { CandidateCard } from "./CandidateCard";
import "./ReviewView.css";

const TABS: { status: CandidateStatus; label: string }[] = [
  { status: "pending", label: "To review" },
  { status: "accepted", label: "Accepted" },
  { status: "rejected", label: "Rejected" },
];

const EMPTY: Record<CandidateStatus, string> = {
  pending: "No possible matches to review. Matching sets uncertain pairs aside here.",
  accepted: "No accepted matches yet.",
  rejected: "No rejected matches yet.",
};

interface ReviewViewProps {
  tenantId: string;
  onOpenRecord: (record: RecordRef) => void;
}

export function ReviewView({ tenantId, onOpenRecord }: ReviewViewProps) {
  const [status, setStatus] = useState<CandidateStatus>("pending");
  const [page, setPage] = useState(1);
  const [reload, setReload] = useState(0);
  const [loaded, setLoaded] = useState<{ key: string; list: Page<MatchCandidate> } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const key = `${tenantId}/${status}/${page}/${reload}`;

  useEffect(() => {
    let current = true;
    getCandidates(tenantId, status, page)
      .then((list) => current && setLoaded({ key, list }))
      .catch((err: unknown) => current && setError(err));
    return () => {
      current = false;
    };
  }, [tenantId, status, page, key]);

  const decide = (candidate: MatchCandidate, action: CandidateAction) => {
    setBusyId(candidate.id);
    setActionError(null);
    decideCandidate(tenantId, candidate.id, action)
      .catch((err: unknown) => setActionError(err))
      .finally(() => {
        setBusyId(null);
        // The pair leaves this tab whatever happened; a conflict means it already did.
        setReload((n) => n + 1);
      });
  };

  const list = loaded?.key === key ? loaded.list : null;
  const pages = list ? Math.max(1, Math.ceil(list.total / list.page_size)) : 1;

  return (
    <section aria-label="Review possible matches" className="review">
      <div className="toolbar">
        <h2>Possible matches{list ? ` (${list.total})` : ""}</h2>
        <div className="filters" role="group" aria-label="Status">
          {TABS.map((tab) => (
            <button
              key={tab.status}
              type="button"
              aria-pressed={status === tab.status}
              className={status === tab.status ? "filter active" : "filter"}
              onClick={() => {
                setStatus(tab.status);
                setPage(1);
              }}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>
      {actionError !== null && <ErrorMessage error={actionError} />}
      {error !== null ? (
        <ErrorMessage error={error} />
      ) : list === null ? (
        <p className="muted">Loading possible matches...</p>
      ) : list.items.length === 0 ? (
        <p className="empty">{EMPTY[status]}</p>
      ) : (
        <>
          {list.items.map((candidate) => (
            <CandidateCard
              key={candidate.id}
              candidate={candidate}
              busy={busyId !== null}
              onDecide={(action) => decide(candidate, action)}
              onOpenRecord={onOpenRecord}
            />
          ))}
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
        </>
      )}
    </section>
  );
}
