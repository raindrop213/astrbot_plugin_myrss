import os
import json
import time
import re
import aiohttp
from html import unescape
from dataclasses import dataclass
from typing import List, Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import feedparser

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp

try:
    from .format_handler import format_item
except ImportError:
    from format_handler import format_item

DATA_FILE = "data/astrbot_plugin_myrss_data.json"


@dataclass
class RSSItem:
    """RSS 条目数据"""
    chan_title: str
    title: str
    link: str
    description: str
    pub_date: str
    pub_date_timestamp: int


@register("astrbot_plugin_myrss", "YourName", "简单 RSS 订阅插件，支持 cron 定时推送", "1.0.0")
class MyRSSPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.context = context
        self.config = config or {}
        self.data: dict = self._load_data()
        self.scheduler = AsyncIOScheduler()

    @property
    def max_items_per_get(self) -> int:
        return self.config.get("max_items_per_get", 5)

    @property
    def max_items_per_poll(self) -> int:
        return self.config.get("max_items_per_poll", 5)

    @property
    def desc_max_len(self) -> int:
        return self.config.get("description_max_length", 200)

    async def initialize(self):
        """插件初始化：启动定时任务调度器"""
        self.scheduler.start()
        self._refresh_scheduler()
        logger.info("MyRSS 插件初始化完成")

    async def terminate(self):
        """插件销毁：关闭调度器"""
        self.scheduler.shutdown(wait=False)

    # ==================== 数据持久化 ====================

    def _load_data(self) -> dict:
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                logger.error("RSS: 数据文件损坏，使用空数据")
        return {}

    def _save_data(self):
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    # ==================== RSS 拉取与解析 ====================

    @staticmethod
    def _strip_html(text: str) -> str:
        if not text:
            return ""
        text = unescape(text)
        text = re.sub(r'<[^>]+>', '', text)
        return re.sub(r'\n{2,}', '\n', text).strip()

    async def _fetch_feed_text(self, url: str) -> Optional[str]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            async with aiohttp.ClientSession(
                trust_env=True, connector=connector, timeout=timeout, headers=headers
            ) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.warning(f"RSS: {url} 返回 {resp.status}")
                        return None
                    return await resp.text()
        except Exception as e:
            logger.warning(f"RSS: 请求失败 {url}: {e}")
            return None

    async def _poll_rss(
        self, url: str, max_items: int = 5, after_ts: int = 0
    ) -> List[RSSItem]:
        text = await self._fetch_feed_text(url)
        if not text:
            return []

        feed = feedparser.parse(text)
        chan_title = feed.feed.get("title", "未知频道")
        items: List[RSSItem] = []
        max_desc = self.desc_max_len

        for entry in feed.entries:
            title = entry.get("title", "无标题")
            link = entry.get("link", "")
            desc_raw = entry.get("description", "") or entry.get("summary", "")
            description = self._strip_html(desc_raw)
            if max_desc > 0 and len(description) > max_desc:
                description = description[:max_desc] + "..."

            pub_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
            if pub_parsed:
                pub_ts = int(time.mktime(pub_parsed))
                pub_date_str = time.strftime("%Y-%m-%d %H:%M:%S", pub_parsed)
            else:
                pub_ts = 0
                pub_date_str = ""

            if pub_ts > after_ts:
                items.append(
                    RSSItem(chan_title, title, link, description, pub_date_str, pub_ts)
                )
                if len(items) >= max_items:
                    break

        return items

    # ==================== 格式化 ====================

    def _format_item(self, url: str, item: RSSItem) -> str:
        """使用 format_handler 格式化单条 RSS 条目"""
        return format_item(
            url=url,
            chan_title=item.chan_title,
            title=item.title,
            link=item.link,
            description=item.description,
            pub_date=item.pub_date,
        )

    # ==================== 定时任务 ====================

    @staticmethod
    def _parse_cron(cron_expr: str) -> dict:
        parts = cron_expr.split()
        if len(parts) != 5:
            raise ValueError(f"cron 表达式需要 5 个字段，当前有 {len(parts)} 个")
        return {
            "minute": parts[0],
            "hour": parts[1],
            "day": parts[2],
            "month": parts[3],
            "day_of_week": parts[4],
        }

    @staticmethod
    def _validate_cron(cron_expr: str):
        parts = cron_expr.split()
        if len(parts) != 5:
            raise ValueError(f"cron 表达式需要 5 个字段，当前有 {len(parts)} 个")
        CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
        )

    def _refresh_scheduler(self):
        self.scheduler.remove_all_jobs()
        for url, info in self.data.items():
            for user, sub in info.get("subscribers", {}).items():
                try:
                    self.scheduler.add_job(
                        self._cron_callback,
                        "cron",
                        **self._parse_cron(sub["cron_expr"]),
                        args=[url, user],
                    )
                except Exception as e:
                    logger.warning(f"RSS: 添加任务失败 {url}: {e}")

    async def _cron_callback(self, url: str, user: str):
        if url not in self.data or user not in self.data[url].get("subscribers", {}):
            return

        sub = self.data[url]["subscribers"][user]
        last_update = sub.get("last_update", 0)

        rss_items = await self._poll_rss(url, max_items=self.max_items_per_poll, after_ts=last_update)
        if not rss_items:
            return

        # 合并为一条消息推送
        parts = [self._format_item(url, item) for item in rss_items]
        text = "\n\n".join(parts)

        chain = MessageChain(chain=[Comp.Plain(text)])
        await self.context.send_message(user, chain)

        # 更新最后拉取时间戳
        max_ts = max(item.pub_date_timestamp for item in rss_items)
        if max_ts > last_update:
            sub["last_update"] = max_ts
        sub["latest_link"] = rss_items[0].link
        self._save_data()

    # ==================== 用户指令 ====================

    @filter.command_group("rss", alias={"RSS"})
    def rss(self):
        """RSS 订阅管理

        子命令: add, list, remove, get

        cron 表达式 (5段): 分 时 日 月 星期
        示例: 0 18 * * * = 每天 18:00
        """
        pass

    @rss.command("add")
    async def add(
        self,
        event: AstrMessageEvent,
        url: str,
        minute: str,
        hour: str,
        day: str,
        month: str,
        day_of_week: str,
    ):
        """添加 RSS 订阅

        Args:
            url: RSS Feed 地址
            minute: cron 分钟
            hour: cron 小时
            day: cron 日期
            month: cron 月份
            day_of_week: cron 星期
        """
        cron_expr = f"{minute} {hour} {day} {month} {day_of_week}"
        user = event.unified_msg_origin

        try:
            self._validate_cron(cron_expr)
        except Exception as e:
            yield event.plain_result(f"cron 表达式无效: {e}")
            return

        text = await self._fetch_feed_text(url)
        if text is None:
            yield event.plain_result(f"无法访问该 RSS 地址: {url}")
            return

        feed = feedparser.parse(text)
        if feed.bozo and not feed.entries:
            yield event.plain_result("该地址不是有效的 RSS/Atom 源。")
            return

        chan_title = feed.feed.get("title", "未知频道")

        # 记录最新条目时间戳作为起点
        latest_ts = int(time.time())
        latest_link = ""
        if feed.entries:
            entry = feed.entries[0]
            parsed_time = entry.get("published_parsed") or entry.get("updated_parsed")
            if parsed_time:
                latest_ts = int(time.mktime(parsed_time))
            latest_link = entry.get("link", "")

        if url not in self.data:
            self.data[url] = {
                "info": {"title": chan_title},
                "subscribers": {},
            }
        self.data[url]["subscribers"][user] = {
            "cron_expr": cron_expr,
            "last_update": latest_ts,
            "latest_link": latest_link,
        }
        self._save_data()
        self._refresh_scheduler()

        yield event.plain_result(
            f"订阅成功!\n频道: {chan_title}\n定时: {cron_expr}"
        )

    @rss.command("list")
    async def list_subs(self, event: AstrMessageEvent):
        """列出当前会话的所有 RSS 订阅"""
        user = event.unified_msg_origin
        subs = []
        for url, info in self.data.items():
            if user in info.get("subscribers", {}):
                subs.append((url, info))

        if not subs:
            yield event.plain_result("当前没有任何 RSS 订阅。")
            return

        lines = ["当前订阅列表:"]
        for i, (url, info) in enumerate(subs):
            cron = info["subscribers"][user]["cron_expr"]
            title = info["info"]["title"]
            lines.append(f"{i}. {title}\n   {url}\n   定时: {cron}")
        yield event.plain_result("\n".join(lines))

    @rss.command("remove")
    async def remove_sub(self, event: AstrMessageEvent, idx: int):
        """删除一个 RSS 订阅

        Args:
            idx: 订阅索引，可通过 /rss list 查看
        """
        user = event.unified_msg_origin
        subs_urls = [
            url for url, info in self.data.items()
            if user in info.get("subscribers", {})
        ]
        if idx < 0 or idx >= len(subs_urls):
            yield event.plain_result("索引无效，请使用 /rss list 查看。")
            return

        url = subs_urls[idx]
        title = self.data[url]["info"]["title"]
        del self.data[url]["subscribers"][user]

        if not self.data[url]["subscribers"]:
            del self.data[url]

        self._save_data()
        self._refresh_scheduler()
        yield event.plain_result(f"已取消订阅: {title}")

    @rss.command("get")
    async def get_latest(self, event: AstrMessageEvent, idx: int):
        """获取指定订阅的最新内容

        Args:
            idx: 订阅索引，可通过 /rss list 查看
        """
        user = event.unified_msg_origin
        subs_urls = [
            url for url, info in self.data.items()
            if user in info.get("subscribers", {})
        ]
        if idx < 0 or idx >= len(subs_urls):
            yield event.plain_result("索引无效，请使用 /rss list 查看。")
            return

        url = subs_urls[idx]
        rss_items = await self._poll_rss(url, max_items=self.max_items_per_get, after_ts=0)
        if not rss_items:
            yield event.plain_result("暂无内容。")
            return

        # 合并为一条消息返回
        parts = [self._format_item(url, item) for item in rss_items]
        yield event.plain_result("\n\n".join(parts))
