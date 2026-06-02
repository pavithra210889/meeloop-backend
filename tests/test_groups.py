from app.messages.models import Chat


class TestGroupCreation:
    def test_create_group(self, client, auth_headers, second_user):
        resp = client.post("/groups/", json={
            "name": "Test Group",
            "member_ids": [second_user.id],
        }, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["chat_type"] == "group"
        assert data["group_name"] == "Test Group"
        assert data["member_count"] == 2
        assert any(m["role"] == "admin" for m in data["members"])

    def test_create_group_no_name(self, client, auth_headers, second_user):
        resp = client.post("/groups/", json={
            "name": "",
            "member_ids": [second_user.id],
        }, headers=auth_headers)
        assert resp.status_code == 400

    def test_create_group_no_members(self, client, auth_headers):
        resp = client.post("/groups/", json={
            "name": "Empty Group",
            "member_ids": [],
        }, headers=auth_headers)
        assert resp.status_code == 400

    def test_create_group_invalid_member(self, client, auth_headers):
        resp = client.post("/groups/", json={
            "name": "Bad Group",
            "member_ids": ["nonexistent-id"],
        }, headers=auth_headers)
        assert resp.status_code == 400


class TestGroupInfo:
    def test_get_group_info(self, client, auth_headers, second_user):
        create = client.post("/groups/", json={
            "name": "Info Group",
            "member_ids": [second_user.id],
        }, headers=auth_headers)
        group_id = create.json()["id"]

        resp = client.get(f"/groups/{group_id}/", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["group_name"] == "Info Group"

    def test_get_group_non_member(self, client, auth_headers, second_auth_headers, second_user, session):
        # Create group with only test_user (need a third user as member)
        from app.users.models import User
        from app.security import get_password_hash
        third = User(name="Third", username="third", email="third@test.com",
                     password=get_password_hash("pass"), is_active=True, is_verified=True)
        session.add(third)
        session.commit()
        session.refresh(third)

        create = client.post("/groups/", json={
            "name": "Private Group",
            "member_ids": [third.id],
        }, headers=auth_headers)
        group_id = create.json()["id"]

        # second_user is NOT a member
        resp = client.get(f"/groups/{group_id}/", headers=second_auth_headers)
        assert resp.status_code == 403

    def test_update_group(self, client, auth_headers, second_user):
        create = client.post("/groups/", json={
            "name": "Old Name",
            "member_ids": [second_user.id],
        }, headers=auth_headers)
        group_id = create.json()["id"]

        resp = client.patch(f"/groups/{group_id}/", json={
            "name": "New Name",
            "description": "A test group",
        }, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["group_name"] == "New Name"
        assert resp.json()["group_description"] == "A test group"

    def test_update_group_non_admin(self, client, auth_headers, second_auth_headers, second_user):
        create = client.post("/groups/", json={
            "name": "Admin Only",
            "member_ids": [second_user.id],
        }, headers=auth_headers)
        group_id = create.json()["id"]

        resp = client.patch(f"/groups/{group_id}/", json={"name": "Hacked"}, headers=second_auth_headers)
        assert resp.status_code == 403


class TestGroupMembers:
    def test_add_member(self, client, auth_headers, second_user, session):
        from app.users.models import User
        from app.security import get_password_hash
        third = User(name="Third", username="thirdmem", email="thirdmem@test.com",
                     password=get_password_hash("pass"), is_active=True, is_verified=True)
        session.add(third)
        session.commit()
        session.refresh(third)

        create = client.post("/groups/", json={
            "name": "Add Test",
            "member_ids": [second_user.id],
        }, headers=auth_headers)
        group_id = create.json()["id"]

        resp = client.post(f"/groups/{group_id}/members/", json={
            "user_ids": [third.id],
        }, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["member_count"] == 3

    def test_remove_member(self, client, auth_headers, second_auth_headers, second_user):
        create = client.post("/groups/", json={
            "name": "Remove Test",
            "member_ids": [second_user.id],
        }, headers=auth_headers)
        group_id = create.json()["id"]

        resp = client.delete(f"/groups/{group_id}/members/{second_user.id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["detail"] == "Member removed"

    def test_self_leave(self, client, auth_headers, second_auth_headers, second_user):
        create = client.post("/groups/", json={
            "name": "Leave Test",
            "member_ids": [second_user.id],
        }, headers=auth_headers)
        group_id = create.json()["id"]

        resp = client.delete(f"/groups/{group_id}/members/{second_user.id}", headers=second_auth_headers)
        assert resp.status_code == 200
        assert resp.json()["detail"] == "Left the group"

    def test_last_admin_cannot_leave(self, client, auth_headers, second_user):
        create = client.post("/groups/", json={
            "name": "Admin Lock",
            "member_ids": [second_user.id],
        }, headers=auth_headers)
        group_id = create.json()["id"]
        # test_user is the only admin — should fail
        resp = client.delete(f"/groups/{group_id}/members/{create.json()['created_by']['id']}", headers=auth_headers)
        assert resp.status_code == 400

    def test_promote_member(self, client, auth_headers, second_user):
        create = client.post("/groups/", json={
            "name": "Promote Test",
            "member_ids": [second_user.id],
        }, headers=auth_headers)
        group_id = create.json()["id"]

        resp = client.patch(f"/groups/{group_id}/members/{second_user.id}",
                            json={"role": "admin"}, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["role"] == "admin"


class TestGroupMessaging:
    def test_send_group_message(self, client, auth_headers, second_user):
        create = client.post("/groups/", json={
            "name": "Msg Test",
            "member_ids": [second_user.id],
        }, headers=auth_headers)
        group_id = create.json()["id"]

        resp = client.post(f"/groups/{group_id}/messages/", json={
            "chat_id": group_id,
            "message": "Hello group!",
        }, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["message"] == "Hello group!"
        assert resp.json()["receiver"] is None
        assert resp.json()["chat_type"] == "group"

    def test_get_group_messages(self, client, auth_headers, second_user):
        create = client.post("/groups/", json={
            "name": "List Msg Test",
            "member_ids": [second_user.id],
        }, headers=auth_headers)
        group_id = create.json()["id"]

        client.post(f"/groups/{group_id}/messages/", json={
            "chat_id": group_id, "message": "msg1",
        }, headers=auth_headers)
        client.post(f"/groups/{group_id}/messages/", json={
            "chat_id": group_id, "message": "msg2",
        }, headers=auth_headers)

        resp = client.get(f"/groups/{group_id}/messages/", headers=auth_headers)
        assert resp.status_code == 200
        # System message from creation + 2 user messages
        assert len(resp.json()) >= 2

    def test_non_member_cannot_send(self, client, auth_headers, second_auth_headers, second_user, session):
        from app.users.models import User
        from app.security import get_password_hash
        third = User(name="Third", username="thirdmsg", email="thirdmsg@test.com",
                     password=get_password_hash("pass"), is_active=True, is_verified=True)
        session.add(third)
        session.commit()
        session.refresh(third)

        create = client.post("/groups/", json={
            "name": "Restricted",
            "member_ids": [third.id],
        }, headers=auth_headers)
        group_id = create.json()["id"]

        resp = client.post(f"/groups/{group_id}/messages/", json={
            "chat_id": group_id, "message": "I'm not a member",
        }, headers=second_auth_headers)
        assert resp.status_code == 403

    def test_mark_read(self, client, auth_headers, second_auth_headers, second_user):
        create = client.post("/groups/", json={
            "name": "Read Test",
            "member_ids": [second_user.id],
        }, headers=auth_headers)
        group_id = create.json()["id"]

        client.post(f"/groups/{group_id}/messages/", json={
            "chat_id": group_id, "message": "unread msg",
        }, headers=auth_headers)

        resp = client.post(f"/groups/{group_id}/messages/read", headers=second_auth_headers)
        assert resp.status_code == 200


class TestExistingChatsUnaffected:
    def test_dm_chats_still_work(self, client, auth_headers, second_user, test_user, session):
        """Existing GET /chats/ returns DMs only, unaffected by group changes."""
        chat = Chat(
            chat_type="dm",
            participant_one_id=test_user.id,
            participant_two_id=second_user.id,
            last_message="hi",
            last_message_type="text",
        )
        session.add(chat)
        session.commit()

        # Also create a group — should NOT appear in /chats/
        client.post("/groups/", json={
            "name": "Hidden Group",
            "member_ids": [second_user.id],
        }, headers=auth_headers)

        resp = client.get("/chats/", headers=auth_headers)
        assert resp.status_code == 200
        # Should only have DMs, no groups
        for c in resp.json():
            assert "user" in c
            assert c.get("chat_type", "dm") != "group" or "chat_type" not in c
