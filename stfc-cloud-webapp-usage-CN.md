# 本地运行 Goldilocks + STFC Cloud vLLM

## 架构

```
浏览器
  │  http://localhost:5174
  ▼
Vite 开发服务器（goldilocks-web，5174 端口）
  │  /api/* 请求 → 自动转发到 localhost:8080
  ▼
goldilocks-api（FastAPI，8080 端口）
  │  LLM 请求 → localhost:8000
  ▼
SSH 隧道
  │  localhost:8000 → 172.16.111.119:8000
  ▼
vLLM + Qwen3（STFC 云服务器）
```

**为什么需要 SSH 隧道？**
STFC 服务器在内网里，本地程序（如 `goldilocks-api`）只能访问 `localhost`，没法直接写服务器 IP。SSH 隧道在本地开一个 8000 端口，任何发到这个端口的流量都自动通过 SSH 传到服务器上的 vLLM，本地程序完全不需要知道服务器的存在。

**为什么需要 goldilocks-api？**
浏览器不知道 SSH 隧道的存在，也无法直接访问 vLLM。`goldilocks-api` 作为中间层，接收浏览器的请求，再通过隧道转发给 vLLM，然后把结果返回给浏览器。

---

## 前提条件

- VPN 已连接到 STFC 网络
- 服务器上 vLLM 已在运行（见下方）
- 本地已安装 `uv`

---

## 工作流程（4 个终端）

### 终端 1 — SSH 隧道

```bash
ssh -i ~/.ssh/id_rsa -L 8000:localhost:8000 ubuntu@172.16.111.119 -N
```

运行后终端会空白挂着——这是正常的，说明隧道已建好。**不要关闭这个终端。**

`-N` 的意思是"只建隧道，不开交互式 shell"。

### 终端 2 — goldilocks-api（后端）

```bash
cd /Users/junwen.yin/research/1-goldilocks/code/goldilocks-api
uv run uvicorn app.main:app --port 8080 --reload
```

### 终端 3 — goldilocks-web（前端）

```bash
cd /Users/junwen.yin/research/1-goldilocks/code/goldilocks-web
npm run dev
```

### 终端 4 — 在服务器上检查/启动 vLLM（按需）

```bash
ssh -i ~/.ssh/id_rsa ubuntu@172.16.111.119
screen -r vllm
```

vLLM 的启动命令：
```bash
screen -S vllm
vllm serve Qwen/Qwen3-8B --host 0.0.0.0 --port 8000 --dtype bfloat16 --max-model-len 32768 --gpu-memory-utilization 0.85
```

---

## 打开应用

浏览器访问 `http://localhost:5174`（如果 5173 被占用，Vite 会自动换端口，以终端输出为准）。

---

## 常见问题

**`bind [127.0.0.1]:8000: Address already in use`**
本地 8000 端口被占用。找到并关掉它：
```bash
lsof -ti:8000 | xargs kill
```
然后重新跑隧道命令。

**`error: Failed to spawn: uvicorn`**
`.venv` 状态损坏（通常是被其他工具创建过）。删掉重建：
```bash
rm -rf .venv
uv sync
uv run uvicorn app.main:app --port 8080 --reload
```
