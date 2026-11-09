# VM 升级：88663e1 → main（goldilocks-core v0.3.0）

目标机：`goldilocks-workbench`（130.246.214.189），需要在 STFC 内网/VPN 上。
仓库：`/home/ubuntu/goldilocks-agent`，compose 目录 `deploy/stfc-cloud`。

整体思路：

- **阶段 A**：旧版本照常服务，用户无感。构建新镜像、备份旧 venv，
  然后在新镜像里把 v0.3.0 的环境和资产预热到**副本**里，
  再在 :18000 起一个临时的 v0.3.0 core 做冒烟测试。
  任何一步失败都不影响线上，可以直接停下来。
- **阶段 B**：切换。停 agent → 换资产目录 → 起新 agent。
  聊天几秒内恢复；DFT Workbench 要等 core 首次启动，约 2–5 分钟。
- **回滚**：镜像、venv、资产、compose 文件四样都恢复到切换前。

为什么不直接在原 volume 上预热：v0.3.0 碰到内容变了但版本号没变的资产
（PseudoDojo、classifier）时，会先删掉旧目录再换上新的
（core `assets/store.py` 的 `_remove_corrupt_destination` + `os.replace`），
这样会把正在服务的旧 core 用的资产抽走。venv 也是同理。

预计耗时：阶段 A 约 30–60 分钟（镜像构建约 15 分钟，PSDI 下载视网速而定），
阶段 B 约 5 分钟。

---

## 0. 现状检查（只读）

```bash
cd ~/goldilocks-agent && git status -sb && git log -1 --oneline
cd deploy/stfc-cloud && docker compose ps
docker volume ls | grep -E 'goldilocks|ollama'
df -h /var/lib/docker
```

预期结果：

- HEAD 是 `88663e1`；
- 唯一的本地修改是 `deploy/stfc-cloud/docker-compose.yml` 的 `ports` 行（`127.0.0.1:8080:8080`）；
- 能看到 `stfc-cloud_goldilocks_home`、`stfc-cloud_goldilocks_core_venv` 两个 volume。

如果有其它本地修改，或 volume 名字不一样，先停下来。

设置变量（volume 前缀以上面 `docker volume ls` 看到的为准）：

```bash
IMG=goldilocks-agent-stfc-cloud
HOME_VOL=stfc-cloud_goldilocks_home
VENV_VOL=stfc-cloud_goldilocks_core_venv
```

估算磁盘：备份 venv 需要和旧 venv 一样大的空间，资产副本约等于现有资产大小，
预热用的临时 venv 约 3 GB（容器退出后释放），新镜像约几 GB。

```bash
docker run --rm -v $VENV_VOL:/v -v $HOME_VOL:/h $IMG:latest \
  sh -c 'du -sh /v /h/.local/share/goldilocks/* /h/.cache/uv 2>/dev/null'
```

## 1. 拉代码（不影响线上）

```bash
cd ~/goldilocks-agent
cp deploy/stfc-cloud/docker-compose.yml ~/docker-compose.pre-v030.yml   # 回滚用（含 ports 本地修改）
git checkout -- deploy/stfc-cloud/docker-compose.yml                    # 丢掉本地修改（上一行已备份）
git pull --ff-only
grep -q '127.0.0.1:8080:8080' ~/docker-compose.pre-v030.yml && \
  sed -i 's|- "8080:8080"|- "127.0.0.1:8080:8080"|' deploy/stfc-cloud/docker-compose.yml
git log -1 --oneline        # 应为当前 main（ae03851 或更新）
git diff                    # 应只剩 ports 那一行
```

不用 `git stash`：VM 上如果其实没有本地修改，`stash push` 什么都不存，
接下来的 `stash pop` 就会弹出一个不相关的旧 stash。

## 2. 保留回滚镜像，构建新镜像（不影响线上）

先确认运行中的容器用的就是 `$IMG:latest`，两行输出必须相同，否则回滚镜像会标错：

```bash
cd ~/goldilocks-agent/deploy/stfc-cloud
docker inspect -f '{{.Image}}' $(docker compose ps -q agent)
docker image inspect -f '{{.Id}}' $IMG:latest
```

```bash
docker tag $IMG:latest $IMG:pre-v030
docker build --network host \
  --build-arg FRONTEND_BUILD_SCRIPT=build:stfc-cloud-internal \
  -t $IMG:latest ../..
docker compose ps           # 运行中的容器仍是旧镜像，不受影响
```

`--network host` 是为了绕开 MTU 问题：构建时要从 GitHub 拉 enumlib 和 core v0.3.0。

## 3. 备份旧 venv（回滚用，不影响线上）

旧 venv 里有当年从 GitHub 装的 mace fork 和 sphericart。容器内访问不了 GitHub
（MTU 问题），回滚时没法重装，所以必须先备份。

```bash
docker volume create ${VENV_VOL}_prev
docker run --rm --user root -v $VENV_VOL:/from:ro -v ${VENV_VOL}_prev:/to \
  $IMG:latest sh -c 'cp -a /from/. /to/'
```

## 4. 预热 + 冒烟测试（不影响线上）

先把本目录的 `prewarm.sh` 和 `smoke.py` 放到 VM 上，二选一：

- 用 scp 从 Mac 传：
  ```bash
  scp -i ~/.ssh/stfc_goldilocks_core_ed25519 prewarm.sh smoke.py ubuntu@130.246.214.189:/tmp/
  ```
