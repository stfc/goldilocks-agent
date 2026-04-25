# Goldilocks 设计文档

*最后更新：2026-04-25（第三次更新）*

## 概述

Goldilocks 是 STFC 的计算材料研究 Web 应用。主界面是以聊天为核心的 LLM 助手。用户也可以通过右侧边栏激活专项工作区模式，侧边栏状态会自动共享给 LLM。

---

## 项目进展

| 层级 | 组件 | 状态 |
|---|---|---|
| 前端 | 完整 UI——聊天、4 个模式、侧边栏、所有工作区面板 | ✅ 已完成 |
| 前端 | 国际化（en / fr / de / it / zh）| ✅ 已完成 |
| 前端 | 结构查看器（WEAS 3D）| ✅ 已完成 |
| 前端 | 元素周期表弹窗 | ✅ 已完成 |
| 前端 | Find in Databases 面板（模拟数据）| ✅ 已完成（模拟） |
| 前端 | DFT Workspace 面板——全部选择器 | ✅ 已完成 |
| 前端 | MLIP Playground 面板（仅 UI）| ✅ 已完成（仅 UI）|
| 前端 | Let's Go Cutting-Edge 面板（仅 UI）| ✅ 已完成（仅 UI）|
| API | `/api/chat`——LLM 代理，SSE 流式 | ✅ 已完成 |
| API | `/api/health` | ✅ 已完成 |
| API | `/api/structure-match`——真实数据库检索 | ✗ 规划中 |
| API | `/api/dft/kpoints`——k-mesh 推荐 | ✗ 规划中 |
| API | `/api/dft/pseudo`——赝势选择 | ✗ 规划中 |
| API | `/api/mlip/*`——MACE / CHGNet 计算 | ✗ 规划中 |
| API | Agent 循环（系统提示 + 工具调用）| ✗ 规划中 |
| 核心库 | `goldilocks-core` | ✗ 规划中 |
| 核心库 | `janus-api` 服务 | ✗ 规划中 |
| 基础设施 | vLLM（Qwen3）运行于 STFC 云 | ✅ 运行中 |

---

## 包结构

| 包 | 类型 | 职责 |
|---|---|---|
| `goldilocks-web` | React/Vite 应用 | 前端 UI |
| `goldilocks-api` | FastAPI 服务 | 编排层，唯一的 HTTP 入口 |
| `goldilocks-core` | Python 库 | DFT 参数预测、轻量计算、ML 模型 |
| `janus-api` | FastAPI 服务 | janus-core 的 HTTP 封装（尚未构建）|
| `janus-core` | Python 库 | MLIP 模拟（MACE、CHGNet 等）|
| vLLM | 外部服务 | Qwen3 推理，托管于 STFC 云 |

`goldilocks-core` 直接导入 `goldilocks-api`（无需独立服务——计算量较轻）。

`janus-api` 是通用的 MLIP HTTP 接口，并非 Goldilocks 专用，其他项目可独立使用。

---

## 前端——模式系统

应用将功能组织为四个**模式**。激活一个模式会打开对应的右侧边栏面板，并将聊天限定在相关领域。

| 模式 | 颜色 | 右侧边栏 | 后端 |
|---|---|---|---|
| Find in Databases | 紫色 | 结构搜索面板 | goldilocks-api `/api/structure-match` |
| MLIP Playground | 绿色 | 模型选择器 + 分析标签页 | janus-api（经由 goldilocks-api）|
| DFT Workspace | 琥珀色 | 任务构建面板 | goldilocks-api `/api/dft/*` |
| Let's Go Cutting-Edge | 蓝色 | 方法选择器 | goldilocks-api（规划中）|

模式从聊天输入框下方的启动行选择，或从顶部的模式图标选择。激活模式后，下一条用户消息会自动加上 `[Mode: ...]` 前缀（正式的系统提示注入实现前的临时方案）。

---

## 前端——UI 结构

