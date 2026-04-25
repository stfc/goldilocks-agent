# Structure Match — 设计文档

## 概述

一个侧边栏工具，获取当前加载的结构，在多个外部数据库和本地数据库中搜索匹配或相似的结构。

---

## 数据来源

| 来源 | 访问方式 | 备注 |
|---|---|---|
| Materials Project | `mp-api`（官方 Python 客户端） | 需要 API key |
| Materials Cloud | OPTIMADE — `mc3d-pbesol-v2` 端点（主要） | 已确认可用 |
| NOMAD | OPTIMADE — `https://nomad-lab.eu/prod/v1/optimade` | 已确认可用 |
| JARVIS | figshare 全量下载，本地缓存 | 压缩包约 40 MB；见缓存策略 |
| 本地数据库 | 自定义查询 | 尚未搭建，暂缓 |

每个来源使用最可靠的原生接口。

### Materials Cloud — 数据集选择

Materials Cloud 托管了多个 OPTIMADE 子数据库。对于三维体相结构匹配，查询 `mc3d-pbesol-v2`（最新版，PBEsol 泛函）。主要端点跑通后，可考虑并发查询 `mc3d-pbe-v1` 作为补充。

可用端点（供参考）：

```
https://optimade.materialscloud.org/main/mc3d-pbesol-v2/   ← 主要
https://optimade.materialscloud.org/main/mc3d-pbe-v1/
https://optimade.materialscloud.org/main/mc3d-pbesol-v1/
https://optimade.materialscloud.org/main/mc2d/
https://optimade.materialscloud.org/main/2dtopo/
https://optimade.materialscloud.org/main/pyrene-mofs/
https://optimade.materialscloud.org/main/curated-cofs/
https://optimade.materialscloud.org/main/autowannier/
```

### JARVIS — 缓存策略

JARVIS 没有 OPTIMADE 端点，也没有适合在线查询的 REST API。`dft_3d` 数据集从 Figshare 一次性下载（压缩包约 40 MB，解压 JSON 约 200 MB），缓存至 `~/.cache/goldilocks/jarvis_dft_3d.json`。goldilocks-api 启动时将缓存加载到内存，之后所有 JARVIS 查询在进程内完成，无需任何网络请求。

缓存**不**打包进 Python 包——它是用户本地数据，不是代码。

---

## 匹配策略

外部数据库不支持服务端结构匹配，因此采用以下流程：

1. **过滤** — 用低成本过滤条件（化学式、原子数、元素种类）向各数据库查询，缩小候选范围
2. **拉取** — 下载候选结构
3. **匹配** — 本地用 `pymatgen StructureMatcher` 对候选结构做精确结构匹配
4. **排序** — 按相似度分数排序结果

```
用户加载结构
    ↓
goldilocks-core：提取特征（化学式、空间群、nsites、元素）
    ↓         ↓              ↓               ↓
  mp-api   MC OPTIMADE   NOMAD OPTIMADE   JARVIS（本地缓存）
    ↓         ↓              ↓               ↓
         并行查询 — 按化学式 / nsites 过滤候选结构
    ↓
goldilocks-core：对候选结构运行 pymatgen StructureMatcher
    ↓
排序后的结果返回前端
```

---

## 触发方式

搜索有两个触发入口：

1. **Structure Match 面板中的"Search databases"按钮** — 主要入口。使用"Structures in Current Chat"中当前选中的结构文件。无结构文件时禁用。
2. **元素周期表化学式构建器中的"Search"按钮** — 无需结构文件，直接按元素集合触发搜索。

避免在每次加载结构时自动触发——那样每次上传都会同时打出 4 个以上的外部请求，在用户并不需要匹配时也会增加延迟。

---

## 前端 UI

### Structure Match 面板（右侧边栏）

用户激活 Structure Match 模式后打开。不分 tab，内容直接平铺。

**Query structure 区块：**
- 显示"Structures in Current Chat"中当前选中的结构文件，无需重新上传，直接读取聊天记录中已存在的结构。
- 若当前聊天中有多个结构文件，用 ‹ › 箭头在文件之间切换。
- 显示文件名；文件已附加但尚未发送时显示"Pending"标签。
- 无结构文件时"Search databases"按钮禁用。

**Candidate structures 区块：**
- 每张结果卡片显示：化学式（粗体）· 空间群 · 数据库名称（灰色）· 可点击的条目链接。
- 已从该面板中移除 Viewer 标签页。结构可视化由 composer 区域的 Structure Viewer 提供。

### Structures in Current Chat

由"Chat Files"改名而来。列出当前聊天中所有结构文件：
- 已附加但尚未发送的文件（标注"Pending"）
- 已发送消息中包含的文件（从消息记录中自动提取）

该列表同时被 Structure Match 面板（选择查询结构）和 Structure Viewer 共用。

### 元素周期表化学式构建器

元素周期表弹窗新增化学式累积区域，嵌入在 H 与 He 之间的 U 型空白区（18 列网格第 1 行的第 2–17 列）。