- 或者直接在 VM 上用 `git show`/复制粘贴创建这两个文件。

然后在 VM 上：

```bash
docker run --rm --network host \
  -v $HOME_VOL:/home/goldilocks \
  -v /tmp/prewarm.sh:/prewarm.sh:ro -v /tmp/smoke.py:/smoke.py:ro \
  -e GOLDILOCKS_ASSET_ROOT=/home/goldilocks/.local/share/goldilocks/assets-next \
  -e UV_PROJECT_ENVIRONMENT=/tmp/prewarm-venv \
  $IMG:latest sh /prewarm.sh
```

这一步做了四件事：

- 复制 `assets` 为 `assets-next`；
- 在容器里的临时 venv 中执行 `uv sync`，下载的东西都进了 home volume 里的 uv 缓存；
- 把 v0.3.0 的资产装进 `assets-next`；
- 在 :18000 起一个临时 core，跑 `smoke.py`。`smoke.py` 已在本地对真实 core v0.3.0 验证过。

线上用的 venv volume 和 `assets` 目录全程只读不写。

成功时最后几行应该是：

```
core 0.3.0 vocabulary 5
scf_single_point: 26 records, blocking=0, unresolved=[], is_magnetic=ml, is_metal=ml
phonon: 32 records, blocking=0, unresolved=[], is_magnetic=ml, is_metal=ml
SMOKE OK
== PREWARM OK
```

**没有出现 `PREWARM OK` 就不要进入阶段 B。** 线上没有被动过，把报错发给我即可。
（常见原因：VM 访问不了 PSDI、download.pytorch.org 或 PyPI。）

---

## 5. 切换（停机约 2–5 分钟）

```bash
cd ~/goldilocks-agent/deploy/stfc-cloud
docker compose stop agent
docker run --rm --user root -v $HOME_VOL:/h $IMG:latest sh -euc \
  'cd /h/.local/share/goldilocks && mv assets assets-prev && mv assets-next assets'
docker compose up -d agent
```

core 是懒启动的：DFT Workbench 被打开或调用 ensure 时才会启动。这里主动触发一次，
然后等它变成 ready：

```bash
while :; do
  s=$(curl -s -X POST http://127.0.0.1:8080/api/core-server/ensure); echo "$s"
  case "$s" in *'"ready"'*|*'"error"'*) break;; esac; sleep 5
done
```

要看 core 的输出，可以另开一个终端：

```bash
docker compose logs -f agent 2>&1 | grep --line-buffered goldilocks-core
```

首次启动会把 venv 从 uv 缓存同步一遍（缓存和 venv 不在同一个 volume，
uv 会提示 hardlink 失败、改为复制，这个提示可以忽略），然后校验资产，
最后开始服务。

## 6. 验证

通过 agent 的代理，用同一个冒烟测试：

```bash
python3 /tmp/smoke.py http://127.0.0.1:8080
```

再到浏览器打开 https://workbench.goldilocks.ac.uk ，**强制刷新**，然后：

- 页面上不应出现 vocabulary 不匹配的横幅；
- 在聊天里上传 Si，让模型 open → review → bundle，能点 Download 拿到 zip；
- 在面板里把 Task 改成 Phonon，再打包一次；
- 内联 Structure 选择框应显示打开的结构名（#50）。

另外：

- 已经打开的旧页面在用户刷新前，用的还是旧前端，可能会看到横幅。刷新即可，
  保存的状态会正常恢复（vocabulary 1 → 5 已在本地验证）。
- 聊天模型仍是 qwen（`GOLDILOCKS_AGENT_MODEL`），这次升级不改变这一点。

---

## 回滚（只有在第 6 步失败时才需要）

```bash
cd ~/goldilocks-agent/deploy/stfc-cloud
docker compose stop agent
docker run --rm --user root -v $HOME_VOL:/h $IMG:pre-v030 sh -euc \
  'cd /h/.local/share/goldilocks && mv assets assets-v030-failed && mv assets-prev assets'
docker run --rm --user root -v ${VENV_VOL}_prev:/from:ro -v $VENV_VOL:/to $IMG:pre-v030 \
  sh -euc 'find /to -mindepth 1 -delete; cp -a /from/. /to/'
docker tag $IMG:pre-v030 $IMG:latest
cp ~/docker-compose.pre-v030.yml docker-compose.yml   # 恢复旧 compose（含 GOLDILOCKS_AGENT_MMACE_ENABLED）
docker compose up -d agent
curl -s -X POST http://127.0.0.1:8080/api/core-server/ensure; echo
```

回滚后工作区里的 compose 文件和 HEAD 不一致，这是有意的，等下次升级时再处理。
旧 mMACE checkpoint（`~/.local/share/goldilocks/mmace/`）在切换中没有被动过。

---

## 7. 清理（稳定运行几天后）

```bash
docker volume rm ${VENV_VOL}_prev
docker run --rm --user root -v $HOME_VOL:/h $IMG:latest sh -c \
  'rm -rf /h/.local/share/goldilocks/assets-prev /h/.local/share/goldilocks/mmace'
docker image rm $IMG:pre-v030
rm ~/docker-compose.pre-v030.yml /tmp/prewarm.sh /tmp/smoke.py
docker image prune
```

`mmace/` 是旧版 agent 自己下载的 checkpoint。v0.3.0 起，backbone 是 core 资产的一部分，
不再需要这个目录。
