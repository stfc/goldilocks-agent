# Goldilocks — 项目现状与路线图

*最后更新：2026-04-25*

本文档记录每个组件的完整现状——哪些是真实可用的，哪些是 mock，以及要让每项功能真正端到端跑通还需要做什么。

---

## 1. 系统架构

```
goldilocks-web  （React/Vite，浏览器端）
      │
      │  POST /api/chat          → SSE 流式输出  ✅ 已可用
      │  POST /api/structure-match               ✗ 未构建
      │  POST /api/dft/kpoints                   ✗ 未构建
      │  POST /api/dft/pseudo                    ✗ 未构建
      │  POST /api/mlip/singlepoint              ✗ 未构建
      ▼
goldilocks-api  （FastAPI）
      │
      ├── import goldilocks-core  （进程内调用）  △ 部分完成
      ├──→ vLLM  /v1/chat/completions            ✅ 已可用
      └──→ janus-api                             ✗ 未构建
```

| 包 | 职责 | 状态 |
|---|---|---|
| `goldilocks-web` | React/Vite 前端 | UI 完整；所有工具均为 mock/存根 |
| `goldilocks-api` | FastAPI 编排层 | 仅有 chat 路由；DFT/MLIP/结构匹配路由均缺失 |
| `goldilocks-core` | Python 计算库 | k-mesh + 伪势已完成；结构匹配缺失 |
| vLLM | Qwen3 推理服务（STFC 云） | 运行中；无系统提示，无工具调用 |
| `janus-api` | MLIP HTTP 包装（janus-core） | 尚未创建 |

---

## 2. goldilocks-web — 前端

### 2.1 已完整构建的内容

| 功能区域 | 详情 |
|---|---|
| 应用壳 | 暗/亮主题、左侧边栏、右侧面板、响应式布局 |
| 会话管理 | 创建/删除/重命名会话；按项目分组 |
| 聊天 | 从 `/api/chat` SSE 流式接收；用户消息、AI 消息、输入中动画 |
| 结构文件附加 | 通过夹子按钮或拖放附加 CIF/POSCAR/XYZ 文件 |
| 结构查看器 | 基于 WEAS 的 3D 查看器；"当前聊天结构"文件列表 |
| 元素周期表 | 元素选择器 + 化学式累积（点击构建 Fe₂O₃ 格式字符串）|
| 模式启动器 | 4 个模式卡片：Find in Databases / MLIP Playground / DFT Workspace / Let's Go Cutting-Edge |
| DFT Workspace 面板 | WorkspacePicker：代码 / 泛函 / 伪势 / 任务 / 机器 |
| Let's Go Cutting-Edge 面板 | WorkspacePicker：方法 / 代码 / 机器 |
| ✦ 推荐按钮 | 自动选中推荐项；在聊天输入框生成解释性 prompt |
| ? 询问按钮 | 为任意选择项生成"我需要了解 X 的什么？"prompt |
| Find in Databases 面板 | 查询结构展示（来自聊天文件）；翻页箭头；"Search databases"按钮 |
| MLIP Playground 面板 | 模型选择器（MACE/CHGNet/ALIGNN）；Analysis / Metrics / Compute 标签页 |
| 经验等级引导 | 一次性等级选择（新手/熟悉/高级）；存于 localStorage |
| 设置覆盖层 | 主题切换、经验等级、模型选择器 |
| 状态持久化 | 会话、主题、经验等级存储于 localStorage |

### 2.2 已存根 / mock 的内容

| 功能区域 | 当前行为 | 应有行为 |
|---|---|---|
| "Search databases" 按钮 | 2 秒假 loading 动画 | POST `/api/structure-match`，渲染真实结果 |
| 候选结构 | 3 条写死卡片（Fe2O3、SrTiO3、TiO2）| 动态渲染 API 返回数据 |
| 元素周期表 → Search | 假的 2 秒 loading | POST `/api/structure-match`（按元素集合搜索）|
| DFT Inputs 标签页 | 写死的 `dftGuidancePreview` 字符串 | 渲染 API 或 LLM 生成的真实输入文件内容 |
| DFT Checks 标签页 | 静态文字片段 | 接入验证逻辑 |
| MLIP 指标（MAE、RMSE）| 写死的 0.041 / 0.063 / 0.118 | 来自 janus-api 响应的计算值 |
| MLIP 计算 | 写死的参考值/预测值/差值 | 来自 janus-api 单点计算结果 |
| 模型选择器 | 仅有 UI——`selectedModel` 未发送给 API | 在 `/api/chat` 请求体中包含 `model` 字段 |
| 模式上下文注入 | 用户消息前仅拼接 `[Mode: DFT Workspace]` | 完整系统提示，包含工作区状态（代码、任务、结构）|

