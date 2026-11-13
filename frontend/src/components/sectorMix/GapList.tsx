// AI contribution: 50% or more AI-generated
import type { Gap, GapKind } from "../../services/api";

const KIND_LABELS: Record<GapKind, string> = {
  too_few_organizations: "Few organizations",
  too_few_people: "Few people",
  below_target: "Below target",
};

interface GapListProps {
  gaps: Gap[];
  selectedCode: string | null;
  onSelect: (code: string) => void;
}

export function GapList({ gaps, selectedCode, onSelect }: GapListProps) {
  if (gaps.length === 0) {
    return <p className="empty">No gaps: every sector is within its threshold and targets.</p>;
  }
  return (
    <ol className="gap-list">
      {gaps.map((gap) => (
        <li key={`${gap.code}-${gap.kind}`}>
          <button
            type="button"
            className={gap.code === selectedCode ? "gap selected" : "gap"}
            aria-pressed={gap.code === selectedCode}
            onClick={() => onSelect(gap.code)}
          >
            <span className="gap-heading">
              <span className="gap-size">{gap.size.toFixed(1)} pts</span>
              <span className="gap-kind">{KIND_LABELS[gap.kind]}</span>
            </span>
            <span>{gap.explanation}</span>
          </button>
        </li>
      ))}
    </ol>
  );
}
