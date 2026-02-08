"""
网站格式化规则

通过域名匹配选择对应的 Formatter，未匹配的使用默认规则。

添加新网站只需 2 步：
  1. 继承 DefaultFormatter，重写 _extract_fields() 指定字段取值
  2. 在底部 FORMATTERS 中注册域名

_extract_fields 返回 4 个字段：
  - title:       标题
  - link:        链接
  - description: 描述（会自动去 HTML 并截断）
  - extra:       额外信息（纯文本，留空则不显示）
"""

import re
import time
from html import unescape
from dataclasses import dataclass
from urllib.parse import urlparse


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
    """去除 HTML 标签，返回纯文本"""
    if not text:
        return ""
    text = unescape(text)
    text = re.sub(r'<[^>]+>', '', text)
    return re.sub(r'\n{2,}', '\n', text).strip()


class DefaultFormatter:
    """默认格式化器，适用于标准 RSS/Atom 源"""

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


# ==================== 自定义网站规则 ====================


class NyaaFormatter(DefaultFormatter):
    """nyaa.si — 用 guid 作页面链接"""

    def _extract_fields(self, entry) -> dict:
        return {
            "title": entry.get("title", "无标题"),
            "link": entry.get("id", "") or entry.get("link", ""),  # guid = 页面链接
            "description": "",  # nyaa description 与标题重复，跳过
            "extra": entry.get("link", ""),  # 种子下载链接
        }


class DmhyFormatter(DefaultFormatter):
    """动漫花园"""

    def _extract_fields(self, entry) -> dict:
        # 从 enclosure 提取磁力链接（去掉冗长的 tracker 列表）
        magnet = ""
        for enc in entry.get("enclosures", []):
            href = enc.get("href", "")
            if href.startswith("magnet:"):
                magnet = href.split("&tr=")[0]  # 只保留 hash 部分
                break
        return {
            "title": entry.get("title", "无标题"),
            "link": entry.get("link", ""),
            "description": entry.get("description", ""),
            "extra": magnet,
        }


class MikanFormatter(DefaultFormatter):
    """蜜柑计划"""

    def _extract_fields(self, entry) -> dict:
        # 从 enclosure 提取种子下载链接
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
# 添加新网站：在此注册域名和对应的 Formatter 类

FORMATTERS: dict[str, type[DefaultFormatter]] = {
    "nyaa.si": NyaaFormatter,
    "share.dmhy.org": DmhyFormatter,
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
