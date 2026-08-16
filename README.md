# 前沿科技信息聚合 Agent (v3)

基于 **GitHub Actions + Python + LLM API** 的自动化前沿科技日报聚合系统。
每日定时采集多源信息，经 11 个垂直领域关键词过滤 + 顶尖机构/顶会/知名研究者质量筛选，跨天历史查重，调用大模型生成结构化解读，归档到飞书云文档并推送链接（或钉钉），同时存档回仓库。

## 目录结构

```
.
├── main.py                            # 采集 / 过滤 / 质量评分 / 查重 / LLM 分析 / 存档 / 推送
├── requirements.txt                   # 依赖(仅 requests)
├── .gitignore                         # 忽略 __pycache__ / .env 等
├── .github/workflows/daily_agent.yml  # GitHub Actions 定时任务(采集 + 提交回仓库)
├── reports/YYYY-MM-DD.md              # 每日日报存档(由 Action 自动提交)
├── data/seen_index.json               # 跨天查重索引(由 Action 自动维护)
└── README.md
```

## 信源

| 源 | 说明 | 是否论文 |
| --- | --- | --- |
| ArXiv | `cs.CV/cs.RO/cs.AI/cs.LG` 最新论文 | 是 |
| OpenAlex | 学术库检索，按**顶级机构/顶会/高被引**筛选高质量论文（含作者单位、引用数、期刊/会议） | 是 |
| HuggingFace Daily Papers | 社区高关注度论文 | 是 |
| GitHub Trending | 当日热门开源仓库（可选） | 否 |
| RSS | OpenAI / Google DeepMind / HuggingFace Blog / Simon Willison / Lilian Weng / Sebastian Raschka 官方博客与研究者长文 | 否 |
| Web Search | Tavily 检索当日相关新闻（OpenAI/Anthropic/人形机器人/NVIDIA 等，可选） | 否 |

### 高质量论文筛选逻辑

`main.py` 内置 `TOP_INSTITUTIONS`（Google/DeepMind/OpenAI/Anthropic/NVIDIA/MIT/Stanford/清华/北大…）、`TOP_VENUES`（NeurIPS/ICML/CVPR/ICRA/Nature/Science…）、`FAMOUS_RESEARCHERS`（Karpathy/Raschka/Lecun/Hinton…）三张信号表：

- OpenAlex 结果**仅保留**命中「顶级机构 / 顶会顶刊 / 高被引」的论文（质量分 ≥ 3）
- 其余源按「机构 + 顶会 + 知名研究者 + 引用数 + 领域命中数」综合打分排序，报告里给高质量条目打 `[大厂/顶级机构]` `[顶会/顶刊]` `[知名研究者]` 等标记

## 飞书文档归档（避免消息轰炸）

飞书对话不支持 Markdown 且单条消息有字数上限，v3 默认把整份日报**归档为飞书云文档**，然后只在群里发一条文档链接。实现基于飞书「企业自建应用」的 docx 开放接口：

