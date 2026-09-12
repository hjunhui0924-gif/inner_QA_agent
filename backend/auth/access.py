"""Trusted identity resolution and fail-closed knowledge ACL policy.

The public interface is deliberately small: callers obtain a
``ServerAccessContext`` and ask one ``KnowledgeAccessPolicy`` whether a
document is visible.  Request-body user IDs, client access scopes, and
frontend visibility are never used as authorization inputs.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import HTTPException, Request

from backend.config import settings
from backend.retrieval.engine import RetrievalFilter


class AuthenticationError(ValueError):
    """Raised when a trusted server identity is missing or malformed."""


class HeaderRequest(Protocol):
    headers: Any


_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,127}$")
_INTERNAL_ACCESS_VALUES = frozenset(
    {"public-internal", "public_internal", "publicinternal"}
)
_AUTHENTICATED_TRUE = frozenset({"1", "true", "yes", "internal"})
_AUTHENTICATED_FALSE = frozenset({"0", "false", "no", "external"})


def _tokens(value: object, *, field: str, allow_empty: bool = True) -> frozenset[str] | None:
    """Parse bounded comma/space separated ACL tokens.

    Wildcards are intentionally rejected.  Scope coverage is explicit and
    all-of, so a caller cannot gain access through an implicit ``*``.
    """

    if value is None:
        return frozenset() if allow_empty else None
    if isinstance(value, str):
        raw_values = re.split(r"[,\s]+", value.strip()) if value.strip() else []
    elif isinstance(value, (list, tuple, set, frozenset)):
        raw_values = list(value)
    else:
        return None
    result: set[str] = set()
    for raw in raw_values:
        if not isinstance(raw, str):
            return None
        token = raw.strip()
        if not token:
            continue
        if token == "*" or not _TOKEN_PATTERN.fullmatch(token):
            return None
        result.add(token)
    if not result and not allow_empty:
        return None
    return frozenset(result)


def _header(request: HeaderRequest, name: str) -> str:
    headers = getattr(request, "headers", {})
    value = headers.get(name) if hasattr(headers, "get") else None
    return str(value).strip() if value is not None else ""


def _safe_identity(value: str, *, field: str) -> str:
    if not value or len(value) > 128 or not _TOKEN_PATTERN.fullmatch(value):
        raise AuthenticationError(f"Invalid authenticated {field}.")
    return value


@dataclass(frozen=True)
class ServerAccessContext:
    """Identity and authorization claims resolved on the server side."""

    user_id: str
    roles: frozenset[str]
    departments: frozenset[str]
    scopes: frozenset[str]
    authenticated: bool = True
    internal_user: bool = True
    auth_source: str = "trusted"

    def __post_init__(self) -> None:
        _safe_identity(self.user_id, field="user")
        for field_name, values in (
            ("roles", self.roles),
            ("departments", self.departments),
            ("scopes", self.scopes),
        ):
            parsed = _tokens(values, field=field_name)
            if parsed is None or parsed != values:
                raise ValueError(f"Invalid {field_name} in access context.")
        if not isinstance(self.authenticated, bool) or not isinstance(
            self.internal_user, bool
        ):
            raise ValueError("Authentication flags must be boolean.")


def development_access_context() -> ServerAccessContext:
    """Return a server-configured identity for local single-user development."""

    return ServerAccessContext(
        user_id=_safe_identity(settings.auth_dev_user_id, field="user"),
        roles=_tokens(settings.auth_dev_roles, field="roles") or frozenset(),
        departments=_tokens(settings.auth_dev_departments, field="departments")
        or frozenset(),
        scopes=_tokens(settings.auth_dev_scopes, field="scopes") or frozenset(),
        authenticated=True,
        internal_user=settings.auth_dev_internal_user,
        auth_source="development",
    )


def _trusted_header_context(request: HeaderRequest) -> ServerAccessContext:
    """Resolve identity headers only behind a configured trusted proxy secret."""

    configured_secret = settings.auth_proxy_secret.strip()
    supplied_secret = _header(request, "X-Auth-Proxy-Secret")
    if not configured_secret or not supplied_secret or not hmac.compare_digest(
        hashlib.sha256(supplied_secret.encode()).digest(),
        hashlib.sha256(configured_secret.encode()).digest(),
    ):
        raise AuthenticationError("Trusted authentication proxy is not configured.")

    user_id = _safe_identity(_header(request, "X-Authenticated-User"), field="user")
    roles = _tokens(_header(request, "X-Authenticated-Roles"), field="roles")
    departments = _tokens(
        _header(request, "X-Authenticated-Departments"),
        field="departments",
    )
    scopes = _tokens(_header(request, "X-Authenticated-Scopes"), field="scopes")
    if roles is None or departments is None or scopes is None:
        raise AuthenticationError("Authenticated claims are malformed.")

    internal_value = _header(request, "X-Authenticated-Internal").casefold()
    if internal_value in _AUTHENTICATED_TRUE:
        internal_user = True
    elif internal_value in _AUTHENTICATED_FALSE:
        internal_user = False
    else:
        raise AuthenticationError("Authenticated internal-user claim is required.")

    return ServerAccessContext(
        user_id=user_id,
        roles=roles,
        departments=departments,
        scopes=scopes,
        authenticated=True,
        internal_user=internal_user,
        auth_source="trusted_proxy",
    )


def resolve_access_context(request: HeaderRequest) -> ServerAccessContext:
    """Resolve the only identity the application is allowed to trust."""

    mode = settings.auth_mode.strip().lower()
    if mode == "development":
        return development_access_context()
    if mode == "trusted_headers":
        return _trusted_header_context(request)
    raise AuthenticationError("Unsupported authentication mode.")


def require_access_context(request: Request) -> ServerAccessContext:
    """FastAPI dependency that converts auth failures into HTTP 401."""

    try:
        context = resolve_access_context(request)
        state = getattr(request, "state", None)
        if state is not None:
            state.access_context = context
        return context
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail="Authentication required.") from exc


def require_knowledge_admin(request: Request) -> ServerAccessContext:
    """FastAPI dependency for knowledge writes and metadata administration."""

    context = require_access_context(request)
    if not KnowledgeAccessPolicy().can_manage_knowledge(context):
        raise HTTPException(status_code=403, detail="Knowledge management requires administrator access.")
    return context


def _metadata_value(metadata: dict[str, object], *names: str) -> object:
    for name in names:
        if name in metadata:
            return metadata[name]
    return None


class KnowledgeAccessPolicy:
    """Fail-closed document policy with explicit deny-before-allow rules."""

    def _acl(self, metadata: dict[str, object]) -> tuple[dict[str, frozenset[str]], bool] | None:
        access_scope = _metadata_value(metadata, "access_scope", "visibility")
        parsed_access = _tokens(access_scope, field="access_scope", allow_empty=False)
        if parsed_access is None:
            return None

        fields: dict[str, frozenset[str]] = {}
        for field_name in (
            "required_scopes",
            "allowed_roles",
            "allowed_departments",
            "denied_roles",
            "denied_departments",
            "denied_scopes",
        ):
            parsed = _tokens(metadata.get(field_name), field=field_name)
            if parsed is None:
                return None
            fields[field_name] = parsed

        required_scopes = set(fields["required_scopes"])
        public_internal = False
        for token in parsed_access:
            if token.casefold() in _INTERNAL_ACCESS_VALUES:
                public_internal = True
            else:
                required_scopes.add(token)

        explicit_public = metadata.get("public_internal")
        if explicit_public is not None:
            if not isinstance(explicit_public, bool):
                return None
            public_internal = public_internal or explicit_public

        fields["required_scopes"] = frozenset(required_scopes)
        return fields, public_internal

    def can_access(
        self,
        context: ServerAccessContext,
        metadata: dict[str, object],
    ) -> bool:
        """Return whether this authenticated context may see one document."""

        if not context.authenticated:
            return False
        parsed = self._acl(metadata)
        if parsed is None:
            return False
        fields, public_internal = parsed

        # Explicit deny always wins, including when an allow claim matches.
        if context.roles & fields["denied_roles"]:
            return False
        if context.departments & fields["denied_departments"]:
            return False
        if context.scopes & fields["denied_scopes"]:
            return False

        if public_internal and not context.internal_user:
            return False
        if not context.scopes.issuperset(fields["required_scopes"]):
            return False
        allowed_roles = fields["allowed_roles"]
        if allowed_roles and not context.roles.intersection(allowed_roles):
            return False
        allowed_departments = fields["allowed_departments"]
        if allowed_departments and not context.departments.intersection(
            allowed_departments
        ):
            return False
        is_admin = bool(
            {role.casefold() for role in context.roles}
            & {"admin", "knowledge_admin"}
        )
        resource_department = str(metadata.get("department", "")).strip()
        if not is_admin and not public_internal:
            if resource_department in {"", "unknown"}:
                return False
            if resource_department not in context.departments:
                return False
        return public_internal or bool(
            fields["required_scopes"] or allowed_roles or allowed_departments
        )

    def validate_metadata(self, metadata: dict[str, object]) -> bool:
        """Validate ACL shape without granting access to the caller."""

        return self._acl(metadata) is not None

    def authorized_document_ids(
        self,
        context: ServerAccessContext,
        documents: list[Any],
    ) -> frozenset[str]:
        """Return source/document IDs allowed before fusion and reranking."""

        authorized: set[str] = set()
        for document in documents:
            metadata = getattr(document, "metadata", {})
            if not isinstance(metadata, dict) or not self.can_access(context, metadata):
                continue
            identifier = str(
                metadata.get("document_id", metadata.get("source_id", ""))
            ).strip()
            if identifier:
                authorized.add(identifier)
        return frozenset(authorized)

    def retrieval_filter(
        self,
        context: ServerAccessContext,
        documents: list[Any],
    ) -> RetrievalFilter:
        """Build a retrieval filter whose empty set means access denied."""

        return RetrievalFilter(
            allowed_document_ids=self.authorized_document_ids(context, documents)
        )

    def can_manage_knowledge(self, context: ServerAccessContext) -> bool:
        """Require a server-resolved admin role for document management."""

        return context.authenticated and bool(
            {role.casefold() for role in context.roles}
            & {"admin", "knowledge_admin"}
        )

    def can_upload(
        self,
        context: ServerAccessContext,
        metadata: dict[str, object],
    ) -> bool:
        """Prevent an uploader from creating ACLs they cannot read."""

        return self.can_manage_knowledge(context) and self.can_access(context, metadata)