```
┌─ 侧边栏（260px）────────────────────────────────────────────────────────┐
│  品牌 Logo + 名称                                                        │
│  ─── 会话 ─────────────────────────────────────────────────────────── │
│  • 会话列表（最近聊天、按项目分组的聊天）                                  │
│  • 项目区块                                                              │
└─────────────────────────────────────────────────────────────────────────┘

┌─ 主区域 ──────────────────────────────────┬─ 右侧面板（320px）──────────┐
│  ┌─ 顶部栏 ─────────────────────────────┐  │  模式标题 + 来源说明         │
│  │  模式标签 · 模型选择器 · 主题切换    │  │  ──────────────────────── │
│  └───────────────────────────────────────┘  │  模式专属内容               │
│                                             │  （见各模式章节）            │
│  ┌─ 聊天记录 ───────────────────────────┐  │                             │
│  │  用户消息（含文件标签）               │  │                             │
│  │  助手消息（Markdown 渲染）           │  │                             │
│  └───────────────────────────────────────┘  │                             │
│                                             │                             │
│  ┌─ 输入区 ─────────────────────────────┐  │                             │
│  │  工具行：[📎 附件] [🔭 表格]        │  │                             │
│  │         [元素周期表] [结构查看器]    │  │                             │
│  │  模式启动器：4 个模式芯片            │  │                             │
│  │  文本输入框（自动调整高度）          │  │                             │
│  │  发送 / 停止按钮                     │  │                             │
│  └───────────────────────────────────────┘  │                             │
└─────────────────────────────────────────────┴─────────────────────────────┘
```

### 当前聊天结构（Structures in Current Chat）

列出当前会话中的所有结构文件：
- 已附加但尚未发送的文件（标注"Pending"）
- 已发送消息中包含的文件（从消息内容中自动提取）

此列表被以下功能共用：结构查看器（3D 展示）、Find in Databases 面板（查询结构选择）、DFT Workspace 面板（结构字段）。

### 结构查看器

基于 WEAS 的 3D 查看器，嵌入在输入区工具行中。展示当前从"当前聊天结构"中选中的结构。翻页箭头可在同一会话的多个结构间切换。

### 元素周期表

从输入区工具行弹出。两项功能：
- **浏览** — 点击元素，在聊天输入框中高亮显示
- **化学式构建器** — 点击元素累积化学式（Fe → Fe₂ → Fe₃）。按钮：↵ 插入输入框 · Search（触发结构搜索）· ✕ 清空

---

## 前端——DFT Workspace 面板

由 goldilocks-core 提供支持。三个标签页：**Setup**、**Inputs**、**Checks**。

### Setup 标签页——选择器布局

```
Structure       <文件名或"No structure in chat">        （只读）
Machine         ARCHER2                                        ▼
Code            Quantum ESPRESSO                   ✦ Ask      ▼
Task            Geometry optimisation              ✦ Ask      ▼
──────────────────────────────────────────────────────────────
Task builder              ✦ Ask Goldilocks
──────────────────────────────────────────────────────────────
Functional      PBE                                            ▼
Pseudopotential ONCV                                           ▼
K-mesh method   Monkhorst-Pack                     ✦ Ask      ▼
K-distance      Standard (~0.25 Å⁻¹)              ✦ Ask      ▼  ← kplib 时禁用
Smearing method Methfessel-Paxton N=1                          ▼
Smearing width  0.02 eV                                        ▼
```

**✦ Ask 药丸按钮** — 出现在有推荐项的选择器触发器上（Code、Task、K-mesh method、K-distance）。点击后自动选中推荐值，并在聊天输入框插入一条对话式 prompt，如"I just picked Quantum ESPRESSO — what makes it a great choice, and what's it really good at?"

**Task builder ✦ Ask Goldilocks** — Task builder 标题上的琥珀色药丸按钮。一键设置 Functional=PBE + Pseudo=ONCV + K-distance=Standard + Smearing=MP1 0.02 eV，并在聊天输入框插入综合解释 prompt。

**Machine 无 ✦** — 用户只能在有权限的机器上运行；推荐了也没有实际意义。

