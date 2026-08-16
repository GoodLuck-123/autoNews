# 前沿科技信息聚合 Agent (v2)

基于 **GitHub Actions + Python + LLM API** 的自动化前沿科技日报聚合系统。
每日定时采集多源信息，经 11 个垂直领域关键词过滤 + 顶尖机构/顶会/知名研究者质量筛选，跨天历史查重，调用大模型生成结构化解读，存档到仓库并推送到飞书 / 钉钉。

## 目录结构

```
.
├── main.py                            # 采集 / 过滤 / 质量评分 / 查重 / LLM 分析 / 存档 / 推送
├── requirements.txt                   # 依赖(仅 requests)
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
| RSS | OpenAI / Google DeepMind / HuggingFace Blog / Simon Willison / Lilian Weng / Sebastian Raschka 等官方博客与研究者长文 | 否 |

### 高质量论文筛选逻辑

`main.py` 内置 `TOP_INSTITUTIONS`（Google/DeepMind/OpenAI/Anthropic/NVIDIA/MIT/Stanford/清华/北大…）、`TOP_VENUES`（NeurIPS/ICML/CVPR/ICRA/Nature/Science…）、`FAMOUS_RESEARCHERS`（Karpathy/Raschka/Lecun/Hinton…）三张信号表：

- OpenAlex 结果**仅保留**命中「顶级机构 / 顶会顶刊 / 高被引」的论文（质量分 ≥ 3）
- 其余源按「机构 + 顶会 + 知名研究者 + 引用数 + 领域命中数」综合打分排序，报告里给高质量条目打 `[大厂/顶级机构]` `[顶会/顶刊]` `[知名研究者]` 等标记

## 跨天查重与存档

- 每次运行把当天命中条目的 URL/标题写入 `data/seen_index.json`（保留 90 天），下次运行自动跳过已收录内容，避免重复推送
- 日报写入 `reports/YYYY-MM-DD.md`，由 GitHub Actions 在采集后 `git commit` 回仓库，形成可回溯的每日存档

## 关于「能否搜索全网」

GitHub Actions 的 runner 有完整的公网出站能力，脚本可访问**任意公开 API/URL**。因此「外网」「OpenAI 等厂商信息」都能取到——本版通过 RSS 抓取厂商官方博客与研究者博客。但**不存在免费的“全网搜索”接口**：如需真正的网页搜索（如搜“OpenAI 今日新闻”），需额外接入带 Key 的搜索 API（如 Tavily / Serper / Bing Search），可后续扩展。

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
| `DINGTALK_WEBHOOK_URL` | 钉钉机器人 Webhook | `https://oapi.dingtalk.com/robot/send?access_token=xxx` |
| `DINGTALK_SECRET` | 钉钉「加签」密钥（可选） | 机器人「安全设置」里的密钥 |

### 可选 Secrets（有默认值）

| Secret 名 | 默认值 | 说明 |
| --- | --- | --- |
| `MAX_LLM_ITEMS` | `20` | 送入 LLM 分析的最大条目数 |
| `MAX_REPORT_ITEMS` | `40` | 日报展示的最大条目数 |
| `LOOKBACK_HOURS` | `96` | 采集回溯小时数（覆盖周末 ArXiv 停更） |
| `OPENALEX_DAYS` | `7` | OpenAlex 回溯天数 |

## 权限说明

工作流 `permissions: contents: write` 已开启，用于把 `reports/` 与 `data/` 自动提交回仓库。若 push 失败，检查仓库 **Settings → Actions → General → Workflow permissions** 是否为「Read and write」，并确认未开启保护分支阻止推送。

## 定时触发

`daily_agent.yml` 中 `cron: "0 0 * * *"` = **UTC 00:00 = 北京时间每日 08:00**（GitHub 按 UTC 计算，实际触发可能有延迟）。手动触发：**Actions → Run workflow**。

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
