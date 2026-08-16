#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
前沿科技信息聚合 Agent (v3)
====================================
流程:
  1. 采集: ArXiv / OpenAlex(高质量学术) / HuggingFace Daily Papers /
           GitHub Trending / 各大实验室与研究者 RSS / 网页搜索(Tavily)
  2. 过滤: 11 个垂直领域关键词 + 顶尖机构/顶会/知名研究者质量信号
  3. 查重: 与仓库内 data/seen_index.json 历史记录去重
  4. 分析: 调用 LLM(OpenAI 兼容接口) 结构化输出 Markdown
  5. 存档: 日报写入 reports/YYYY-MM-DD.md (由 GitHub Actions 提交回仓库)
  6. 推送: 飞书(归档为云文档后只发链接, 避免消息轰炸) / 钉钉 Webhook

全部配置通过环境变量注入, 配合 GitHub Actions Secrets 使用, 零服务器运维。
"""

import os
import re
import json
import time
import hmac
import base64
import hashlib
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter
from email.utils import parsedate_to_datetime
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

# ---------------------------------------------------------------------------
# 环境变量配置
# ---------------------------------------------------------------------------
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")

FEISHU_WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")
FEISHU_SECRET = os.environ.get("FEISHU_SECRET", "")
DINGTALK_WEBHOOK_URL = os.environ.get("DINGTALK_WEBHOOK_URL", "")
DINGTALK_SECRET = os.environ.get("DINGTALK_SECRET", "")

# 飞书自建应用凭证(用于把日报归档为云文档后分享链接, 避免消息轰炸)
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_DOC_FOLDER_TOKEN = os.environ.get("FEISHU_DOC_FOLDER_TOKEN", "")
FEISHU_DOC_BASE_URL = os.environ.get("FEISHU_DOC_BASE_URL", "https://feishu.cn")
FEISHU_OWNER_EMAIL = os.environ.get("FEISHU_OWNER_EMAIL", "")  # 文档所有者邮箱(可选)
FEISHU_OWNER_MEMBER_TYPE = os.environ.get("FEISHU_OWNER_MEMBER_TYPE", "userid")  # userid/openid/email
FEISHU_OWNER_MEMBER_ID = os.environ.get("FEISHU_OWNER_MEMBER_ID", "")  # 你的用户ID或 open_id
FEISHU_HOST = "https://open.feishu.cn"

# 网页搜索 API(Tavily, 可选)
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")

MAX_LLM_ITEMS = int(os.environ.get("MAX_LLM_ITEMS", "20"))        # 送入 LLM 的最大条目数
MAX_REPORT_ITEMS = int(os.environ.get("MAX_REPORT_ITEMS", "40"))  # 日报展示的最大条目数
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "96"))      # 采集回溯小时数(覆盖周末停更)
OPENALEX_DAYS = int(os.environ.get("OPENALEX_DAYS", "7"))         # OpenAlex 回溯天数
ENABLE_GITHUB_TRENDING = os.environ.get("ENABLE_GITHUB_TRENDING", "1") == "1"
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"                   # 只打印, 不写文件/不推送
HTTP_TIMEOUT = 60

BEIJING_TZ = timezone(timedelta(hours=8))

REPORTS_DIR = "reports"
DATA_DIR = "data"
INDEX_PATH = os.path.join(DATA_DIR, "seen_index.json")
INDEX_RETENTION_DAYS = 90

# ---------------------------------------------------------------------------
# 垂直领域定义 (11 个领域 + 中英文关键词)
# ---------------------------------------------------------------------------
DOMAINS: List[Dict[str, Any]] = [
    {
        "name": "具身智能与端侧具身落地",
        "keywords": [
            "embodied ai", "embodied intelligence", "autonomous driving", "self-driving",
            "end-to-end driving", "tesla fsd", "waymo", "nvidia drive", "alpamayo",
            "boston dynamics", "figure ai", "unitree", "宇树", "智元", "银河通用",
            "humanoid robot", "humanoid", "sim-to-real", "sim2real", "edge deployment",
            "robotics", "robot", "manipulation", "grasp", "legged", "quadruped",
            "mobile robot", "navigation",
        ],
    },
    {
        "name": "物理世界模型与具身决策控制",
        "keywords": [
            "world model", "world models", "worldmodel", "video prediction",
            "openvla", "octo", "rt-2", "rt2", "diffusion policy", "vision-language-action",
            "vla", "visuomotor", "physical simulation", "generative simulation",
            "long-horizon", "long horizon", "action model", "control policy",
            "sora", "cosmos", "world simulation", "causal",
        ],
    },
    {
        "name": "三维重建与空间智能",
        "keywords": [
            "3d gaussian splatting", "gaussian splatting", "3dgs", "nerf", "neural radiance",
            "3d reconstruction", "dynamic scene", "mesh generation", "mesh topology",
            "dense slam", "slam", "spatial intelligence", "spatial understanding",
            "vision pro", "ar/vr", "augmented reality", "virtual reality", "multiview",
            "multi-view", "depth estimation", "novel view synthesis", "surface reconstruction",
        ],
    },
    {
        "name": "各大厂商基座大模型动态",
        "keywords": [
            "openai", "anthropic", "claude", "deepmind", "gemini", "meta", "llama",
            "deepseek", "qwen", "glm", "mistral", "foundation model", "large language model",
            "llm", "gpt", "model release", "open weights", "benchmark", "api price",
            "通义千问", "文心", "开源权重", "基座模型",
        ],
    },
    {
        "name": "自研 Agent 与端到端应用架构",
        "keywords": [
            "autonomous agent", "multi-agent", "multi agent", "tool use", "tool calling",
            "function calling", "self-correction", "self correction", "long-horizon task",
            "task planning", "manus", "devin", "operator", "claude code", "cursor",
            "windsurf", "coding agent", "agentic", "agentic workflow", "agent framework",
            "智能体", "多智能体",
        ],
    },
    {
        "name": "开源高星工具与实用 Skills",
        "keywords": [
            "mcp server", "mcp", "model context protocol", "agent skills", "composable tools",
            "open source", "github trending", "cli tool", "plugin", "plugin ecosystem",
            "developer tool", "langchain", "llamaindex",
        ],
    },
    {
        "name": "AI 基础设施与算力硬件",
        "keywords": [
            "nvidia", "blackwell", "b200", "gb200", "amd", "mi300", "mi350", "tpu", "tpu v5",
            "tpu v6", "infiniband", "roce", "rdma", "hpc", "compute cluster", "data center",
            "gpu", "accelerator", "hbm", "nvlink", "interconnect", "power consumption",
            "算力", "芯片", "英伟达",
        ],
    },
    {
        "name": "推理加速与训练框架",
        "keywords": [
            "vllm", "sglang", "tensorrt-llm", "tensorrt", "flashattention", "flash attention",
            "triton", "megatron-lm", "megatron", "distributed training", "data parallel",
            "tensor parallel", "pipeline parallel", "kv cache", "quantization", "quantize",
            "int8", "int4", "speculative decoding", "llm inference", "serving", "compiler",
            "kernel", "cuda", "算子", "推理加速",
        ],
    },
    {
        "name": "上下文工程与持久化记忆",
        "keywords": [
            "context engineering", "long context", "context window", "context caching",
            "context degradation", "graphrag", "rag", "retrieval augmented", "agent memory",
            "persistent memory", "mem0", "cognee", "memory", "knowledge graph",
            "vector database", "embedding", "记忆", "上下文",
        ],
    },
    {
        "name": "大模型安全与真实评测基准",
        "keywords": [
            "agentic security", "prompt injection", "jailbreak", "adversarial", "safety",
            "red team", "red teaming", "alignment", "swe-bench", "swebench", "gaia",
            "livecodebench", "eval", "evaluation", "benchmark", "评测", "安全", "越狱",
        ],
    },
    {
        "name": "顶级研究者与工程师一手洞察",
        "keywords": [
            "karpathy", "sebastian raschka", "nathan lambert", "simon willison", "shawn wang",
            "swyx", "lilian weng", "blog post", "insight", "深度长文", "技术博客",
        ],
    },
]

# ---------------------------------------------------------------------------
# 质量信号 (用于筛选/排序 "大厂、顶级高校、知名实验室" 论文)
# ---------------------------------------------------------------------------
TOP_INSTITUTIONS = [
    "google", "deepmind", "openai", "anthropic", "microsoft", "nvidia", "meta ai",
    "amazon", "apple", "tesla", "baidu", "alibaba", "tencent", "bytedance", "huawei",
    "mit", "massachusetts institute", "stanford", "berkeley", "carnegie mellon", "cmu",
    "oxford", "cambridge", "eth zurich", "princeton", "cornell", "columbia", "nyu",
    "new york university", "university of washington", "tsinghua", "peking", "zhejiang",
    "shanghai jiao tong", "sjtu", "ustc", "fudan", "nus", "epfl", "university of toronto",
    "mcgill", "waterloo", "illinois", "umich", "ucla", "ucsd", "georgia tech", "caltech",
    "harvard", "yale", "johns hopkins", "umd", "pennsylvania", "duke", "imperial college",
    "max planck", "inria", "kaist", "seoul national", "university of tokyo",
]

TOP_VENUES = [
    "neurips", "icml", "iclr", "cvpr", "iccv", "eccv", "acl", "emnlp", "naacl",
    "icra", "rss", "corl", "siggraph", "nature", "science", "aaai", "ijcai", "jmlr",
    "tacl", "icml", "transaction",
]

FAMOUS_RESEARCHERS = [
    "karpathy", "sebastian raschka", "nathan lambert", "simon willison", "shawn wang",
    "swyx", "lilian weng", "ilya sutskever", "andrew ng", "yann lecun", "geoffrey hinton",
    "yoshua bengio", "fei-fei li", "demis hassabis", "noam shazeer", "jason wei",
    "denny zhou", "thomas wolf", "jim fan", "pieter abbeel", "sergey levine",
    "chelsea finn", "deepak pathak", "sam altman", "dario amodei",
]

# ---------------------------------------------------------------------------
# RSS 源 (厂商官方博客 + 知名研究者, 失败自动跳过)
# ---------------------------------------------------------------------------
RSS_FEEDS = [
    ("OpenAI", "https://openai.com/news/rss.xml"),
    ("Google DeepMind", "https://deepmind.google/blog/rss.xml"),
    ("HuggingFace Blog", "https://huggingface.co/blog/feed.xml"),
    ("Simon Willison", "https://simonwillison.net/atom/everything/"),
    ("Lilian Weng", "https://lilianweng.github.io/index.xml"),
    ("Sebastian Raschka", "https://magazine.sebastianraschka.com/feed"),
]

# 网页搜索查询词(启用 TAVILY_API_KEY 后逐条检索当日新闻)
WEB_SEARCH_QUERIES = [
    q.strip()
    for q in os.environ.get(
        "WEB_SEARCH_QUERIES",
        "OpenAI model announcement,"
        "Anthropic Claude news,"
        "humanoid robot release,"
        "NVIDIA GPU AI chip announcement,"
        "AI coding agent launch",
    ).split(",")
    if q.strip()
]

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class Item:
    title: str
    summary: str
    url: str
    source: str
    published: str = ""
    domains: List[str] = field(default_factory=list)
    quality: int = 0
    flags: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def beijing_now() -> datetime:
    return datetime.now(BEIJING_TZ)


def parse_iso(ts: str) -> Optional[datetime]:
    """解析 ISO 或 RFC822 时间字符串, 统一为 UTC-aware, 失败返回 None。"""
    if not ts:
        return None
    ts = ts.strip()
    dt: Optional[datetime] = None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(ts)
        except (TypeError, ValueError):
            return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "")


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").strip().lower())[:200]


def match_domains(text: str) -> List[str]:
    lowered = (text or "").lower()
    matched: List[str] = []
    for domain in DOMAINS:
        for kw in domain["keywords"]:
            if kw.lower() in lowered:
                matched.append(domain["name"])
                break
    return matched


def item_key(it: Item) -> str:
    if it.url:
        return "u:" + re.sub(r"\?.*$", "", it.url.rstrip("/"))
    return "t:" + hashlib.sha1(normalize_title(it.title).encode("utf-8")).hexdigest()


# 条目分类(报告按此分栏目)
CATEGORY_ORDER = ["论文", "开源项目", "资讯动态"]


def categorize(it: Item) -> str:
    if it.source in ("ArXiv", "OpenAlex", "HuggingFace"):
        return "论文"
    if it.source == "GitHub Trending" or "github.com" in (it.url or "").lower():
        return "开源项目"
    return "资讯动态"


def extract_github_link(text: str) -> str:
    m = re.search(r"(?:https?://)?github\.com/[\w\-./]+", text or "")
    if not m:
        return ""
    link = m.group(0).rstrip(".,;:)")
    if not link.startswith("http"):
        link = "https://" + link
    return link


def group_items(items: List[Item]) -> Dict[str, List[Item]]:
    groups: Dict[str, List[Item]] = {c: [] for c in CATEGORY_ORDER}
    for it in items:
        groups[categorize(it)].append(it)
    return {c: g for c, g in groups.items() if g}


# ---------------------------------------------------------------------------
# 历史查重索引 (持久化到仓库, 用于跨天去重)
# ---------------------------------------------------------------------------
def load_index() -> Dict[str, Dict[str, str]]:
    if not os.path.exists(INDEX_PATH):
        return {}
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_index(index: Dict[str, Dict[str, str]]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def prune_index(index: Dict[str, Dict[str, str]], days: int = INDEX_RETENTION_DAYS) -> Dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    return {k: v for k, v in index.items() if (v.get("first_seen") or "") >= cutoff}


# ---------------------------------------------------------------------------
# 质量评分
# ---------------------------------------------------------------------------
def _extra_text(it: Item, key: str) -> str:
    val = it.extra.get(key, "")
    if isinstance(val, list):
        return " ".join(str(x) for x in val)
    return str(val)


def compute_quality(it: Item) -> int:
    score = 0
    inst = _extra_text(it, "institutions").lower()
    authors = _extra_text(it, "authors").lower()
    venue = _extra_text(it, "venue").lower()
    full = f"{inst} {authors} {venue} {it.title}".lower()

    if any(k in full for k in TOP_INSTITUTIONS):
        score += 3
        it.flags.append("大厂/顶级机构")
    if any(v in venue for v in TOP_VENUES):
        score += 2
        it.flags.append("顶会/顶刊")
    if any(r in full for r in FAMOUS_RESEARCHERS):
        score += 2
        it.flags.append("知名研究者")
    cited = it.extra.get("cited_by_count")
    if isinstance(cited, (int, float)) and cited >= 10:
        score += 1
        it.flags.append(f"引用{cited}")
    score += min(len(it.domains), 2)
    return score


# ---------------------------------------------------------------------------
# 1. 信源采集
# ---------------------------------------------------------------------------
ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom"}


def fetch_arxiv(max_results: int = 120) -> List[Item]:
    """采集 ArXiv cs.CV/cs.RO/cs.AI/cs.LG 最新论文。"""
    url = "https://export.arxiv.org/api/query"
    params = {
        "search_query": "cat:cs.CV OR cat:cs.RO OR cat:cs.AI OR cat:cs.LG",
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "start": 0,
        "max_results": max_results,
    }
    resp = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    items: List[Item] = []
    for entry in root.findall("atom:entry", ARXIV_NS):
        title = (entry.findtext("atom:title", "", ARXIV_NS) or "").strip()
        summary = (entry.findtext("atom:summary", "", ARXIV_NS) or "").strip()
        arxiv_id = (entry.findtext("atom:id", "", ARXIV_NS) or "").split("/abs/")[-1]
        published = entry.findtext("atom:published", "", ARXIV_NS) or ""
        if not title:
            continue
        items.append(Item(
            title=title, summary=summary,
            url=f"https://arxiv.org/abs/{arxiv_id}",
            source="ArXiv", published=published,
        ))
    return items


def reconstruct_abstract(inverted: Optional[Dict[str, List[int]]]) -> str:
    if not inverted:
        return ""
    pos: Dict[int, str] = {}
    for word, idxs in inverted.items():
        for i in idxs:
            pos[i] = word
    return strip_tags(" ".join(pos[i] for i in sorted(pos))).strip()


def fetch_openalex() -> List[Item]:
    """采集 OpenAlex 学术库, 按顶尖机构/顶会/高被引筛选高质量论文。"""
    queries = [
        "embodied intelligence robot manipulation",
        "large language model",
        "world model video generation",
        "3d reconstruction gaussian splatting",
        "autonomous agent tool use",
        "diffusion model",
        "llm inference serving",
    ]
    from_date = (datetime.now(timezone.utc) - timedelta(days=OPENALEX_DAYS)).strftime("%Y-%m-%d")
    items: List[Item] = []
    for q in queries:
        try:
            resp = requests.get(
                "https://api.openalex.org/works",
                params={
                    "search": q,
                    "filter": f"from_publication_date:{from_date}",
                    "sort": "relevance_score:desc",
                    "per-page": 15,
                },
                timeout=HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            for w in resp.json().get("results", []) or []:
                title = w.get("title") or ""
                if not title:
                    continue
                abstract = reconstruct_abstract(w.get("abstract_inverted_index"))
                doi = w.get("doi")
                venue = ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
                institutions = [
                    (inst.get("display_name") or "")
                    for a in (w.get("authorships") or [])
                    for inst in (a.get("institutions") or [])
                ]
                authors = [
                    (a.get("author") or {}).get("display_name") or ""
                    for a in (w.get("authorships") or [])
                ]
                items.append(Item(
                    title=title, summary=abstract, url=doi or w.get("id") or "",
                    source="OpenAlex", published=w.get("publication_date") or "",
                    extra={
                        "cited_by_count": w.get("cited_by_count") or 0,
                        "venue": venue, "institutions": institutions, "authors": authors,
                    },
                ))
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] OpenAlex 查询失败(已跳过 '{q}'): {exc}")
    return items


def fetch_hf_daily_papers() -> List[Item]:
    """采集 HuggingFace Daily Papers。"""
    url = "https://huggingface.co/api/daily_papers"
    resp = requests.get(url, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    items: List[Item] = []
    for raw in data or []:
        paper = raw.get("paper") or {}
        title = paper.get("title") or raw.get("title") or ""
        summary = paper.get("summary") or raw.get("summary") or ""
        paper_id = paper.get("id") or raw.get("id") or ""
        upvotes = raw.get("numUpvotes") or raw.get("upvotes") or 0
        published = raw.get("publishedAt") or raw.get("published") or ""
        if not title:
            continue
        items.append(Item(
            title=title, summary=summary,
            url=f"https://huggingface.co/papers/{paper_id}",
            source="HuggingFace", published=published,
            extra={"upvotes": upvotes},
        ))
    return items


def fetch_github_trending(limit: int = 30) -> List[Item]:
    """采集 GitHub Trending 当日热门仓库(可选, 失败静默降级)。"""
    if not ENABLE_GITHUB_TRENDING:
        return []
    try:
        resp = requests.get(
            "https://github.com/trending?since=daily",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        html = resp.text
        repos = re.findall(
            r'<h2[^>]*>\s*<a[^>]*href="/([A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+)"[^>]*>', html
        )
        descs = re.findall(r'<p class="col-9[^"]*"[^>]*>(.*?)</p>', html, re.S)
        items: List[Item] = []
        seen = set()
        for i, repo in enumerate(repos):
            if repo in seen:
                continue
            seen.add(repo)
            desc = strip_tags(descs[i]) if i < len(descs) else ""
            items.append(Item(
                title=repo, summary=desc.strip(),
                url=f"https://github.com/{repo}", source="GitHub Trending",
            ))
            if len(items) >= limit:
                break
        return items
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] GitHub Trending 采集失败(已跳过): {exc}")
        return []


def _child_text(elem: ET.Element, *names: str) -> str:
    for child in elem:
        tag = child.tag.rsplit("}", 1)[-1].lower()
        if tag in names:
            if child.text and child.text.strip():
                return child.text.strip()
            href = child.get("href")
            if href:
                return href.strip()
    return ""


def fetch_rss(max_per_feed: int = 10) -> List[Item]:
    """采集厂商博客与研究者 RSS(兼容 RSS 2.0 与 Atom)。"""
    items: List[Item] = []
    for name, feed in RSS_FEEDS:
        try:
            resp = requests.get(
                feed,
                headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                    "Accept-Encoding": "gzip, deflate",
                },
                timeout=HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            count = 0
            for node in root.iter():
                tag = node.tag.rsplit("}", 1)[-1].lower()
                if tag not in ("item", "entry"):
                    continue
                title = _child_text(node, "title")
                link = _child_text(node, "link")
                desc = _child_text(node, "description", "summary", "content")
                pub = _child_text(node, "pubdate", "published", "updated")
                if not title:
                    continue
                items.append(Item(
                    title=title, summary=strip_tags(desc).strip()[:400],
                    url=link, source=name, published=pub,
                ))
                count += 1
                if count >= max_per_feed:
                    break
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] RSS 采集失败(已跳过 '{name}'): {exc}")
    return items


def fetch_web_search(max_results: int = 5) -> List[Item]:
    """通过 Tavily 检索当日相关新闻(可选, 需配置 TAVILY_API_KEY)。"""
    if not TAVILY_API_KEY:
        return []
    items: List[Item] = []
    for query in WEB_SEARCH_QUERIES:
        try:
            resp = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query,
                    "topic": "news",
                    "days": 2,
                    "search_depth": "advanced",
                    "max_results": max_results,
                },
                timeout=HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            for r in (data.get("results") or []):
                title = r.get("title") or ""
                url = r.get("url") or ""
                content = (r.get("content") or "")[:400]
                if not title or not url:
                    continue
                items.append(Item(
                    title=title, summary=content, url=url,
                    source="Web Search", published="",
                ))
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] 网页搜索失败(已跳过 '{query}'): {exc}")
    return items


# ---------------------------------------------------------------------------
# 2. 领域过滤 / 质量评分 / 去重
# ---------------------------------------------------------------------------
def filter_and_score(items: List[Item], index: Dict[str, Dict[str, str]]) -> List[Item]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    today = beijing_now().strftime("%Y-%m-%d")
    candidates: List[Item] = []

    for it in items:
        text = f"{it.title} {it.summary}".strip()
        domains = match_domains(text)
        if not domains:
            continue
        it.domains = domains
        it.quality = compute_quality(it)

        # OpenAlex 仅保留高质量(顶级机构/顶会/高被引)结果
        if it.source == "OpenAlex" and it.quality < 3:
            continue

        # 时间回溯过滤(ArXiv 周末停更, 故窗口放宽)
        published_dt = parse_iso(it.published)
        if it.published and published_dt and published_dt < cutoff:
            continue

        # 与历史索引去重(仅排除"非今日"已收录项; 当天重跑仍会重新生成报告)
        seen = index.get(item_key(it))
        if seen and seen.get("first_seen") and seen["first_seen"] < today:
            continue

        candidates.append(it)

    # 标题去重(显示层): 同一标题多源出现时保留高质量版本
    best: Dict[str, Item] = {}
    for it in candidates:
        tkey = normalize_title(it.title)
        existing = best.get(tkey)
        if existing is None or it.quality > existing.quality:
            best[tkey] = it

    # 将本次所有通过过滤的 URL 写入索引(含被标题去重掉的), 避免次日重复收录
    for it in candidates:
        key = item_key(it)
        if key not in index:
            index[key] = {"title": it.title, "first_seen": today, "url": it.url}

    final = list(best.values())
    final.sort(key=lambda x: (x.quality, x.published), reverse=True)
    return final


# ---------------------------------------------------------------------------
# 3. LLM 结构化分析
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "你是一名资深的前沿科技分析师。请对给定的每条科技信息, 严格以 Markdown 输出以下三项内容:\n"
    "1. **核心技术突破**: 一句话点明技术关键点\n"
    "2. **开源情况**: 是否附带代码/项目链接(若条目提供了 github_link, 请直接写明该开源链接)\n"
    "3. **工业落地可行性**: 评级(高/中/低) + 一句简短逻辑依据\n\n"
    "格式要求:\n"
    "- 每个条目一个小节, 标题使用 \"### N. 标题\"(N 为序号, 标题用中文概括)\n"
    "- 三项内容各占一行, 以无序列表呈现\n"
    "- 只基于给定信息, 不要编造; 语言精炼专业"
)


def call_llm(system: str, user: str) -> str:
    url = LLM_BASE_URL.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


def llm_analyze(items: List[Item], category: str = "") -> str:
    if not LLM_API_KEY:
        print("[warn] 未配置 LLM_API_KEY, 跳过 LLM 解读")
        return ""
    subset = items[:MAX_LLM_ITEMS]
    payload_items = [
        {
            "title": i.title,
            "summary": (i.summary or "")[:500],
            "url": i.url,
            "source": i.source,
            "domains": i.domains,
            "github_link": extract_github_link(i.summary),
        }
        for i in subset
    ]
    cat_note = f"以下条目均属于「{category}」类别。" if category else ""
    user = "以下是今日采集到的前沿科技信息。%s 请逐条分析:\n\n%s" % (
        cat_note, json.dumps(payload_items, ensure_ascii=False, indent=2)
    )
    try:
        return call_llm(SYSTEM_PROMPT, user)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] LLM 调用失败, 使用模板降级: {exc}")
        return ""


def fallback_analysis(items: List[Item]) -> str:
    lines: List[str] = []
    for idx, it in enumerate(items[:MAX_LLM_ITEMS], 1):
        lines.append(f"### {idx}. {it.title}")
        snippet = re.sub(r"\s+", " ", it.summary or "").strip()[:160] or "暂无摘要"
        lines.append(f"- **核心技术突破**: {snippet}")
        lines.append(f"- **开源情况**: {'附项目链接 ' + it.url if it.url else '未提供链接'}")
        lines.append("- **工业落地可行性**: 中 (依据: 领域热度较高, 需进一步评估)")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 4. 报告生成与存档
# ---------------------------------------------------------------------------
def build_report(items: List[Item], llm_by_category: Dict[str, str]) -> str:
    now = beijing_now()
    groups = group_items(items)
    lines: List[str] = []
    lines.append(f"# 前沿科技日报 · {now.strftime('%Y-%m-%d')}")
    lines.append("")
    lines.append(f"> 采集时间: {now.strftime('%Y-%m-%d %H:%M')} (北京时间)")
    lines.append("> 数据源: ArXiv / OpenAlex / HuggingFace / GitHub Trending / RSS / Web Search")
    lines.append("")

    counter = Counter(d for i in items for d in i.domains)
    cat_counts = " / ".join(f"{c} {len(groups.get(c, []))}" for c in CATEGORY_ORDER)
    lines.append("## 今日概览")
    lines.append(f"- 相关条目: {len(items)}")
    lines.append(f"- 分类: {cat_counts}")
    lines.append("- 领域分布:")
    for name, cnt in counter.most_common():
        lines.append(f"  - {name}: {cnt}")
    lines.append("")

    lines.append("## LLM 深度解读")
    for cat in CATEGORY_ORDER:
        group = groups.get(cat)
        if not group:
            continue
        analysis = llm_by_category.get(cat) or fallback_analysis(group)
        lines.append(f"### {cat}")
        lines.append(analysis.strip())
        lines.append("")
    if not any(groups.get(c) for c in CATEGORY_ORDER):
        lines.append("_今日无新增内容。_")
        lines.append("")

    lines.append("## 完整条目清单")
    for cat in CATEGORY_ORDER:
        group = groups.get(cat)
        if not group:
            continue
        lines.append(f"### {cat}")
        for idx, it in enumerate(group, 1):
            flag = (" [" + " / ".join(it.flags) + "]") if it.flags else ""
            lines.append(f"{idx}. **{it.title}**  [{it.source}]{flag}")
            if it.url:
                lines.append(f"   - 链接: {it.url}")
            gh = extract_github_link(it.summary)
            if gh and gh.rstrip("/") != (it.url or "").rstrip("/"):
                lines.append(f"   - 开源: {gh}")
            lines.append(f"   - 领域: {' / '.join(it.domains)}")
            if it.summary:
                snippet = re.sub(r"\s+", " ", it.summary).strip()[:220]
                lines.append(f"   - 摘要: {snippet}")
            lines.append("")
    return "\n".join(lines)


def save_report(md: str) -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, beijing_now().strftime("%Y-%m-%d") + ".md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    return path


# ---------------------------------------------------------------------------
# 5. Webhook 推送
# ---------------------------------------------------------------------------
def chunk_text(text: str, size: int) -> List[str]:
    return [text[i:i + size] for i in range(0, len(text), size)]


def feishu_sign(timestamp: str, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def push_feishu_text(md: str) -> bool:
    """降级方案: 分片纯文本消息(有字数上限, 会多条)。"""
    ok = True
    for chunk in chunk_text(md, 1500):
        body: Dict[str, Any] = {"msg_type": "text", "content": {"text": chunk}}
        if FEISHU_SECRET:
            timestamp = str(int(time.time()))
            body["timestamp"] = timestamp
            body["sign"] = feishu_sign(timestamp, FEISHU_SECRET)
        resp = requests.post(FEISHU_WEBHOOK_URL, json=body, timeout=30)
        if resp.status_code != 200:
            ok = False
            print(f"[error] 飞书推送失败 {resp.status_code}: {resp.text[:200]}")
    return ok


# --- 飞书云文档归档(自建应用 docx API) ---
_tenant_token_cache: Dict[str, Any] = {}


def _feishu_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _feishu_err(resp: requests.Response, action: str) -> str:
    """把飞书错误响应格式化为可读信息(含常见原因提示)。"""
    try:
        data = resp.json()
    except ValueError:
        return f"{action}失败 HTTP {resp.status_code}: {resp.text[:200]}"
    code = data.get("code")
    msg = data.get("msg", "")
    hints = {
        "99991400": "应用未发布, 去开放平台「版本管理与发布」发布应用",
        "99991663": "缺少权限, 去应用「权限管理」开通 docx:document / drive:drive",
        "1254046": "folder_token 无效或应用无权访问该文件夹",
        "99990000": "参数或鉴权错误, 检查 App ID / App Secret",
    }.get(str(code), "")
    return f"{action}失败 code={code} msg={msg} {hints}".strip()


def get_tenant_access_token() -> str:
    if _tenant_token_cache.get("token") and _tenant_token_cache.get("expires_at", 0) > time.time():
        return _tenant_token_cache["token"]
    resp = requests.post(
        f"{FEISHU_HOST}/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
        timeout=30,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(_feishu_err(resp, "获取 tenant_access_token"))
    _tenant_token_cache["token"] = data["tenant_access_token"]
    _tenant_token_cache["expires_at"] = time.time() + int(data.get("expire", 7200)) - 300
    return data["tenant_access_token"]


def _text_run(content: str, bold: bool = False, link: Optional[str] = None) -> Dict[str, Any]:
    run: Dict[str, Any] = {"content": content}
    style: Dict[str, Any] = {}
    if bold:
        style["bold"] = True
    if link:
        style["link"] = {"url": link}
    if style:
        run["text_element_style"] = style
    return {"text_run": run}


def _inline_elements(text: str) -> List[Dict[str, Any]]:
    """解析 **加粗** 与 [标题](链接) 为飞书 text_run 元素。"""
    elements: List[Dict[str, Any]] = []
    for part in re.split(r"(\*\*.+?\*\*|\[[^\]]+\]\([^)]+\))", text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            elements.append(_text_run(part[2:-2], bold=True))
        elif part.startswith("["):
            m = re.match(r"\[([^\]]+)\]\(([^)]+)\)", part)
            if m:
                elements.append(_text_run(m.group(1), link=m.group(2)))
            else:
                elements.append(_text_run(part))
        else:
            elements.append(_text_run(part))
    return elements or [_text_run("")]


def _mk_block(block_type: int, field: str, text: str) -> Dict[str, Any]:
    return {
        "block_type": block_type,
        field: {"elements": _inline_elements(text), "style": {}},
    }


def md_to_blocks(md: str) -> List[Dict[str, Any]]:
    """把日报 Markdown 转成飞书 docx 块(标题/文本/列表/引用/代码)。"""
    blocks: List[Dict[str, Any]] = []
    code_buf: List[str] = []
    in_code = False
    for raw in md.split("\n"):
        line = raw.rstrip()
        if line.startswith("```"):
            if in_code:
                blocks.append({
                    "block_type": 14,
                    "code": {"elements": [_text_run("\n".join(code_buf))], "style": {}},
                })
                code_buf = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_buf.append(raw)
            continue
        if not line.strip():
            continue
        if line.startswith("### "):
            blocks.append(_mk_block(5, "heading3", line[4:]))
        elif line.startswith("## "):
            blocks.append(_mk_block(4, "heading2", line[3:]))
        elif line.startswith("# "):
            blocks.append(_mk_block(3, "heading1", line[2:]))
        elif line.startswith("> "):
            blocks.append(_mk_block(15, "quote", line[2:]))
        elif re.match(r"^\s*[-*]\s+", line):
            # 无序列表(容忍缩进), 去掉前导空白与 "- "/"* "
            content = re.sub(r"^\s*[-*]\s+", "", line)
            blocks.append(_mk_block(12, "bullet", content))
        elif re.match(r"^\d+\.\s", line):
            # 有序列表: 保留序号为普通文本, 避免飞书 ordered 块每项都从 1 编号
            blocks.append(_mk_block(2, "text", line))
        else:
            blocks.append(_mk_block(2, "text", line))
    if in_code and code_buf:
        blocks.append({
            "block_type": 14,
            "code": {"elements": [_text_run("\n".join(code_buf))], "style": {}},
        })
    return blocks


def create_docx(token: str, title: str) -> str:
    # 优先归档到指定文件夹, 失败则回退到应用自己的空间
    folders = [FEISHU_DOC_FOLDER_TOKEN] if FEISHU_DOC_FOLDER_TOKEN else []
    folders.append("")
    last_err = ""
    for folder in folders:
        resp = requests.post(
            f"{FEISHU_HOST}/open-apis/docx/v1/documents",
            json={"folder_token": folder, "title": title},
            headers=_feishu_headers(token),
            timeout=60,
        )
        data = resp.json()
        if data.get("code") == 0:
            return data["data"]["document"]["document_id"]
        last_err = _feishu_err(resp, f"创建文档(folder={'<指定文件夹>' if folder else '<应用空间>'})")
        print(f"[warn] {last_err}")
    print("[hint] 若需归档到指定文件夹, 请在飞书里把该应用(机器人)添加为目标文件夹的「可编辑」协作者")
    raise RuntimeError(last_err or "创建飞书文档失败")


def append_docx_blocks(token: str, document_id: str, blocks: List[Dict[str, Any]]) -> None:
    url = f"{FEISHU_HOST}/open-apis/docx/v1/documents/{document_id}/blocks/{document_id}/children"
    for i in range(0, len(blocks), 50):
        resp = requests.post(
            url,
            json={"children": blocks[i:i + 50], "index": -1},
            headers=_feishu_headers(token),
            timeout=60,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(_feishu_err(resp, "写入文档块"))


def set_doc_tenant_readable(token: str, document_id: str) -> None:
    """把文档设为租户内可读(全公司成员可打开), 失败不影响主流程。"""
    try:
        resp = requests.post(
            f"{FEISHU_HOST}/open-apis/drive/v1/permissions/{document_id}/public",
            params={"type": "docx"},
            json={"link_share_entity": "tenant_readable", "external_access": False},
            headers=_feishu_headers(token),
            timeout=30,
        )
        if resp.status_code == 200:
            try:
                data = resp.json()
            except ValueError:
                data = {}
            if data.get("code") not in (0, None):
                print(f"[warn] 设置文档租户权限失败(已忽略): {data}")
        else:
            print(f"[warn] 设置文档租户权限失败(已忽略): HTTP {resp.status_code} {resp.text[:200]}")
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 设置文档租户权限失败(已忽略): {exc}")


def add_doc_collaborator(token: str, document_id: str, member_type: str, member_id: str,
                         perm: str = "full_access") -> bool:
    """给文档添加协作者(使指定用户获得编辑/管理权限)。"""
    try:
        resp = requests.post(
            f"{FEISHU_HOST}/open-apis/drive/v1/permissions/{document_id}/members",
            params={"type": "docx"},
            json={"member_type": member_type, "member_id": member_id, "perm": perm},
            headers=_feishu_headers(token),
            timeout=30,
        )
        data = resp.json()
        if data.get("code") != 0:
            print(f"[warn] 添加文档协作者失败: {_feishu_err(resp, '添加协作者')}")
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 添加文档协作者失败: {exc}")
        return False


def transfer_doc_owner(token: str, document_id: str, member_type: str, member_id: str) -> bool:
    """把文档所有权转让给指定用户(最佳努力, 失败不影响主流程)。"""
    try:
        resp = requests.post(
            f"{FEISHU_HOST}/open-apis/drive/v1/permissions/{document_id}/members/transfer_owner",
            params={"type": "docx"},
            json={"member_type": member_type, "member_id": member_id},
            headers=_feishu_headers(token),
            timeout=30,
        )
        data = resp.json()
        if data.get("code") != 0:
            print(f"[warn] 转让文档所有者失败(已忽略): {_feishu_err(resp, '转让所有者')}")
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 转让文档所有者失败(已忽略): {exc}")
        return False


def push_feishu_doc(md: str) -> Optional[str]:
    """归档日报到飞书云文档并返回文档链接。"""
    token = get_tenant_access_token()
    title = f"前沿科技日报 · {beijing_now().strftime('%Y-%m-%d')}"
    document_id = create_docx(token, title)
    append_docx_blocks(token, document_id, md_to_blocks(md))
    set_doc_tenant_readable(token, document_id)
    # 让指定用户(你)拥有编辑/管理权限, 并把所有权转让给 TA
    owner_type = FEISHU_OWNER_MEMBER_TYPE
    owner_id = FEISHU_OWNER_MEMBER_ID
    if not owner_id and FEISHU_OWNER_EMAIL:
        owner_type = "email"
        owner_id = FEISHU_OWNER_EMAIL
    if owner_id:
        add_doc_collaborator(token, document_id, owner_type, owner_id, "full_access")
        transfer_doc_owner(token, document_id, owner_type, owner_id)
    else:
        print("[info] 未配置 FEISHU_OWNER_MEMBER_ID / FEISHU_OWNER_EMAIL, 文档仍归应用所有")
    return f"{FEISHU_DOC_BASE_URL.rstrip('/')}/docx/{document_id}"


def push_feishu(md: str) -> bool:
    if not FEISHU_WEBHOOK_URL:
        print("[info] 未配置 FEISHU_WEBHOOK_URL, 跳过飞书推送")
        return False
    # 优先: 归档为云文档后只发一个链接(避免消息轰炸)
    if FEISHU_APP_ID and FEISHU_APP_SECRET:
        try:
            doc_url = push_feishu_doc(md)
            body: Dict[str, Any] = {
                "msg_type": "text",
                "content": {"text": f"今日前沿科技日报已归档, 点击查看:\n{doc_url}"},
            }
            if FEISHU_SECRET:
                timestamp = str(int(time.time()))
                body["timestamp"] = timestamp
                body["sign"] = feishu_sign(timestamp, FEISHU_SECRET)
            resp = requests.post(FEISHU_WEBHOOK_URL, json=body, timeout=30)
            ok = resp.status_code == 200
            if not ok:
                print(f"[error] 飞书链接推送失败 {resp.status_code}: {resp.text[:200]}")
            return ok
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] 飞书文档归档失败, 回退为分片文本: {exc}")
    return push_feishu_text(md)


def dingtalk_sign(timestamp: str, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        secret.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    return urllib.parse.quote_plus(base64.b64encode(hmac_code))


def push_dingtalk(md: str) -> bool:
    if not DINGTALK_WEBHOOK_URL:
        print("[info] 未配置 DINGTALK_WEBHOOK_URL, 跳过钉钉推送")
        return False
    url = DINGTALK_WEBHOOK_URL
    if DINGTALK_SECRET:
        timestamp = str(round(time.time() * 1000))
        sign = dingtalk_sign(timestamp, DINGTALK_SECRET)
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}timestamp={timestamp}&sign={sign}"
    ok = True
    for chunk in chunk_text(md, 3000):
        resp = requests.post(
            url, json={"msgtype": "text", "text": {"content": chunk}}, timeout=30
        )
        if resp.status_code != 200:
            ok = False
            print(f"[error] 钉钉推送失败 {resp.status_code}: {resp.text[:200]}")
    return ok


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> None:
    print(f"[info] 开始采集, 北京时间: {beijing_now().strftime('%Y-%m-%d %H:%M:%S')}")

    index = load_index()
    print(f"[info] 历史索引条目数: {len(index)}")

    raw: List[Item] = []
    sources = [
        ("ArXiv", fetch_arxiv),
        ("OpenAlex", fetch_openalex),
        ("HuggingFace", fetch_hf_daily_papers),
        ("GitHub Trending", fetch_github_trending),
        ("RSS", fetch_rss),
        ("Web Search", fetch_web_search),
    ]
    for name, fn in sources:
        before = len(raw)
        try:
            raw += fn()
            print(f"[info] {name} 采集 {len(raw) - before} 条")
        except Exception as exc:  # noqa: BLE001
            print(f"[error] {name} 采集失败: {exc}")

    new_items = filter_and_score(raw, index)
    print(f"[info] 过滤/查重后新增 {len(new_items)} 条")

    if new_items:
        new_items = new_items[:MAX_REPORT_ITEMS]
        groups = group_items(new_items)
        llm_by_category: Dict[str, str] = {}
        for cat in CATEGORY_ORDER:
            if groups.get(cat):
                llm_by_category[cat] = llm_analyze(groups[cat], cat)
        report = build_report(new_items, llm_by_category)
    else:
        report = (
            f"# 前沿科技日报 · {beijing_now().strftime('%Y-%m-%d')}\n\n"
            "今日无新增匹配条目(可能为周末 ArXiv 停更, 或历史已收录)。\n"
        )

    if DRY_RUN:
        print("[dry-run] 以下为报告内容:\n")
        print(report)
        print("\n[dry-run] 未写入文件、未推送、未更新索引")
        return

    # 持久化: 更新索引 + 存档日报
    save_index(prune_index(index))
    report_path = save_report(report)
    print(f"[info] 日报已存档: {report_path}")

    if not new_items:
        print("[info] 无新增条目, 跳过 Webhook 推送")
        return

    feishu_ok = push_feishu(report)
    dingtalk_ok = push_dingtalk(report)
    print(f"[info] 完成: 飞书={feishu_ok}, 钉钉={dingtalk_ok}")


if __name__ == "__main__":
    main()
