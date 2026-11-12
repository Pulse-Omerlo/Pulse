// AI contribution: 50% or more AI-generated
import type { MixOrganization, MixPerson, RecordRef } from "../../services/api";

interface OrganizationLinksProps {
  organizations: MixOrganization[];
  onOpenOrganization: (organizationId: string) => void;
  onOpenRecord: (record: RecordRef) => void;
}

export function OrganizationLinks({
  organizations,
  onOpenOrganization,
  onOpenRecord,
}: OrganizationLinksProps) {
  if (organizations.length === 0) return <p className="empty">No organizations.</p>;
  return (
    <ul className="name-list">
      {[...organizations]
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((organization) => {
          const { organization_id: id, record } = organization;
          return (
            <li key={organization.key}>
              {id || record ? (
                <button
                  type="button"
                  className="link"
                  onClick={() => (id ? onOpenOrganization(id) : record && onOpenRecord(record))}
                >
                  {organization.name}
                </button>
              ) : (
                organization.name
              )}
            </li>
          );
        })}
    </ul>
  );
}

export function PeopleNames({ people }: { people: MixPerson[] }) {
  if (people.length === 0) return <p className="empty">No people.</p>;
  return (
    <ul className="name-list">
      {[...people]
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((person) => (
          <li key={person.id}>{person.name}</li>
        ))}
    </ul>
  );
}
