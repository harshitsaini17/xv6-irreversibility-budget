"""Small dashboard: chat with the agent + live xv6 console.

Run:  .venv/bin/python server.py        then open http://127.0.0.1:8765
Binds to 127.0.0.1 only. There is no authentication, so do not expose it.
"""

import os
import threading

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from agent import DEFAULT_MODEL, Agent
from xv6_bridge import XV6Bridge

HERE = os.path.dirname(os.path.abspath(__file__))
bridge = XV6Bridge(cpus=int(os.environ.get("CPUS", "1")))
agent = Agent(bridge)
agent_lock = threading.Lock()
app = FastAPI()


class Chat(BaseModel):
    message: str
    mode: str = "llm"          # "llm" or "rules"
    model: str = DEFAULT_MODEL


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"))


@app.post("/api/chat")
def chat(req: Chat):
    with agent_lock:
        agent.model = req.model
        try:
            return agent.handle(req.message, mode=req.mode)
        except Exception as e:     # LLM/gateway errors are shown, not hidden
            return {"reply": f"error: {e}", "trace": []}


@app.post("/api/reset")
def reset():
    agent.reset()
    return {"ok": True}


@app.get("/api/status")
def status():
    return {"running": bridge.is_running(), "console": bridge.snapshot(8000)}


@app.on_event("shutdown")
def _stop():
    bridge.shutdown()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8765")))