**下拉选项中的 ✦ 星形按钮** — 每个选项行在悬停时显示小型琥珀色 ✦ 圆形按钮。点击后在聊天输入框插入关于该选项的对话式 prompt。

### 选择器选项分组

| 选择器 | 分组 |
|---|---|
| Machine | 英国通用国家级和 Tier-2 系统 · 加速器和专项系统 |
| Code | 周期性固态主力代码 · 高精度和线性缩放 · 全电子和专项 · 分子和混合工作流 |
| Task | 基态和弛豫 · 电子结构 · 振动和响应 · 缺陷、表面和输运 |
| Functional | LDA · GGA · Meta-GGA · Hybrid |
| Pseudopotential | 赝势类型 · 库 |
| K-mesh method | 网格类型（Monkhorst-Pack / Γ-centred）· 自动 k 路径（kplib）|
| K-distance | 间距预设（Γ-only / Light / Standard / Dense / Very dense）— kplib 时禁用 |
| Smearing method | 金属（MP1 / MP2 / Marzari-Vanderbilt / Fermi-Dirac）· 绝缘体/半导体（Gaussian / Tetrahedron）|
| Smearing width | 0.005 / 0.01 / 0.02 / 0.05 / 0.1 / 0.2 eV |

### Inputs 标签页

DFT 引导上下文的预览，显示将发送给 LLM 的代码、任务、机器摘要。未来计划展示 goldilocks-core 生成的真实输入文件框架。

### Checks 标签页

基于当前选择器状态的验证提示清单（代码一致性、k 点、展宽、并行化适配性）。

---

## 前端——Let's Go Cutting-Edge 面板

由 goldilocks-core 提供支持（规划中）。目前仅有 Setup 标签页（尚无 Inputs 或 Checks）。

选择器：Method · Code · Machine。

方法分组：关联修正 DFT（DFT+U、DFT+DMFT、杂化泛函、SIC）· 多体微扰理论（GW、BSE、RPA、MP2）· 含时方法（TDDFT）· 量子蒙特卡洛（QMC）· 模型与嵌入（MFT、Wannier 函数、QM/MM）。

---

## 前端——Find in Databases 面板

由 goldilocks-api 提供支持。无标签页。

**查询框** — 单一卡片，顶部有 **Structure | Formula** 切换按钮：
- **Structure 模式**：显示"当前聊天结构"中选中的结构（多个文件时附翻页箭头）；无结构时 Search 按钮禁用。
- **Formula 模式**：自由文本输入（如 `Fe2O3`）；输入为空时 Search 按钮禁用。

**What are you looking for?**（你在找什么？）— 查询框下方的多选属性芯片，用于过滤各数据库的子数据集。选项：Electronic structure · Stability & energy · Magnetic · Elastic & mechanical · Phonons & thermal · Optical。

**候选结构区块** — 结果按数据库分组展示，每组可折叠。每条记录显示：化学式（粗体）· 空间群 · 可点击链接。标题处有 **✦ Ask Goldilocks** 药丸按钮，可与 LLM 讨论搜索结果。

两种搜索模式：

| 模式 | 触发方式 | 过滤条件 | StructureMatcher |
|---|---|---|---|
| 文件搜索 | "Search databases"按钮（Structure 模式）| 化学式 + nsites + 元素 | 是 |
| 化学式搜索 | "Search databases"按钮（Formula 模式）| 仅元素 | 否 |

### 数据库访问方案

| 数据库 | 访问方式 | 认证 | 结果链接格式 |
|---|---|---|---|
| Materials Project | `mp-api` Python 库（服务端） | API key 存于 goldilocks-api `.env`，用户不可见 | `https://next-gen.materialsproject.org/materials/{mp-id}` |
| JARVIS | `jarvis-tools` 本地数据集（一次性下载，本地缓存） | 无 | `https://www.ctcms.nist.gov/~knc6/static/JARVIS-DFT/{JVASP-ID}.xml`（公开，无需登录）|
| Materials Cloud | OPTIMADE REST API（`optimade.materialscloud.org`）| 无 | `https://mc3d.materialscloud.org/details/{mc3d-id}/pbesol-v2` |
| NOMAD | NOMAD API / OPTIMADE | 无 | NOMAD 条目 URL |

