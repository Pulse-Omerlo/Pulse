import { logger } from "./logger";

const API_BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  readonly status: number | null;
  readonly code: string;
  readonly requestId: string | null;

  constructor(
    message: string,
    options: { status: number | null; code: string; requestId?: string | null },
  ) {
    super(message);
    this.name = "ApiError";
    this.status = options.status;
    this.code = options.code;
    this.requestId = options.requestId ?? null;
  }
}

interface ErrorBody {
  error?: { code?: string; message?: string; request_id?: string | null };
}

export interface Health {
  status: string;
  service: string;
}

function failed(path: string, error: ApiError): ApiError {
  logger.warn("api_request_failed", {
    path,
    status: error.status,
    code: error.code,
    requestId: error.requestId,
  });
  return error;
}

async function request<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`);
  } catch {
    throw failed(
      path,
      new ApiError("Cannot reach the PULSE server.", { status: null, code: "network_error" }),
    );
  }

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as ErrorBody | null;
    throw failed(
      path,
      new ApiError(body?.error?.message ?? `Request failed with status ${response.status}`, {
        status: response.status,
        code: body?.error?.code ?? "http_error",
        requestId: body?.error?.request_id ?? null,
      }),
    );
  }

  return (await response.json()) as T;
}

export function getHealth(): Promise<Health> {
  return request<Health>("/api/health");
}

export type ChangeType = "added" | "modified" | "removed";

export interface FieldChange {
  before: unknown;
  after: unknown;
}

export interface Tenant {
  id: string;
  name: string;
  slug: string | null;
}

export interface SyncRun {
  id: string;
  source_id: string;
  mode: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  counts: Record<string, unknown> & { changes?: Partial<Record<ChangeType, number>> };
  error: string | null;
}

export interface RecordChange {
  change_type: ChangeType;
  kind: string;
  source_id: string;
  external_id: string;
  label: string;
  changed_fields: Record<string, FieldChange>;
}

export interface RecordSummary {
  source_id: string;
  external_id: string;
  kind: string;
  label: string;
  domain: string | null;
  linkedin: string | null;
  valid_from: string;
}

export interface VersionEvent {
  sync_run_id: string;
  change_type: ChangeType;
  changed_fields: Record<string, FieldChange>;
}

export interface RecordVersion {
  id: string;
  label: string;
  name_normalized: string | null;
  domain: string | null;
  linkedin: string | null;
  external_ids: Record<string, unknown>;
  valid_from: string;
  valid_to: string | null;
  first_seen_run: string;
  removed_run: string | null;
  events: VersionEvent[];
}

export interface RecordHistory {
  source_id: string;
  external_id: string;
  kind: string;
  versions: RecordVersion[];
}

export function getTenants(): Promise<Tenant[]> {
  return request<Tenant[]>("/api/tenants");
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

// The largest page the API serves.
const MAX_PAGE_SIZE = 100;

// Every item of a paged list, read page by page.
async function requestAll<T>(path: string): Promise<T[]> {
  const items: T[] = [];
  for (let page = 1; ; page++) {
    const params = new URLSearchParams({ page: String(page), page_size: String(MAX_PAGE_SIZE) });
    const result = await request<Page<T>>(`${path}?${params}`);
    items.push(...result.items);
    if (result.items.length === 0 || items.length >= result.total) {
      return items;
    }
  }
}

export async function getRuns(tenantId: string): Promise<SyncRun[]> {
  const params = new URLSearchParams({ page_size: "50" });
  const page = await request<Page<SyncRun>>(`/api/tenants/${tenantId}/runs?${params}`);
  return page.items;
}

export function getRunChanges(tenantId: string, runId: string): Promise<RecordChange[]> {
  return requestAll<RecordChange>(`/api/tenants/${tenantId}/runs/${runId}/changes`);
}

export function searchRecords(tenantId: string, query: string): Promise<RecordSummary[]> {
  const params = new URLSearchParams({ q: query });
  return request<RecordSummary[]>(`/api/tenants/${tenantId}/records?${params}`);
}

export function getRecordHistory(
  tenantId: string,
  sourceId: string,
  externalId: string,
): Promise<RecordHistory> {
  return request<RecordHistory>(
    `/api/tenants/${tenantId}/records/${sourceId}/${encodeURIComponent(externalId)}`,
  );
}

export interface RecordRef {
  source_id: string;
  external_id: string;
  version_id: string;
  label: string;
}

export interface OrganizationSummary {
  organization_id: string | null;
  record: RecordRef | null;
  name: string;
  records: number;
  last_changed: string;
}

export interface LinkedRecord {
  record: RecordRef;
  link: { method: string; confidence: number; decided_by: string };
  versions: { id: string; label: string; domain: string | null; valid_from: string }[];
}

export interface Organization {
  id: string;
  display_name: string;
  primary_domain: string | null;
  records: LinkedRecord[];
}

export interface TimelineEntry {
  sync_run_id: string;
  started_at: string;
  change_type: ChangeType;
  record: RecordRef;
  changed_fields: Record<string, FieldChange>;
}

export type OrganizationSort = "name" | "last_changed";

export function getOrganizations(
  tenantId: string,
  sort: OrganizationSort,
  page: number,
): Promise<Page<OrganizationSummary>> {
  const params = new URLSearchParams({
    sort,
    order: sort === "name" ? "asc" : "desc",
    page: String(page),
    page_size: "25",
  });
  return request<Page<OrganizationSummary>>(`/api/tenants/${tenantId}/organizations?${params}`);
}

export function getOrganization(tenantId: string, organizationId: string): Promise<Organization> {
  return request<Organization>(`/api/tenants/${tenantId}/organizations/${organizationId}`);
}

export function getOrganizationTimeline(
  tenantId: string,
  organizationId: string,
): Promise<TimelineEntry[]> {
  return requestAll<TimelineEntry>(
    `/api/tenants/${tenantId}/organizations/${organizationId}/timeline`,
  );
}

export interface MixOrganization {
  key: string;
  name: string;
  records: string[];
  // The organization page to open, or the record when it matched no other.
  organization_id: string | null;
  record: RecordRef | null;
}

export interface MixPerson {
  id: string;
  name: string;
  organization: string | null;
}

export interface SectorShare {
  code: string;
  name: string;
  organization_count: number;
  organization_share: number;
  // Null while people are not linked to organizations.
  people_count: number | null;
  people_share: number | null;
  target: number | null;
  organizations: MixOrganization[];
  people: MixPerson[] | null;
}

export type GapKind = "too_few_organizations" | "too_few_people" | "below_target";

export interface Gap {
  code: string;
  kind: GapKind;
  // Percentage points.
  size: number;
  explanation: string;
}

export interface SectorMix {
  sync_run_id: string;
  computed_at: string;
  settings: { gap_points: number; targets: Record<string, number>; version: string };
  organizations: number;
  classified: number;
  people_status: "linked" | "not_linked";
  people: number;
  sectors: SectorShare[];
  gaps: Gap[];
  unclassified: MixOrganization[];
  people_unplaced: MixPerson[];
}

// The latest sector mix, or the one computed after `runId`.
export function getSectorMix(tenantId: string, runId?: string): Promise<SectorMix> {
  const path = runId
    ? `/api/tenants/${tenantId}/runs/${runId}/sector-mix`
    : `/api/tenants/${tenantId}/sector-mix`;
  return request<SectorMix>(path);
}
