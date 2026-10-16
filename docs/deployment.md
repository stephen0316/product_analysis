# 公司服务器部署与 GitHub 自动发布

本方案实现：向 `main` 推送代码 → GitHub 自动测试 → 构建并检查镜像 → 发布到 GHCR → 公司服务器领取任务 → 备份数据 → 更新容器 → 检查运行状态。

仓库侧配置已提供。服务器网络、GitHub 权限和首次部署仍需技术人员接入并实测；这份文档不代表生产环境已经启用。

## 1. 技术人员先确认的条件

| 项目 | 本配置要求 |
| --- | --- |
| 服务器 | Linux x64；如为 ARM64，需调整镜像构建平台和 Runner 标签 |
| 容器环境 | Docker Engine + Docker Compose v2.20 或更新版本 |
| 仓库 | 私有 GitHub 仓库；部署任务只接受 `main`，不运行 PR 代码 |
| 出站网络 | GitHub、GitHub Actions 及其依赖域名、GHCR 镜像下载域名，可通过 HTTPS 443 访问 |
| 入站网络 | 部署流程无需开放公网 SSH；用户通过公司已有反向代理访问应用 |
| 运行目录 | 默认 `/opt/product-evaluation`，Runner 服务账户可写 |
| 模型调用 | 服务器可访问 Gemini；当前代码固定使用 Google 地址，内部网关需要另做适配 |
| 用户访问 | 通过公司统一登录或已有访问网关保护页面与全部 API；应用当前没有账户隔离 |

