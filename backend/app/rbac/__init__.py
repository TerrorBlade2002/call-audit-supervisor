"""Role-based access control (RBAC with per-portfolio resource scoping, §2).

One permission matrix in code; one ``is_allowed(role, action)`` predicate the single
authorization middleware consults. ADMIN is org-wide and implicitly holds every right;
the other four roles are scoped to a portfolio via ``portfolio_members``.
"""

from app.rbac.permissions import Action, Role, is_allowed

__all__ = ["Action", "Role", "is_allowed"]