---

## 前端——MLIP Playground 面板

由 janus-core 经 janus-api 提供支持（尚未接通）。顶部为模型选择器（MACE-MP-0、CHGNet、ALIGNN）。三个标签页：Analysis · Metrics · Compute。

---

## API 结构（goldilocks-api）

```
goldilocks-api
  ├── /api/chat              LLM 代理（SSE 流式）        ✅ 已构建
  ├── /api/health            状态检查                    ✅ 已构建
  ├── /api/structure-match   跨数据库结构搜索             ✗ 规划中
  ├── /api/dft/
  │     ├── /kpoints         k-mesh 推荐                 ✗ 规划中
  │     └── /pseudo          赝势选择                    ✗ 规划中
  └── /api/mlip/
        ├── /singlepoint     单点计算（via janus-api）    ✗ 规划中
        ├── /optimise        结构优化                    ✗ 规划中
        └── /md              分子动力学                  ✗ 规划中
```

`/api/dft/*` 直接调用 `goldilocks-core`（进程内）。  
`/api/mlip/*` 转发至 `janus-api`。

---

## Agent 循环（/api/chat——规划中）

**当前状态**：`/api/chat` 将消息直接代理给 vLLM，无系统提示，无工具调用。

**目标状态**：

```
前端发送：messages + mode + workspaceState + structure + experienceLevel
  → goldilocks-api 构建系统提示 + tool schemas，发送至 vLLM
  → vLLM 返回 tool_call（非流式）
  → goldilocks-api 执行工具（goldilocks-core 或 janus-api）
  → 工具结果作为 tool message 发回 vLLM
  → vLLM 流式返回最终自然语言响应
  → SSE 返回前端
```

暴露给 LLM 的工具：
- `predict_kpoints(structure)` → goldilocks-core
- `select_pseudopotentials(structure, code)` → goldilocks-core
- `search_databases(structure)` → `/api/structure-match`
- `run_singlepoint(structure, model)` → janus-api
- `run_optimise(structure, model)` → janus-api

---

## 侧边栏 → 聊天集成

当用户直接在侧边栏运行计算时：

1. `goldilocks-api` 对每个计算端点同时返回 `raw` 和 `summary` 字段
2. 前端使用 `raw` 在侧边栏渲染结果
3. 前端**自动**将 `summary` 作为系统消息追加到对话历史
4. 下次 LLM 调用时，侧边栏结果已包含在上下文中——无需用户操作

```json
{
  "raw": { "energy": -3.42, "forces": [[...], ...] },
  "summary": "MACE 单点计算完成：能量 -3.42 eV，最大力 0.02 eV/Å，结构稳定"
}
```

---

## 前端共享状态

```
session
  ├── messages: Message[]           完整对话历史
  ├── mode: string | null           当前激活的模式 id
  ├── rightPanelOpen: bool
  └── rightPanelView: string | null 右侧面板中的当前标签页

attachedFile                        待发送的结构文件（发送前）
chatStructures                      从消息历史中提取的所有结构文件
viewerIdx                           查看器/匹配查询中当前选中的结构索引

selectedDftCode                     代码选择器值
selectedDftFunctional               泛函选择器值
selectedDftPseudo                   赝势选择器值
selectedDftTask                     任务选择器值
selectedDftMachine                  机器选择器值
selectedDftKmethod                  k-mesh 方法选择器值（Monkhorst-Pack / Γ-centred / kplib）
selectedDftKdistance                k-distance 选择器值 — kmethod = kplib 时忽略
selectedDftSmearingMethod           展宽方法选择器值
selectedDftSmearingWidth            展宽数值选择器值
selectedBeyondDftMethod             Cutting-Edge 方法选择器值
selectedMlipModel                   MLIP 模型选择

input                               聊天输入框内容
loading                             SSE 流传输进行中
structureMatchLoading               结构匹配进行中

language                            UI 语言（en / fr / de / it / zh），持久化到 localStorage
```

