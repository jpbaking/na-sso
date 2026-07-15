from dataclasses import dataclass


MANAGE_USERS = "users.manage"
MANAGE_TARGETS = "targets.manage"
VIEW_AUDIT = "audit.view"
MANAGE_SECURITY = "security.manage"


@dataclass(frozen=True)
class RoleDefinition:
    value: str
    label: str
    description: str
    permissions: frozenset[str]


ROLE_DEFINITIONS = {
    "user": RoleDefinition(
        "user", "Managed user", "Can manage only their own password and SSH key.",
        frozenset(),
    ),
    "user_operator": RoleDefinition(
        "user_operator", "User operator",
        "Can create and manage ordinary users, assignments, and lifecycle actions.",
        frozenset({MANAGE_USERS}),
    ),
    "target_operator": RoleDefinition(
        "target_operator", "Target operator",
        "Can configure target credentials, test connections, and view target health.",
        frozenset({MANAGE_TARGETS}),
    ),
    "auditor": RoleDefinition(
        "auditor", "Auditor",
        "Can investigate and export the audit record without changing users or targets.",
        frozenset({VIEW_AUDIT}),
    ),
    "root": RoleDefinition(
        "root", "Root security administrator",
        "Protected recovery administrator with all capabilities and role assignment.",
        frozenset({MANAGE_USERS, MANAGE_TARGETS, VIEW_AUDIT, MANAGE_SECURITY}),
    ),
}

ASSIGNABLE_ROLES = tuple(
    ROLE_DEFINITIONS[value]
    for value in ("user", "user_operator", "target_operator", "auditor")
)


def normalise_role(role: str) -> str:
    # Databases created before scoped roles used the broad `admin` value.
    return "user_operator" if role == "admin" else role


def role_definition(role: str) -> RoleDefinition:
    return ROLE_DEFINITIONS.get(normalise_role(role), ROLE_DEFINITIONS["user"])


def has_permission(role: str, permission: str) -> bool:
    return permission in role_definition(role).permissions


def permission_context(role: str) -> dict[str, bool]:
    return {
        "users": has_permission(role, MANAGE_USERS),
        "targets": has_permission(role, MANAGE_TARGETS),
        "audit": has_permission(role, VIEW_AUDIT),
        "roles": has_permission(role, MANAGE_SECURITY),
    }


def default_home(role: str) -> str:
    for permission, path in (
        (MANAGE_USERS, "/users"),
        (MANAGE_TARGETS, "/status"),
        (VIEW_AUDIT, "/audit"),
    ):
        if has_permission(role, permission):
            return path
    return "/account"
