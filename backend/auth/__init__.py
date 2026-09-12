"""Authentication and server-side access control seams."""

from backend.auth.access import (
    AuthenticationError,
    KnowledgeAccessPolicy,
    ServerAccessContext,
    development_access_context,
    require_access_context,
    require_knowledge_admin,
    resolve_access_context,
)

__all__ = [
    "AuthenticationError",
    "KnowledgeAccessPolicy",
    "ServerAccessContext",
    "development_access_context",
    "require_access_context",
    "require_knowledge_admin",
    "resolve_access_context",
]