1. 到 [飞书开放平台](https://open.feishu.cn/) 创建「企业自建应用」
2. 应用「权限管理」里开通：`docx:document`（文档读写）、`drive:drive`（云空间/权限）等
3. 发布应用并创建版本，让应用在你的租户内可用
4. 拿到 **App ID** 与 **App Secret** 填入 Secrets（见下表）

配置了 `FEISHU_APP_ID` + `FEISHU_APP_SECRET` 时：自动创建文档 → 写入内容 → 设为「租户内可读」→ 群里只发一条链接；未配置时回退为「分片纯文本」旧行为。

### 归档到指定文件夹 + 让你能编辑

文档默认由**应用（机器人）所有**，创建在应用自己的空间。要「存到你指定的文件夹」且「你能编辑」，需要两步：

1. **给文件夹授权**：在飞书里打开目标文件夹 → 添加协作者 → 搜索并添加该应用（机器人）为「可编辑」。否则应用无权写入你的文件夹，会回退到应用空间。
2. **配置所有者邮箱**：添加 Secret `FEISHU_OWNER_EMAIL`（你的飞书登录邮箱）。运行时会把你添加为文档协作者（可管理），并尝试把所有权转让给你。

> `FEISHU_DOC_FOLDER_TOKEN` 取文件夹 URL 中 `/folder/` 后的那串字符（不是 `?folderToken=`）。

## 跨天查重与存档

- 每次运行把当天命中条目的 URL/标题写入 `data/seen_index.json`（保留 90 天），**只对「非今日」已收录内容去重**——跨天不重复推送，但当天重复触发（如手动测试）仍会重新生成并推送报告，无需手动删除索引
- 日报写入 `reports/YYYY-MM-DD.md`，由 GitHub Actions 在采集后 `git commit` 回仓库，形成可回溯的每日存档

## 本地不拉取 md 存档（sparse-checkout）

`reports/*.md` 会被 Action 提交到远端仓库用于存档，但你本地开发时不想把它们拉下来。用 git sparse-checkout 即可（`.gitignore` 管不到已跟踪文件）：

```bash
git sparse-checkout init --no-cone
git sparse-checkout set '/*' '!/reports/'
```

之后 `git pull` 不再拉取 `reports/` 内容（`data/`、`main.py` 等仍正常）。恢复：`git sparse-checkout disable`。

## 部署步骤

1. 将本目录推到 GitHub 仓库
2. **Settings → Secrets and variables → Actions** 添加 Secrets

### 必填 Secrets

| Secret 名 | 说明 | 示例 |
| --- | --- | --- |
| `LLM_API_KEY` | 大模型 API Key | `sk-xxxx` |
| `LLM_BASE_URL` | 大模型接口地址(OpenAI 兼容) | `https://api.deepseek.com/v1` |
| `LLM_MODEL` | 模型名 | `deepseek-chat` |

### 推送 Secrets（至少一个）

| Secret 名 | 说明 | 示例 |
| --- | --- | --- |
| `FEISHU_WEBHOOK_URL` | 飞书自定义机器人 Webhook | `https://open.feishu.cn/open-apis/bot/v2/hook/xxx` |
| `FEISHU_SECRET` | 飞书签名密钥（可选） | 机器人「安全设置」里的密钥 |
| `FEISHU_APP_ID` | 飞书自建应用 App ID（归档云文档用，可选） | `cli_xxxx` |
| `FEISHU_APP_SECRET` | 飞书自建应用 App Secret（可选） | `xxxx` |
| `FEISHU_DOC_FOLDER_TOKEN` | 归档目标文件夹 token（可选） | 文件夹 URL `/folder/` 后的值 |
| `FEISHU_DOC_BASE_URL` | 你的飞书租户域名（自定义域名必填，否则链接打不开） | `https://ucnf8fdogxx6.feishu.cn` |
| `FEISHU_OWNER_EMAIL` | 你的飞书邮箱（让文档归你所有、可编辑） | `you@company.com` |
| `DINGTALK_WEBHOOK_URL` | 钉钉机器人 Webhook | `https://oapi.dingtalk.com/robot/send?access_token=xxx` |
| `DINGTALK_SECRET` | 钉钉「加签」密钥（可选） | 机器人「安全设置」里的密钥 |

### 可选 Secrets（有默认值）

| Secret 名 | 默认值 | 说明 |
| --- | --- | --- |
| `TAVILY_API_KEY` | 空 | 网页搜索 API Key（tavily.com 免费注册），配置后启用 Web Search 源 |
| `MAX_LLM_ITEMS` | `20` | 送入 LLM 分析的最大条目数 |
| `MAX_REPORT_ITEMS` | `40` | 日报展示的最大条目数 |
| `LOOKBACK_HOURS` | `96` | 采集回溯小时数（覆盖周末 ArXiv 停更） |
| `OPENALEX_DAYS` | `7` | OpenAlex 回溯天数 |

## 权限说明

工作流 `permissions: contents: write` 已开启，用于把 `reports/` 与 `data/` 自动提交回仓库。若 push 失败，检查仓库 **Settings → Actions → General → Workflow permissions** 是否为「Read and write」，并确认未开启保护分支阻止推送。

## 定时触发

`daily_agent.yml` 中 `cron: "0 0 * * *"` = **UTC 00:00 = 北京时间每日 08:00**（GitHub 按 UTC 计算，实际触发可能有延迟）。手动触发：**Actions → Run workflow**。

## 飞书文档归档排查

失败时会在日志打印 `[warn] 创建文档... code=xxx msg=...`，按 code 定位：

| 现象 | 原因 / 解决 |
| --- | --- |
| `code=99991400` | 应用**未发布** → 开放平台「版本管理与发布」创建版本并发布 |
| `code=99991663` | 缺少权限 → 应用「权限管理」开通 `docx:document`、`drive:drive` |
| `code=1254046` | `FEISHU_DOC_FOLDER_TOKEN` 无效或应用无权访问该文件夹 → 可先留空（自动归档到应用空间） |
| 文档没进指定文件夹 | 应用没被加为该文件夹的「可编辑」协作者 → 在飞书文件夹里添加应用为协作者 |
| 文档所有者是应用、自己没法编辑 | 配置 `FEISHU_OWNER_EMAIL`（你的飞书邮箱），运行时会自动加协作者并转让所有权 |
| 文档创建成功但链接打不开 | 自定义域名租户需配置 `FEISHU_DOC_BASE_URL`（如 `https://ucnf8fdogxx6.feishu.cn`） |
| 仍是消息轰炸 | 说明归档失败已回退分片文本；看上面的 `[warn]` 行定位具体原因 |

## 本地调试

```bash
pip install -r requirements.txt

# 只打印报告、不写文件/不推送/不改索引
DRY_RUN=1 \
LLM_API_KEY=sk-xxx LLM_BASE_URL=https://api.deepseek.com/v1 LLM_MODEL=deepseek-chat \
python main.py
```

## 可替换大模型（OpenAI 兼容 `/chat/completions`）

- DeepSeek：`https://api.deepseek.com/v1`，模型 `deepseek-chat`
- 通义千问：`https://dashscope.aliyuncs.com/compatible-mode/v1`，模型 `qwen-plus`
- 智谱 GLM：`https://open.bigmodel.cn/api/paas/v4`，模型 `glm-4-flash`
- OpenAI：`https://api.openai.com/v1`，模型 `gpt-4o-mini`
