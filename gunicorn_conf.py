# 更高并发可适当调大 workers 与 threads；根据 CPU/内存实际调整
workers = 2
threads = 2
bind = "0.0.0.0:8000"
worker_class = "uvicorn.workers.UvicornWorker"
timeout = 30
graceful_timeout = 30
keepalive = 75
accesslog = "-"
errorlog = "-"

