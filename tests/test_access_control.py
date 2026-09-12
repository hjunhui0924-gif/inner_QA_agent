"""Adversarial coverage for server identity and knowledge ACL filtering."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from langchain_core.documents import Document
from starlette.requests import Request

from backend.agent import memory
from backend.agent.nodes import _authorization_filter
from backend.auth.access import (
    AuthenticationError,
    KnowledgeAccessPolicy,
    ServerAccessContext,
    require_access_context,
    resolve_access_context,
)
from backend.config import settings
from backend.knowledge.embeddings import HashingEmbeddings
from backend.api.routes import (
    _assert_user_path,
    download_knowledge_record,
    knowledge_record,
)
from backend.retrieval.engine import RetrievalConfig, RetrievalEngine


def _request(headers: dict[str, str] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [
                (key.lower().encode(), value.encode())
                for key, value in (headers or {}).items()
            ],
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 1234),
        }
    )


class IdentityResolutionTests(unittest.TestCase):
    def test_trusted_headers_resolve_only_with_proxy_secret_and_claims(self) -> None:
        with patch.object(settings, "auth_mode", "trusted_headers"), patch.object(
            settings, "auth_proxy_secret", "proxy-secret"
        ):
            context = resolve_access_context(
                _request(
                    {
                        "X-Auth-Proxy-Secret": "proxy-secret",
                        "X-Authenticated-User": "alice",
                        "X-Authenticated-Roles": "employee",
                        "X-Authenticated-Departments": "Finance,HR",
                        "X-Authenticated-Scopes": "internal,finance.read",
                        "X-Authenticated-Internal": "true",
                    }
                )
            )

        self.assertEqual(context.user_id, "alice")
        self.assertEqual(context.departments, frozenset({"Finance", "HR"}))
        self.assertEqual(context.scopes, frozenset({"internal", "finance.read"}))
        self.assertEqual(context.auth_source, "trusted_proxy")

    def test_missing_or_malformed_trusted_identity_fails_closed(self) -> None:
        with patch.object(settings, "auth_mode", "trusted_headers"), patch.object(
            settings, "auth_proxy_secret", "proxy-secret"
        ):
            with self.assertRaises(AuthenticationError):
                resolve_access_context(_request())
            with self.assertRaises(AuthenticationError):
                resolve_access_context(
                    _request(
                        {
                            "X-Auth-Proxy-Secret": "proxy-secret",
                            "X-Authenticated-User": "alice",
                            "X-Authenticated-Roles": "*",
                            "X-Authenticated-Departments": "Finance",
                            "X-Authenticated-Scopes": "internal",
                            "X-Authenticated-Internal": "true",
                        }
                    )
                )

    def test_fastapi_dependency_hides_auth_details(self) -> None:
        with patch.object(settings, "auth_mode", "trusted_headers"), patch.object(
            settings, "auth_proxy_secret", "proxy-secret"
        ):
            with self.assertRaises(HTTPException) as raised:
                require_access_context(_request())
        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(raised.exception.detail, "Authentication required.")


class KnowledgePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = KnowledgeAccessPolicy()
        self.finance_user = ServerAccessContext(
            user_id="alice",
            roles=frozenset({"employee"}),
            departments=frozenset({"Finance"}),
            scopes=frozenset({"internal", "finance.read"}),
        )

    def test_acl_is_default_deny_and_scopes_are_all_of(self) -> None:
        self.assertFalse(self.policy.can_access(self.finance_user, {}))
        self.assertFalse(
            self.policy.can_access(
                self.finance_user,
                {
                    "access_scope": "internal",
                    "department": "Finance",
                    "required_scopes": ["finance.read", "finance.payments"],
                },
            )
        )
        self.assertTrue(
            self.policy.can_access(
                self.finance_user,
                {
                    "access_scope": "internal",
                    "department": "Finance",
                    "required_scopes": ["finance.read"],
                },
            )
        )

    def test_explicit_deny_wins_and_department_is_exact(self) -> None:
        metadata = {
            "access_scope": "internal",
            "department": "Finance",
            "denied_scopes": ["finance.read"],
        }
        self.assertFalse(self.policy.can_access(self.finance_user, metadata))
        self.assertFalse(
            self.policy.can_access(
                self.finance_user,
                {"access_scope": "internal", "department": "HR"},
            )
        )

    def test_public_internal_requires_internal_user_but_not_department(self) -> None:
        metadata = {"access_scope": "public-internal", "department": "unknown"}
        self.assertTrue(self.policy.can_access(self.finance_user, metadata))
        external = ServerAccessContext(
            user_id="external",
            roles=frozenset({"employee"}),
            departments=frozenset(),
            scopes=frozenset(),
            internal_user=False,
        )
        self.assertFalse(self.policy.can_access(external, metadata))

    def test_admin_does_not_bypass_required_scopes_or_explicit_deny(self) -> None:
        admin = ServerAccessContext(
            user_id="admin",
            roles=frozenset({"knowledge_admin"}),
            departments=frozenset(),
            scopes=frozenset({"internal"}),
        )
        self.assertFalse(
            self.policy.can_access(
                admin,
                {
                    "access_scope": "internal",
                    "department": "Finance",
                    "required_scopes": ["finance.secret"],
                },
            )
        )
        self.assertFalse(
            self.policy.can_access(
                admin,
                {
                    "access_scope": "internal",
                    "department": "Finance",
                    "denied_roles": ["knowledge_admin"],
                },
            )
        )


class RetrievalAuthorizationTests(unittest.TestCase):
    def test_authorized_source_ids_are_filtered_before_fusion(self) -> None:
        allowed = Document(
            page_content="Finance reimbursement policy",
            metadata={
                "document_id": "finance",
                "source_id": "finance",
                "chunk_id": "finance:0",
                "access_scope": "internal",
                "department": "Finance",
            },
        )
        forbidden = Document(
            page_content="Finance secret payment policy",
            metadata={
                "document_id": "secret",
                "source_id": "secret",
                "chunk_id": "secret:0",
                "access_scope": "internal",
                "department": "Legal",
            },
        )
        store = memory._LocalVectorStore(
            HashingEmbeddings(64),
            [allowed, forbidden],
        )
        engine = RetrievalEngine(
            store,
            [allowed, forbidden],
            config=RetrievalConfig(production_strategy="fusion"),
        )
        context = ServerAccessContext(
            user_id="alice",
            roles=frozenset({"employee"}),
            departments=frozenset({"Finance"}),
            scopes=frozenset({"internal"}),
        )
        filters = KnowledgeAccessPolicy().retrieval_filter(context, engine.documents)
        result = engine.retrieve("Finance policy", top_k=4, filters=filters)

        self.assertEqual(filters.allowed_document_ids, frozenset({"finance"}))
        self.assertEqual([doc.metadata["source_id"] for doc in result.documents], ["finance"])
        self.assertNotIn("secret", [doc.metadata["source_id"] for doc in result.documents])

    def test_graph_authorization_filter_has_empty_allow_set_when_index_unavailable(self) -> None:
        runtime = SimpleNamespace(
            context=SimpleNamespace(
                access_context=ServerAccessContext(
                    user_id="alice",
                    roles=frozenset({"employee"}),
                    departments=frozenset({"Finance"}),
                    scopes=frozenset({"internal"}),
                )
            )
        )
        with patch("backend.agent.nodes.get_retriever", side_effect=RuntimeError("offline")):
            result = _authorization_filter(runtime)
        self.assertEqual(result.allowed_document_ids, frozenset())


class SourceAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_view_returns_content_only_for_authorized_context(self) -> None:
        request = _request()
        record = {
            "id": "finance-policy",
            "title": "Finance",
            "content": "sensitive finance content",
            "source": "upload",
            "source_type": "upload",
            "department": "Finance",
            "access_scope": "internal",
            "required_scopes": [],
            "public_internal": False,
            "content_checksum": "checksum",
        }
        with patch("backend.api.routes.get_knowledge_record", return_value=record):
            response = await knowledge_record(request, "finance-policy")
        self.assertEqual(response["record"]["content"], "sensitive finance content")

    async def test_source_view_hides_unauthorized_existence(self) -> None:
        request = _request()
        record = {
            "id": "legal-policy",
            "title": "Legal",
            "content": "secret",
            "department": "Legal",
            "access_scope": "internal",
        }
        user = ServerAccessContext(
            user_id="alice",
            roles=frozenset({"employee"}),
            departments=frozenset({"Finance"}),
            scopes=frozenset({"internal"}),
        )
        with patch("backend.api.routes.get_knowledge_record", return_value=record), patch(
            "backend.api.routes._request_access_context", return_value=user
        ):
            with self.assertRaises(HTTPException) as raised:
                await knowledge_record(request, "legal-policy")
        self.assertEqual(raised.exception.status_code, 404)

    async def test_download_is_confined_to_upload_root(self) -> None:
        with TemporaryDirectory() as directory:
            file_path = Path(directory) / "policy.txt"
            file_path.write_text("authorized body", encoding="utf-8")
            request = _request()
            record = {
                "id": "finance-policy",
                "title": "Finance",
                "content": "authorized body",
                "department": "Finance",
                "access_scope": "internal",
                "original_filename": "policy.txt",
            }
            with patch("backend.api.routes.settings.upload_dir", directory), patch(
                "backend.api.routes.get_knowledge_record", return_value=record
            ):
                response = await download_knowledge_record(request, "finance-policy")
            self.assertEqual(Path(response.path).resolve(), file_path.resolve())

    def test_user_path_cannot_be_selected_by_client(self) -> None:
        context = ServerAccessContext(
            user_id="trusted-user",
            roles=frozenset({"employee"}),
            departments=frozenset({"Finance"}),
            scopes=frozenset({"internal"}),
        )
        with self.assertRaises(HTTPException) as raised:
            _assert_user_path("attacker", context)
        self.assertEqual(raised.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
