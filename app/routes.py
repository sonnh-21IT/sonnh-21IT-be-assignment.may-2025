# FastAPI routes
import uuid
from datetime import datetime, timezone
from typing import List, Annotated

from fastapi import APIRouter, Depends, HTTPException, status, Body, Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from . import models, schemas
from .db import get_db

# Initializing the main APIRouter for all APIs.
api_router = APIRouter()

# =====================================================================
# USER API
# =====================================================================

@api_router.post(
    "/users/",
    response_model=schemas.User,
    status_code=status.HTTP_201_CREATED,
    tags=["users"],
)
async def create_user(user: Annotated[schemas.UserCreate, Body()], db: AsyncSession = Depends(get_db)):
    """
    Create a new user in the system.
    """
    result = await db.execute(select(models.User).filter(models.User.email == user.email))
    db_user = result.scalars().first()

    if db_user:
        raise HTTPException(status_code=400, detail="Email already registered.")

    new_user_id = uuid.uuid4()
    db_user = models.User(id=new_user_id, email=user.email, name=user.name)
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user

@api_router.get("/users/{user_id}", response_model=schemas.User, tags=["users"])
async def read_user(user_id: Annotated[uuid.UUID, Path()], db: AsyncSession = Depends(get_db)):
    """
    Get detailed information of a user by ID.
    """
    result = await db.execute(select(models.User).filter(models.User.id == user_id))
    db_user = result.scalars().first()
    if db_user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return db_user

@api_router.get("/users/", response_model=List[schemas.User], tags=["users"])
async def read_users(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db)):
    """
    List all users in the system.
    """
    result = await db.execute(select(models.User).offset(skip).limit(limit))
    users = result.scalars().all()
    return users

# =====================================================================
# MESSAGE API
# =====================================================================