- **点击元素** → 加入化学式；再次点击同一元素则计数递增（Fe → Fe2 → Fe3）。
- **代码框** — 在表格顶部空白区显示当前化学式（如 `Fe2 O`）。
- **↵ 按钮** — 将化学式字符串插入 chat 输入框并关闭弹窗。
- **Search 按钮** — 直接激活 Structure Match 并触发按元素集合的搜索（无需结构文件）。
- **✕ 按钮** — 清空当前化学式。

### 两种搜索模式

| 模式 | 输入 | 过滤条件 | StructureMatcher | 结果数量 |
|---|---|---|---|---|
| 文件搜索 | 上传的结构文件 | 化学式 + nsites + 元素 | 是（精确匹配） | 少 |
| 化学式搜索 | 元素周期表选出的元素集合 | 仅元素（无配比） | 否 | 多 |

化学式搜索返回所有包含所选元素的数据库条目，无论元素配比如何，结果数量会多于文件搜索。UI 应明确区分两种模式的结果（具体展示方式待后端接入后设计）。

---

## 结果展示

### 右侧面板（Structure Match 工具）

显示一个简洁的匹配列表，每行包含：

| 字段 | 说明 |
|---|---|
| Formula | 匹配结构的化学式 |
| Source | 数据库名称（MP、Materials Cloud、NOMAD、JARVIS） |
| Link | 指向该条目在来源数据库中的网页链接 |

### 链接 URL 模板

| 来源 | 获取方式 | 示例 |
|---|---|---|
| Materials Project | 从 `material_id` 字段构造 | `https://next-gen.materialsproject.org/materials/mp-149` |
| NOMAD | 直接读取 `_nmd_entry_page_url` 属性，无需构造 | `https://nomad-lab.eu/prod/v1/gui/entry/id/{upload_id}/{entry_id}` |
| Materials Cloud | 从 `_mcloud_mc3d_id` 属性构造 | `https://mc3d.materialscloud.org/mc3d-23122` |
| JARVIS | 从 `jid` 字段构造 | `https://jarvis.nist.gov/?jid=JVASP-14831` |

说明：
- NOMAD URL 包含 `upload_id` 和 `entry_id` 两段，完整地址已在 `_nmd_entry_page_url` 属性中，无需拼接。
- Materials Cloud 的 OPTIMADE 响应中 `links` 字段为 null，可用的网页 URL 来自 provider 扩展属性 `_mcloud_mc3d_id`。
- JARVIS 的 `/?jid=` 返回 HTTP 200，在浏览器中打开即为对应条目页面（SPA，内容通过 JS 渲染）。

### Chat（LLM）

匹配结果同时作为上下文传给 LLM，LLM 在 chat 面板中以自然语言给出分析。右侧面板提供原始列表便于快速浏览，chat 提供解读，二者互补。

---

## 代码组织

### goldilocks-core（纯计算，无网络）

在 `structure/` 下新增：

| 文件 | 内容 |
|---|---|
| `structure/features.py` | `extract_match_features()` — 返回化学式、空间群、nsites、元素 |
| `structure/match.py` | `run_matcher()`、`MatchResult` 数据类 |

### goldilocks-api（所有网络调用）

新增路由：

```
routes/structure_match.py
    POST /api/structure-match
        — 并行查询 MP、MC OPTIMADE、NOMAD OPTIMADE、JARVIS 缓存
        — 调用 goldilocks-core 运行 StructureMatcher
        — 返回带链接的排序 MatchResult 列表
```

所有外部 API 调用（mp-api、OPTIMADE requests、JARVIS 缓存加载）放在 goldilocks-api。goldilocks-core 保持纯计算，无网络依赖。

---

## 待决事项

- 本地数据库：schema 和查询接口（待数据库搭建完成后确定）

---

## Beyond DFT 方法列表

Beyond DFT 工具涵盖超越标准 DFT 的各类方法，按类别分组：

| 类别 | 方法 |
|---|---|
| 关联修正 DFT | DFT+U、DFT+DMFT、杂化泛函（PBE0/HSE06）、SIC |
| 多体微扰理论 | GW、BSE、RPA、MP2 |
| 含时方法 | TDDFT |
| 量子蒙特卡洛 | QMC（DMC/VMC） |
| 模型与嵌入 | MFT、Wannier 函数、QM/MM |

**DFT+DMFT**：DFT 嵌入动力学平均场理论。捕捉强关联 d/f 电子体系（如过渡金属氧化物、重费米子体系）中超越静态 DFT+U 的动态关联效应。常用代码包括 TRIQS、Wien2k+DMFT、VASP+DMFT 等。

**QM/MM**：量子力学与分子力学混合方法。将 DFT 区域嵌入经典力场环境，适用于全 DFT 计算代价过高的大体系——常见场景包括表面反应、沸石催化、生物分子等。支持 CP2K、ONIOM（Gaussian）等框架。
