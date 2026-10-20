"""联网能力 —— 搜索、抓网页、抓热点

给机器人真正的"信息来源"：
1. web_search：多引擎兜底搜索（DuckDuckGo → Bing → 搜狗），拿标题+摘要
2. web_fetch：抓网页正文（去标签、截断）
3. hot_topics：抓国内/国外热点（HN、V2EX、GitHub Trending、B站热搜、微博热搜）
4. 结果带缓存（默认 30 分钟），避免频繁请求被封

原则：全部 try/except 兜底，单个源挂了不影响其它源。
"""

import re
import time
import json
import asyncio
import logging
import httpx

logger = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _strip_html(html: str, max_chars: int = 3000) -> str:
    """粗暴但有效的 HTML 转文本"""
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = (html.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&#39;", "'"))
    html = re.sub(r"\s+", " ", html).strip()
    return html[:max_chars]


class WebClient:
    def __init__(self, db=None, cache_ttl: int = 1800, timeout: int = 20):
        self.db = db
        self.cache_ttl = cache_ttl
        self.timeout = httpx.Timeout(float(timeout), connect=8.0)
        self._mem_cache: dict[str, tuple[float, str]] = {}

    # ---------- 缓存 ----------

    async def _cache_get(self, key: str) -> str | None:
        now = time.time()
        if key in self._mem_cache:
            ts, val = self._mem_cache[key]
            if now - ts < self.cache_ttl:
                return val
        if self.db:
            try:
                row = await self.db.fetchone(
                    "SELECT payload, created_at FROM web_cache WHERE key = ?", (key,)
                )
                if row and now - row["created_at"] < self.cache_ttl:
                    return row["payload"]
            except Exception:
                pass
        return None

    async def _cache_set(self, key: str, val: str):
        self._mem_cache[key] = (time.time(), val)
        if self.db:
            try:
                await self.db.execute(
                    """INSERT INTO web_cache (key, payload, created_at) VALUES (?, ?, ?)
                       ON CONFLICT(key) DO UPDATE SET payload=excluded.payload,
                           created_at=excluded.created_at""",
                    (key, val, int(time.time())),
                )
            except Exception:
                pass
        # 控制内存缓存大小
        if len(self._mem_cache) > 200:
            oldest = sorted(self._mem_cache.items(), key=lambda x: x[1][0])[:50]
            for k, _ in oldest:
                self._mem_cache.pop(k, None)

    # ---------- HTTP ----------

    async def _get(self, url: str, headers: dict = None, as_json: bool = False,
                   max_chars: int = 200000):
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as c:
            r = await c.get(url, headers=headers or HEADERS)
            r.raise_for_status()
            if as_json:
                return r.json()
            return r.text[:max_chars]

    # ---------- 搜索 ----------

    async def search(self, query: str, limit: int = 5) -> list[dict]:
        """多引擎搜索，返回 [{title, url, snippet}]"""
        key = f"search:{query}:{limit}"
        cached = await self._cache_get(key)
        if cached:
            try:
                return json.loads(cached)
            except Exception:
                pass

        results = []
        for fetcher in (self._search_ddg, self._search_bing, self._search_sogou):
            try:
                results = await fetcher(query, limit)
                if results:
                    break
            except Exception as e:
                logger.debug(f"搜索源失败 {fetcher.__name__}: {e}")
                continue

        if results:
            await self._cache_set(key, json.dumps(results, ensure_ascii=False))
        return results

    async def _search_ddg(self, q: str, limit: int) -> list[dict]:
        html = await self._get("https://html.duckduckgo.com/html/", headers={
            **HEADERS,
        })
        # DDG HTML 需要 POST，改用 lite 版本 GET
        html = await self._get("https://lite.duckduckgo.com/lite/?q=" + httpx.QueryParams({"q": q}).__str__())
        out = []
        # lite 版结果：<a rel="nofollow" href="URL" class='result-link'>TITLE</a>
        for m in re.finditer(
            r'<a[^>]+href="([^"]+)"[^>]*class=[\'"]result-link[\'"][^>]*>(.*?)</a>',
            html, re.S | re.I
        ):
            url, title = m.group(1), _strip_html(m.group(2), 200)
            if title and url.startswith("http"):
                out.append({"title": title, "url": url, "snippet": ""})
            if len(out) >= limit:
                break
        return out

    async def _search_bing(self, q: str, limit: int) -> list[dict]:
        from urllib.parse import quote
        html = await self._get(f"https://www.bing.com/search?q={quote(q)}&setlang=zh-CN")
        out = []
        for m in re.finditer(r'(?is)<li class="b_algo".*?</li>', html):
            block = m.group(0)
            lm = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
            if not lm:
                continue
            url = lm.group(1)
            title = _strip_html(lm.group(2), 150)
            sm = re.search(r'(?is)<p[^>]*>(.*?)</p>', block)
            snippet = _strip_html(sm.group(1), 200) if sm else ""
            out.append({"title": title, "url": url, "snippet": snippet})
            if len(out) >= limit:
                break
        return out

    async def _search_sogou(self, q: str, limit: int) -> list[dict]:
        from urllib.parse import quote
        html = await self._get(f"https://www.sogou.com/web?query={quote(q)}")
        out = []
        for m in re.finditer(r'(?is)<h3[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html):
            url, title = m.group(1), _strip_html(m.group(2), 150)
            if not title:
                continue
            if url.startswith("/"):
                url = "https://www.sogou.com" + url
            out.append({"title": title, "url": url, "snippet": ""})
            if len(out) >= limit:
                break
        return out

    # ---------- 抓网页 ----------

    async def fetch(self, url: str, max_chars: int = 2500) -> str:
        key = f"fetch:{url}:{max_chars}"
        cached = await self._cache_get(key)
        if cached:
            return cached
        try:
            html = await self._get(url)
            text = _strip_html(html, max_chars)
            await self._cache_set(key, text)
            return text
        except Exception as e:
            return f"抓取失败: {e}"

    # ---------- 热点 ----------

    async def hot_topics(self, sources: list[str] = None, limit: int = 6) -> dict:
        """聚合热点。返回 {源名称: [标题...]}"""
        sources = sources or ["hn", "v2ex", "github", "bilibili", "weibo"]
        key = "hot:" + ",".join(sorted(sources)) + f":{limit}"
        cached = await self._cache_get(key)
        if cached:
            try:
                return json.loads(cached)
            except Exception:
                pass

        tasks = {}
        if "hn" in sources:
            tasks["HackerNews"] = asyncio.create_task(self._hot_hn(limit))
        if "v2ex" in sources:
            tasks["V2EX"] = asyncio.create_task(self._hot_v2ex(limit))
        if "github" in sources:
            tasks["GitHub Trending"] = asyncio.create_task(self._hot_github(limit))
        if "bilibili" in sources:
            tasks["B站热搜"] = asyncio.create_task(self._hot_bilibili(limit))
        if "weibo" in sources:
            tasks["微博热搜"] = asyncio.create_task(self._hot_weibo(limit))

        out = {}
        for name, task in tasks.items():
            try:
                res = await task
                if res:
                    out[name] = res
            except Exception as e:
                logger.debug(f"热点源 {name} 失败: {e}")

        if out:
            await self._cache_set(key, json.dumps(out, ensure_ascii=False))
        return out

    async def _hot_hn(self, limit: int) -> list[str]:
        ids = await self._get("https://hacker-news.firebaseio.com/v0/topstories.json",
                              as_json=True)
        out = []
        for sid in ids[:limit]:
            try:
                item = await self._get(
                    f"https://hacker-news.firebaseio.com/v0/item/{sid}.json", as_json=True)
                if item.get("title"):
                    out.append(item["title"][:120])
            except Exception:
                continue
        return out

    async def _hot_v2ex(self, limit: int) -> list[str]:
        data = await self._get("https://www.v2ex.com/api/topics/hot.json", as_json=True)
        return [d["title"][:120] for d in data[:limit] if d.get("title")]

    async def _hot_github(self, limit: int) -> list[str]:
        html = await self._get("https://github.com/trending?since=daily")
        repos = re.findall(r'<h2[^>]*class="h3 lh-condensed"[^>]*>\s*<a[^>]+href="(/[^"]+)"', html)
        out = []
        for r in repos[:limit]:
            out.append(r.strip("/").replace("/", " / "))
        return out

    async def _hot_bilibili(self, limit: int) -> list[str]:
        data = await self._get(
            "https://api.bilibili.com/x/web-interface/search/square?limit=20", as_json=True)
        trending = (data.get("data") or {}).get("trending") or {}
        items = trending.get("list") or []
        return [i.get("keyword", "")[:60] for i in items[:limit] if i.get("keyword")]

    async def _hot_weibo(self, limit: int) -> list[str]:
        data = await self._get("https://weibo.com/ajax/side/hotSearch", as_json=True)
        rt = (data.get("data") or {}).get("realtime") or []
        return [i.get("word", "")[:60] for i in rt[:limit] if i.get("word")]
