# tests/test_users.py
import uuid

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
# Import các module bất đồng bộ từ SQLAlchemy
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session # Vẫn giữ để type hinting cho client fixture
from sqlalchemy.future import select # Cần cho các truy vấn bất đồng bộ trong test

from app.db import Base, get_db
from app.main import app
from app.models import User

# Configure a separate database for testing (SQLite in-memory).
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

    # Ghi đè dependency TRƯỚC KHI tạo TestClient
    app.dependency_overrides[get_db] = override_get_db
    
    # Tạo TestClient. Đảm bảo nó được khởi tạo trong phạm vi của fixture.
    # TestClient không cần await, nó là sync client cho async app.
    client = TestClient(app) 
    try:
        yield client
    finally:
        # Xóa ghi đè dependency sau khi test hoàn tất
        app.dependency_overrides.clear()


# =====================================================================
# TESTS FOR USER API
# =====================================================================

@pytest.mark.asyncio
async def test_client_user(client: TestClient, session: AsyncSession):
    response = client.post(
        "/api/v1/users", json={"email": "test@example.com", "name": "Test User"}
    )
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "test@example.com"
    assert data["name"] == "Test User"
    assert "id" in data
    assert "created_at" in data
    
    result = await session.execute(select(User).filter(User.email == "test@example.com"))
    user_in_db = result.scalar_one_or_none()
    assert user_in_db is not None


@pytest.mark.asyncio
async def test_create_user_duplicate_email(client: TestClient):
    response_one = client.post(
        "/api/v1/users/", json={"email": "duplicate@example.com", "name": "User One"}
    )
    # Đây là một điểm cần chú ý: nếu app.main.py không xử lý lỗi trùng lặp email tốt
    # hoặc có vấn đề với DB session, nó có thể gây ra lỗi.
    # Đảm bảo logic API của bạn xử lý trường hợp này đúng cách.
    assert response_one.status_code == 201 # Giả sử tạo user đầu tiên thành công
    response = client.post(
        "/api/v1/users/", json={"email": "duplicate@example.com", "name": "User Two"}
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Email already registered."


@pytest.mark.asyncio
async def test_read_users_empty(client: TestClient):
    response = client.get("/api/v1/users/")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_read_users_with_data(client: TestClient, session: AsyncSession):
    user1 = User(id=uuid.uuid4(), email="user1@example.com", name="User One")
    user2 = User(id=uuid.uuid4(), email="user2@example.com", name="User Two")
    session.add_all([user1, user2])
    await session.commit()
    await session.refresh(user1)
    await session.refresh(user2)

    response = client.get("/api/v1/users/")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert any(user["email"] == "user1@example.com" for user in data)
    assert any(user["email"] == "user2@example.com" for user in data)


@pytest.mark.asyncio
async def test_read_user_by_id(client: TestClient, session: AsyncSession):
    test_user_id = uuid.uuid4()
    test_user = User(
        id=test_user_id, email="specific@example.com", name="Specific User"
    )
    session.add(test_user)
    await session.commit()
    await session.refresh(test_user)

    response = client.get(f"/api/v1/users/{test_user_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(test_user_id)
    assert data["email"] == "specific@example.com"
    assert data["name"] == "Specific User"


@pytest.mark.asyncio
async def test_read_user_not_found(client: TestClient):
    non_existent_id = uuid.uuid4()
    response = client.get(f"/api/v1/users/{non_existent_id}")
    assert response.status_code == 404
    assert response.json()["detail"] == "User not found."