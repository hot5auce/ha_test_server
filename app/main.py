# app.py
import os
import time
import socket
import asyncio
import json
import requests
from typing import Callable, Optional

import psutil
import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse, JSONResponse
from pydantic import BaseModel

# Prometheus
from prometheus_client import (
    Counter, Gauge, Histogram,
    CollectorRegistry, REGISTRY,
    CONTENT_TYPE_LATEST, generate_latest
)
from prometheus_client import multiprocess

app = FastAPI(title="fastapi-minimal-observability")

# =========================
# 实例标识与网络信息
# =========================
HOSTNAME = os.getenv("HOSTNAME", socket.gethostname())

def get_local_ip() -> str:
    """获取实例的内网 IP（适用于 VPC 场景）"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # 不会真的发包，只是用来选择一块合适的出站网卡
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"

INSTANCE_IP = get_local_ip()

def get_ecs_instance_id(timeout=1.0) -> Optional[str]:
    """阿里云 ECS 元数据：100.100.100.200"""
    meta_url = "http://100.100.100.200/latest/meta-data/instance-id"
    try:
        r = httpx.get(meta_url, timeout=timeout)
        if r.status_code == 200:
            return r.text.strip()
    except Exception:
        pass
    return None

# 优先用 ECS 的 instance-id；否则用 HOSTNAME（K8s 可注入 POD 名）
INSTANCE_ID = os.getenv("INSTANCE_ID") or get_ecs_instance_id() or HOSTNAME

# =========================
# Prometheus 指标注册
# 支持多进程模式（gunicorn 等）
# =========================
if os.getenv("PROMETHEUS_MULTIPROC_DIR"):
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)
else:
    registry = REGISTRY

# 应用级指标
REQ_INFLIGHT = Gauge(
    "app_requests_inflight",
    "Number of in-flight HTTP requests",
    ["instance", "ip", "host"], registry=registry
)
REQ_TOTAL = Counter(
    "app_requests_total",
    "Total HTTP requests",
    ["path", "method", "code", "instance", "ip"], registry=registry
)
REQ_LATENCY = Histogram(
    "app_request_latency_seconds",
    "Request latency (seconds)",
    ["path", "method", "instance", "ip"], registry=registry,
    # 自定义桶：微秒到秒级
    buckets=(0.003, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5)
)

CPU_USAGE = Gauge(
    "app_cpu_usage_percent",
    "Process CPU percent", ["instance", "ip"], registry=registry
)
MEM_USAGE = Gauge(
    "app_mem_usage_mb",
    "Process RSS memory (MB)", ["instance", "ip"], registry=registry
)
CONCURRENCY = Gauge(
    "app_concurrency",
    "Current concurrency (same as inflight)", ["instance", "ip"], registry=registry
)

# =========================
# 请求并发中间件
# =========================
_inflight = 0
_inflight_lock = asyncio.Lock()

@app.middleware("http")
async def metrics_middleware(request: Request, call_next: Callable):
    global _inflight
    path = request.url.path
    method = request.method

    async with _inflight_lock:
        _inflight += 1
        CONCURRENCY.labels(instance=INSTANCE_ID, ip=INSTANCE_IP).set(_inflight)
        REQ_INFLIGHT.labels(instance=INSTANCE_ID, ip=INSTANCE_IP, host=HOSTNAME).inc()

    start = time.perf_counter()
    status_code = 500
    try:
        response: Response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        latency = time.perf_counter() - start
        REQ_TOTAL.labels(
            path=path, method=method, code=str(status_code),
            instance=INSTANCE_ID, ip=INSTANCE_IP
        ).inc()
        REQ_LATENCY.labels(
            path=path, method=method,
            instance=INSTANCE_ID, ip=INSTANCE_IP
        ).observe(latency)

        async with _inflight_lock:
            _inflight -= 1
            if _inflight < 0:
                _inflight = 0
            CONCURRENCY.labels(instance=INSTANCE_ID, ip=INSTANCE_IP).set(_inflight)
            REQ_INFLIGHT.labels(instance=INSTANCE_ID, ip=INSTANCE_IP, host=HOSTNAME).dec()

# =========================
# 后台采集：CPU/内存
# =========================
async def refresh_resource_metrics():
    proc = psutil.Process()
    # 预热一次 cpu_percent，否则第一次可能是 0
    try:
        proc.cpu_percent(interval=None)
    except Exception:
        pass

    while True:
        try:
            CPU_USAGE.labels(instance=INSTANCE_ID, ip=INSTANCE_IP).set(proc.cpu_percent(interval=None))
            rss_mb = proc.memory_info().rss / 1024 / 1024
            MEM_USAGE.labels(instance=INSTANCE_ID, ip=INSTANCE_IP).set(rss_mb)
        except Exception:
            pass
        await asyncio.sleep(1.0)

@app.on_event("startup")
async def _on_startup():
    asyncio.create_task(refresh_resource_metrics())

# =========================
# 业务与健康接口
# =========================
READY = True

class EchoIn(BaseModel):
    message: Optional[str] = None
    trace_id: Optional[str] = None

@app.get("/healthz")
def healthz():
    """
    liveness：进程还活着就返回 200（适合 SLB/NLB 基础健康检查）
    """
    return {"status": "ok", "ts": int(time.time())}

@app.get("/readyz")
def readyz():
    """
    readiness：应用是否已就绪（依赖是否准备好，比如 DB 连接、模型加载）
    """
    if READY:
        return {"ready": True}
    return JSONResponse({"ready": False}, status_code=503)

@app.post("/api/v1/echo")
async def echo(payload: EchoIn, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    return {
        "echo": payload.message,
        "trace_id": payload.trace_id or request.headers.get("x-trace-id"),
        "server_version": os.getenv("VERSION", "0.1.0"),
        "client_ip": client_ip,
        "instance": INSTANCE_ID,
        "instance_ip": INSTANCE_IP,
        "hostname": HOSTNAME,
    }

@app.get("/")
def root():
    return {
        "service": "fastapi-minimal",
        "version": os.getenv("VERSION", "0.1.0"),
        "instance": INSTANCE_ID,
        "instance_ip": INSTANCE_IP,
        "hostname": HOSTNAME,
        "endpoints": ["/healthz", "/readyz", "/api/v1/echo", "/metrics"]
    }

# =========================
# Prometheus /metrics
# =========================
# @app.get("/metrics")
# def metrics():
#     return PlainTextResponse(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

@app.get("/metrics")
def metrics():
    zone_id = requests.get("http://100.100.100.200/latest/meta-data/zone-id")
    zone_id = zone_id.content.decode("utf-8")
    instance_id = requests.get("http://100.100.100.200/latest/meta-data/instance-id")
    instance_id = instance_id.content.decode("utf-8")
    return {"zone_id": zone_id, "instance_id": instance_id}