@api_router.post(
    "/messages/",
    response_model=schemas.Message,
    status_code=status.HTTP_201_CREATED,
    tags=["messages"],
)
async def create_message(
    message_data: Annotated[schemas.MessageCreate, Body()], db: AsyncSession = Depends(get_db) # AsyncSession
):
    """
    Send a message to one or more recipients.
    """
    result_sender = await db.execute(select(models.User).filter(models.User.id == message_data.sender_id))
    sender = result_sender.scalars().first()
    if not sender:
        raise HTTPException(status_code=404, detail="Sender not found.")

    new_message_id = uuid.uuid4()
    db_message = models.Message(
        id=new_message_id,
        sender_id=message_data.sender_id,
        subject=message_data.subject,
        content=message_data.content,
        timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(db_message)

    # Create recipient records for messages.
    if not message_data.recipient_ids:
        raise HTTPException(
            status_code=400, detail="Message must have at least one recipient."
        )

    for recipient_id in message_data.recipient_ids:
        result_recipient = await db.execute(select(models.User).filter(models.User.id == recipient_id))
        db_recipient_user = result_recipient.scalars().first()
        if not db_recipient_user:
            await db.rollback()
            raise HTTPException(
                status_code=404, detail=f"Recipient with ID {recipient_id} not found."
            )

        db_msg_recipient = models.MessageRecipient(
            message_id=new_message_id, recipient_id=recipient_id
        )
        db.add(db_msg_recipient)

    await db.commit()
    await db.refresh(db_message)
    return db_message

@api_router.get(
    "/users/{user_id}/sent_messages",
    response_model=List[schemas.Message],
    tags=["messages", "users"],
)
async def get_sent_messages(user_id: Annotated[uuid.UUID, Path()], db: AsyncSession = Depends(get_db)): # AsyncSession
    """
    View a list of all messages a user has sent.
    """
    result_user = await db.execute(select(models.User).filter(models.User.id == user_id))
    user_exists = result_user.scalars().first()
    if not user_exists:
        raise HTTPException(status_code=404, detail="User not found.")

    # Load sent messages.
    result_messages = await db.execute(select(models.Message).filter(models.Message.sender_id == user_id))
    messages = result_messages.scalars().all()
    return messages

@api_router.get(
    "/users/{user_id}/inbox",
    response_model=List[schemas.MessageInboxItem],
    tags=["messages", "users"],
)
async def get_inbox_messages(user_id: Annotated[uuid.UUID, Path()], db: AsyncSession = Depends(get_db)): # AsyncSession
    """
    View all messages in a user's inbox. Include both read and unread messages.
    """
    result_user = await db.execute(select(models.User).filter(models.User.id == user_id))
    user_exists = result_user.scalars().first()
    if not user_exists:
        raise HTTPException(status_code=404, detail="User not found.")

    stmt = (
        select(models.Message, models.MessageRecipient)
        .join(
            models.MessageRecipient,
            models.Message.id == models.MessageRecipient.message_id,
        )
        .options(joinedload(models.Message.sender))
        .filter(models.MessageRecipient.recipient_id == user_id)
    )
    result_inbox = await db.execute(stmt)
    inbox_entries = result_inbox.all()

    # Convert the results to the MessageInboxItem format.
    result = []
    for message, recipient_entry in inbox_entries:
        sender_schema = (
            schemas.User.model_validate(message.sender) if message.sender else None
        )

        result.append(
            schemas.MessageInboxItem(
                id=message.id,
                sender_id=message.sender_id,
                subject=message.subject,
                content=message.content,
                timestamp=message.timestamp,
                recipient_entry_id=recipient_entry.id,
                read=recipient_entry.read,
                read_at=recipient_entry.read_at,
                sender=sender_schema,
            )
        )
    return result

@api_router.get(
    "/users/{user_id}/inbox/unread",
    response_model=List[schemas.MessageInboxItem],
    tags=["messages", "users"],
)
async def get_unread_inbox_messages(user_id: Annotated[uuid.UUID, Path()], db: AsyncSession = Depends(get_db)): # AsyncSession
    """
    View all unread messages in a user's inbox.
    """
    result_user = await db.execute(select(models.User).filter(models.User.id == user_id))
    user_exists = result_user.scalars().first()
    if not user_exists:
        raise HTTPException(status_code=404, detail="User not found.")

    stmt = (
        select(models.Message, models.MessageRecipient)
        .join(
            models.MessageRecipient,
            models.Message.id == models.MessageRecipient.message_id,
        )
        .options(joinedload(models.Message.sender))
        .filter(
            models.MessageRecipient.recipient_id == user_id,
            models.MessageRecipient.read == False,
        )
    )
    result_inbox = await db.execute(stmt)
    inbox_entries = result_inbox.all()

    # Convert the results to the MessageInboxItem format.
    result = []
    for message, recipient_entry in inbox_entries:
        sender_schema = (
            schemas.User.model_validate(message.sender) if message.sender else None
        )
        result.append(
            schemas.MessageInboxItem(
                id=message.id,
                sender_id=message.sender_id,
                subject=message.subject,
                content=message.content,
                timestamp=message.timestamp,
                recipient_entry_id=recipient_entry.id,
                read=recipient_entry.read,
                read_at=recipient_entry.read_at,
                sender=sender_schema,
            )
        )
    return result

@api_router.get("/messages/{message_id}/recipients", tags=["messages"])
async def get_message_recipient(message_id: Annotated[uuid.UUID, Path()], db: AsyncSession = Depends(get_db)): # AsyncSession
    """
    View all recipients of a specific message and their read status.
    """
    result_message = await db.execute(select(models.Message).filter(models.Message.id == message_id))
    message_exists = result_message.scalars().first()
    if not message_exists:
        raise HTTPException(status_code=404, detail="Message not found.")

    # Get recipient information and join with the User table to get name/email.
    stmt = (
        select(models.MessageRecipient, models.User)
        .join(models.User, models.MessageRecipient.recipient_id == models.User.id)
        .filter(models.MessageRecipient.message_id == message_id)
    )
    result_recipients = await db.execute(stmt)
    recipient_data = result_recipients.all()

    # Format result
    result = []
    for recipient_entry, user in recipient_data:
        result.append(
            {
                "recipient_entry_id": recipient_entry.id,
                "recipient_id": user.id,
                "recipient_name": user.name,
                "recipient_email": user.email,
                "read": recipient_entry.read,
                "read_at": recipient_entry.read_at,
            }
        )
    return result

@api_router.patch(
    "/messages/recipients/{recipient_entry_id}/read",
    response_model=schemas.MessageRecipient,
    tags=["messages"],
)
async def mark_message_as_read(recipient_entry_id: Annotated[uuid.UUID, Path()], db: AsyncSession = Depends(get_db)): # AsyncSession
    """
    Mark a specific message (received by a particular user) as read.
    """
    result_entry = await db.execute(select(models.MessageRecipient).filter(models.MessageRecipient.id == recipient_entry_id))
    db_recipient_entry = result_entry.scalars().first()

    if db_recipient_entry is None:
        raise HTTPException(
            status_code=404, detail="Message recipient entry not found."
        )

    if not db_recipient_entry.read:
        db_recipient_entry.read = True
        db_recipient_entry.read_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.add(db_recipient_entry)
        await db.commit()
        await db.refresh(db_recipient_entry)

    return db_recipient_entry

@api_router.get(
    "/messages/{message_id}", response_model=schemas.Message, tags=["messages"]
)
async def read_message(message_id: Annotated[uuid.UUID, Path()], db: AsyncSession = Depends(get_db)): # AsyncSession
    """
    Get message details by ID.
    """
    result_message = await db.execute(select(models.Message).filter(models.Message.id == message_id))
    db_message = result_message.scalars().first()
    if db_message is None:
        raise HTTPException(status_code=404, detail="Message not found.") # Sửa "Messages" thành "Message"

    return db_message