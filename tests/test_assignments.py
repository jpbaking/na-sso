import re

from na_sso.connectors import Connector, IdentityCapabilities, SyncResult
from na_sso.models import ManagedUser, SyncState
from na_sso.reconciliation import (
    InspectionCapabilities,
    RemoteIdentitySnapshot,
    compare_snapshot,
)


class AssignmentConnector(Connector):
    capabilities = IdentityCapabilities(password=False)
    inspection_capabilities = InspectionCapabilities(
        display_name=True, email=True, status=True, memberships=True,
    )

    def __init__(self, target_id, default_memberships):
        self.target_id = target_id
        self.target_type = "nexus" if target_id == "cloud" else "ssh"
        self.display_name = target_id.title()
        self._groups = list(default_memberships)
        self.ensure_memberships = []
        self.inspect_memberships = []

    async def inspect_user_for_assignment(self, user, memberships):
        self.inspect_memberships.append((user.username, memberships))
        return compare_snapshot(
            target_id=self.target_id, target_name=self.display_name,
            user=user, capabilities=self.inspection_capabilities,
            snapshot=RemoteIdentitySnapshot(
                present=True, username=user.username,
                display_name=user.display_name or user.username,
                email=user.email,
                status="disabled" if user.status == "disabled" else "active",
                memberships=memberships,
            ),
            required_memberships=memberships,
        )

    async def ensure_user_for_assignment(self, user, password, memberships):
        self.ensure_memberships.append((user.username, memberships))
        return SyncResult(True, "saved")

    async def ensure_user(self, user, password):
        return await self.ensure_user_for_assignment(user, password, self.default_memberships)

    async def disable_user(self, user):
        return SyncResult(True, "disabled")

    async def delete_user(self, user):
        return SyncResult(True, "deleted")

    async def probe(self):
        return SyncResult(True, "reachable")


def _install(monkeypatch, connectors):
    for path in (
        "na_sso.assignments.get_connectors",
        "na_sso.connectors.get_connectors",
        "na_sso.users.get_connectors",
        "na_sso.sync.get_connectors",
        "na_sso.reconcile.get_connectors",
    ):
        monkeypatch.setattr(path, lambda connectors=connectors: connectors)


def _user_with_shell():
    from na_sso.db import get_session

    with get_session() as db:
        user = ManagedUser(
            username="profile_user", display_name="Profile User",
            email="profile@example.test",
        )
        db.add(user)
        db.flush()
        db.add(SyncState(
            user=user, target="shell", target_type="ssh", assigned=True, state="ok",
        ))
        db.commit()
        return user.id