---

## 数据流总览

```
goldilocks-web
      │ HTTPS / SSE
      ▼
goldilocks-api          ← 轻量编排层
   ├── import goldilocks-core    （DFT，进程内）
   ├── → vLLM                   （LLM 推理）
   └── → janus-api              （MLIP）
              └── import janus-core
```

---

## 决策记录

| 决策 | 选择 | 原因 |
|---|---|---|
| 前端框架 | React + Vite（非 Next.js）| 此场景不需要 SSR |
| 工具结果在聊天中的呈现 | 纯文本（LLM 描述）| 更简单、对话更自然 |
| 侧边栏 → 聊天注入 | 自动注入，仅注入 summary | UX 低门槛，token 数量保持低位 |
| goldilocks-core 部署方式 | 进程内（由 goldilocks-api 导入）| 计算量轻，无需独立服务 |
| janus-api 范围 | 通用，非 Goldilocks 专用 | 可被其他项目复用 |
| 结构数据格式 | CIF / POSCAR / XYZ 上传，内部使用 ASE 兼容 JSON | 材料科学标准格式 |
| 模式名称 | Find in Databases · MLIP Playground · DFT Workspace · Let's Go Cutting-Edge | 描述性 + 对前沿方法有活力感 |
| Machine 无 ✦ 推荐 | 用户只能在有权限的机器上运行 | 推荐了也没有实际意义 |
| Task builder ✦ 的范围 | 一次推荐 Functional + Pseudo + K-mesh + Smearing | 四者紧密耦合，分别推荐易造成部分不一致 |
| Code 和 Task 置于 Task builder 之外 | Code 和 Task 各有独立 ✦ | Code 是所有参数的上游；Task 与泛函/赝势/网格/展宽块正交 |
| UI 国际化 | TRANSLATIONS 常量 + `t(key)` 函数，语言存于 localStorage | 简单的扁平键值查找；PBE、ONCV、MACE 等专有名词在所有语言版本中保持英文 |
| 支持语言 | English · Français · Deutsch · Italiano · 中文 | 覆盖 STFC 及欧洲合作机构的主要用户群体 |
| Materials Project 认证 | API key 存于 goldilocks-api `.env`，不暴露给前端 | MP 使用条款允许用一个 key 为多个用户服务；用户无需自备账号 |
| JARVIS 数据访问 | `jarvis-tools` 下载完整数据集至本地；结果链接指向公开 XML 文件 | `ctcms.nist.gov` 上的 XML 无需登录即可访问；避免依赖 JARVIS 网站的认证机制 |
| Materials Cloud 链接格式 | `mc3d.materialscloud.org/details/{id}/pbesol-v2` | 直接条目 URL；旧格式 `mc3d.materialscloud.org/{id}` 无法解析 |
| K-mesh 拆分为方法 + 间距 | 两个独立选择器：K-mesh method（网格类型 / kplib）和 K-distance（间距预设）| kplib 自动生成 k 路径，不接受 k-distance；单一选择器无法同时表达两个概念 |
| ✦ Ask 替代 ? 按钮 | 所有询问按钮（选择器触发器药丸和下拉选项圆形按钮）均使用 ✦ 琥珀色星形 | 全局统一琥珀色星形语言；对话式 prompt 比冷冰冰的"What should I know about X?"更自然温暖 |
| Settings 按钮简化 | 仅显示居中的"Settings"文字，去掉图标和副标题 | 减轻侧边栏底部的视觉负担 |

---

## 待决事项

- 存储方案：是否持久化上传的结构和计算结果（文件系统 + SQLite，还是仅保留会话状态）？
- Agent 循环流式 UX：工具执行期间前端究竟展示什么？
- Let's Go Cutting-Edge：是否添加 Inputs 和 Checks 标签页以与 DFT Workspace 保持一致？
- 化学式搜索结果展示：如何在视觉上区分化学式搜索结果和文件搜索结果？