### 2.3 前端管理的状态

```
session.messages          完整聊天历史（role + content + display）
attachedFile              待发送的结构文件（发送前）
chatStructures            从消息历史中提取的所有结构文件
viewerIdx                 查看器/匹配查询中当前选中的结构索引
selectedDftCode           代码选择器值
selectedDftFunctional     泛函选择器值
selectedDftPseudo         伪势选择器值
selectedDftTask           任务选择器值
selectedDftMachine        机器选择器值
selectedBeyondDftMethod   Cutting-Edge 方法选择器值
selectedMlipModel         MLIP 模型选择
input                     聊天输入框内容
loading                   SSE 流式传输进行中时为 true
structureMatchLoading     结构匹配运行中时为 true
```

---

## 3. goldilocks-api — 后端

### 3.1 已构建的内容

| 路由 | 状态 | 详情 |
|---|---|---|
| `POST /api/chat` | ✅ | SSE 流代理至 vLLM；过滤 `<think>` 标记；已注入系统提示 |
| `GET /api/health` | ✅ | 返回 vLLM URL 和模型名 |
| `POST /api/dft/kpoints` | ✅ | 根据结构和 k-distance 返回 k-mesh 网格及不可约 k 点数 |
| `POST /api/dft/pseudo` | ✅ | 按泛函/类型过滤，返回每个元素的伪势候选列表 |

**现有能力：**
- httpx 异步客户端，支持流式传输
- `_filter_think()` 过滤 Qwen3 思维链中的推理标记
- 未设置 `VLLM_MODEL` 时通过 `/v1/models` 自动检测模型
- 已为 `localhost:5173` 和 `localhost:4173` 配置 CORS
- `services/prompt.py` 中的 `build_system_prompt(mode, experience_level, workspace_state)`；每次 `/api/chat` 调用时注入系统提示
- goldilocks-core 以 editable 路径依赖安装（`../goldilocks-core`）；对 goldilocks-core 的修改 Python 会自动感知，但 **uvicorn `--reload` 只监听 `app/` 目录——修改 goldilocks-core 后需手动重启 uvicorn**

**`/api/dft/kpoints` 实现说明：**
使用 `k_distance_to_mesh()`（基于几何的 heuristic 方法），而非 ML advisor。ML advisor（`advise_kpoints`）需要训练好的 `.joblib` 模型文件，该文件目前不存在。待模型可用后，切换到 `advise_kpoints()`。

**`/api/dft/pseudo` 实现说明：**
直接使用 `select_pp_candidates_for_structure()`。`goldilocks_core.advisors.pp_advisor` 中的 `advise_pseudos()` 尚未实现（`raise NotImplementedError`）。需要配置 `PSEUDO_ROOT` 环境变量，指向本地 UPF 文件目录。

**系统提示结构示例：**
```
你是 Goldilocks，STFC 计算材料研究的 AI 助手。
模式：DFT Workspace
用户经验：熟悉工作流

当前工作区：
- 代码：Quantum ESPRESSO
- 任务：几何优化
- 泛函：PBE
- 伪势：ONCV (SG15)
- 机器：ARCHER2

聊天中的结构：Si.cif（2 个原子，Si₂，空间群 Fd-3m）

请简洁回答，代码示例使用 QE 输入文件格式。
```

#### B. `POST /api/structure-match`

```python
class StructureMatchRequest(BaseModel):
    structure_content: str      # 原始文件内容
    structure_name: str         # 文件名（用于推断格式）
    # 或：
    elements: list[str]         # 化学式搜索（无结构文件）
    mode: Literal["file", "formula"]
```

```python
class StructureMatchResult(BaseModel):
    formula: str
    spacegroup: str | None
    source: str                 # "Materials Project" | "Materials Cloud" | "NOMAD" | "JARVIS"
    url: str
    matched: bool               # True 表示 StructureMatcher 确认精确匹配
    score: float | None
```

实现步骤：
1. 使用 goldilocks-core 解析结构
2. 提取特征：化学式、nsites、元素、空间群
3. 并行查询 MP（mp-api）、MC OPTIMADE、NOMAD OPTIMADE、JARVIS（本地缓存）
4. 对候选结构运行 `pymatgen.analysis.structure_matcher.StructureMatcher`
5. 返回排序后的 `StructureMatchResult` 列表

