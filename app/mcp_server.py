from fastapi import FastAPI
from fastapi_mcp.server import FastApiMCP

from .routes import api_router 
from .db import engine, Base

def create_mcp_app():
    # Initialize the native FastAPI application.
    app = FastAPI(
        title="FastAPI MCP Assignment Server",
        description="This is a FastAPI application transformed into an MCP-compatible server for user and message management.",
        version="0.0.1",
    )

    # Initialize FastApiMCP by passing in the original FastAPI 'app' object.
    # FastApiMCP will automatically make the necessary changes on this 'app' object
    # to make it MCP-compatible.
    mcp_instance = FastApiMCP(app)

    # Include your defined routes from api_router into the root 'app' application.
    # These routes will be automatically detected by FastApiMCP and converted to tools.
    app.include_router(api_router)

    @app.get("/")
    async def root():
        return {"message": "Welcome to FastAPI MCP Server!"}
    
    # Returns the original FastAPI 'app' object.
    # This is the ASGI application configured with the MCP features that Uvicorn expects to run.
    return app

# Assign global variable 'app' to Uvicorn.
app = create_mcp_app()