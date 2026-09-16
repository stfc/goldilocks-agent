# goldilocks-agent 实施计划

> **状态**：草稿，2026-09-15 首版。
> **这份文档管什么**：**用什么技术、按什么步骤把 `goldilocks-agent-design.md`
> 里已经定的东西建出来**——不重复"为什么这样设计"，那些理由都在设计文档里，
> 这里只挑"现在要做什么、用什么库、先后顺序"。
> **与生态实施计划的关系**：[`goldilocks-implementation-plan.md`](../../0-goldilocks-ecosystem/1-docs/v2/goldilocks-implementation-plan.md)
> 第七节"阶段四 · agent"是这份文档的上位文档——那边定的是**agent 在整条
> `data→ml→core→agent` 建设顺序里排第几个动工**，这里定的是**agent 自己
> 动工之后内部怎么排**。

---

## 一、技术栈总览

只列已经在设计文档里拍板的选择，不在这里重新论证——每行标注出处，
有疑问回那一节找理由。

| 层 | 技术 | 出处 |
|---|---|---|
| 编排 | **LangGraph**（不用 LangChain 的模型抽象，CrewAI 排除）| 设计文档十七 |
| checkpointer | `AsyncSqliteSaver`（`langgraph-checkpoint-sqlite>=3.1,<4`）| 十七之六② |
| 与 core 通信 | **MCP，agent 拉起 core 的 stdio 子进程**（不直接 `import`，应用逻辑也走）| 十四之三 |
| 对话引擎（云端）| **litellm**（多家供应商统一） | 十七之六① |
| 契约模型 / 需要 grammar 的场景 | **直连运行时**（llama.cpp GBNF 是唯一强制直连的理由）| 十七之六① |
| 本地推理运行时 | **Ollama**（第一阶段），走 Ollama **官方库**（`ollama pull qwen3.8`，非 HuggingFace 第三方量化）。⚠️ **"不走 registry、走 PSDI 校验"这条只在用我们自己发布的 fine-tuned 权重时才适用**——当前默认的 Qwen3.8-27B 是拿来就用的基座模型，没有 PSDI 发布物要保护，不需要搭那套校验管线（2026-09-15 收窄） | 十一之四 · 十一之二 |
| 本地 server | **FastAPI**，只听 `127.0.0.1`，一次性 token，检查 `Origin` | 十 · 十.1 |
| 持久化 | **SQLite 单文件**：六张自建表 + LangGraph 的 `checkpoints`/`writes`，同一个 `.db`，前缀只加在自己的表上 | 十六之五 · 十七之六② |
| 前端 | **React + Vite**，沿用旧 `goldilocks-web`（现 `app/`），视觉身份独立于 `core/web` | 十三 · 十二之二 |
| AiiDA | **外部服务**，agent 自己的工具节点直接调，不经 core 的 MCP | 十五之一 |
| 凭证存储 | `~/.config/goldilocks/config.toml`（`0600`）：LLM API key、AiiDA profile 引用 | 十一 · 十五之一 |

### 1.1 对话引擎默认模型