OPTIMADE 查询模式和链接 URL 模板详见 `structure-match-design.md`。

#### C. `POST /api/dft/kpoints`

```python
class KpointsRequest(BaseModel):
    structure_content: str
    structure_name: str
    code: str                   # "quantum-espresso" | "vasp" | "castep" | ...
    task: str                   # "geometry-optimisation" | "band-structure" | ...
```

调用 `goldilocks_core.kpoints.advisor.advise_kpoints()`，返回推荐的 k-mesh 以及供 LLM 使用的自然语言摘要。

#### D. `POST /api/dft/pseudo`

调用 `goldilocks_core.pseudo.pp_selector`，返回结构中每种元素的推荐伪势。

#### E. `/api/mlip/*` — 转发至 janus-api

```
POST /api/mlip/singlepoint   → janus-api /singlepoint
POST /api/mlip/optimise      → janus-api /optimise
POST /api/mlip/md            → janus-api /md
```

#### F. 工具调用 / Agent Loop

当前流程：前端发消息 → vLLM → 流式返回 token，没有工具调用循环。要支持自主工具使用：

1. 使用 vLLM 的 OpenAI 兼容工具调用 API（先做非流式调用）
2. 如果响应中有 `tool_calls` → 执行工具（goldilocks-core 或 janus-api）
3. 追加 `role: "tool"` 消息附上结果
4. 重新发送给 vLLM → 流式传输最终响应给前端

暴露给 LLM 的工具：
```
predict_kpoints(structure)               → goldilocks-core
select_pseudopotentials(structure, code) → goldilocks-core
search_databases(structure)              → structure-match 路由
run_singlepoint(structure, model)        → janus-api
run_optimise(structure, model)           → janus-api
```

前端需要同步改动：工具执行期间显示"🔧 工具运行中..."指示器；将工具结果摘要注入可见消息中。

---

## 4. goldilocks-core — 计算库

### 4.1 已构建的内容

| 模块 | 内容 | 状态 |
|---|---|---|
| `structure/io.py` | `load_structure()`、`analyze_structure()` | ✅ |
| `kpoints/features.py` | 从 Structure 提取 CSLR 特征 | ✅ |
| `kpoints/kmesh.py` | k-mesh 候选生成 | ✅ |
| `kpoints/advisor.py` | `advise_kpoints()` 完整流水线 | ✅ |
| `ml/models.py` | 从 ModelSpec 加载 joblib 模型 | ✅ |
| `ml/inference.py` | `predict()` 标量推理 | ✅ |
| `pseudo/parse_upf.py` | 解析 UPF 文件（属性式 + 文本式）| ✅ |
| `pseudo/pp_registry.py` | 从本地目录扫描构建注册表 | ✅ |
| `pseudo/pp_selector.py` | 按元素选择最优伪势 | ✅ |
| `pseudo/pp_policy.py` | 策略规则（泛函、类型等）| ✅ |
| `pseudo/download.py` | 获取伪势库 | ✅ |
| `shared/types.py` | 共享数据类 | ✅ |
| CLI `goldilocks-kmesh` | 命令行 k-mesh 推荐 | ✅ |

### 4.2 需要构建的内容

#### A. `structure/features.py` — 结构匹配特征提取

```python
@dataclass
class MatchFeatures:
    formula_reduced: str        # 如 "Si"
    formula_anonymous: str      # 如 "A"
    nsites: int
    elements: list[str]
    spacegroup_number: int
    spacegroup_symbol: str

def extract_match_features(structure: Structure) -> MatchFeatures: ...
```

#### B. `structure/match.py` — StructureMatcher 封装

```python
@dataclass
class MatchResult:
    formula: str
    spacegroup: str | None
    source: str
    url: str
    matched: bool
    score: float | None

def run_matcher(
    query: Structure,
    candidates: list[tuple[Structure, dict]]   # (结构, 元数据)
) -> list[MatchResult]: ...
```

使用 `pymatgen.analysis.structure_matcher.StructureMatcher`，参数：`ltol=0.2, stol=0.3, angle_tol=5`。

#### C. API 集成层

从外部数据库获取候选结构的函数：
```
goldilocks-api/app/services/mp.py       — 通过 mp-api 查 Materials Project
goldilocks-api/app/services/optimade.py — 通用 OPTIMADE 辅助（MC + NOMAD 共用）
goldilocks-api/app/services/jarvis.py   — JARVIS 本地缓存加载 + 查询
```

