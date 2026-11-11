# AI contribution: 50% or more AI-generated
"""Write a plan into an Omerlo test workspace: organizations, then people, then links."""

import time
from collections.abc import Callable
from typing import Any, TypeVar
from urllib.parse import urlsplit

from synthetic.pipeline.payloads import PayloadError, organization_payload, person_payload
from synthetic.pipeline.plan import EXTERNAL_ID_KEY, Plan
from synthetic.pipeline.report import LINKS, RunReport
from synthetic.pipeline.target import Target
from synthetic.pipeline.writers.base import (
    KINDS,
    ORGANIZATIONS,
    PEOPLE,
    OmerloWriter,
    WriteRejected,
)

PRODUCTION_HOSTS = frozenset({"ca.omerlo.app", "eu.omerlo.app"})
TEST_HOST_LABELS = frozenset({"staging", "dev", "test", "qa", "sandbox"})

T = TypeVar("T")


class TenantGuardError(Exception):
    """The target does not look like the configured test tenant; nothing was written."""


def check_host(base_url: str) -> None:
    host = urlsplit(base_url).hostname or ""
    if host in PRODUCTION_HOSTS:
        raise TenantGuardError(f"{host} is a production host")
    if not TEST_HOST_LABELS & set(host.split(".")):
        raise TenantGuardError(f"{host} is not a test host (no staging, dev or test label)")


class OmerloSink:
    def __init__(
        self,
        writer: OmerloWriter,
        target: Target,
        *,
        retry_delay: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.writer = writer
        self.target = target
        self.retry_delay = retry_delay
        self.sleep = sleep
        self.report = RunReport()

    def run(self, plan: Plan, *, dry_run: bool = False) -> RunReport:
        self.report = RunReport(dry_run=dry_run)
        listings = self.guard(plan)
        existing = self.reconcile(listings)
        org_ids = {
            org.external_id: self.write(
                ORGANIZATIONS, org.external_id, org.record, org.metadata, existing, dry_run
            )
            for org in plan.organizations
        }
        person_ids = {
            person.external_id: self.write(
                PEOPLE, person.external_id, person.record, [], existing, dry_run
            )
            for person in plan.people
        }
        self.link(plan, org_ids, person_ids, existing[PEOPLE], dry_run)
        return self.report

    def guard(self, plan: Plan) -> dict[str, list[dict[str, Any]]]:
        """Refuse any target that is not the configured test workspace, before writing."""
        target = self.target
        check_host(target.base_url)
        visible = {w["id"] for w in self.writer.list_workspaces()}
        if target.workspace_id not in visible:
            raise TenantGuardError(
                f"workspace {target.workspace_id} is not visible to this key; is it a member?"
            )
        extra = sorted(plan.locales - set(target.locales))
        if extra:
            raise TenantGuardError(
                f"the plan has locale(s) {', '.join(extra)} the target does not allow"
            )
        listings = {kind: self.writer.list_records(kind, target.workspace_id) for kind in KINDS}
        prefix = f"{target.tenant_id}/"
        for records in listings.values():
            for record in records:
                key = record.get("profile_image_key")
                if key and not key.startswith(prefix):
                    raise TenantGuardError(
                        f"record {record['id']} belongs to another tenant (image key {key})"
                    )
        return listings

    def reconcile(self, listings: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, str]]:
        """External id to Omerlo id for records an earlier run made; others are left alone."""
        existing: dict[str, dict[str, str]] = {kind: {} for kind in KINDS}
        for kind, records in listings.items():
            for record in records:
                entries = self.writer.get_metadata(kind, record["id"])
                ours = next((e["value"] for e in entries if e["key"] == EXTERNAL_ID_KEY), None)
                if ours is None:
                    self.report.untouched += 1
                else:
                    existing[kind][ours] = record["id"]
        return existing

    def write(
        self,
        kind: str,
        external_id: str,
        record: dict[str, Any],
        metadata: list[dict[str, str]],
        existing: dict[str, dict[str, str]],
        dry_run: bool,
    ) -> str | None:
        """The record's Omerlo id once it exists, or None when it was refused or not written."""
        counts = self.report.counts[kind]
        if external_id in existing[kind]:
            counts.skipped += 1
            return existing[kind][external_id]
        build = organization_payload if kind == ORGANIZATIONS else person_payload
        try:
            payload = build(record, self.target)
        except PayloadError as error:
            self.report.reject(kind, external_id, str(error))
            return None
        if dry_run:
            counts.created += 1
            return None
        try:
            record_id = self.attempt(
                lambda: self.writer.create(kind, self.target.workspace_id, payload)
            )
        except WriteRejected as error:
            self.report.reject(kind, external_id, str(error))
            return None
        entries = [{"key": EXTERNAL_ID_KEY, "value": external_id}, *metadata]
        try:
            self.attempt(lambda: self.writer.set_metadata(kind, record_id, entries))
        except WriteRejected as error:
            reason = f"created as {record_id} but its metadata was refused: {error}"
            self.report.reject(kind, external_id, reason)
            return None
        counts.created += 1
        return record_id

    def link(
        self,
        plan: Plan,
        org_ids: dict[str, str | None],
        person_ids: dict[str, str | None],
        existing_people: dict[str, str],
        dry_run: bool,
    ) -> None:
        counts = self.report.counts[LINKS]
        rejected_people = {r.external_id for r in self.report.rejections if r.kind == PEOPLE}
        rejected_orgs = {r.external_id for r in self.report.rejections if r.kind == ORGANIZATIONS}
        block_type = self.target.employer_block_type
        for person in plan.people:
            if person.external_id in rejected_people:
                self.report.reject(LINKS, person.external_id, "the person was not created")
                continue
            if person.employer in rejected_orgs:
                reason = f"employer {person.employer} was not created"
                self.report.reject(LINKS, person.external_id, reason)
                continue
            person_id, org_id = person_ids[person.external_id], org_ids[person.employer]
            if person.external_id in existing_people and org_id is not None:
                assert person_id is not None
                if self.writer.get_employer(person_id, block_type) == org_id:
                    counts.skipped += 1
                    continue
            if dry_run:
                counts.created += 1
                continue
            assert person_id is not None and org_id is not None
            try:
                self.attempt(
                    lambda p=person_id, o=org_id: self.writer.set_employer(p, o, block_type)
                )
            except WriteRejected as error:
                self.report.reject(LINKS, person.external_id, str(error))
                continue
            counts.created += 1

    def attempt(self, call: Callable[[], T]) -> T:
        """Run a write, and run it once more if Omerlo refuses it the first time."""
        try:
            return call()
        except WriteRejected:
            self.sleep(self.retry_delay)
            return call()
