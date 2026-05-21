"""Integration tests for the admin API."""

from __future__ import annotations

import uuid

import pytest


@pytest.mark.asyncio
class TestTenantCRUD:
    async def test_create_tenant(self, client):
        resp = await client.post("/admin/tenants", json={"name": "acme-corp"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "acme-corp"
        assert body["is_active"] is True
        assert "id" in body

    async def test_create_duplicate_tenant_fails(self, client):
        await client.post("/admin/tenants", json={"name": "duplicate-co"})
        resp = await client.post("/admin/tenants", json={"name": "duplicate-co"})
        assert resp.status_code == 400

    async def test_list_tenants(self, client):
        await client.post("/admin/tenants", json={"name": "tenant-a"})
        await client.post("/admin/tenants", json={"name": "tenant-b"})
        resp = await client.get("/admin/tenants")
        assert resp.status_code == 200
        names = {t["name"] for t in resp.json()}
        assert {"tenant-a", "tenant-b"}.issubset(names)

    async def test_get_tenant(self, client):
        create = await client.post("/admin/tenants", json={"name": "get-me"})
        tenant_id = create.json()["id"]
        resp = await client.get(f"/admin/tenants/{tenant_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == tenant_id

    async def test_get_missing_tenant_404(self, client):
        resp = await client.get(f"/admin/tenants/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_deactivate_tenant(self, client):
        create = await client.post("/admin/tenants", json={"name": "bye-co"})
        tenant_id = create.json()["id"]
        resp = await client.delete(f"/admin/tenants/{tenant_id}")
        assert resp.status_code == 204
        # Now get should return deactivated tenant
        get = await client.get(f"/admin/tenants/{tenant_id}")
        assert get.json()["is_active"] is False


@pytest.mark.asyncio
class TestApiKeys:
    async def _create_tenant(self, client) -> str:
        r = await client.post("/admin/tenants", json={"name": f"key-tenant-{uuid.uuid4().hex[:6]}"})
        return r.json()["id"]

    async def test_issue_key(self, client):
        tid = await self._create_tenant(client)
        resp = await client.post(f"/admin/tenants/{tid}/keys", json={"label": "test-key"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["plaintext"].startswith("sk_live_")
        assert "plaintext" in body  # shown once
        assert body["label"] == "test-key"

    async def test_list_keys_hides_plaintext(self, client):
        tid = await self._create_tenant(client)
        await client.post(f"/admin/tenants/{tid}/keys", json={"label": "k1"})
        resp = await client.get(f"/admin/tenants/{tid}/keys")
        assert resp.status_code == 200
        keys = resp.json()
        assert len(keys) == 1
        assert "plaintext" not in keys[0]
        assert "prefix" in keys[0]

    async def test_revoke_key(self, client):
        tid = await self._create_tenant(client)
        issue = await client.post(f"/admin/tenants/{tid}/keys", json={})
        key_id = issue.json()["id"]
        resp = await client.delete(f"/admin/tenants/{tid}/keys/{key_id}")
        assert resp.status_code == 204
        # Key should now show as inactive
        keys = await client.get(f"/admin/tenants/{tid}/keys")
        assert keys.json()[0]["is_active"] is False


@pytest.mark.asyncio
class TestPolicyCRUD:
    async def _create_tenant(self, client) -> str:
        r = await client.post(
            "/admin/tenants", json={"name": f"policy-tenant-{uuid.uuid4().hex[:6]}"}
        )
        return r.json()["id"]

    async def test_get_policy_none_before_set(self, client):
        tid = await self._create_tenant(client)
        resp = await client.get(f"/admin/tenants/{tid}/policy")
        assert resp.status_code == 200
        assert resp.json() is None

    async def test_set_and_get_policy(self, client):
        tid = await self._create_tenant(client)
        payload = {
            "max_tokens_per_request": 512,
            "max_requests_per_minute": 30,
            "monthly_cost_ceiling_usd": 25.0,
            "allowed_models": ["claude-haiku-4-5"],
            "denied_topics": ["weapons", "malware"],
            "redact_pii": True,
        }
        put = await client.put(f"/admin/tenants/{tid}/policy", json=payload)
        assert put.status_code == 200
        body = put.json()
        assert body["max_tokens_per_request"] == 512
        assert body["allowed_models"] == ["claude-haiku-4-5"]
        assert body["denied_topics"] == ["weapons", "malware"]

    async def test_update_policy(self, client):
        tid = await self._create_tenant(client)
        await client.put(f"/admin/tenants/{tid}/policy", json={"max_tokens_per_request": 1024})
        resp = await client.put(
            f"/admin/tenants/{tid}/policy", json={"max_tokens_per_request": 2048}
        )
        assert resp.json()["max_tokens_per_request"] == 2048

    async def test_policy_on_missing_tenant(self, client):
        resp = await client.put(
            f"/admin/tenants/{uuid.uuid4()}/policy",
            json={"max_tokens_per_request": 512},
        )
        assert resp.status_code == 404