这些属于 goldilocks-api（网络调用），而非 goldilocks-core（纯计算）。goldilocks-core 提供 `extract_match_features()` 和 `run_matcher()`；所有 HTTP 调用由 goldilocks-api 负责。

#### D. 响应摘要生成

每个 API 端点应在 `raw` 数据旁返回 `summary` 字段（纯文本，约 1–3 句），用于注入 LLM 上下文，控制 token 用量。

示例：
```python
def kpoints_summary(advice: KPointsAdvice) -> str:
    return (
        f"推荐 k-grid：{advice.kgrid}（k-间距 {advice.k_distance:.3f} Å⁻¹）。"
        f"约化 k 点数：{advice.n_kpoints}。"
    )
```

---

## 5. LLM 层 — vLLM / Qwen3

### 5.1 当前状态

- vLLM 运行于 STFC 云虚拟机（`172.16.111.119`）
- 模型：Qwen3（具体版本通过 `VLLM_MODEL` 环境变量设置，或自动检测）
- goldilocks-api 通过 `VLLM_BASE_URL` 环境变量连接
- 通过 `/v1/chat/completions`（`stream: true`）逐 token 流式传输
- 转发给前端前过滤 `<think>...</think>` 标记

### 5.2 需要做的事

| 事项 | 优先级 | 备注 |
|---|---|---|
| 系统提示注入 | 高 | 感知模式，包含工作区状态和结构 |
| 经验等级语气调整 | 中 | "新手"用户获得详细解释；"高级"用户获得简洁输出 |
| 工具调用配置 | 高 | vLLM 支持 OpenAI function-calling 格式 |
| 微调 Goldilocks 模型 | 低（未来）| UI 中列出了 `goldilocks-dft` 和 `goldilocks-mlip`，但尚不存在 |
| 上下文窗口管理 | 中 | 长对话可能超出上下文；实现消息截断策略 |

**系统提示设计原则：** 短小精悍，聚焦领域，不堆砌废话。提示只需告诉模型它的名字、受众（材料科学家）、当前模式和工作区状态。不要试图在提示里向模型解释 DFT。

---

## 6. janus-api — MLIP 服务

### 6.1 当前状态

**尚未创建。** 在 `goldilocks-design.md` 中有所提及，MLIP Playground 面板也假设其存在。

### 6.2 需要构建的内容

对 `janus-core`（STFC 的 MLIP 仿真库）的最小化 FastAPI 封装：

```
POST /singlepoint   { structure: str, model: str } → { energy, forces, stress, summary }
POST /optimise      { structure: str, model: str } → { final_structure, energy, summary }
POST /md            { structure: str, model: str, steps: int, T: float } → { trajectory, summary }
```

支持的模型：MACE-MP-0、CHGNet、ALIGNN（对应前端 `MLIP_MODELS` 列表）。

---

## 7. 部署

### 7.1 当前部署状态

- 前端：Nginx 在 STFC 云虚拟机上，提供 Vite 构建产物
- goldilocks-api：uvicorn 在 STFC 云虚拟机上
- vLLM：在 STFC 云虚拟机上独立运行
- 配置：`docker-compose.stfc.yml` + `deploy/stfc-cloud/nginx.conf`

### 7.2 随后端扩展需要变更的内容

- 在 goldilocks-api 启动时添加 JARVIS 缓存下载步骤（或独立初始化脚本）
- 在 `.env` 中添加 `MP_API_KEY`，用于 Materials Project 查询
- 构建 janus-api 后，将其加入 docker-compose
- 公开部署时，在 goldilocks-api 中添加生产域名至 CORS

---

## 8. 功能实现顺序

按用户价值和依赖链排列的优先级：

### Phase 1 — 让聊天真正有用（无需新建后端路由）✅ 已完成
1. **goldilocks-api `/api/chat` 中加入系统提示注入** ✅
2. **前端发送 `mode`、`experience_level`、`workspace_state`** ✅

### Phase 2 — DFT Workspace 后端
3. **`POST /api/dft/kpoints`** ✅ — heuristic 网格，使用 `k_distance_to_mesh()`；ML advisor 待模型文件就绪后切换
4. **`POST /api/dft/pseudo`** ✅ — `select_pp_candidates_for_structure()` + 本地 UPF 文件
5. **前端 DFT Inputs 标签页** — 调用 `/api/dft/kpoints` + `/api/dft/pseudo` 并展示结果 ✗ 未完成