def test_profiles_are_versioned_previewed_and_preserve_visible_user_exceptions(
    admin_client, monkeypatch,
):
    from na_sso.db import get_session
    from na_sso.models import (
        AssignmentProfile, ProfileApplication, UserAssignmentException,
        UserAssignmentProfile,
    )

    cloud = AssignmentConnector("cloud", {"base-role"})
    shell = AssignmentConnector("shell", {"shell-base"})
    _install(monkeypatch, [cloud, shell])
    user_id = _user_with_shell()

    page = admin_client.get("/assignment-profiles")
    assert page.status_code == 200 and "No-change preview" in page.text
    preview = admin_client.post("/assignment-profiles/preview", data={
        "name": "Engineering access",
        "description": "Reusable engineering targets",
        "bundle": "cloud:employees|engineering",
        "profile_key": "",
    }, follow_redirects=False)
    assert preview.status_code == 303
    with get_session() as db:
        profile = db.query(AssignmentProfile).one()
        assert profile.status == "draft" and profile.version == 1
        assert db.get(UserAssignmentProfile, user_id) is None
        profile_id, profile_key, publish_token = profile.id, profile.profile_key, profile.approval_token

    detail = admin_client.get(preview.headers["location"])
    assert "No assignments changed" in detail.text
    assert "employees" in detail.text and "engineering" in detail.text
    published = admin_client.post(
        f"/assignment-profiles/{profile_id}/publish",
        data={"approval_token": publish_token}, follow_redirects=False,
    )
    assert published.status_code == 303

    application_preview = admin_client.post(
        f"/assignment-profiles/{profile_id}/apply/preview",
        data={"user_id": user_id}, follow_redirects=False,
    )
    application_page = admin_client.get(application_preview.headers["location"])
    assert "cloud" in application_page.text
    assert "shell" in application_page.text
    assert "No assignments changed during this preview" in application_page.text
    token = re.search(r'name="approval_token" value="([^"]+)"', application_page.text).group(1)
    with get_session() as db:
        application = db.query(ProfileApplication).one()
        application_id = application.id

    applied = admin_client.post(
        f"/assignment-profiles/applications/{application_id}/confirm",
        data={"approval_token": token}, follow_redirects=False,
    )
    assert applied.status_code == 303
    assert cloud.ensure_memberships[-1][1] == frozenset({"employees", "engineering"})
    assert shell.ensure_memberships[-1][1] == frozenset({"shell-base"})
    with get_session() as db:
        assignment = db.get(UserAssignmentProfile, user_id)
        exception = db.query(UserAssignmentException).filter_by(
            user_id=user_id, target_id="shell"
        ).one()
        assert assignment.profile_id == profile_id
        assert exception.assignment_mode == "include"
        assert db.get(ProfileApplication, application_id).status == "applied"

    replay = admin_client.post(
        f"/assignment-profiles/applications/{application_id}/confirm",
        data={"approval_token": token}, follow_redirects=False,
    )
    assert replay.status_code == 303
    assert len(cloud.ensure_memberships) == 1 and len(shell.ensure_memberships) == 1

    override = admin_client.post(
        f"/users/{user_id}/assignment-exceptions",
        data={
            "target_id": "cloud", "assignment_mode": "inherit",
            "add_memberships": "special", "remove_memberships": "engineering",
        }, follow_redirects=False,
    )
    assert override.status_code == 303
    assert cloud.ensure_memberships[-1][1] == frozenset({"employees", "special"})
    exceptions_page = admin_client.get(f"/users/{user_id}/assignment-exceptions")
    assert "Engineering access v1" in exceptions_page.text
    assert "special" in exceptions_page.text and "engineering" in exceptions_page.text

    reconciliation = admin_client.post(
        "/reconciliation/preview",
        data={"user_id": user_id, "target_id": "cloud"},
        follow_redirects=False,
    )
    assert reconciliation.status_code == 303
    assert cloud.inspect_memberships[-1][1] == frozenset({"employees", "special"})

    version_two = admin_client.post("/assignment-profiles/preview", data={
        "name": "Engineering access", "description": "Next version",
        "bundle": "cloud:employees|platform", "profile_key": profile_key,
    }, follow_redirects=False)
    assert version_two.status_code == 303
    with get_session() as db:
        versions = db.query(AssignmentProfile).filter_by(profile_key=profile_key).order_by(
            AssignmentProfile.version
        ).all()
        assert [item.version for item in versions] == [1, 2]
        assert db.get(UserAssignmentProfile, user_id).profile_id == profile_id


def test_manual_target_selection_becomes_profile_exception(client, monkeypatch):
    from na_sso.assignments import record_selected_target_exceptions
    from na_sso.db import get_session
    from na_sso.models import (
        AssignmentProfile, AssignmentProfileTarget, UserAssignmentException,
        UserAssignmentProfile,
    )

    cloud = AssignmentConnector("cloud", set())
    shell = AssignmentConnector("shell", set())
    _install(monkeypatch, [cloud, shell])
    user_id = _user_with_shell()
    with get_session() as db:
        profile = AssignmentProfile(
            name="Cloud", version=1, status="published", created_by="admin",
        )
        db.add(profile)
        db.flush()
        db.add(AssignmentProfileTarget(
            profile_id=profile.id, target_id="cloud", memberships="[]",
        ))
        db.add(UserAssignmentProfile(
            user_id=user_id, profile_id=profile.id, assigned_by="admin",
        ))
        db.flush()
        user = db.get(ManagedUser, user_id)
        record_selected_target_exceptions(db, user, {"cloud", "shell"}, actor="admin")
        db.commit()
        assert db.query(UserAssignmentException).filter_by(
            user_id=user_id, target_id="shell", assignment_mode="include"
        ).count() == 1

        record_selected_target_exceptions(db, user, {"cloud"}, actor="admin")
        db.commit()
        assert db.query(UserAssignmentException).filter_by(
            user_id=user_id, target_id="shell"
        ).count() == 0
