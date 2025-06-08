# Test message-related functionality
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session
from sqlalchemy.future import select

from app.db import Base, get_db
from app.main import app
from app.models import Message, MessageRecipient, User
from app.schemas import MessageInboxItem
from app.schemas import User as UserSchema

# Configure a separate database for testing (SQLite in-memory).
# This allows tests to run quickly and independently without affecting the development database.
SQLALCHEMY_DATABASE_URL = "sqlite+aiosqlite:///./test.db"
test_engine = create_async_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
TestingSessionLocal = async_sessionmaker(
    autocommit=False, autoflush=False, bind=test_engine, class_=AsyncSession, expire_on_commit=False
)


@pytest_asyncio.fixture(name="session")
async def session_fixture():
    """Recreate the database and tables for each test."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        await db.close()
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture(name="client")
async def client_fixture(session: AsyncSession):
    """Override the get_db dependency to use the test database session."""
    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    
    client = TestClient(app)
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


@pytest_asyncio.fixture(name="setup_users")
async def setup_users_fixture(session: AsyncSession):
    """Fixture to create and return users."""
    user1 = User(id=uuid.uuid4(), email="user1@example.com", name="User One")
    user2 = User(id=uuid.uuid4(), email="user2@example.com", name="User Two")
    user3 = User(id=uuid.uuid4(), email="user3@example.com", name="User Three")
    session.add_all([user1, user2, user3])
    await session.commit()
    await session.refresh(user1)
    await session.refresh(user2)
    await session.refresh(user3)
    return user1, user2, user3


# =====================================================================
# TESTS FOR MESSAGE API
# =====================================================================

@pytest.mark.asyncio
async def test_create_message_single_recipient(client: TestClient, setup_users):
    sender_user, recipient_user, _ = setup_users
    message_data = {
        "sender_id": str(sender_user.id),
        "recipient_ids": [str(recipient_user.id)],
        "subject": "Test Single Message",
        "content": "Hello, recipient!",
    }
    response = client.post("/api/v1/messages/", json=message_data)
    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert data["sender_id"] == str(sender_user.id)
    assert data["subject"] == message_data["subject"]
    assert data["content"] == message_data["content"]
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_create_message_multiple_recipients(client: TestClient, setup_users):
    sender_user, recipient_user_1, recipient_user_2 = setup_users
    message_data = {
        "sender_id": str(sender_user.id),
        "recipient_ids": [str(recipient_user_1.id), str(recipient_user_2.id)],
        "subject": "Test Multiple Recipients",
        "content": "Hello, all!",
    }
    response = client.post("/api/v1/messages/", json=message_data)
    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert data["sender_id"] == str(sender_user.id)


@pytest.mark.asyncio
async def test_create_message_sender_not_found(client: TestClient, setup_users):
    _, recipient_user, _ = setup_users
    non_existent_sender_id = uuid.uuid4()
    message_data = {
        "sender_id": str(non_existent_sender_id),
        "recipient_ids": [str(recipient_user.id)],
        "subject": "Invalid Sender",
        "content": "This should fail.",
    }
    response = client.post("/api/v1/messages/", json=message_data)
    assert response.status_code == 404
    assert response.json()["detail"] == "Sender not found."


@pytest.mark.asyncio
async def test_create_message_recipient_not_found(client: TestClient, setup_users):
    sender_user, _, _ = setup_users
    non_existent_recipient_id = uuid.uuid4()
    message_data = {
        "sender_id": str(sender_user.id),
        "recipient_ids": [str(non_existent_recipient_id)],
        "subject": "Invalid Recipient",
        "content": "This should fail.",
    }
    response = client.post("/api/v1/messages/", json=message_data)
    assert response.status_code == 404
    assert (
        response.json()["detail"]
        == f"Recipient with ID {non_existent_recipient_id} not found."
    )


@pytest.mark.asyncio
async def test_create_message_no_recipients(client: TestClient, setup_users):
    sender_user, _, _ = setup_users
    message_data = {
        "sender_id": str(sender_user.id),
        "recipient_ids": [],
        "subject": "No Recipient",
        "content": "This should fail.",
    }
    response = client.post("/api/v1/messages/", json=message_data)
    assert response.status_code == 400
    assert response.json()["detail"] == "Message must have at least one recipient."


@pytest.mark.asyncio
async def test_read_message_success(client: TestClient, setup_users, session: AsyncSession):
    sender, recipient, _ = setup_users
    # Create a message directly in DB or via API to get its ID
    message = Message(
        id=uuid.uuid4(),
        sender_id=sender.id,
        subject="Read Me",
        content="This is a message to be read.",
        timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    session.add(message)
    await session.commit()
    await session.refresh(message)

    # Add a recipient entry
    msg_recipient = MessageRecipient(message_id=message.id, recipient_id=recipient.id)
    session.add(msg_recipient)
    await session.commit()

    response = client.get(f"/api/v1/messages/{message.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(message.id)
    assert data["subject"] == "Read Me"
    assert data["content"] == "This is a message to be read."
    assert data["sender_id"] == str(sender.id)


@pytest.mark.asyncio
async def test_read_message_not_found(client: TestClient):
    non_existent_id = uuid.uuid4()
    response = client.get(f"/api/v1/messages/{non_existent_id}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Message not found."


@pytest.mark.asyncio
async def test_get_sent_messages(client: TestClient, setup_users, session: AsyncSession):
    user_a, user_b, _ = setup_users
    # User A sends 2 messages (directly add to DB for control)
    msg1 = Message(
        id=uuid.uuid4(),
        sender_id=user_a.id,
        subject="Sent Msg 1",
        content="Content 1",
        timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    msg2 = Message(
        id=uuid.uuid4(),
        sender_id=user_a.id,
        subject="Sent Msg 2",
        content="Content 2",
        timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    session.add_all([msg1, msg2])
    await session.commit()
    await session.refresh(msg1)
    await session.refresh(msg2)

    # Add recipients for messages
    session.add(MessageRecipient(message_id=msg1.id, recipient_id=user_b.id))
    session.add(MessageRecipient(message_id=msg2.id, recipient_id=user_b.id))
    await session.commit()

    response = client.get(f"/api/v1/users/{user_a.id}/sent_messages")
    assert response.status_code == 200
    sent_messages = response.json()
    assert len(sent_messages) == 2
    assert any(m["subject"] == "Sent Msg 1" for m in sent_messages)
    assert any(m["subject"] == "Sent Msg 2" for m in sent_messages)
    assert all(m["sender_id"] == str(user_a.id) for m in sent_messages)


@pytest.mark.asyncio
async def test_get_inbox_messages(client: TestClient, setup_users, session: AsyncSession):
    user_a, user_b, _ = setup_users
    # User A sends a message to User B
    msg_a_to_b = Message(
        id=uuid.uuid4(),
        sender_id=user_a.id,
        subject="Msg from A to B",
        content="Hello B from A",
        timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    session.add(msg_a_to_b)
    await session.commit()
    await session.refresh(msg_a_to_b)
    session.add(MessageRecipient(message_id=msg_a_to_b.id, recipient_id=user_b.id))
    await session.commit()

    # User B sends a message to User A (not relevant for user B's inbox, but good for setup)
    msg_b_to_a = Message(
        id=uuid.uuid4(),
        sender_id=user_b.id,
        subject="Msg from B to A",
        content="Hello A from B",
        timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    session.add(msg_b_to_a)
    await session.commit()
    await session.refresh(msg_b_to_a)
    session.add(MessageRecipient(message_id=msg_b_to_a.id, recipient_id=user_a.id))
    await session.commit()

    # Get inbox for User B
    response_inbox_b = client.get(f"/api/v1/users/{user_b.id}/inbox")
    assert response_inbox_b.status_code == 200
    inbox_b_data = response_inbox_b.json()
    assert len(inbox_b_data) == 1  # Only the message from A to B
    assert inbox_b_data[0]["subject"] == "Msg from A to B"
    assert inbox_b_data[0]["sender"]["id"] == str(user_a.id)
    assert not inbox_b_data[0]["read"]  # Should be unread by default
    assert "recipient_entry_id" in inbox_b_data[0]


@pytest.mark.asyncio
async def test_get_message_recipients(client: TestClient, setup_users, session: AsyncSession):
    sender, recipient1, recipient2 = setup_users
    # Create a message
    message = Message(
        id=uuid.uuid4(),
        sender_id=sender.id,
        subject="Check Recipients",
        content="Who received this?",
        timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    session.add(message)
    await session.commit()
    await session.refresh(message)

    # Add two recipients
    recipient_entry_1 = MessageRecipient(
        message_id=message.id, recipient_id=recipient1.id, read=False
    )
    recipient_entry_2 = MessageRecipient(
        message_id=message.id, recipient_id=recipient2.id, read=False
    )
    session.add_all([recipient_entry_1, recipient_entry_2])
    await session.commit()
    await session.refresh(recipient_entry_1)
    await session.refresh(recipient_entry_2)

    # Mark recipient1's message as read
    response_patch = client.patch(f"/api/v1/messages/recipients/{recipient_entry_1.id}/read")
    assert response_patch.status_code == 200

    response = client.get(f"/api/v1/messages/{message.id}/recipients")
    assert response.status_code == 200
    recipients_data = response.json()
    assert len(recipients_data) == 2

    # Check recipient1's status
    r1_status = next(
        item for item in recipients_data if item["recipient_id"] == str(recipient1.id)
    )
    assert r1_status["read"] is True
    assert r1_status["read_at"] is not None
    assert r1_status["recipient_name"] == recipient1.name

    # Check recipient2's status
    r2_status = next(
        item for item in recipients_data if item["recipient_id"] == str(recipient2.id)
    )
    assert r2_status["read"] is False
    assert r2_status["read_at"] is None
    assert r2_status["recipient_name"] == recipient2.name


# =====================================================================
# READ STATUS API
# =====================================================================


@pytest.mark.asyncio
async def test_mark_message_as_read_success(
    client: TestClient, setup_users, session: AsyncSession
):
    sender, recipient, _ = setup_users
    # Create a message and recipient entry directly
    message = Message(
        id=uuid.uuid4(),
        sender_id=sender.id,
        subject="Mark as read",
        content="Please read this.",
        timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    session.add(message)
    await session.commit()
    await session.refresh(message)

    recipient_entry = MessageRecipient(
        message_id=message.id, recipient_id=recipient.id, read=False
    )
    session.add(recipient_entry)
    await session.commit()
    await session.refresh(recipient_entry)  # Refresh to get the ID

    # Mark as read
    response = client.patch(f"/api/v1/messages/recipients/{recipient_entry.id}/read")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(recipient_entry.id)
    assert data["read"] is True
    assert data["read_at"] is not None

    # Verify by getting inbox again
    inbox_response = client.get(f"/api/v1/users/{recipient.id}/inbox")
    inbox_after_read = inbox_response.json()
    assert len(inbox_after_read) == 1
    assert inbox_after_read[0]["read"] is True


@pytest.mark.asyncio
async def test_mark_message_as_read_already_read(
    client: TestClient, setup_users, session: AsyncSession
):
    sender, recipient, _ = setup_users
    # Create a message and recipient entry, initially marked as read
    message = Message(
        id=uuid.uuid4(),
        sender_id=sender.id,
        subject="Already read",
        content="This will be read twice.",
        timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    session.add(message)
    await session.commit()
    await session.refresh(message)

    recipient_entry = MessageRecipient(
        id=uuid.uuid4(),
        message_id=message.id,
        recipient_id=recipient.id,
        read=True,
        read_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    session.add(recipient_entry)
    await session.commit()
    await session.refresh(recipient_entry)

    response = client.patch(f"/api/v1/messages/recipients/{recipient_entry.id}/read")
    assert response.status_code == 200
    data = response.json()
    assert data["read"] is True
    assert data["read_at"] is not None


@pytest.mark.asyncio
async def test_mark_message_as_read_entry_not_found(client: TestClient):
    non_existent_entry_id = uuid.uuid4()
    response = client.patch(f"/api/v1/messages/recipients/{non_existent_entry_id}/read")
    assert response.status_code == 404
    assert response.json()["detail"] == "Message recipient entry not found."


@pytest.mark.asyncio
async def test_get_unread_inbox_messages(client: TestClient, setup_users, session: AsyncSession):
    sender, recipient, user_c = setup_users
    # Message 1: from sender to recipient (will be read)
    msg1 = Message(
        id=uuid.uuid4(),
        sender_id=sender.id,
        subject="Message to be read",
        content="Read me!",
        timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    session.add(msg1)
    await session.commit()
    await session.refresh(msg1)
    recipient_entry_1 = MessageRecipient(
        message_id=msg1.id, recipient_id=recipient.id, read=False
    )
    session.add(recipient_entry_1)
    await session.commit()
    await session.refresh(recipient_entry_1)

    # Message 2: from sender to recipient (will remain unread)
    msg2 = Message(
        id=uuid.uuid4(),
        sender_id=sender.id,
        subject="Message to remain unread",
        content="Don't read me!",
        timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    session.add(msg2)
    await session.commit()
    await session.refresh(msg2)
    recipient_entry_2 = MessageRecipient(
        message_id=msg2.id, recipient_id=recipient.id, read=False
    )
    session.add(recipient_entry_2)
    await session.commit()

    # Message 3: from user_c to recipient (will remain unread)
    msg3 = Message(
        id=uuid.uuid4(),
        sender_id=user_c.id,
        subject="Another unread message",
        content="Unread!",
        timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    session.add(msg3)
    await session.commit()
    await session.refresh(msg3)
    recipient_entry_3 = MessageRecipient(
        message_id=msg3.id, recipient_id=recipient.id, read=False
    )
    session.add(recipient_entry_3)
    await session.commit()

    # Mark Message 1 as read via API
    client.patch(f"/api/v1/messages/recipients/{recipient_entry_1.id}/read")

    # Get unread inbox messages for recipient
    unread_response = client.get(f"/api/v1/users/{recipient.id}/inbox/unread")
    assert unread_response.status_code == 200
    unread_messages = unread_response.json()
    assert len(unread_messages) == 2  # Only Message 2 and 3 should be there

    unread_subjects = {msg["subject"] for msg in unread_messages}
    assert "Message to remain unread" in unread_subjects
    assert "Another unread message" in unread_subjects
    assert "Message to be read" not in unread_subjects

    # Check sender information in unread messages
    for msg in unread_messages:
        assert "sender" in msg
        assert msg["sender"] is not None
        if msg["subject"] == "Message to remain unread":
            assert msg["sender"]["id"] == str(sender.id)
        elif msg["subject"] == "Another unread message":
            assert msg["sender"]["id"] == str(user_c.id)