### Phase 3 — 结构匹配
6. **goldilocks-core `structure/features.py`** — `extract_match_features()`
7. **goldilocks-core `structure/match.py`** — `run_matcher()` + `MatchResult`
8. **goldilocks-api `POST /api/structure-match`** — 并行 DB 查询 + StructureMatcher
9. **前端：接通"Search databases"按钮** — 替换 mock，调用真实 API
10. **前端：渲染动态结果卡片** — 替换写死的 3 条数据

### Phase 4 — MLIP Playground
11. **janus-api** — 构建 FastAPI 封装（singlepoint + optimise）
12. **goldilocks-api `/api/mlip/*`** — 代理路由至 janus-api
13. **前端 MLIP 指标/计算标签页** — 调用真实 API，渲染结果

### Phase 5 — Agent / 工具循环
14. **goldilocks-api 中实现工具调用** — 带 function calling 的 agent loop
15. **前端：工具执行指示器** — 工具运行时显示"🔧 调用 goldilocks-core..."

### Phase 6 — 微调模型（研究阶段）
16. 基于领域特定 DFT Q&A 数据训练/微调 `goldilocks-dft`
17. 与基础 Qwen3 一起部署在 vLLM 上
18. 前端模型选择器接入真实 vLLM 模型 ID

---

## 9. 已知问题 / 局限性

| 问题 | 所在位置 | 备注 |
|---|---|---|
| 结构内容截断至 3000 字符 | goldilocks-web `send()` | 大型 CIF 文件可能丢失数据 |
| `/api/dft/kpoints` 使用 heuristic 而非 ML | goldilocks-api | `advise_kpoints()` 需要 `.joblib` 模型文件；模型训练完成后切换 |
| `advise_pseudos()` 未实现 | goldilocks-core | `raise NotImplementedError`；`/api/dft/pseudo` 直接调用 `select_pp_candidates_for_structure()` |
| uvicorn `--reload` 不感知 goldilocks-core 变更 | goldilocks-api | 修改 goldilocks-core 源码后需手动重启 uvicorn |
| 无 JARVIS 缓存 | goldilocks-api | 结构匹配功能运行前必须先下载缓存 |
| 未配置 MP API key | goldilocks-api | `.env.example` 存在但密钥未设置 |
| Let's Go Cutting-Edge：仅有 Setup 标签页 | goldilocks-web | 没有像 DFT Workspace 那样的 Inputs 或 Checks 标签页 |
| MLIP 全部 mock | goldilocks-web | 每个 MLIP 数字都是写死的 |
| 页面刷新后无会话持久化 | goldilocks-web | 使用 localStorage，但结构文件内容会丢失 |
| `janus-api` 缺失 | — | 在构建之前 MLIP Playground 无法运行 |

---

## 10. 文件目录

```
code/
├── goldilocks-web/
│   └── src/
│       ├── App.jsx             主应用（~5100 行；所有 UI + 状态）
│       ├── App.css             （空文件；所有样式内联在 App.jsx 中）
│       ├── components/
│       │   └── WeasStructureViewport.jsx   3D 结构查看器
│       └── utils/
│           └── structureFiles.js           文件扩展名 / fence 语言辅助工具
│
├── goldilocks-api/
│   └── app/
│       ├── main.py             FastAPI 应用 + CORS
│       ├── routes/
│       │   └── chat.py         POST /api/chat (SSE)
│       └── services/
│           └── llm.py          vLLM 客户端 + _filter_think
│
├── goldilocks-core/
│   └── src/goldilocks_core/
│       ├── structure/
│       │   └── io.py           load_structure, analyze_structure
│       ├── kpoints/
│       │   ├── advisor.py      advise_kpoints（完整流水线）
│       │   ├── features.py     CSLR 特征提取
│       │   └── kmesh.py        k-mesh 构造
│       ├── pseudo/             UPF 解析 + 注册表 + 选择器
│       ├── ml/                 模型加载 + 推理
│       └── shared/types.py     共享数据类
│
├── goldilocks-design.md          系统级架构（英文）
├── goldilocks-design-CN.md       系统级架构（中文）
├── structure-match-design.md     结构匹配功能规格（英文）
├── structure-match-design-CN.md  结构匹配功能规格（中文）
├── stfc-cloud-webapp-usage.md    STFC 云部署说明（英文）
├── stfc-cloud-webapp-usage-CN.md （中文）
├── project-status.md             项目现状（英文）
└── project-status-CN.md          项目现状（本文件）
```
