# 公司服务器配置（副本运行方式）

`docker-compose.company.yml` 保留副本的公司服务器设置：`product-fenxi` 服务和容器名、4100 端口、公司网关固定地址、源码及前端构建目录挂载，以及 `product-fenxi-storage` 数据卷。因副本注明服务器 runc 兼容问题，该配置未启用 init；本次未连接服务器验证。

根目录 `docker-compose.yml` 继续配套 `scripts/deploy.sh`，使用 `app` 服务和按摘要部署的镜像。这两种方式单独使用，公司配置不运行 `scripts/deploy.sh`。副本的网关 IP 与端口是已有配置，不代表已在当前机器或其他服务器验证。

## 首次或继续使用副本服务器

1. 继续使用服务器原有 `.env` 的 GLM 网关地址、密钥、`LLM_MODEL` 和可选 `LLM_REPORT_MODEL`，不要用示例覆盖旧配置。新部署才参考 `.env.example` 并填写实际 GLM 标识。密钥不提交 Git；Compose 要求 `LLM_MODEL` 非空。
2. 云端 Compose 默认 `LLM_PROVIDER=openai`，通过兼容协议调用原 GLM 模型；本地 `run_local.py` 默认 Gemini。显式 provider 优先于按密钥选择，不会交叉借用密钥。
3. 服务器构建前端，再构建应用镜像、启动服务：

```bash
cd frontend
npm ci
npm run build
cd ..
docker compose -f docker-compose.company.yml build product-fenxi
docker compose -f docker-compose.company.yml up -d product-fenxi
```

公司配置将宿主机的 `post_evaluation/` 和 `frontend/dist/` 挂载到容器，所以运行代码取决于这些目录。未构建 frontend/dist 时不要启动，以免空目录遮蔽镜像内前端。后端源码更新后运行：

```bash
docker compose -f docker-compose.company.yml restart product-fenxi
```

如果 `.env`、镜像依赖或 Compose 配置变化，应重新运行 `up -d` 以重建容器，单纯 restart 不会加载新环境变量；依赖变化还需要重新 build。不要使用 `down -v`。

默认访问为 `http://服务器地址:4100`，该配置将 4100 绑定到所有网卡，访问范围由公司网络、网关或防火墙控制。网关地址映射 `onerouter.cmaiot.cn:10.12.84.1` 仅在对应内网环境有效。

## 数据卷与已有部署

Compose 项目名默认来自部署目录，同名 `product-fenxi-storage` 卷仍会带项目名前缀。继续使用副本服务器时必须保留原部署目录/项目名；如果原先通过 `-p` 或 `COMPOSE_PROJECT_NAME` 指定过项目名，后续所有命令必须继续沿用该值。先用 `docker inspect product-fenxi` 确认实际挂载，再启动，避免连接新空卷。

当前本地项目的 SQLite 和上传目录保留在 `local_data/`，本次合并没有迁移它们到服务器，也没有导入副本 `.env` 或数据库。不得用副本空库覆盖当前批次。若要从根目录通用 Compose 的 `data` 卷迁移，需停写后备份数据库和上传文件，核对数据库绝对路径与卷权限，再迁移到目标卷；仅改卷名不会迁移数据。

## 验收

确认真实网关规则解析、表头映射与报告评价；验证上传→校验→计算→XLSX/DOCX 导出→容器重启后的数据保留。健康检查通过仅代表本地数据库和应用可用，不代表网关可调用。

内网自动发布仍按 [内网自动部署方案](deployment-intranet.md) 对接公司流水线。此文件保留副本手动运行方式，不宣称已经实现内网自动发布。