Runner 主动连接 GitHub。不要在 GitHub 界面填一个内网 IP 并期望云端任务直接访问它。完整网络白名单以 [GitHub Runner 通信要求](https://docs.github.com/en/actions/reference/runners/self-hosted-runners#communication) 为准，包括镜像和 Actions 的下载域名。

如果服务器只能访问内网，需要由技术人员提供代理、可联网的内部发布节点，或公司 Git 服务/CI/镜像仓库。本配置无法跨越完全断开的网络。

默认部署任务限定私有仓库。GitHub 不建议把公有仓库的工作流直接连接到持有生产权限的自托管 Runner；公有源码场景应由技术人员设计独立的私有部署仓库或受控的发布通道。[Runner 安全说明](https://docs.github.com/en/actions/reference/security/secure-use#hardening-for-self-hosted-runners)

## 2. 已提供的文件

- `Dockerfile`：Node 构建 React，Python 运行 FastAPI；包含 XLSX 模板，镜像不含本地业务文件或密钥。
- `docker-compose.yml`：一个应用实例、持久化卷、日志轮换、自动重启；只映射本机 `127.0.0.1:8765`。
- `.env.example`：服务端配置模板。
- `.github/workflows/deploy.yml`：检查、镜像构建与发布、服务器部署三个任务。
- `scripts/deploy.sh`：按镜像摘要部署，更新前备份，失败时尝试恢复旧应用版本。
- `requirements-dev.txt`：CI 测试依赖。

工作流的测试和构建运行在 GitHub 托管机器上。公司 Runner 只执行通过检查的 `main` 发布任务，拉取构建好的镜像，无需安装 Node 或 Python，也无需直接访问 npm/PyPI。首次镜像构建由 GitHub 机器访问 Docker Hub、npm 和 PyPI。

## 3. GitHub 一次性设置

1. 确认仓库为私有，开启 Actions。为 `main` 限制可推送人员；团队有多人协作时可要求先通过检查再合并。
2. 在 **Settings → Environments** 创建 `production`，部署分支限制为 `main`。需要全自动发布时不要配置每次必须人工审批。具体保护选项取决于 GitHub 套餐。
3. 在 **Settings → Actions → Runners → New self-hosted runner** 选择 Linux x64，按 GitHub 当时提供的命令安装到公司服务器。注册时添加自定义标签 `product-evaluation-deploy`，保留 `self-hosted`、`linux`、`x64` 默认标签。
4. 将 Runner 安装成系统服务，由专用账户运行；该账户须有访问 Docker 的权限。Docker 权限相当于对主机有很高权限，限制可修改生产工作流的人员。优先使用专用部署主机；如与应用共用服务器，应由技术人员确认权限范围。
5. 在 **Settings → Secrets and variables → Actions → Variables** 准备：

| Variable | 值 | 含义 |
| --- | --- | --- |
| `AUTO_DEPLOY` | 首次先设 `false`，接入完成后设 `true` | 是否自动部署到公司服务器 |
| `PRODUCT_EVAL_DEPLOY_DIR` | `/opt/product-evaluation` | 服务器部署目录；可省略使用默认值 |

6. GHCR 使用工作流自带的短期 `GITHUB_TOKEN`，无需把个人令牌写进仓库。首次推送镜像后，核对仓库 **Packages** 中镜像权限：本仓库的 Actions 需要读取权限。组织若限制包发布或 Actions 权限，需要管理员放行。

`AUTO_DEPLOY=false` 或未设置时，代码仍会自动测试、构建、推送镜像，部署任务显示跳过。PR 只运行检查；手动 **Run workflow** 也只能从 `main` 发布。

## 4. 服务器一次性设置

以下由技术人员执行，先把示例账户名改成实际 Runner 服务账户：

```bash
DEPLOY_USER=github-runner
sudo install -d -m 700 -o "$DEPLOY_USER" -g "$(id -gn "$DEPLOY_USER")" /opt/product-evaluation
```

从已拉取的项目目录复制配置，以 Runner 账户操作并填写真实密钥：

```bash
cp .env.example /opt/product-evaluation/.env
chmod 600 /opt/product-evaluation/.env
```

编辑 `/opt/product-evaluation/.env`：

```dotenv
GEMINI_API_KEY=实际服务端密钥
GEMINI_MODEL=gemini-2.5-flash
PRODUCT_EVAL_PORT=8765
```

密钥只保存在服务器。工作流不会从 GitHub 覆盖 `.env`。应用内部数据路径固定为 `/app/local_data`，Compose 项目固定为 `product-evaluation`，命名卷通常为 `product-evaluation_data`。不要改变项目名后误用一个新空卷，也不要把 ChatBI 的卷挂到本项目。

如果要保留本机的已发布评分模型、历史批次与上传文件，必须在启用自动发布前单独迁移并验收。GitHub 不包含 `local_data`。当前数据库保存文件路径，迁移时需要把文件记录指向容器内的 `/app/local_data/uploads/...`，并把卷内文件所有者设为 `10001:10001`。不要直接把开发机数据库打进镜像。

## 5. 访问入口

让公司现有 Nginx/网关转发到 `http://127.0.0.1:8765`。下面仅为需要合并到公司配置的示例，不含公司证书与统一登录配置：

```nginx
location / {
    client_max_body_size 300m;
    proxy_pass http://127.0.0.1:8765;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 300s;
}
```

上传大小是整个批量请求的上限，按真实批量文件调整；应用仍限制每份 XLSX 最大 30 MB。若网关运行在另一个容器或另一台主机，`127.0.0.1` 不是应用所在主机，需由技术人员配置受控网络访问。

## 6. 启用与后续日常使用

1. 将部署配置提交并推送 GitHub `main`，确认 **Actions → Check, package and deploy** 的 `check`、`package` 成功。
2. 技术人员确认 Runner 在线、服务器 `.env` 和网关已配置、数据迁移完成、镜像可下载。
3. 设置 `AUTO_DEPLOY=true`，在 Actions 对 `main` 执行一次 **Run workflow**，首次部署并验收。
4. 验证域名、创建批次、上传、校验、计算、XLSX/DOCX 导出及真实模型解析；容器重启后确认历史数据仍在。
5. 此后向 `main` 推送更新即可。无需每次联系技术人员；在 Actions 查看绿灯和失败日志。

每个提交生成 `sha-<commit>` 镜像标签，服务器实际按 `@sha256:...` 摘要拉取，以确保部署的是已检查的同一个镜像。构建或测试失败不会进入部署。过时的提交会在部署任务开始时跳过，避免较早提交覆盖更新的 `main`。发布任务串行执行，不中途取消；主机上另有文件锁防止脚本并发执行。

这是单实例更新，会在停止旧服务、备份、启动新服务期间短暂停机。时间取决于数据量和启动速度，不承诺零停机；正在处理的请求最多等待 90 秒后停止。如以后需要无感更新，应另行设计双实例切换和数据库迁移流程。

## 7. 自动备份与失败处理

每次更新现有实例前：

1. 先下载目标镜像，下载失败不停止原服务。
2. 停止原服务，把整个数据目录打包到 `/opt/product-evaluation/backups/*-pre-deploy.tar.gz`，包括数据库、上传文件与已生成报告。
3. 启动目标镜像，检查容器健康状态。`/api/health` 检查本地数据库访问，不调用 Gemini。
4. 备份或更新失败时，尝试使用旧 Compose 文件和镜像恢复服务；工作流仍标记失败。

**恢复旧镜像不等于恢复数据库。** 新代码可能已进行数据迁移；脚本不会自动还原数据库，避免丢弃上线后的业务写入。若旧版本也不能运行，需要技术人员审查迁移兼容性、日志，并在保留当前数据副本后选择恢复备份。健康检查也不能替代真实业务验收。

服务器目录包括：

| 文件 | 含义 |
| --- | --- |
| `.env` | 服务器配置和密钥，由技术人员维护 |
| `image.env` | 当前镜像摘要，由发布脚本维护 |
| `previous-image.env` | 最近一次成功发布前的镜像 |
| `compose.previous.yml` | 最近切换前的 Compose 配置 |
| `deployed-image.txt` | 最近成功部署的镜像摘要 |
| `backups/` | 更新前一致性备份，技术人员应另行配置异机备份与保留策略 |

备份默认不自动删除，技术人员需根据容量设置保留策略。代码更新不会替换数据卷；不要执行 `docker compose down -v`，该选项会删除受管理数据卷。

## 8. 常用排查命令

以 Runner 账户在部署目录操作：

```bash
cd /opt/product-evaluation
docker compose -p product-evaluation --env-file .env --env-file image.env ps
docker compose -p product-evaluation --env-file .env --env-file image.env logs --tail 100 app
curl --fail http://127.0.0.1:8765/api/health
```

- `deploy` 为 **Skipped**：检查 `AUTO_DEPLOY`、仓库是否私有、是否为 `main`，或该提交是否已过时。
- 长期 **Queued**：检查 Runner 服务和四个标签是否匹配。
- 拉镜像报 403：检查本仓库 Actions 对 GHCR 包的读取权限。
- Gemini 不可用：检查服务器密钥及出站网络；健康检查成功不代表模型接口可达。
- 新镜像失败：查看 Actions 及服务日志、确认是否已恢复旧应用。

停止自动发布只需把 `AUTO_DEPLOY` 改成 `false`；已运行的应用继续提供服务。正在执行的部署不会因为变量改变立即停止。

参考：[GitHub 部署控制](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/control-deployments)、[GHCR 镜像发布](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images)、[Docker Compose 生产部署](https://docs.docker.com/compose/how-tos/production/)。
