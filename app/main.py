from fastapi import FastAPI, Request
from pydantic import BaseModel
from typing import Optional
import os
import time

app = FastAPI(title="FastAPI Minimal Server", version=os.getenv("VERSION", "0.1.0"))

# ---- 简单的“就绪”状态控制（示例）----
# 你可以在启动后做一些初始化（加载模型/读配置等），期间 ready 先设为 False
READY = True  # 演示用，实际可在 lifespan 或后台任务中切换

class EchoIn(BaseModel):
    message: str
    trace_id: Optional[str] = None


@app.get("/healthz")
def healthz():
    """
    liveness：进程还活着就返回 200
    适合 SLB/NLB 的基础健康检查
    """
    return {"status": "ok", "ts": int(time.time())}


@app.get("/readyz")
def readyz():
    """
    readiness：应用是否已就绪（依赖是否准备好，比如DB连接、模型加载）
    """
    if READY:
        return {"ready": True}
    return {"ready": False}


@app.post("/api/v1/echo")
async def echo(payload: EchoIn, request: Request):
    """
    业务示例：回显输入，并附带服务信息（版本、客户端IP、简易trace）
    """
    client_ip = request.client.host if request.client else "unknown"
    return {
        "echo": payload.message,
        "trace_id": payload.trace_id or request.headers.get("x-trace-id"),
        "server_version": os.getenv("VERSION", "0.1.0"),
        "client_ip": client_ip,
    }


@app.get("/")
def root():
    return {
        "service": "fastapi-minimal",
        "version": os.getenv("VERSION", "0.1.0"),
        "endpoints": ["/healthz", "/readyz", "/api/v1/echo"]
    }

