# AI contribution: 50% or more AI-generated
"""Simulated people browsing one tenant in PULSE, for measuring API response times.

uv run --group load locust -f loadtest/locustfile.py --host http://127.0.0.1:8000
See loadtest/README.md for the headless run and the target.
"""

import os
import random

from locust import HttpUser, between, task

from loadtest.pages import organization_ids, page_count, tenant_id_for_slug

TENANT_SLUG = os.environ.get("LOAD_TENANT", "demo-capitale")
PAGE_SIZE = 50


class TenantUser(HttpUser):
    # A person reads a page for 1 to 3 seconds before clicking the next one.
    wait_time = between(1, 3)

    def on_start(self):
        tenants = self.client.get("/api/tenants").json()
        self.tenant_id = tenant_id_for_slug(tenants, TENANT_SLUG)
        self.base = f"/api/tenants/{self.tenant_id}"
        self.pages = 1
        self.known_ids = []

    @task(5)
    def organization_list(self):
        page = random.randint(1, self.pages)
        response = self.client.get(
            f"{self.base}/organizations",
            params={"page": page, "page_size": PAGE_SIZE},
            name="/organizations",
        )
        if response.ok:
            body = response.json()
            self.pages = page_count(body["total"], PAGE_SIZE)
            self.known_ids += organization_ids(body)
            # Keep only the latest ids so memory stays small during long runs.
            self.known_ids = self.known_ids[-200:]

    @task(3)
    def one_organization(self):
        if not self.known_ids:
            return
        organization_id = random.choice(self.known_ids)
        self.client.get(f"{self.base}/organizations/{organization_id}", name="/organizations/{id}")

    @task(2)
    def duplicate_candidates(self):
        self.client.get(f"{self.base}/duplicates/candidates", name="/duplicates/candidates")

    @task(1)
    def sync_runs(self):
        self.client.get(f"{self.base}/runs", name="/runs")

    @task(1)
    def sector_mix(self):
        self.client.get(f"{self.base}/sector-mix", name="/sector-mix")