> **★ 已定（2026-09-15，用户拍板）：本地对话引擎默认用 [`Qwen/Qwen3.8-27B`](https://huggingface.co/Qwen/Qwen3.8-27B) 作为基座。**

✅ **现状（已核实、已跑通）**：`Qwen/Qwen3.8-27B` 是真实的 Qwen 官方仓库，
Apache-2.0，27B 参数，原生 262K 上下文（Ollama 实际加载时给的运行时窗口是
32768，见 `ollama ps`，代码里没有覆盖这个默认值）。走 **Ollama 官方库**：
`ollama pull qwen3.8` → 注册为 `qwen3.8:latest`，`graph.py` 里
`LOCAL_MODEL = "ollama_chat/qwen3.8:latest"`。是**视觉-语言多模态模型**
（`app/`已经在用它的视觉能力，见第 5 步图片附件那条）。

Qwen3.8-27B **不是我们微调的**，是拿来就用的基座模型——十一之四"不走
Ollama registry、走 PSDI 校验"那条规则的适用对象是"goldilocks-ml 自己发布
的权重"，这个模型不满足这个前提，**不需要搭那一整套 PSDI→assets/→校验→
Modelfile 的管线**，`llm_calls.model_identity_pinned` 记 `false`。composer
里的 "Goldilocks LLM" 是留给 `goldilocks-ml` 未来真的发布 fine-tuned 版本时
用的占位条目（`disabled: true`，还没有实体）。

27B 这个体量对十一之四"Mac 走 Metal，不需要 GPU"这条已定结论提出了真实的
资源要求（Q4 量化约 15-18GB，需要对应的统一内存）——不构成否决理由，但值得
记一笔，免得实现时忘了在系统需求里写出来。

⚠️ **踩过的坑，供以后同类核实参考**：①HuggingFace 上搜同名模型会出一批
`Uncensored`/`Abliterated` 之类社区"越狱"仓库，选型时要认官方仓库；
②最初以为 Ollama 官方库没有 Qwen3.8（只查了 `ollama.com/library/qwen3`
这个旧大版本页面，看到只到 `qwen3:32b` 就下结论"没有"），漏查了 `qwen3.8`
自己独立的库页面——新大版本号是新页面，不是挂在旧版本下的 tag，一度绕道
HuggingFace 第三方量化（`hf.co/unsloth/Qwen3.8-27B-GGUF`）拉取，后来用真实
`ollama pull qwen3.8` 验证官方库确实有这个模型后，删掉了那份第三方量化，
改用现在这条官方路径。

---

## 二、动工前必须先做的三件事（顺序不能反）

来自生态实施计划第七节，原样保留在这里因为它是**agent 自己动工前的检查表**，
不是"以后阶段再看"的东西：

### 2.1 走一遍 [17.8 状态流转](goldilocks-agent-design.md)，不要跳过

> 设计文档自己的原话：*"实现本地 SCF 端到端路径之前，先让这条链走通。"*

理由：**"草稿归谁、版本怎么走、什么时候落盘"这三件事，条文互相对照对不出来，
只有走一遍真实时序才验得出来**——这批修正（`draft_revision`、审计凭证由运行时
注入、只有编辑过的字段进 `human`、`operation_id` 幂等）就是这么漏出来的。
写代码前，先把 17.8 那张时序表里的六步在纸上（或者写成一个测试）过一遍。

### 2.2 划死"传路径 vs 传内容"这条边界

> 浏览器 → agent server：**路径**。agent → core（MCP）：**内容**。

这条现在改，比以后改便宜（九之一）。**不要图省事让 core 读 agent server
的本地路径**——那等于无声地放宽了所有 MCP 客户端（包括 Claude Desktop 这类
外部 agent）的权限。

### 2.3 六张表 + LangGraph checkpointer 从第一天起就同一个 `.db`

不要先按自己的 schema 建表、以后再引入 LangGraph——**两套状态存储一旦并存，
很难拆**（十七之二）。`checkpoints`/`writes` 是 LangGraph 的地盘，迁移脚本
只碰自己的六张表。

---

## 三、建设顺序（第一阶段：DFT Workspace 的 QE scf 端到端）

判据来自设计文档十二之一：**先做 DFT Workspace 不是因为它最容易，
是因为只有它能检验 v2 core 的契约**（字段优先级 · 三态 · 契约白名单 ·
缺赝势诊断 · 打包保证）。以下步骤按依赖顺序排列，**不是并行任务清单**。

⚠️ **2026-09-15 用户改动实际动工顺序（跟下面的步骤编号不再一一对应，
以此处为准）**：**Find in Databases → janus-core/aiida MCP 接入 → DFT
Workspace → 复杂 graph**。跟十二之一"先做 DFT Workspace 验证 core 契约"
的理由不冲突——**Find in Databases 对 core"几乎没有"依赖，janus-core/aiida
是外部服务、不经 core**（十二 · 十五已定），所以先做这三个不会在"core 契约
到底长什么样"这件事上走弯路：真正会因为 core 契约细节而需要返工的只有
DFT Workspace 本身，而它仍然排在"复杂 graph"之前完成，验证契约这件事没有
被无限期推迟，只是不再是第一步。

### 已经在跑的（不用重做）

- ✅ `1-goldilocks-agent` 的标准包结构（`pyproject.toml` · `src/goldilocks_agent/`
  · `tests/` · `AGENTS.md`），`uv sync` / `uv run poe check` 都过
- ✅ `app/`（前端）已从旧 `goldilocks-web` 搬进来，改了名字避免跟 `core/web`
  撞（十三）
- ✅ **Tool / mode / widget 三层词汇已经在代码里对齐**（2026-09-15 定：
  界面上顶层入口统一叫 **Tool**，历史上 `MODES`/`activeMode`/`activateMode`/
  `ModeGlyph` 等内部变量名也已经跟着整体改成 `TOOLS`/`activeTool`/
  `activateTool`/`ToolGlyph`——**前端会话对象的字段名实际是 `tool`，不是
  `mode`**（`App.jsx` 的 `session.tool`），`app/src/App.jsx` 里已经 grep
  不到跟"顶层入口"相关的 `mode` 拼写了（`dbQueryMode`/`mlipRelaxMode`/
  `relax-mode-desc`/`mode: "formula"` 这几个是完全不同的概念——分别是"按
  公式还是按文件查库"、"MLIP 计算是弛豫还是单点"，故意没有改，别把它们和
  Tool 搞混）。
  ⚠️ **2026-09-15 现状**：`store.py` 的 SQLite `conversations` 表列名仍叫
  `mode`（设计文档十六之五定的名字，没有跟着前端改），但这一列目前完全
  没被接通——`touch_conversation()` 插入新行时恒写 `NULL`，`server.py` 也
  没有暴露修改它的接口。也就是说前端 `tool` 状态目前**不落库**，`mode` 是
  个还没连上的死列——这条贯通留给第 3 步接 tool 节点/系统提示词时一起做
- ✅ 前端里 DFT Workspace 的 Setup/Inputs/Checks 三个标签页骨架已经在
  （`App.jsx` 里 `activeTool.id === "dft-setup"` 分支），**但内容是硬编码的
  本地数组，不是真的接 core**
- ✅ 顺手删掉的死代码：`buildMockReply`（旧的假回复生成器，十三之 x 早就
  点名要删，这次改名顺带清掉了）、composer 里不接后端的 "Web Search" 挂件、
  欢迎页的四条硬编码 suggestion prompt
- ✅ **`TOOLS` 从四个加到六个**（十二已定，2026-09-15）：新增 **Post
  Analysis**（📊，对应二节职责 3）和 **AiiDA**（⚙️，对应十五节，但是
  "浏览/监控"不是"提交"）。两个都只是**占位面板**——`renderWorkspace()`
  加了一个通用 fallback（原来 MLIP Playground 那段是无条件 `return`，
  顺手补了 `if (activeTool.id === "ml-analysis")` 判断，不然新加的两个
  tool 会错误地掉进 MLIP 的面板里），显示 "Coming soon" + 该 tool 的
  `desc`，真正的功能仍在下面待排
- ✅ **Beyond DFT "没有 core 兜底"已显式标出**（十八已定）——原来的
  "Powered by goldilocks-core"徽标（和其它三个 tool 共用样式，会误导）换成
  了黄色警告徽标 `.workspace-no-backing`，文案照设计文档 5.2 措辞（五语言）
- ✅ **Find in Databases，formula 模式**（2026-09-15，按上面确认的新顺序做的
  第一块）——`src/goldilocks_agent/databases.py` + `jarvis_cache.py`，从
  `old-goldilcoks-webapp` 按formula 路径原样移植（Materials Project 用
  `mp-api`，需要免费注册的 `MP_API_KEY`；JARVIS 无需 key，数据集缓存到
  `~/.cache/goldilocks`，`poe fetch-jarvis-cache` 手动触发一次性下载；
  Materials Cloud/NOMAD 是公开 OPTIMADE 端点，都不需要 MCP——十四之三"全部走
  MCP"约束的对象是 core，不是任意外部服务）。**"file" 匹配模式没有做**：
  旧代码那部分直接 `import goldilocks_core.structure.{features,io}`，按已定
  规则必须经过 core 的 MCP client，而那个 client 还没搭（第 1 步），所以
  `server.py` 对 `mode="file"` 直接返回 501 说明原因，不是假装支持。
  `server.py` 新增 `POST /api/structure-match`、`GET /api/fetch-structure`，
  前端（`App.jsx` 的 `handleFormulaSearch`/`handleImportStructure`）本来就
  已经按这个契约写好了，之前一直是打真实 404。
  **真实验证**：`tests/test_databases.py` 对 Materials Cloud/NOMAD 做真实
  网络请求查 NaCl（不 mock），断言真的拿到结果；另外用 curl 经 Vite 代理走了
  一遍完整流程——搜 "Si" 拿到 40 条真实候选，用其中一个 entry_id 调
  `/api/fetch-structure` 拿到真实、正确的 CIF 内容。

  > ⚠️⚠️ **2026-09-15 用户纠正（当天晚些时候）：上面"file 模式卡在 core 的
  > MCP client"这条判断是错的，不是待重新评估——是错的。** 核实过旧代码
  > （`old-goldilcoks-webapp/goldilocks-core/src/goldilocks_core/structure/
  > {io,features}.py`）：`load_structure` 整个函数体就是
  > `Structure.from_file(...)`（纯 pymatgen），`extract_match_features`
  > 就是 `SpacegroupAnalyzer` + `Composition`（同样纯 pymatgen/spglib）——
  > **零 core 特有的 DFT 领域逻辑**，跟`query.py`现在`_optimade_to_structure`/
  > `_enrich_with_spacegroup`已经在做的事完全同一类。现在的 core（v2）
  > 自己的维护者也已经把这个函数从 core 里删了，理由记在 `inputs/structure.py`
  > 的 docstring 里："zero non-test callers... dead code, not an edge worth
  > keeping"——**core 自己都不认为这是它的职责**。
  > → **十四之三"全部走 MCP"这条规则被引用错了对象**：那条规则管的是
  > 调 core 的**领域决策**（`advise`/`generate`/`capabilities`），不是"解析
  > 一个上传的结构文件拿出公式和空间群"这种通用晶体学操作。**file 模式
  > 不需要等 core 的 MCP client，现在就能直接在 `query.py`/`server.py` 里用
  > pymatgen 实现**（`Structure.from_file`/`from_str` + `SpacegroupAnalyzer`
  > + `Composition`，复用 formula 模式已有的 OPTIMADE/mp-api/JARVIS 查询
  > 路径按 formula+spacegroup 过滤）——**只是这次先不动手，记录下来以后再做**。

### 第 1 步：让 core 跑起来、能被够到

- 起 `goldilocks-core` 的 HTTP 服务（`poe serve`，`:8000`）或者验证 MCP
  stdio 子进程能拉起来（`goldilocks serve mcp` 之类，具体命令看 core 的
  `pyproject.toml`）
- **先选 HTTP 还是先选 MCP**：设计文档已定"全部走 MCP"是**终态**（十四之三），
  但生态实施计划没有规定"验证阶段必须一开始就上 MCP"。★ **建议**：先用
  core 自带的 HTTP（`core/web` 已经在用的那条 `/capabilities` 代理路径）
  把 DFT Workspace 面板的数据先跑真，**这一步不产生需要推翻的架构决定**——
  MCP 那层是"agent 怎么调 core"，跟"面板从哪读 capabilities"是十四之三
  自己区分过的两件事（应用逻辑 vs LLM 工具调用）。等 Python 后端真的要接
  LangGraph 时，再把这条路径切到走 agent 自己的 MCP client。

### 第 2 步：DFT Workspace 面板接 `capabilities`，删掉硬编码数组

- 复用 `core/web` 已验证的**模式**（不是代码）：`ScientificRecord` 的
  来源徽章、`CalculationForm` 的 `capabilities` 驱动渲染（十二之三 · 十三）
- 可机械检查的验收标准（十三已定）：**`app/` 里 grep 不到任何泛函名 /
  赝势族名 / smearing 类型名**

✅ **第 2 步已建成（2026-09-15）**：

- **走 CLI，不是 HTTP/MCP**（当天用户明确改的主意，理由记在
  `tools/dft_workspace/`包自己的 docstring 里）：`goldilocks
  {inspect,explain,run,settings}` 跟 HTTP 的 `/inspect`/`/explain`/`/run`/
  `/capabilities.settings[]` 是同一套底层函数，CLI 不需要起一个常驻服务、
  不用管端口，`goldilocks settings --json` 就能拿到全部 49 个真实设置项
  （字段结构跟 `/capabilities` 的 `settings[]` 完全一样）。唯一的真缺口：
  **CLI 没有一个命令能一次吐出 `/capabilities` 那份组合数据**
  （`codes[]`/`tasks[]`/`pseudopotential_tables[]`/`hpc_profiles[]`/
  `warnings[]`目录/`models[]`/`sources[]`）——这条已经报给
  goldilocks-core 了：[junwen94/goldilocks-core#62](https://github.com/junwen94/goldilocks-core/issues/62)，
  不是绕过去，是照用户要求"先用 CLI 能做的做，做不到的记文档、提 issue"。
- `src/goldilocks_agent/tools/dft_workspace/`（`client.py` 拉 `uv run
  --project <core路径> goldilocks ...` 子进程 ·`models.py` 结果模型 ·
  无 `tool.py`，这一轮明确只接面板，LLM 工具调用留到下一阶段，跟
  Find in Databases/MLIP Playground 当初"先面板后 tool node"的顺序一样）。
  `server.py` 新增 `GET /api/dft/settings`、`POST /api/dft/{inspect,
  explain,run}`，都不需要确认卡片（这是推荐逻辑+本地写文件，不是花算力
  的真实计算，跟 `/api/structure-match` 一个待遇）。
- **`run` 的真实返回形状踩了一个坑，如实记录**：`goldilocks run -o <dir>
  --json`（真正写文件那次）返回的是 `{files: [...], kind, path}`，**不是**
  `{files, records, warnings}`——后者只在"内存预览"模式（不带 `-o`）才有。
  一开始按后者的假设写了 `RunResult`，真跑一次才发现不对，改成 `run()`
  只管文件内容，`records`/`warnings` 全部走面板已经在调的 `explain()`，
  不为了凑数据结构多打一次子进程。
- **`app/src/App.jsx` 的 DFT Workspace 面板整个从假的 v1 词汇表换成真的
  v2 词汇表**：17 个假 DFT 码、19 个假 task、10 个假 UK HPC 机器、9 个假
  泛函、8 个假赝势类型/库、假的 k 点方法/k 距离/smearing 方法/宽度分桶
  ——一个都不对应 v2 真实存在的东西，全删了。换成：真实的 1 个 code
  （Quantum ESPRESSO）+ 4 个 task（来自 `--task` 的 argparse 固定选项，
  不是猜的）硬编码在前端（注释标明原因，等 goldilocks-core#62 解决了
  就该从真实接口拿）；HPC 改成纯文本框（`hpc_profiles[]`拿不到，蒙一个
  假下拉比不下拉更糟）；原来 6 组假 picker（functional/pseudo/kmethod/
  kdistance/smearing-method/smearing-width）**全部**换成一个通用的
  "settings overrides"区块——真从 `/api/dft/settings` 拉 49 项真实设置，
  用户按需勾选覆盖，不勾的全部交给 advisor 自动判断（这才是 v2 真实的
  `overrides: {}`/`--set` 模型，不是"每个旋钮都得自己转一遍"的 v1 心智
  模型）。
- **顺手修了一个独立的真 bug**：`send()`里判断要不要发 `workspace_state`
  用的是 `targetSession.tool === "dft"`，但 DFT Workspace 真实的 tool id
  是 `"dft-setup"`——这个条件从来没成立过，不管词汇表对不对，选的东西
  从来没发到后端。同一行代码 `"mlip"` vs 真实 id `"ml-analysis"` 也是
  同一个坑，一并修了。Beyond DFT 面板原来共用 DFT Workspace 的
  `selectedDftCode`/`selectedDftMachine` 这两个全局变量画它自己的
  Code/Machine picker——这次 DFT Workspace 的这两个概念改成了按 chat
  隔离的真实状态，就给 Beyond DFT 单独拆了两个不共享的
  `beyondDftCode`/`beyondDftMachine`（Beyond DFT 本来就有诚实的"无 core
  支撑"徽章，这两个 picker 一直只是装饰，拆开不影响它的行为）。
- Inputs tab 现在点"Generate"真的调 `/api/dft/run`，显示真实生成的
  `scf.in`/`submit.sh`/`goldilocks.json` 等文件内容；Checks tab 显示
  `/api/dft/explain`真实返回的 `warnings[]`——都不再是原来的静态模板字符串。
- **真实验证**：`tests/tools/test_dft_workspace.py`——3 个纯解析单测
  （断言 `run()` 的返回形状确实没有 `records`/`warnings`，防止上面那个坑
  回归）+ 4 个 `integration` 标记、真的对本机 `4-goldilocks-core`（分支
  `61-docs-v2-rewrite`）跑 `goldilocks explain`/`run`/`settings` 的测试，
  全部通过；另外直接对本机真实跑起来的 `goldilocks-agent` 后端 curl 了
  一遍 `/api/dft/{settings,inspect,explain,run}` 四个端点，数据真实
  （如 `ecutwfc_ry: 48.0`、真实生成的 `scf.in` 内容）。前端本身的点击
  流程没有在浏览器里手动验证过（沙盒连不到用户自己起的 Vite dev
  server），只验证了 `npm run lint`/`npm run build` 干净。

### 第 3 步：最小 LangGraph 图 + 云端 LLM（不碰本地模型，先求闭环）

- 十七之四那张最小图：一个 `llm` 节点、一个 `tool` 节点、一条 `interrupt` 边
- **先用云端 LLM（litellm 随便接一家）**，不是因为本地模型不重要，是因为
  这一步要验证的是**图的形状对不对**（工具调用、interrupt、草稿状态），
  这些跟"用哪个模型"正交——先把最省事的模型接上，图跑通了再换本地模型，
  避免两个未知数（图对不对 / 模型部署对不对）一起排查
- 这一步同时把 §12.5 已定的"chat 与面板同一份草稿"、§17.10 的 interrupt
  卡片、§12.6 的多 mode 一个会话，在真实工具调用下走一遍

### 第 4 步：六张表 + `AsyncSqliteSaver` 落地

- 严格按十六之五的 schema；`values` 表只在 `generate()` 成功（含被 blocking
  拦下）时写，`advise()` 不写
- `bundles` 表带上十五之四补的 `sink`/`aiida_pk`/`job_status`（哪怕 AiiDA
  还没接，列先留着，全部走 `sink="download"`）

### 第 5 步：本地模型（Qwen3.8-27B via Ollama）接上，云端降级为可选项

- ⚠️ **已定简化**：Qwen3.8-27B 不是我们微调的，**跳过 PSDI 校验链**，走 Ollama
  官方库，再把 litellm 的 provider 指向本地 Ollama endpoint——十一之四那条
  "不走 registry"的规则留给以后 `goldilocks-ml` 真的发布 fine-tuned 版本时
  再启用（见 §1.1）

**现状（已建成、已验证，过程细节见 git 历史，这里只写结论）**：

- `src/goldilocks_agent/graph.py`：十七之四最小图的缩减版，一个 `llm` 节点
  （`litellm.acompletion`，先用 `convert_to_openai_messages()` 把 LangChain
  消息转成 OpenAI 风格纯 dict 再传给 litellm——直接传 LangChain 消息对象会
  被静默转错角色）。`resolve_model()` 按 `model_id`/`GOLDILOCKS_AGENT_MODEL`
  环境变量在本地 Ollama（`ollama_chat/qwen3.8:latest`）和云端（`CLOUD_MODELS`：
  `anthropic-claude`/`openai-gpt`/`google-gemini`）之间路由。**不含 tool 节点
  和 interrupt 边**（等第 3 步真的要接 core 工具调用时再补）。`build_graph`
  接一个 checkpointer，`open_checkpointer()` 打开 `~/.goldilocks/goldilocks.db`
  （跟 `AsyncSqliteSaver` 的 `checkpoints`/`writes` 表、后面的六张表共存）。
- `src/goldilocks_agent/server.py`：真图 + 真 checkpointer 的 FastAPI 桥接，
  `POST /api/chat` 请求体只发 `{thread_id, message, model_id, title,
  project_id, ...}`——**只发新消息，不发全部历史**，历史完全交给 checkpointer
  按 `thread_id` 接住（十六已定"状态的真相只能有一处"）。流式经
  `graph.astream(..., stream_mode="custom")`；报错统一用 `openai.OpenAIError`
  接住转成可读 SSE 消息，不让请求直接崩掉。还有
  `GET/POST /api/projects`、`DELETE /api/projects/{id}`、
  `GET/PATCH/DELETE /api/conversations/{id}`、`GET /api/chat/{thread_id}`
  （从 checkpointer 读回历史消息）、`GET/POST /api/credentials`。
- `src/goldilocks_agent/store.py`：六张表里的两张（`projects`
  id·name·color·**description**·created_at，`conversations`
  id(=thread_id)·project_id·title·mode·updated_at·last_viewed_at；`mode` 列
  目前未接通，恒为 `NULL`），跟 checkpointer 共用同一个 `.db`（独立
  `aiosqlite` 连接，文件已是 WAL 模式，并存安全）。`app/` 的项目/会话列表
  已经是真实持久化（不再是 `INITIAL_PROJECTS` 那种纯前端 mock），删除会话
  会连带 `checkpointer.adelete_thread()` 清掉消息，不留孤儿数据；删除项目
  只解绑（`project_id=NULL`），不删会话。
- `src/goldilocks_agent/config.py`：`~/.config/goldilocks/config.toml` 读写
  凭证（`0600`，环境变量覆盖），`configured_providers()` 只答"有没有存"、
  绝不回传 key——composer 的云端模型下拉和 Settings 面板的 placeholder/角标
  都据此显示"已配置"状态。
- `app/`：图片附件走真实多模态（`FileReader.readAsDataURL()` →
  `attachedImages`，8MB 上限；`content` 只在真有图片时才变成 OpenAI 风格的
  多段数组，纯文本场景不变），不再被误当结构文件用 `file.text()` 解析成乱码；
  模型选择器如实显示本地模型为 "Qwen3.8-27B"，"Goldilocks LLM" 是
  `disabled: true` 的占位条目（点击 no-op，不会被误选或静默路由到别的模型）。
- 测试：`tests/test_graph.py`（含一个多模态图片测试，手搓纯色 PNG 问颜色）、
  `tests/test_server.py`、`tests/test_store.py`——均为 `integration` 标记 +
  结构性断言（"有回复""历史接上了""颜色答对了"），不断言具体文字内容
  （AGENTS.md 测试哲学），按本地模型是否已拉取自动 skip。
- ⚠️ **`openai-gpt` 那行的 litellm 模型串还没人拿真 key 测过**，用的时候
  大概率要改；`anthropic`/`google` 两行已用真 key 验证（google 那行是
  Google API 报 404 后直接采纳错误信息里给的替代型号）。

上面这些的详细踩坑过程（server.py 第一版打补丁返工、Ollama 官方库漏查绕道
HuggingFace 又改回来、模型选择器改名来回两次、图片分类 bug 的根因排查）
不再逐条复述——这些是同一天的调试叙事，不是面向未来的计划内容，完整过程见
git 历史/commit message。

- ⚠️ **真正的 TODO**：`llm_calls.model_identity_pinned` 要记 **`false`**
  （十六之五已定含义：这份权重身份没被钉死校验过，跟云端模型同一等级，
  不要因为"是本地模型"就默认记 `true`）。今天还没有六张表（第 4 步跳过了），
  这条先记在这里，等第 4 步真的把 `llm_calls` 表建出来时别忘了执行

### 第 6 步：AiiDA sink B

- 排在最后，因为它依赖前面所有环节都已经用 sink A（bundle 下载）验证过
  "core 出的参数是对的"——sink B 只做格式翻译，翻译前必须已经有一个可信的
  参数来源（十五之一）
- 先接 `aiida-quantumespresso` 的 `PwBaseWorkChain`（对应第一阶段唯一支持
  的 code/task：QE scf）

#### 参考 `aiidateam/aiida-agents`（2026-09-15 核实，不直接依赖）

用户提醒去看这个仓库。核实下来：**官方组织下的真实项目，但是 GSoC 2026
学生项目，pre-alpha，未发布**（`pip install git+...`，版本号 `0.0.0`）。
不建议直接依赖——理由三条：

| 维度 | aiida-agents | 我们 |
|---|---|---|
| 框架 | Pydantic AI，**明确在 ADR 里拒绝了 LangGraph**（嫌重）| LangGraph（十七已定）|
| 模型接入 | 自己的 provider 抽象 | litellm（十七之六①）|
| 提交（submit）在不在 MCP 上 | **不在**——`submit_workflow` 等排除在 MCP 之外，只走自带的 HITL CLI | 待定，见下 |

**但两处值得直接借鉴：**

1. **它的 MCP server 只暴露只读工具**（`status`/`query`/`list`/`describe`/
   `diagnose`/`wait_for_process`），**提交类工具故意不放 MCP 上**——这条
   推理直接对得上我们还没细定的一个点：**AiiDA 的提交动作要不要经过 MCP，
   还是应该跟十四之三"只有 core 受'全部走 MCP'约束"这条一样，走 agent
   自己更直接受控的工具节点**。去读它的 ADR（尤其是提交排除在 MCP 之外
   的那条理由）比自己从头想更快。
2. **它的轮询是"一次工具调用里阻塞等待"（`wait_for_process`，2 秒间隔，
   最多 30 分钟），没有主动通知机制**——这反过来确认了十五之四"后台轮询 +
   完成时主动推一条消息"这个设计是**我们比它更完整的地方，不是要去补的
   缺口**，动工时不用怀疑这个方向。

⚠️ **不确定的地方留着不下结论**：MCP 只读工具这条，我们要不要也照抄
"AiiDA 的查询类工具走 MCP、提交类工具不走"这个二分——现在还没到需要
拍板的时候（第 6 步本来就排在最后），到时候再定。

### 与主线并行、不互相阻塞的两块（随时可以插进去做）

这两块不依赖 core 是否接通，纯前端 + 本地存储就能做完，**排在这里是因为
容易被"建设顺序"这张单向清单漏掉，不是因为它们排最后**：

- ✅ **Settings 里补了三个新分区**（十一 · 十五之一，2026-09-15 用户确认都放
  Settings 里，已实现为 UI 壳子）：
  - **Model**：per-provider（OpenAI / Claude / Gemini）API key 输入框，
    密码型输入，本地 state；**模型选择本身**不在这里——那是 composer 里
    已经有的 `model-selector` 下拉菜单的事，Settings 只管凭证，两者不重复
  - **Compute**：AiiDA / HPC 连接状态 + Connect/Disconnect 占位按钮
  - 真正落盘到 `~/.config/goldilocks/config.toml` 等 Python 后端起来后再接，
    现在只是 `useState`，跟 `experienceLevel`/主题一个待遇
  - 顺带把 composer 里 `model-selector` 那份半成品数据修完整了：本地
    Qwen3.8-27B 替掉了旧的占位 "Qwen 3.5 8B"，"Cloud" 分组（此前只定义了
    颜色没有条目）现在真的列出 OpenAI/Claude/Gemini 三项
- **工作目录选择器**（十节 · 十.2，2026-09-15 补的已知缺口）：界面上要有
  一个入口选/显示当前工作目录，**且要拒绝设成 `~`/`/`/`/Users` 这类根目录**
  （十.2.2）。这是安全边界，不是可选项——**在 core 真的开始读写文件之前
  必须有**，建议跟第 1-2 步一起做，因为"core 读结构文件"本来就要有个
  目录概念才有意义

### 第 7 步（MLIP Playground，排在 DFT Workspace 之后）：接 janus-api

十二之一原话："MLIP Playground——要等 janus 接好"。这一步之前的草稿漏写了，
2026-09-15 补：

- **不是 MCP**——2026-09-15 核实过：`github.com/stfc/janus-core`（上游库）
  只是一个 Python 包 + Typer CLI，没有 HTTP、没有 MCP；
  `context7.com/stfc/janus-core` 只是一个第三方文档索引服务的注册页
  （很多不相关的库都挂在上面），不代表 janus-core 有 MCP server。
  ⚠️ **这条待核实的说法已经证伪，不要在后续讨论里重新捡起来**。
- **走 FastAPI，走十八已定的路**：本地已经有一份现成的包装层
  `old-goldilcoks-webapp/janus-api`（`janus_api/main.py` 里
  `from fastapi import FastAPI`），暴露 6 组 REST 端点：
  `singlepoint` · `geomopt` · `eos`（equation of state）· `neb`
  （nudged elastic band）· `phonons` · `upload`（结构文件上传/查询）。
  agent 拉子进程 + HTTP 调用（十八已定的形状，不写成 MCP，因为它不是
  core，"全部走 MCP"那条约束的对象只是 core）
- **进度依赖**：这一步排在 AiiDA sink B 之后，不是因为技术上更难，是因为
  十二之一的分期判据——DFT Workspace 是唯一能检验 v2 core 契约的 mode，
  MLIP Playground 检验不到任何 core 的东西，优先级天然更低
- 结构来历要记 `transform`（十五已定的 `structures` 表机制）：MLIP 弛豫后
  的结构如果被拿去做 DFT，`parent_id` + `transform = {tool: janus, model:
  ..., fmax: ...}` 这条链现在的机制已经够用，不用另外设计

✅ **第 7 步已建成（2026-09-15，跳过第 6 步 AiiDA sink B 直接做，用户要求）**：

- `src/goldilocks_agent/tools/mlip_playground/`（`service.py` 子进程生命周期 ·
  `client.py` httpx 调 janus-api · `models.py` 5 个结果模型 · `tool.py` LLM
  工具 schema/dispatch），结构完全照抄 `structure_search/` 的分层。`arch`
  固定走 `mace_mp`（用户要求这一轮只做 MACE，其它势以后再说），5 种计算
  （singlepoint · geomopt · eos · neb · phonons）跟 `janus-api` 现成的 6 个
  REST 端点一一对应，字段名逐个核对过 `janus_api/utils/*_helper.py` 的真实
  返回值（不是猜的）。
- `service.py`：`JANUS_API_PATH` 未配置就返回清晰的 503（跟 `MP_API_KEY`
  未配置一个待遇），配置了才用 `uv run --project <path> uvicorn
  janus_api.main:app` 拉子进程——**用它自己的 env，不把 torch/mace/phonopy
  拉进 goldilocks-agent 自己的依赖图**，十八已定的"agent 拉子进程 + HTTP"
  这条路照做。进程常驻（模块级单例，跟 `jarvis_cache` 一个模式），避免每次
  调用都重新付 MACE 模型加载的秒级代价。
- **`app/src/App.jsx` 的 MLIP Playground 面板本来就是现成的**（模型/结构
  选择器、5 种计算的参数表单、"Run calculation"按钮、结果卡片、结构导回、
  ✦ 讨论按钮）——之前一直在打 `/api/mlip/*` 的真实 404，这次把后端接上，
  前端幾乎没改面板本身。顺手修了两处顺带发现的问题：① `MLIP_MODELS`
  之前列着 CHGNet/ALIGNN 两个选了也不会工作的假选项（ALIGNN 甚至不是
  janus-core 真的 `arch` 值）——跟模型选择器之前犯过的"占位品牌"问题
  同一类，缩到只剩 MACE-MP-0；② 面板的 18 个 `useState`（含
  `sessionFiles`/`attachedFiles`，MLIP 的导入-结构循环依赖它们）会跨 chat
  泄漏，跟 Find in Databases 的 formula 框是同一类 bug，一并挪进
  `session.modeState`。另外**去掉了跑完计算自动把 summary 贴进 chat 的
  代码**（原来就有但从没真的跑到过，因为后端不存在）——统一成跟
  Find in Databases 一样：直接操作面板不进 chat，除非点 ✦。
- **十七之三的 interrupt 机制第一次真正用上**：`graph.py`
  的 `call_tool` 对 5 个 `run_mlip_*` 工具名统一走
  `langgraph.types.interrupt()`，**每一次调用都要问**（用户 2026-09-15
  明确选择：不是"这个 chat 问一次以后免问"，是每次都问），graph 不需要
  额外的 state schema（`MessagesState` 本身就够）。`server.py` 新增
  `event: confirmation_needed` SSE 帧 + `ChatRequest.resume` 字段
  （`Command(resume=...)` 续跑，不带新 message）。前端在聊天记录里加了一张
  确认卡片（十七之十已定的形状：卡片在记录里，不是弹窗），Approve/Decline
  两个按钮。**真实验证**：`tests/test_graph.py` 用真实 Claude 调用让模型
  主动调 `run_mlip_singlepoint`，断言 graph 真的暂停（`state.next` 非空）、
  拒绝分支正确续跑；另外直接对本机真实跑起来的 server 用 curl 走了一遍
  完整 SSE 流程（含暂停、拒绝续跑），行为跟 pytest 断言的一致。
- **没有验证的部分，如实记录**：本机没有配置 `JANUS_API_PATH`（配置它需要
  在 `old-goldilcoks-webapp/janus-api` 目录跑 `uv sync`，装 `mace` extra，
  下载 MACE-MP 权重），所以"批准后真的跑一次 MACE 计算拿到真实能量"这条
  没有在这台机器上跑通——`tests/tools/test_mlip_playground.py`/
  `tests/test_graph.py` 里对应的 `integration` 测试写好了，`JANUS_API_PATH`
  一配置好就能跑,不需要再改代码。前端确认卡片本身也没有在浏览器里手动点过
  （沙盒里的 Bash 工具连不到用户自己起的 Vite dev server 那个网络命名空间，
  只验证了 `npm run lint`/`npm run build` 干净）。

> ⚠️ **2026-09-15 用户当天晚些时候补充：`janus-api` 先搬进
> `1-goldilocks-agent` 这个仓库本身，以后再挪进 `janus-core`。** 上面写的
> "本地已经有一份现成的包装层 `old-goldilcoks-webapp/janus-api`"那条已经
> 不是当前状态了——那份代码已经原样搬到 `<repo-root>/janus-api/`（自己的
> `pyproject.toml`/`uv.lock`，独立于 `goldilocks-agent` 自己的依赖环境，
> `service.py` 照样用 `uv run --project ./janus-api ...`拉子进程，没变），
> `old-goldilcoks-webapp` 那份只是历史来源，不再是权威副本。顺手改了两处：
> ① 把 `pyproject.toml` 里 `[tool.uv.sources] janus-core = {path =
> "../janus-core", ...}` 那条指向个人机器上兄弟目录的相对路径删了（挪到这
> 仓库里之后那条路径根本不存在），改成走 PyPI 上真实发布的 `janus-core`
> 包；② 依赖串从 `"janus-core"` 改成 `"janus-core[mace]"`（默认走
> MACE，之前没显式装 `mace` extra，装了普通 `janus-core` 也跑不出
> MACE-MP 计算）。`JANUS_API_PATH` 这个环境变量**继续要求显式设置，没有
> 自动指向 `<repo-root>/janus-api/`**——试过加自动兜底，结果`pytest`一
> 跑就真的去拉子进程（因为仓库里的 `janus-api/` 现在永远存在），"默认不起，
> 配置了才起"这条就名存实亡了，所以撤回了那个"方便"，还是老老实实要求
> `export JANUS_API_PATH=<repo-root>/janus-api`（`README.md` 补了这段
> setup 说明）。

> ⚠️⚠️ **同一次补充里用户还问了"mcp 在哪儿"** ——这是在检查最早那句
> "mcp 用 `https://context7.com/stfc/janus-core`"是不是被漏实现了。核实
> 结论：**没有漏，这句话原本就不是要求 goldilocks-agent 的运行时架构里接
> 一个 MCP**——`janus-core`/`janus-api` 走子进程 + HTTP 这条路是十八已经
> 定的，2026-09-15 早些时候已经核实过 `janus-core` 上游根本没有 HTTP/MCP
> server（见上面"不是 MCP"那条，已证伪，不要重新捡起来）。`context7`是
> 一个面向**写代码的助手**（也就是我自己）拉取第三方库最新文档的服务，
> 不是给 goldilocks-agent 的最终用户（材料科学研究者跟它聊天）用的运行时
> 工具——这次开发过程里因为当时会话里没有接好 context7 的 MCP
> server，改用普通网页检索（WebFetch/WebSearch 走一个 research agent）
> 拿到了同等的最新信息，效果上等价，只是没有走 context7 这一条特定路径。
> 如果以后真的要在这台机器/这个 Claude Code 会话里配 context7 的 MCP
> server 给我用，是可以配的，但那是"我怎么查文档"的问题，不是
> goldilocks-agent 本身要新增一层 MCP 调用。

> ✅ **2026-09-15 当天再晚一点：`janus-api` 整个拆掉，改成直接拉
> `janus-core` 自己的 CLI**——用户看到 app 界面崩了之后顺带提的一句
> "janus-core 也没必要用 api 啊，可以全部用 CLI"，跟当天早些时候给
> DFT Workspace 做的 HTTP→CLI 决定是同一个道理。动手前先验证可行性
> （没有直接假设 `janus` CLI 也有 `goldilocks` CLI 那种 `--json` 输出）：
> - **核实结论：`janus` 没有 `--json`/结构化 stdout**——每个子命令
>   （`singlepoint`/`geomopt`/`eos`/`neb`/`phonons`）都是"算完把结果写
>   文件"，不是"打印 JSON"。真跑了一遍 NaCl 的 5 种计算确认输出文件
>   形状：`*-results.extxyz`/`*-opt.extxyz`（能量/力/应力，ASE 可以直接
>   读，因为文件就是 ASE 自己写的）、`*-eos-{fit,raw}.dat`／
>   `*-neb-results.dat`（纯文本表格）、`*-thermal.yml`（phonopy 原生单位，
>   kJ/mol 不是 eV——`janus-api` 的 helper 之前也没转换过，不是这次才变的）、
>   `--plot-to-file`/`--plot-band`/`--bands` 会真的写 `.svg`（`eos-plot.svg`
>   / `neb-plot.svg` / `bands.svg`），跟 `janus-api` 自己用 matplotlib 现
>   画的 SVG 是同一件事，只是现在 janus-core 自己画。唯一没有对应物的是
>   `PhononsResult.band_yaml`（`janus-api` 用
>   `phonopy_obj.write_yaml_band_structure()` 单独导出的带本征矢量的
>   YAML）——CLI 的 `--bands` 只会写二进制 `*-auto_bands.hdf5`，没有等价
>   YAML，这个字段现在永远是 `None`，老实记下而不是假装还有。
> - **新建 `mlip-cli/`**（仓库内、非 package 的 uv 项目，`[tool.uv]
>   package = false`，依赖只有 `janus-core[mace]`）替代原来
>   vendor 进来的 `<repo-root>/janus-api/`（已删除）。`JANUS_API_PATH`
>   废弃，改成 `GOLDILOCKS_AGENT_MLIP_ENABLED`（布尔开关，不再是路径——
>   `mlip-cli/` 是仓库自带的固定相对路径，没有外部 checkout 可指，但"默认
>   关闭，显式打开才跑"这条不变，理由同以前：第一次跑要在 `mlip-cli/` 里
>   `uv sync` 下载 torch/mace，几个 G，不该在每次 `pytest`/每个新 clone
>   上悄悄触发）。
> - `tools/mlip_playground/client.py` 整个重写：不再是 `httpx` 打
>   `janus-api` 的 HTTP 端点，改成 `asyncio.create_subprocess_exec`
>   拉 `uv run --project mlip-cli janus ...`，再用 ASE（`ase.io.read`，
>   新增的轻量依赖，不含 torch）／自写的 `.dat` 列解析／`pyyaml`
>   （新增依赖）读生成的文件。`service.py`（原来管长驻 uvicorn 子进程的
>   模块）整个删除——**代价老实记一笔**：以前长驻子进程能让 MACE 模型在
>   同一个 server 生命周期内保持"热"（只有第一次调用付模型加载的
>   代价），现在每次调用都是全新子进程，每次都要重新付 MACE 模型加载的
>   代价（实测个位数秒到几十秒）。这是拿掉 HTTP 服务、换成纯 CLI 子进程
>   模式必然的取舍，不是遗漏。
> - `Result` 模型字段名（`energy`/`forces`/`optimised_structure`/
>   `bulk_modulus`/`barrier`/... 等）**全部保持不变**——这是跟前端
>   `app/src/App.jsx` 面板和 `/api/mlip/*` 契约的稳定接口，不因为底层实现
>   换了就跟着重命名。
> - 更新了 `tests/tools/test_mlip_playground.py`／`tests/test_graph.py`
>   （`requires_janus_api` → `requires_mlip_enabled`，环境变量名同步换）、
>   `tests/test_server.py`、`README.md`、`docs/goldilocks-agent-design.md`
>   （十五节开头 + 已定清单各补一条更正，不重写原始决策记录）。
> - 验证：`uv run poe lint`/`uv run poe test`（不含 `integration`）全过；
>   `mlip-cli/` 里手动跑过真实 `janus singlepoint`/`geomopt`/`eos`/
>   `phonons`（NaCl，MACE-MP）确认文件形状后才写的解析代码，不是照着
>   `--help` 猜的。`neb` 的输出形状是读 `janus-core` 源码
>   （`calculations/neb.py`）核实的，没有实跑（要两个结构、更慢），
>   下次真的用到 NEB 时应该补一次真实运行验证。

> ✅ **2026-09-15 又晚一点：`PhononsResult.band_yaml` 补上了**——用户看到
> "永远是 None"这条记录后追问"那怎么下载 yaml 文件放到 phonon
> visualizer 里"。核实用户给的 janus-core 官方教程链接
> （stfc.github.io/janus-core/tutorials/cli/phonons.html）：确认官方教程
> 自己也只用 SVG，没有 band.yaml 这条路，不是我漏查。但
> `janus phonons` 无论如何都会写 `*-phonopy.yml`＋
> `*-force_constants.hdf5`（跟 `--bands`/`--thermal`无关）——这俩文件足够
> 喂给 phonopy 自己的 `phonopy.load()` + `auto_band_structure(
> with_eigenvectors=True, write_yaml=True)`（`auto_band_structure`就是
> `*-auto_bands.hdf5`文件名里那个"auto"，同一条 seekpath 自动路径，只是
> 顺便也导出成人类/工具可读的 YAML）。新增
> `mlip-cli/render_band_yaml.py`（一个很小的独立脚本，不是 `janus`
> 自己的 CLI，但用的是同一个隔离环境里本来就有的 phonopy，不引入新依赖），
> `client.py`的`run_phonons()`跑完`janus phonons`后额外拉一次这个脚本作为
> 子进程，best-effort（失败不影响其余真实结果）。实测验证：真跑出的
> `band.yaml`里`eigenvector`字段确实存在（4680 处），前端"Phonon
> visualizer ↗"按钮（`app/src/App.jsx`，早就写好但因为 `band_yaml`
> 一直是 `None` 从没真正生效过）现在能用了。顺手发现并修了一个相关的小
> 遗留问题：这个按钮原本链接到外部的
> `henriquemiranda.github.io/phononwebsite`，但仓库自己
> `app/public/phonon/` 下已经 vendor 了同一个工具的本地副本（`phonon.html`
> /`main.min.js`/`css/`，此前没人接上），改成打开本地 `/phonon/phonon.html`
> ——同源、不依赖外部网站，且这个 vendored 文件之前一直被 eslint 误当成
> 源码去 lint（`app/eslint.config.js` 补了 `public` 到 `globalIgnores`）。

> ✅ ★★ **2026-09-15 又更晚：DFT Workspace 跟着 goldilocks-core#62 的关闭
> 整个再设计一遍**——用户提醒"goldilocks-core 的 cli 已经更新了"：核实
> 发现 `goldilocks capabilities --json`（issue #62 提的那个命令）已经在
> `4-goldilocks-core`（分支 `62-cli-capabilities-command`）里落地了，
> 一次调用给出真实的 `codes`/`tasks`/`pseudopotential_tables`（71 个，含
> `elements`/`functional`/`accuracy`/`citation`/`licence`）/`hpc_profiles`/
> `facts`（is_metal/is_magnetic/needs_soc/needs_correlation）/`settings`
> （49 个，不变）/`warnings`目录/`sources`。同时发现 `goldilocks run
> -o <path>.zip` 直接支持打包成 zip（不只是目录），`unzip -l`验证过真的
> 包含 `scf.in`/`submit.sh`/`pseudo/*.upf`。
>
> 后端（`tools/dft_workspace/`）：`models.py`新增
> `CapabilitiesResult`/`CodeInfo`/`TaskInfo`/`PseudoTableInfo`/
> `HpcProfileInfo`/`FactInfo`/`WarningCatalogEntry`；`client.py`新增
> `capabilities()`（`goldilocks capabilities --json`）和`run_bundle()`
> （`goldilocks run -o <path>.zip --json`，返回真实 zip 字节）；`run()`
> 不再排除`pseudo/*`（UPF 是纯文本，不是二进制，Inputs 标签页现在要展示
> 它）；删掉了`list_settings()`（`/api/dft/settings`），换成缓存在进程内的
> `GET /api/dft/capabilities`；新增`POST /api/dft/bundle`（真实 zip 字节，
> `Content-Disposition: attachment`）。
>
> 前端（`app/src/App.jsx`）按用户原话重新设计：
> - **Setup**：Code/Task/HPC 三个选择器现在读真实`/api/dft/capabilities`
>   数据（HPC 从"自由文本，因为#62 没关"变成真下拉框；Code/Task 也从硬编码
>   常量换成真数据，硬编码常量降级为加载中/失败时的兜底，不再是唯一来源）；
>   **Generate 按钮挪到 Setup**（原来只在 Inputs 标签页），一次点击并行调
>   `explain`+`run`两个接口，同时填满 Inputs 和 Explain 两个标签页，不用
>   分别点两次；**Settings overrides 挪到 Generate 下面**，从"49 个都摊平
>   显示 checkbox"改成"分组下拉选择要加哪个 override"（一个`<select>`，
>   按`group`分`<optgroup>`，选中就在下面加一行可编辑的 override，带删除
>   按钮）——`pseudo_table_id`这个 override 现在是真下拉框（列出 71 个
>   pseudopotential table 的 provider/functional/accuracy），不再是要手
>   打 id 的文本框。
> - **Inputs**：新增"⬇ Download bundle (.zip)"按钮（调新的
>   `/api/dft/bundle`，触发浏览器真实下载）；文件列表现在包含 pseudo（用
>   `<details>`折叠，默认收起，因为 UPF 文件比较长），submission
>   script/input 默认展开。
> - **Checks 改名 Explain**：新增"Analysis & advisors"区块，把
>   `explain`的`records{}`（每条都有`status`/`source`/`value`/`reason`）
>   渲染出来——这才是用户说的"主要展示 goldilocks-core 的 analysis 和
>   advisors"；原来的 warnings 列表挪到下面，不再是这个标签页唯一的内容。
>
> 验证：`uv run poe lint`/`uv run poe test`（不含 integration）全过，
> `npm run build`/`npm run lint`全过；`GOLDILOCKS_CORE_PATH`指向真实
> checkout 后 4 个 dft_workspace 集成测试（explain/run/capabilities/
> run_bundle）全过；重启 backend 后用真实 curl 分别测过
> `/api/dft/capabilities`（真的返回 15 个 pseudo table、1 个 hpc
> profile）、`/api/dft/bundle`（真的下载出 66756 字节的 zip，`unzip -l`
> 确认 6 个文件都在）、`/api/dft/run`（`files`现在真的包含
> `pseudo/Si.upf`）。

> ✅ **2026-09-16：DFT Workspace 补上 `tool.py`，LLM 工具调用接通**——用户
> 发现聊天里 LLM 从来不会调 DFT Workspace 的工具，一问才发现确实之前只
> 接了面板（见上面 2026-09-15 那条"这一轮明确只接面板"的记录），跟
> Find in Databases/MLIP Playground 当初约定的"先面板后 tool node"节奏一样，
> 只是这次轮到 DFT Workspace 补第二阶段了。新增
> `tools/dft_workspace/tool.py`：`dft_explain`（对应面板的 Explain 逻辑，
> 只做分析不生成文件）、`dft_generate`（对应面板的 Generate，真的生成
> QE input/pseudo/提交脚本）——**都不需要确认卡片**，跟 `find_in_databases`
> 一个待遇（本地文件生成、非破坏性、非花算力计算，`server.py`
> 里 `/api/dft/explain`/`/api/dft/run` 早就是同一个"不需要确认"的
> 判断，这次只是把同一个判断也套到 LLM 工具调用路径上）。`models.py`
> 的 `RunResult` 新增 `model_dump_for_llm()`——`pseudo/*.upf`
> 对面板是必须的（真实几百 KB 文本，Inputs 标签页要展示/打包下载），
> 但对 LLM 那份会被 `json.dumps()` 塞进 `checkpointer` 历史、永久占用
> 后续每一轮的上下文，所以复用 MLIP Playground 那套"面板拿全量、LLM
> 拿裁剪版"的既有机制（`call_tool` 里那个通用的
> `model_dump_for_llm()`优先钩子，graph.py 完全不用改）。前端
> `app/src/App.jsx` 的 `TOOL_CALL_TO_UI_TOOL` 加了
> `dft_explain`/`dft_generate` → `"dft-setup"`的映射，`tool_result`
> 事件处理也加了对应分支，把 LLM 触发的结果同步进面板的
> `explainResult`/`runResult`（这条走的是"面板拿全量"那份，pseudo
> 文件也在）——跟 find_in_databases/MLIP 已经验证过的"LLM 触发 ⇄
> 面板同步"是同一条路径,不是另起一套。
>
> 验证：`uv run poe lint`/`uv run poe test`（不含 integration）全过；
> 配置真实 `GOLDILOCKS_CORE_PATH` + `ANTHROPIC_API_KEY` 后，两个新的
> graph.py 集成测试（`dft_explain`/`dft_generate` 各一个，真实 Claude
> 调用）全过，其中 `dft_generate` 那个额外断言了 LLM 收到的
> `tool` 消息里确实没有 `pseudo/`开头的文件，证明裁剪真的生效而不只是
> 单元测试层面成立；又单独用真实 HTTP `/api/chat` 调了一次
> `dft_generate`，确认 `tool_result` SSE 事件里 `files` 字段是完整的
> （含 `pseudo/Si.upf`），跟面板要的形状一致。前端 build/lint 全过，
> 但没有真实浏览器环境验证面板同步的视觉效果，需要用户自己确认。

> 🐛 **2026-09-16：确认卡片未处理完就发新消息，永久冲垮某条线程**——用户
> 报告"跑了多轮对话之后，突然报错了"：LLM 让 MACE 跑两个计算（single-point
> + geometry optimization，一条 assistant 消息里两个 `tool_calls`），用户
> 只点了第一张确认卡的"Approved"，接着没等第二张卡（如果出现了）直接打字
> 发了"run mlip for phonons"——从那以后**这条线程上任何新消息都必现同一个
> `litellm.BadRequestError`**（Anthropic/OpenAI 都报"tool_use ids were
> found without tool_result blocks immediately after"，且报错里点名的两个
> `tool_use` id 三次一模一样）。
>
> 根因（读了 `langgraph` 1.2.11 的 `pregel/_loop.py::_first` 源码确认，不
> 是猜测）：`call_tool` 里 `interrupt()` 暂停时，**整个函数体还没
> `return`**，所以到目前为止一次 tool 结果都没提交进 checkpoint（哪怕第一
> 个确认已经点了"Approved"、第一个计算已经真的跑了）——真正提交只发生在
> 整个 for 循环走完之后。而只要发一条**普通新消息**（不是
> `Command(resume=...)`），`_first()`判断 `is_resuming` 时要求
> `input is None or isinstance(input, Command)`，普通 dict 输入两条都不
> 满足，于是走`"discard any unfinished tasks from previous checkpoint"`
> 那条分支——暂停中的 tool 任务被直接丢弃，但**之前 `call_llm` 已经
> `return` 并写入 checkpoint 的那条 `AIMessage(tool_calls=[...])` 不会被
> 一并清掉**。于是这条被丢弃了 tool 结果的 assistant 消息永久留在
> `state["messages"]`里，往后每一轮 `call_llm` 都把它连同后面的新消息一起
> 发给模型，Anthropic/OpenAI 每次都在同一个位置拒绝——**不是随机 bug，是
> 结构性的，且一旦发生就不可能自愈，除非改代码**。
>
> 修复：`graph.py`新增 `_repair_orphaned_tool_calls()`——在
> `call_llm`每次调用时（不是写回 state，只处理发给 litellm 的那份临时
> 列表），扫描`assistant`消息的`tool_calls`，凡是紧跟着的`tool`消息没有
> 覆盖到的 id，补一条`{"error": "...interrupted before it completed..."}`
> 的合成 tool 结果。选择"每次临时修补"而不是"一次性写回 checkpoint"：
> 这个函数足够便宜可以每次重跑，而且**对已经损坏的历史线程也立刻生效**，
> 不需要写 DB 迁移脚本去清理 sqlite 里已经存在的坏数据。前端
> `app/src/App.jsx`同时加了防御：`hasPendingConfirmation`
> （当前会话里存在`role === "confirmation" && resolved === null`的卡片）
> 时，`canSend`/`send()`/输入框都被禁用并提示"Respond to the confirmation
> above before sending another message."——防止用户再次"打字抢跑"制造出
> 同样的孤儿 tool_calls，但真正让线程"不可能永久冲垮"的是后端那条修复，
> 前端只是体验层面的第一道防线。
>
> 验证：新增 3 个纯函数单元测试（`_repair_orphaned_tool_calls`覆盖"两个
> id 都丢"/"历史本来就完整不应被动"/"两个里只丢一个,只补丢的那个"三种
> 情况）；新增 1 个集成测试
> `test_new_message_while_confirmation_pending_self_heals_instead_of_crashing`
> ——**先用真实 Anthropic API 端到端复现了用户报的原始 bug**（暂时移除
> `_repair_orphaned_tool_calls`调用后跑这个测试，拿到了跟用户截图一模一样
> 的`litellm.BadRequestError: ... tool_use ids were found without
> tool_result blocks immediately after ...`），确认是真实复现而不是臆测，
> 再恢复修复代码验证测试转绿、且同一线程后续两轮对话都能正常应答。
> `uv run ruff check`/`ruff format --check`/`npm run build`全过；`npm run
> lint`剩下的 4 个`no-unused-vars`跟这次改动无关（改动前就存在，不在这次
> 范围内）。

> 🔍 **2026-09-16：举一反三——从上面这次事故里验证到的两个相关风险**（用户
> 明确要求"从这次里面举一反三"，两条都用独立的最小 LangGraph 脚本实测
> 确认，不是读代码猜的）：
>
> 1. ⚠️ **同一轮里多个需确认的 tool_calls，前面的会真的重复执行**——这个
>    比刚修的崩溃更隐蔽，因为它不报错，只是默默浪费算力/时间。用最小
>    graph 实测（一个节点里连续两个 `interrupt()`，中间各插一句"真实副
>    作用"的打印）：调用序列是 `ainvoke(初始)` → `ainvoke(resume=第1个
>    决定)` → `ainvoke(resume=第2个决定)`，第 3 次调用时**第 1 个
>    `interrupt()`和它后面的副作用代码又被完整重放了一遍**——因为
>    LangGraph 只缓存 `interrupt()`调用本身的返回值，**不缓存两次
>    `interrupt()`之间跑过的普通代码**，而节点没走完就会从头重放整个
>    函数体（这正是本文件模块开头就写明的"两个确认卡依次弹出"机制的
>    副作用，之前只记录了行为，没意识到代价）。换算到 `call_tool`：一轮
>    里有 N 个需确认的 tool_calls、且都被批准时，第 k 个的
>    `await fn(**call["args"])` 会被重复执行 `N-k+1` 次——两个calls时
>    第一个跑 2 次、第二个跑 1 次；三个calls时依次是 3/2/1 次。这次事故
>    本身的触发场景（"I'll do two things: 1. Single-point... 2. Full
>    geometry optimization"，一轮两个 MLIP confirmation-required 调用）
>    正好就是会触发这个的形状——如果那天两张卡都点了 Approved 而不是中途
>    打字岔开，single-point 那次 MACE 计算会不声不响地真的跑两遍。
>    `_repair_orphaned_tool_calls`对此**没有防护**，它只修补发给模型的
>    历史形状，不改变 `call_tool` 本身的重放行为。真正的修复需要让每个
>    `await fn(...)`调用本身可缓存/幂等（LangGraph 自己的 functional
>    API `@task`装饰器就是为这个场景设计的，或者把每个 tool call 拆成
>    独立节点让 LangGraph 按节点级别做 checkpoint），这次先只记录，已经
>    加进下面"已知缺口"表。
> 2. ✅ **修复的保护范围比触发它的场景更广，这是好事，但要记住原因**——
>    另起一个最小 graph 验证：`tool`节点里如果抛一个**普通异常**（不是
>    `interrupt()`，比如`CONFIRMATION_LABELS`查表失败、`model_dump()`
>    报错、`json.dumps()`遇到不可序列化字段——`call_tool`目前只在
>    `await fn(**call["args"])`这一步包了`try/except`，其它步骤都没有），
>    `graph.aget_state()`显示**同样的孤儿`AIMessage(tool_calls=...)`
>    被永久提交、`next`显示`tool`节点仍然待跑**——跟确认未答完全是同一种
>    损坏，只是触发方式不同。好消息是`_repair_orphaned_tool_calls`是纯
>    按消息形状扫描的，不关心"为什么"缺了 tool 结果，所以这个类别现在
>    也顺带被保护了，不需要专门再写一份修复。但这也说明`call_tool`每个
>    call 的处理体本身还是脆弱的——目前`CONFIRMATION_LABELS`里唯一一个
>    实现（`_confirmation_label`）恰好用了`.get()`兜底所以躲过了，但这
>    只是约定不是强制，以后新加一个 tool 的确认文案函数忘了兜底，同样的
>    异常路径立刻复现。
>
> 另外两条只是推理出来、还没实测验证的类似风险，也一并记下防止以后
> "重新发现"：**(a)** 现在没有任何独立于"发一条消息/resume"之外的方式去
> 查"这条线程是不是还卡在一个待确认的 interrupt 上"——如果前端本地状态
> 丢了（清缓存、换设备）而服务端确实还暂停着，用户在 UI 上根本看不出来，
> 只能靠发消息撞见这次事故本身；**(b)** 同一个`thread_id`被两个标签页/
> 两次并发请求同时`POST /api/chat`时会发生什么完全没分析过，也没有测试
> 覆盖，理论上和这次"客户端与图对轮次是否完成的判断不一致"是同一类风险。

> ✅ **2026-09-16：Docker 打包（新增 `Dockerfile`/`docker-compose.yml`/
> `.dockerignore`/`docs/getting-started.md`）落地并端到端跑通**——用户
> 决定在做 Electron 桌面应用之前先做 Docker + "本地自装 Ollama"教程两条
> 路径（见本文档第六节两条"待决"记录的前置讨论）。范围按讨论决定：
> `agent`（FastAPI 服务真实的built前端静态文件 + `/api/*`）+ `ollama` +
> 一次性`ollama-pull`三个 compose service，端口只发布到
> `127.0.0.1:8080`（保持 `server.py`"Design doc 9: local server, 127.0.0.1
> only"的既有姿态），MLIP Playground 默认开（`GOLDILOCKS_AGENT_MLIP_ENABLED=1`），
> DFT Workspace 保持现状（挂外部 goldilocks-core checkout 的可选项，本来
> 就不 vendor）。`server.py`只加了一处：文件末尾一段被
> `GOLDILOCKS_AGENT_STATIC_DIR`环境变量门控的`StaticFiles`挂载，裸机
> `uv run poe serve`/`npm run dev`不受影响（这个变量在那条路径上从不设置）。
>
> 实测踩到两个坑，都已修：①`CMD`最初是`uv run uvicorn ...`——`uv run`
> 自己的隐式 sync 检查会在**每次容器启动**时无视构建期已经做过的
> `--no-group dev`，重新装回 `ruff`/`pytest`等 dev 依赖（实测看到
> `Downloading ruff`/`Downloading virtualenv`），加`--no-sync`后确认
> 容器启动不再触发任何网络请求；②`mlip_cli_venv`具名卷第一次挂载时
> Docker 会把镜像里已有的同路径内容（含权限）复制进新卷来初始化它——
> 如果不在构建期预先`mkdir`那个目录并`chown`成非 root 用户，卷会以
> root 权限自动创建，之后`uv sync`（MLIP Playground 首次真实计算触发的
> 那个懒加载 install）会因为容器用非 root 用户跑而写不进去；已在
> Dockerfile 里预先创建空目录解决。
>
> 端到端验证（真实 `docker build`/`docker compose up`，不是只读代码）：
> 镜像构建成功；单独 `docker run` 确认 `/`返回真实构建出的前端
> `index.html`、`/api/preferences`返回真实 JSON（证明 static mount 没有
> shadow 掉 API 路由）、端口确实只绑在 127.0.0.1；`docker compose up`
> 三个 service 都启动，`ollama-pull`真实等到 `ollama` 就绪后跑
> `ollama pull qwen3.8`并 exit 0；`uv run poe check`（42 passed, 9
> skipped，跳过的都是本来就 opt-in 的 DFT Workspace/MLIP 测试）、
> `npm run lint`（4 个 pre-existing `no-unused-vars`，跟这次改动无关）、
> `npm run build`全过。
>
> ⚠️ **额外真实发现，顺手核实了 1.1 节早先"值得记一笔，免得忘了写系统
> 需求"那条预判**：真的对 compose 起的 `agent` 发了一条 `/api/chat`
> 消息，`ollama`真实回的是`litellm.InternalServerError: ... "llama-server
> process has terminated: signal: killed"`——不是 wiring 的 bug（网络
> 路由、`OLLAMA_API_BASE`、SSE 错误透传全部正常工作），是**真的被
> OOM-kill 了**：`qwen3.8`实际是 27B 参数模型，`ollama`日志显示光是
> `CPU_REPACK model buffer`就要 15.3GB，而这台机器 Docker Desktop 当时的
> 内存分配上限只有 7.75GiB。已把这条写进 `README.md`和
> `docs/getting-started.md`（Docker 路径需要把 Docker Desktop 内存
> 分配调到 ~20GB+；裸机路径需要系统整体有对应的空闲内存），1.1 节当初
> "Q4 量化约 15-18GB"的预判现在有了真实复现验证，不再只是理论推算。

> ✅ **2026-09-16 晚：`agent` 镜像发布到 GHCR，`docker compose up` 从"本地构建"
> 变成"直接拉预构建镜像"**——用户明确"现阶段专心 deliver docker"、决定不做
> desktop app 之后提的需求："整理出一个能直接 `docker pull` 或者给同事一条
> 命令就能跑起来的正式版本"。新增 `.github/workflows/docker-publish.yml`：
> `test` job（`uv run poe lint`/`poe test` + `npm run lint`/`build`，跟
> `AGENTS.md` 的本地 pre-PR 门禁同一套检查）先过，`docker` job 才用
> `docker/build-push-action` 建 `linux/amd64,linux/arm64` 双架构镜像推到
> `ghcr.io/junwen94/goldilocks-agent`——push 到 `main` 打 `latest`，
> push `v*.*.*` 标签打对应 semver 标签，PR 只跑 `test` 不推镜像。
> `docker-compose.yml` 的 `agent` service 加了 `image:`
> （指向上面那个 GHCR 地址）+ `pull_policy: always`，`build: .` 保留作本地
> 开发的手动回退（`docker compose up --build` 强制本地重建）。
>
> **可见性决定**：这个仓库 GitHub 上目前是 private，问过用户后确认——
> "这个仓库以后会公开的"，所以镜像发布定成**公开**（不是照搬仓库当前的
> private 状态）。⚠️ **一个手动步骤还没做**：用 `GITHUB_TOKEN` 从 CI 推的
> GHCR package 首次创建时默认是 private，需要人工去 GitHub 网页的
> package 设置里手动切成 Public 一次（`gh` 当前登录的 token 没有
> `write:packages` scope，API 也做不了，且"设为公开"这类动作本来就不该
> 由自动化脚本代劳）——**这一步是发布正式生效前的最后一个手动 gate**，
> 第一次真实 push 触发 workflow 之后需要单独去做。
>
> **验证现状（如实记录，还没端到端跑完）**：`docker compose config` 本地
> 跑通确认新 `docker-compose.yml` 语法合法；workflow 的 YAML 语法本地过了
> `yaml.safe_load`；**但 workflow 本身还没有被真实触发过一次**（要等这次
> 改动被推到 GitHub 才会跑），所以"镜像真的能被 `docker pull`/
> `docker compose up` 拉下来跑起来"这条现在还是**推测，不是已验证的事实**，
> 需要推送之后看一次真实的 Actions 运行结果、再手动确认包可见性，才能算
> 这条真的做完。

---

## 五、已知但还没排进上面步骤里的缺口

这些是设计讨论里已经记下、但**还没决定具体哪一步做**的东西，列在这里防止
掉出视野——不是"忘了"，是"故意先不排期"：

| 缺口 | 出处 | 为什么先不排 |
|---|---|---|
| DFT Workspace 加 Jobs / Results 标签页 | 十五之四 | 依赖第 6 步 AiiDA 接通之后才有内容可显示，提前搭 UI 骨架会是空壳 |
| **Post Analysis** tool 的实际功能（解析输出文件、画图、解读）| 十二已定新增，2026-09-15，用户点名"将来必须要实现" | 依赖第 1-2 步 core 接通（解析要用到 core 的解析机制/单位处理）之后再做，现在只有一个占位面板 |
| **AiiDA** tool 的实际功能（浏览 profile、进程列表、状态、诊断）| 十二已定新增，2026-09-15，用户点名"将来必须要实现" | 依赖第 6 步 AiiDA 真的接通（有 profile 可浏览）之后才有意义做；`aiidateam/aiida-agents` 那几个只读 MCP 工具（`status`/`query`/`list`/`describe`/`diagnose`）到时候是现成的参考起点 |
| 新手怎么连上 AiiDA/HPC 账号（引导流程）| 十五之三 | 依赖上面"Compute" Settings 分区的壳子先存在，且这是一整套引导 UX，需要单独设计不是单独实现 |
| ⚠️ **Settings 的 "Compute" 分区概念不对**（2026-09-15 用户指出）——AiiDA 不是"连接/断开"一个远程服务那种关系，**它要的是本地配置文件**（类似 `~/.aiida/` 那套 profile/数据库/broker 配置）。现在的 Connect/Disconnect 按钮是按"云端账号绑定"那套心智模型做的，跟 AiiDA 实际的配置方式不匹配 | 十五之一 · 十五之三 | 用户明确说"先留着吧"——现在的壳子占位即可，等真的接 AiiDA（第 6 步）时再回来重新设计这块 UI，不要现在猜 |
| chat 与右侧面板"共享一份草稿"的交互模型，产品里没有在场教学 | 设计文档 §18 待决，2026-09-15 UI/UX 审查新记 | 用户明确说"这个很复杂，先记录下来，以后解决"——需要专门设计教学时机，不是这次审查顺手做的范围 |
| ⚠️ **长对话超出模型上下文窗口后没有任何处理**——`call_llm` 每一轮都把 `state["messages"]` 里从第一轮起积累的全部消息原样转给模型，没有裁剪、没有摘要、没有滑动窗口。本地 `qwen3.8` 架构上支持 262144 tokens，但 **Ollama 实际跑起来给的窗口是 32768**（`ollama ps` 实测，代码里从没设过 `num_ctx` 去覆盖，这是 Ollama 自己的运行时默认值）；云端模型上限更大但同样没有裁剪逻辑。对话一旦聊到超过这个窗口，现在大概率是模型/litellm 直接报错，不是优雅降级 | 2026-09-15，用户问答中发现 | 用户明确说"先记录下来，以后决定怎么解决"——候选方案包括 LangGraph 自带的 `trim_messages` 按 token 数截断、或对早期消息做摘要，但選哪种、什么时候触发还没定 |
| ⚠️ **一轮里多个需确认的 tool_calls，前面的会被 LangGraph 重放重复执行**——`call_tool`没走完就从头重放整个函数体，但两次`interrupt()`之间跑过的普通代码不被缓存；N 个都批准时第 k 个会执行`N-k+1`次。已用最小 graph 实测确认（见上面 2026-09-16"举一反三"那条），对 MLIP Playground 这种真实跑 MACE 计算的 confirmation-required 工具是真实的算力/时间浪费，且完全静默、不报错 | 2026-09-16，修复确认卡片崩溃 bug 时举一反三验证到 | 需要 LangGraph 的 `@task`（functional API，按调用缓存结果）或把每个 tool call 拆成独立节点（按节点级别 checkpoint）才能根治，工作量不小，先记录等排期 |
| `call_tool` 每个 call 的处理体里，除了 `await fn(**call["args"])` 外没有其它 try/except——确认文案函数、`model_dump()`、`json.dumps()`任一处抛异常都会让整个节点崩溃且不返回，产生跟"确认未答完"完全相同的孤儿`tool_calls`永久损坏（已实测确认）。现在靠`_repair_orphaned_tool_calls`兜底不至于死循环，但没有从根上堵住 | 2026-09-16，同上举一反三验证到 | 影响面小（目前唯一一处`CONFIRMATION_LABELS`实现恰好用`.get()`兜底躲过了），先记录，等下次新增 tool 或再出事故时一起处理，不值得现在单独起一轮改动 |
| 没有独立于"发消息/resume"之外的方式查"这条线程是否还卡在待确认的 interrupt 上"；同一 `thread_id` 被多标签页/并发请求同时命中时会发生什么完全没分析/没测试 | 2026-09-16，同上举一反三，推理得出、未实测 | 都是"客户端与图对轮次是否完成的判断不一致"这同一类风险的推论，没有具体触发场景报告之前不值得花时间验证 |
| ⚠️ **模型对 app 自身状态"失明"**——用户问"你知道我本地有哪些 projects 吗"，模型完全不知道，给了一个查文件系统的通用答案。根因：`call_llm` 没有系统提示词、没有 tool 节点，模型能看到的只有用户在输入框里打的文字，`projects`/`conversations` 这两张表的数据完全没有任何路径能进到模型的上下文里 | 2026-09-15，用户问答中发现；本质是第 3 步"tool 节点"缺失的一个具体表现 | 用户在两个方案（① 给模型挂 `list_projects`/`list_conversations` 这类真工具，走 LangGraph tool 节点，跟以后接 core 工具复用同一套机制；② 每次调用前把项目列表轻量塞进 system message，不涉及 tool-calling）里选了"先不做，记录下来，以后再决定" |
| ⚠️⚠️ **三个真实 Tool 的产物完全没有持久化——"工作目录"这条已定决策（§十.2）从未落地**——核实 `dft_workspace/client.py`/`mlip_playground/client.py`：每个函数都是 `with tempfile.TemporaryDirectory() as tmp:` 模式，请求一结束这个目录就被整个删除，包括从没被读进任何 Pydantic 模型字段的二进制/中间产物（`*-force_constants.hdf5`、`*-phonopy.yml`、janus 写的原始 extxyz 里模型没解析的额外列）——这些是**真正、永久、无法补救的数据丢失**，不只是"没地方点下载"。前端零散补了几个客户端 Blob 下载按钮（EOS 重建的 CSV、NEB 的 `.extxyz` 轨迹、phonons 的 SVG+band.yaml），但覆盖不全（geomopt 弛豫出的结构只有"Import"没有下载按钮，NEB 的能量图 SVG 没有下载），而且这些按钮吃的是前端本地 `result.raw` state——重开一个旧会话不会被重新水合（只有聊天文字/图片走了 `GET /api/chat/{thread_id}` 的重新水合逻辑），数据其实还原样躺在 checkpointer 里那条 tool 消息的 JSON 里，但没有任何代码把它解析回下载按钮，等于**跨会话就彻底拿不到**。DFT Workspace 是三者里覆盖最好的（`run_bundle()` 触发真实的浏览器 zip 下载），但那是重新跑一次 `goldilocks run` 现生成的，不是"把已经生成的产物存起来" | 十.2/十.2.4（"工作目录"——安全边界+产物落点，已定但从未实现），也是十六节 `bundles.bundle_path` 未来要指向的东西 | 2026-09-16，用户指出后经代码核实确认——不是设计阶段就决定往后放的，是三个 Tool 各自"先做通面板/API"这一步时，`tempfile.TemporaryDirectory()` 是最省事的临时选择，写完之后没人回头把它和已经定好的"工作目录"概念对上。优先级应该提前：往后接 AiiDA sink B、往 `structures`/`bundles` 表填真实数据，都需要先有一个真实存在的落盘位置，建议跟第 4 步（六张表）一起做，不要等到那之后 |

---

## 六、待决（本文档范围内，不是设计文档的待决）

- [ ] core 的具体启动命令（HTTP `poe serve` 参数、MCP stdio 命令）——本文档
      写的是猜测的形状，要对着 core 当前的 `pyproject.toml`/`AGENTS.md` 核一遍
- [ ] 第 1 步"先 HTTP 后 MCP"的具体切换时间点——现在只说"Python 后端要接
      LangGraph 时"，没有更精确的触发条件
- [ ] **Electron 打包时 Ollama 怎么来**——2026-09-15 用户提醒最终形态是 Docker
      或 Electron 桌面应用，这条是在此背景下新记的。模型权重现在存在
      `~/.ollama/models`（Ollama 自己的默认目录，不在我们项目/`~/.goldilocks*`
      路径体系里）——**这个位置本身是对的**：Docker 场景下 Ollama 单独起
      容器、挂具名 volume 是标准做法；Electron 场景下只要我们是 shell out
      到系统装的 Ollama 而不是自研内嵌 llama.cpp，权重留在 Ollama 自己的
      用户目录里也是对的，天然在 app bundle 之外，应用更新不用管迁移。
      Docker 场景不受这条影响（`ollama/ollama` 官方镜像本来就是独立容器，
      不存在"用户有没有装"这个问题）

      ✅ 2026-09-16 已定（Electron 场景的打包方式，不是"bring your own
      Ollama"）：用户明确表态倾向**把 Ollama 的 server 二进制打包进 app、
      由 app 自己拉起/关掉当子进程**，不要求用户单独装一个叫"Ollama"的
      东西——目标用户明确包含 DFT novice，感知不到背后有个独立软件更好。
      这条路径对现有代码零改动：litellm 现在打的就是 Ollama 自己的 HTTP
      契约（`localhost:11434`），换的只是"谁来启动这个进程"，不涉及换成
      自研 llama.cpp。同时应该做探测：如果用户机器上已经有 Ollama 在跑，
      复用它，不抢端口、不重复占用资源。License 上没有障碍（Ollama 是
      MIT）。

      ✅ 2026-09-16 又定（多平台打包的构建方案，讨论后用户拍板）：
      **v1 只打包 CPU-only 的 Ollama 二进制，不碰 GPU 加速库**——Linux
      官方 release 的 GPU 库（CUDA/ROCm 的 `.so`）能把体积从几十 MB 拉到
      几百 MB～1GB+，v1 先不碰这个坑；想用 GPU 加速的用户走前面已定的
      "探测到系统已装 Ollama 就复用"那条路径，他们自己装的 Ollama 天然
      带 GPU 支持，不需要我们打包的这份管。三个平台对应关系：Linux 直接
      用 tarball 里的 `bin/ollama`；macOS 官方发行的是完整 `Ollama.app`
      （带菜单栏 UI），得从里面抠出裸的可执行本体（`ollama serve` 本身
      就能纯后台跑，不需要那层菜单栏）；Windows 官方是 `OllamaSetup.exe`
      安装包，同样需要抠出裸的 `ollama.exe`。用 `electron-builder` 的
      `extraResources`（按 `mac`/`win`/`linux` 分别配置）把对应平台那一份
      塞进各自安装包。

      **还没定的执行细节**：①二进制不建议提交进代码仓库（体积大、多平台
      冗余），倾向在 CI 按平台矩阵在构建时去 Ollama 官方 GitHub release
      下载对应资产、校验 checksum 再打包，但具体 CI 配置没写；②★
      **macOS 公证（notarization）的重签步骤**——`.app` 里每个可执行二进制
      公证时都会被递归检查，直接塞官方编译的 Ollama 二进制大概率公证
      不过或运行时被 Gatekeeper 拦，需要在打包流程里对这个二进制单独用
      自己的 Developer ID 重新 `codesign`，这一步还没设计进构建流程；
      ③模型权重是几个 GB，没法塞进安装包本身，首次启动"正在下载本地
      模型"的引导流程还没设计。
- [ ] **DFT Workspace 走 CLI，云端部署下会不会有重复加载的开销**——
      2026-09-16 用户提出以后要有两种部署形态：用户本地部署 + 云端服务
      （已确认：云端不是共享的中心化 core 服务，是每个云端实例照样自带
      一份 goldilocks-core，跟本地部署同一套拓扑，只是打包方式不同）。
      在这个前提下，`tools/dft_workspace/client.py`的`_run_cli()`每次调用
      都用 `uv run --project <core_path> goldilocks ...` 起一个全新子
      进程——没有任何跨调用的常驻状态，Python 解释器、goldilocks-core 的
      模块、pseudopotential 表等数据每次都从头 import/加载，`uv run`本身
      还会先做一次 lockfile/venv 同步检查。本地单用户偶发调用场景下这点
      开销无感知，不值得改；但云端部署如果调用频率上来，这个成本会被
      放大。候选方案（未验证、未实测具体开销大小）：云端实例启动时把
      `goldilocks serve http`（memory 里记录过已确认真实存在、能跑通，见
      `reference_goldilocks_core_location.md`）常驻拉起来，agent 那边改成
      打 `localhost:<port>`而不是每次都起子进程；本地部署继续用现在的
      CLI-per-call 不用动——两边还是各自独立、不共享的部署模型，只是云端
      那份把"CLI 子进程"换成"本地常驻 HTTP"。用户明确说先记录，以后再
      决定要不要做，也没决定要不要先实测现在单次 CLI 调用具体慢多少
