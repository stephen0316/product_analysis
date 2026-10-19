# 内网自动部署接入方案

更新时间：2026-10-19。

已知条件：公司内网不能访问 GitHub、不允许翻墙；已有内部 GitLab / Jenkins / Harbor 等平台。具体可用组件、项目地址、构建节点和权限仍需技术人员确认。本文件是接入方案，尚未实现或启用公司内网流水线。

## 1. 推荐流程

复用公司的代码与发布平台，不为本项目另外搭建一套平台：

```text
开发者推送公司 GitLab 的 main
    ↓
Jenkins 或 GitLab CI 接收提交事件
    ↓
运行后端测试、前端测试与构建
    ↓
构建 Docker 镜像并启动临时容器检查
    ↓
推送到内部 Harbor，记录镜像摘要和提交版本
    ↓
内网发布节点调用 Docker Compose
    ↓
下载镜像 → 停止旧实例 → 备份数据 → 切换镜像 → 检查健康
```

公司已有 Jenkins 时，优先沿用现有 Jenkins 发布模板；已有可用 GitLab Runner 和 CI 模板时，可以直接使用 GitLab CI，无需为了本项目额外引入 Jenkins。Harbor 负责保存内部镜像，不负责独立完成代码构建和发布。

日常更新目标是“提交公司 GitLab 后自动部署”，无需人工逐次操作服务器。GitHub 不再是内网发布的必经环节。仅在公司允许时另行保留 GitHub 副本；不要在禁止访问 GitHub 的网络上安排自动同步任务。

## 2. 当前文件如何复用

| 当前文件 | 内网接入方式 |
| --- | --- |
| `Dockerfile` | 复用构建结构；根据公司要求替换基础镜像、apt、npm、pip 来源及镜像源码标签 |
| `docker-compose.yml` | 复用应用、数据卷、日志、端口设置；`PRODUCT_EVAL_IMAGE` 可传入 Harbor 镜像引用 |
| `.env.example` | 复用，真实密钥继续只保存在服务器 |
| `/api/health` | 复用，检查本地数据库与版本，不调用模型 |
| `scripts/deploy.sh` | 复用备份、切换、恢复思路；当前校验限定 `ghcr.io`，必须适配 Harbor 仓库地址后才能使用 |
| `.github/workflows/deploy.yml` | 不适用于本内网，改成公司的 Jenkinsfile 或 `.gitlab-ci.yml` |

不能只换代码仓库地址就认为已完成内网自动部署。Harbor 登录凭据、镜像摘要传递、提交事件触发、构建节点、发布节点和部署权限均需对接。

## 3. 技术人员需要一次性提供

1. **公司 GitLab 项目地址**：给开发者推送权限，并确定正式发布分支。
2. **流水线入口**：使用现有 Jenkins 还是 GitLab CI，提供公司模板、构建节点及发布凭据管理方式。
3. **Harbor 项目与仓库地址**：构建节点可推送，服务器可拉取；使用受限的机器账户或公司现有凭据管理方式。
4. **依赖来源**：基础镜像、Debian 软件源、npm 和 PyPI 的内部镜像或公司批准的访问方式；内部 HTTPS 证书需加入构建节点、容器和 Docker 的信任配置，不能关闭证书校验。
5. **服务器部署信息**：Linux 架构、Compose 版本、部署目录、发布节点到服务器的连接方式、服务账户及端口。
6. **模型服务信息**：确认公司模型网关地址、认证方式、模型名称及结构化输出能力；代码已支持 LLM_* 配置的 OpenAI 兼容接口，并保留 Google Gemini 原生协议。
7. **访问及数据管理**：域名、统一登录、备份位置、历史模型与批次迁移安排。

GitHub 不可达不等于所有外网服务均不可达，这些连接分别验证。公司网关使用 LLM_API_KEY、LLM_BASE_URL、LLM_MODEL，可另设 LLM_REPORT_MODEL；仅配置 GEMINI_API_KEY 时沿用 Google 原生协议。公司网关输出需通过本地业务校验，接入后仍需完成真实规则解析、表头映射及报告评价验收。

## 4. 接入后的发布要求

- 按正式分支的提交触发发布，测试失败则不更新线上。
- 镜像关联提交版本，按摘要部署同一份已检查的镜像。
- 正式发布串行执行，过时构建不得覆盖更新版本。
- 切换前停止单实例并备份 SQLite 和上传目录；这种方式会短暂停机。
- 更新代码时保留现有数据卷和服务器 `.env`。
- 失败时尝试恢复旧应用；数据库有迁移时由技术人员评估恢复，不能直接覆盖业务数据。
- 首次完成上传、校验、计算、XLSX/DOCX 导出、模型解析与重启后数据保留的实际验收，再启用自动发布。

## 5. 可直接转发给技术人员

> 产品后评估项目需要接入公司已有的 GitLab / Jenkins / Harbor 等平台，实现提交正式分支后自动测试、构建镜像并部署公司服务器。内网不访问 GitHub，也不使用 GHCR 或 GitHub Runner。
>
> 项目已有 Dockerfile、Compose、环境变量模板、健康检查及部署脚本。当前 GitHub 流水线需要替换为公司流水线，发布脚本的镜像地址校验需要从 GHCR 适配到内部 Harbor。
>
> 请提供 GitLab 项目地址与权限、Jenkins 或 GitLab CI 模板、Harbor 仓库及凭据配置方式、内网构建依赖来源、部署节点和公司模型网关信息。配置完成后，目标是我日常推送公司仓库即可更新，无需逐次联系技术人员。

参考：[GitLab 构建 Docker 镜像](https://docs.gitlab.com/ci/docker/using_docker_build/)、[Jenkins Docker Pipeline](https://www.jenkins.io/doc/book/pipeline/docker/)。

## 6. 已合入的公司服务器配置

副本服务器运行方式已单独保存在 `docker-compose.company.yml`，使用说明见 [公司服务器配置](deployment-company.md)。根目录通用 Compose 与现有发布脚本保持配套；公司配置不使用该发布脚本。内网流水线接入与 Harbor 适配仍需按本方案完成。
