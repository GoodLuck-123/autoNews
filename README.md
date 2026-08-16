# 前沿科技日报

每天自动采集全球前沿 AI 资讯，筛选出 11 个垂直领域的高质量内容，用大模型生成结构化解读，归档成飞书云文档发到群里。全程运行在 GitHub Actions 上，零服务器成本。

## 它能做什么

- **多源采集**：论文（ArXiv / OpenAlex / HuggingFace）+ 开源项目（GitHub Trending）+ 资讯动态（官方博客 RSS / 网页搜索）
- **领域过滤**：具身智能、世界模型、三维重建、基座大模型、Agent、开源工具、算力硬件、推理框架、上下文工程、安全评测、研究者洞察等 11 个方向
- **质量筛选**：优先收录大厂 / 顶级高校 / 顶会论文，报告里打 `[大厂/顶级机构]` `[顶会/顶刊]` `[知名研究者]` 标记
- **结构化解读**：每条给出「核心技术突破 / 开源情况 / 工业落地可行性(高/中/低)」
- **分栏排版**：论文、开源项目、资讯动态分开展示；论文若附带开源链接会单独列出
- **推送与存档**：归档为飞书云文档只发一条链接（不刷屏），同时把日报存回仓库，跨天自动查重

## 目录结构

```
.
├── main.py                            # 主逻辑
├── requirements.txt                   # 依赖(仅 requests)
├── .gitignore
├── .github/workflows/daily_agent.yml  # 定时任务
├── reports/YYYY-MM-DD.md              # 每日日报存档(自动提交)
└── data/seen_index.json               # 跨天查重索引(自动维护)
```

## 快速开始

1. 把代码推到一个 GitHub 仓库
2. 在 **Settings → Secrets and variables → Actions** 里配置 Secrets
3. 到 **Actions** 页手动跑一次 `Daily AI Tech Agent`，群里就能收到日报
4. 之后每天北京时间 08:00 自动运行（`cron: "0 0 * * *"` = UTC 00:00，GitHub 触发可能有几分钟延迟）

## Secrets 配置

### 必填

| Secret | 说明 | 示例 |
| --- | --- | --- |
| `LLM_API_KEY` | 大模型 API Key | `sk-xxxx` |
| `LLM_BASE_URL` | 接口地址（OpenAI 兼容） | `https://api.deepseek.com/v1` |
| `LLM_MODEL` | 模型名 | `deepseek-chat` |

### 推送（飞书 / 钉钉至少配一个）

| Secret | 说明 | 示例 |
| --- | --- | --- |
| `FEISHU_WEBHOOK_URL` | 飞书群机器人 Webhook | `https://open.feishu.cn/open-apis/bot/v2/hook/xxx` |
| `FEISHU_APP_ID` | 飞书自建应用 App ID（归档云文档用） | `cli_xxxx` |
| `FEISHU_APP_SECRET` | 飞书自建应用 App Secret | `xxxx` |
| `FEISHU_DOC_FOLDER_TOKEN` | 归档目标文件夹 token（可选） | 文件夹 URL `/folder/` 后的值 |
| `FEISHU_DOC_BASE_URL` | 你的飞书租户域名（自定义域名必填） | `https://xxx.feishu.cn` |
| `FEISHU_OWNER_MEMBER_TYPE` | 文档所有者身份类型（默认 `userid`） | `userid` / `openid` |
| `FEISHU_OWNER_MEMBER_ID` | 你的用户ID / open_id（没邮箱时用） | `abc123` / `ou_xxx` |
| `FEISHU_OWNER_EMAIL` | 你的飞书邮箱（有邮箱时用这个即可） | `you@company.com` |
| `FEISHU_SECRET` | 飞书机器人签名密钥（可选） | 机器人「安全设置」里 |
| `DINGTALK_WEBHOOK_URL` | 钉钉机器人 Webhook | `https://oapi.dingtalk.com/robot/send?access_token=xxx` |
| `DINGTALK_SECRET` | 钉钉加签密钥（可选） | 机器人「安全设置」里 |

### 可选

| Secret | 默认值 | 说明 |
| --- | --- | --- |
| `TAVILY_API_KEY` | 空 | 网页搜索 Key（tavily.com 免费注册），不配则跳过新闻搜索 |
| `MAX_LLM_ITEMS` | `20` | 送入 LLM 分析的最大条目数 |
| `MAX_REPORT_ITEMS` | `40` | 日报展示的最大条目数 |
| `LOOKBACK_HOURS` | `96` | 采集回溯小时数（覆盖周末 ArXiv 停更） |

## 飞书设置

在 [飞书开放平台](https://open.feishu.cn/) 建一个「企业自建应用」，开通 `docx:document`、`drive:drive` 权限后**发布**，把 App ID / App Secret 填进上面的 Secrets；再把该应用加为群机器人，并作为目标文件夹的「可编辑」协作者，配置好所有者身份（邮箱或用户ID）即可。运行时会自动创建文档 → 写入内容 → 转让所有权给你 → 群里只发一条文档链接；没配 App ID 时会回退为「分片纯文本」消息。

## 常见问题

| 现象 | 原因 / 解决 |
| --- | --- |
| 日志显示 `Web Search 采集 0 条` | 没配 `TAVILY_API_KEY`，网页搜索默认关闭，不是报错 |
| 文档没进指定文件夹 | 应用没被加为该文件夹的「可编辑」协作者 |
| 文档所有者是应用、自己没法编辑 | 配 `FEISHU_OWNER_EMAIL` 或 `FEISHU_OWNER_MEMBER_ID` |
| 文档链接打不开 | 自定义域名租户需配 `FEISHU_DOC_BASE_URL` |
| 创建文档报 `code=99991400` | 应用未发布 |
| 创建文档报 `code=99991663` | 缺权限，补 `docx:document` / `drive:drive` |
| 重复触发不推送 | 已改为「仅跨天去重」，当天重复触发仍会重新推送 |

## 本地调试

```bash
pip install -r requirements.txt

# 只打印报告、不写文件、不推送、不改索引
DRY_RUN=1 \
LLM_API_KEY=sk-xxx LLM_BASE_URL=https://api.deepseek.com/v1 LLM_MODEL=deepseek-chat \
python main.py
```

不想把 `reports/` 里的日报拉到本地，用 sparse-checkout：

```bash
git sparse-checkout init --no-cone
git sparse-checkout set '/*' '!/reports/'
```

## 可替换大模型

任意 OpenAI 兼容的 `/chat/completions` 接口均可：

- DeepSeek：`https://api.deepseek.com/v1`，模型 `deepseek-chat`
- 通义千问：`https://dashscope.aliyuncs.com/compatible-mode/v1`，模型 `qwen-plus`
- 智谱 GLM：`https://open.bigmodel.cn/api/paas/v4`，模型 `glm-4-flash`
- OpenAI：`https://api.openai.com/v1`，模型 `gpt-4o-mini`
