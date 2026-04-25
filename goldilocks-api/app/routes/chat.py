from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from app.services.llm import stream_chat
from app.services.prompt import build_system_prompt

router = APIRouter()


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]
    mode: str | None = None
    experience_level: str | None = None
    workspace_state: dict | None = None


@router.post("/chat")
async def chat(req: ChatRequest):
    system_prompt = build_system_prompt(req.mode, req.experience_level, req.workspace_state)
    messages = [{"role": "system", "content": system_prompt}] + [
        m.model_dump() for m in req.messages
    ]
    return StreamingResponse(
        stream_chat(messages),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
