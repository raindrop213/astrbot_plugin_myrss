"""
网站格式化规则

通过域名匹配选择对应的 Formatter，未匹配的使用默认规则。

添加新网站只需 2 步：
  1. 继承 DefaultFormatter，重写 _extract_fields() 指定字段取值
  2. 在底部 FORMATTERS 中注册域名

可重写的方法：
  - _extract_fields(entry): 指定 title/link/description/extra 从哪取
  - get_chan_title(feed, url): 自定义频道名称
"""

import re
import time
from html import unescape
from dataclasses import dataclass
from urllib.parse import urlparse, parse_qs, unquote


@dataclass
class RSSItem:
    """RSS 条目（统一结构）"""
    chan_title: str
    title: str
    link: str
    description: str
    pub_date: str
    pub_date_timestamp: int
    extra: str = ""


def strip_html(text: str) -> str:
    """去除 HTML 标签和多余空白，返回纯文本"""
    if not text:
        return ""
    text = unescape(text)
    text = re.sub(r'<[^>]+>', '', text)
    # 去掉只含空白的行，合并连续换行
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


class DefaultFormatter:
    """默认格式化器，适用于标准 RSS/Atom 源"""

    def get_chan_title(self, feed, url: str) -> str:
        """频道名称，默认用 feed 自带的标题"""
        return feed.feed.get("title", "未知频道")

    def _extract_fields(self, entry) -> dict:
        """从 feedparser entry 中提取字段，自定义网站重写此方法即可

        返回 dict: title, link, description, extra
        """
        return {
            "title": entry.get("title", "无标题"),
            "link": entry.get("link", ""),
            "description": entry.get("description", "") or entry.get("summary", ""),
            "extra": "",
        }

    def parse_entry(self, entry, chan_title: str, desc_max_len: int = 200) -> RSSItem:
        """完整解析一条 entry 为 RSSItem（一般不需要重写）"""
        fields = self._extract_fields(entry)

        # 时间解析
        pub_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
        pub_ts = int(time.mktime(pub_parsed)) if pub_parsed else 0
        pub_date = time.strftime("%Y-%m-%d %H:%M:%S", pub_parsed) if pub_parsed else ""

        # 描述处理
        desc = strip_html(fields["description"])
        if desc and len(desc) > desc_max_len:
            desc = desc[:desc_max_len] + "..."

        return RSSItem(
            chan_title=chan_title,
            title=fields["title"],
            link=fields["link"],
            description=desc,
            pub_date=pub_date,
            pub_date_timestamp=pub_ts,
            extra=fields.get("extra", ""),
        )


def _get_url_param(url: str, key: str) -> str:
    """从 URL 中提取指定查询参数"""
    try:
        params = parse_qs(urlparse(url).query)
        values = params.get(key, [])
        return values[0] if values else ""
    except Exception:
        return ""


# ==================== 自定义网站规则 ====================


class NyaaFormatter(DefaultFormatter):
    """nyaa.si — 用 guid 作页面链接，从 URL 的 q 参数取频道名"""

    def get_chan_title(self, feed, url: str) -> str:
        q = _get_url_param(url, "q")
        return f"Nyaa | {q}" if q else "Nyaa"

    def _extract_fields(self, entry) -> dict:
        return {
            "title": entry.get("title", "无标题"),
            "link": entry.get("id", "") or entry.get("link", ""),
            "description": "",
            "extra": entry.get("link", ""),
        }


class DmhyFormatter(DefaultFormatter):
    """动漫花园 — 从 URL 的 keyword 参数生成频道名"""

    def get_chan_title(self, feed, url: str) -> str:
        kw = _get_url_param(url, "keyword")
        return f'DMHY - {kw}' if kw else feed.feed.get("title", "動漫花園")

    def _extract_fields(self, entry) -> dict:
        magnet = ""
        for enc in entry.get("enclosures", []):
            href = enc.get("href", "")
            if href.startswith("magnet:"):
                magnet = href.split("&tr=")[0]
                break
        return {
            "title": entry.get("title", "无标题"),
            "link": entry.get("link", ""),
            "description": entry.get("description", ""),
            "extra": magnet,
        }


class AcgnxFormatter(DmhyFormatter):
    """末日動漫資源庫 (AcgnX) — DMHY 镜像站，结构相同"""

    def get_chan_title(self, feed, url: str) -> str:
        kw = _get_url_param(url, "keyword")
        return f'AcgnX - {kw}' if kw else feed.feed.get("title", "末日動漫資源庫")


class MikanFormatter(DefaultFormatter):
    """蜜柑计划"""

    def _extract_fields(self, entry) -> dict:
        torrent = ""
        for enc in entry.get("enclosures", []):
            href = enc.get("href", "")
            if href.endswith(".torrent"):
                torrent = href
                break
        return {
            "title": entry.get("title", "无标题"),
            "link": entry.get("link", ""),
            "description": entry.get("description", ""),
            "extra": torrent,
        }


# ==================== 域名 -> 格式化器 映射 ====================

FORMATTERS: dict[str, type[DefaultFormatter]] = {
    "nyaa.si": NyaaFormatter,
    "share.dmhy.org": DmhyFormatter,
    "share.acgnx.se": AcgnxFormatter,
    "mikan.tangbai.cc": MikanFormatter,
}

_default = DefaultFormatter()


def get_formatter(url: str) -> DefaultFormatter:
    """根据 URL 域名获取对应的格式化器，未匹配则返回默认"""
    try:
        domain = urlparse(url).netloc.lower()
        for key, cls in FORMATTERS.items():
            if domain == key or domain.endswith("." + key):
                return cls()
    except Exception:
        pass
    return _default
