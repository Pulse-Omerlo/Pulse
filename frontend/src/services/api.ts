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

// Called when the API answers 401 to a signed-in request: the session ended or expired.
let unauthorizedHandler: (() => void) | null = null;

export function onUnauthorized(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    // The session is an HttpOnly cookie, sent with every request to the API.
    response = await fetch(`${API_BASE_URL}${path}`, { credentials: "include", ...init });
  } catch {
    throw failed(
      path,
      new ApiError("Cannot reach the PULSE server.", { status: null, code: "network_error" }),
    );
  }

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as ErrorBody | null;
    if (response.status === 401 && !path.startsWith("/api/auth/")) {
      unauthorizedHandler?.();
    }
    throw failed(
      path,
      new ApiError(body?.error?.message ?? `Request failed with status ${response.status}`, {
        status: response.status,
        code: body?.error?.code ?? "http_error",
        requestId: body?.error?.request_id ?? null,
      }),
    );
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export interface SignedInUser {
  email: string;
  tenant_id: string;
  tenant_name: string;
}

export function signIn(email: string, password: string): Promise<SignedInUser> {
  return request<SignedInUser>("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
}

export function signOut(): Promise<void> {
  return request<void>("/api/auth/logout", { method: "POST" });
}

// The signed-in user, or an ApiError with status 401 when there is no session.
export function getMe(): Promise<SignedInUser> {
  return request<SignedInUser>("/api/auth/me");
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

export type CandidateStatus = "pending" | "accepted" | "rejected";

export interface FieldValue {
  field: string;
  // As stored in the source, and as PULSE normalized it for matching.
  value: string | null;
  normalized: string | null;
}

export interface MatchCandidate {
  id: string;
  score: number;
  status: CandidateStatus;
  left: RecordRef;
  right: RecordRef;
  matched_fields: Record<string, unknown>[];
  left_values: FieldValue[];
  right_values: FieldValue[];
  decided_by: string | null;
  decided_at: string | null;
}

// Possible matches with a status: pending ones by score, decided ones newest first.
export function getCandidates(
  tenantId: string,
  status: CandidateStatus,
  page: number,
): Promise<Page<MatchCandidate>> {
  const params = new URLSearchParams({ status, page: String(page), page_size: "10" });
  return request<Page<MatchCandidate>>(`/api/tenants/${tenantId}/duplicates/candidates?${params}`);
}

export type CandidateAction = "accept" | "reject" | "undo";

export function decideCandidate(
  tenantId: string,
  candidateId: string,
  action: CandidateAction,
): Promise<MatchCandidate> {
  return request<MatchCandidate>(
    `/api/tenants/${tenantId}/duplicates/candidates/${candidateId}/${action}`,
    { method: "POST" },
  );
}
