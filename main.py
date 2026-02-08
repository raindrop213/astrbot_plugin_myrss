import os
import json
import time
import re
import aiohttp
import logging
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
    def __init__(self, context: Context):
        super().__init__(context)
        self.context = context
        self.data: dict = self._load_data()
        self.scheduler = AsyncIOScheduler()

    async def initialize(self):
        """插件初始化：启动定时任务调度器"""
        self.scheduler.start()
        self._refresh_scheduler()
        logger.info("MyRSS 插件初始化完成")

    async def terminate(self):
        """插件销毁：关闭调度器"""
        self.scheduler.shutdown(wait=False)
        logger.info("MyRSS 插件已停止")

    # ==================== 数据持久化 ====================

    def _load_data(self) -> dict:
        """从 JSON 文件加载订阅数据"""
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                logger.error("RSS: 数据文件损坏，使用空数据")
        return {}

    def _save_data(self):
        """保存订阅数据到 JSON 文件"""
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    # ==================== RSS 拉取与解析 ====================

    @staticmethod
    def _strip_html(text: str) -> str:
        """去除 HTML 标签，返回纯文本"""
        if not text:
            return ""
        text = unescape(text)
        text = re.sub(r'<[^>]+>', '', text)
        return re.sub(r'\n{2,}', '\n', text).strip()

    async def _fetch_feed_text(self, url: str) -> Optional[str]:
        """异步请求 RSS 源，返回响应文本"""
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
                        logger.error(f"RSS: 站点 {url} 返回状态码 {resp.status}")
                        return None
                    return await resp.text()
        except Exception as e:
            logger.error(f"RSS: 请求 {url} 失败: {e}")
            return None

    async def _poll_rss(
        self, url: str, max_items: int = 5, after_ts: int = 0
    ) -> List[RSSItem]:
        """拉取 RSS 并解析，返回比 after_ts 更新的条目列表"""
        text = await self._fetch_feed_text(url)
        if not text:
            return []

        feed = feedparser.parse(text)
        chan_title = feed.feed.get("title", "未知频道")
        items: List[RSSItem] = []

        for entry in feed.entries:
            title = entry.get("title", "无标题")
            link = entry.get("link", "")
            desc_raw = entry.get("description", "") or entry.get("summary", "")
            description = self._strip_html(desc_raw)
            if len(description) > 200:
                description = description[:200] + "..."

            # 解析发布时间
            pub_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
            if pub_parsed:
                pub_ts = int(time.mktime(pub_parsed))
                pub_date_str = time.strftime("%Y-%m-%d %H:%M:%S", pub_parsed)
            else:
                pub_ts = 0
                pub_date_str = ""

            # 只保留比上次更新时间戳更新的条目
            if pub_ts > after_ts:
                items.append(
                    RSSItem(chan_title, title, link, description, pub_date_str, pub_ts)
                )
                if len(items) >= max_items:
                    break

        return items

    # ==================== 定时任务 ====================

    @staticmethod
    def _parse_cron(cron_expr: str) -> dict:
        """将 5 段 cron 表达式解析为 dict"""
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
        """验证 cron 表达式是否合法，不合法则抛出异常"""
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
        """根据当前订阅数据，刷新全部定时任务"""
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
                    logger.error(f"RSS: 添加定时任务失败 {url} -> {user}: {e}")
        logger.info(f"RSS: 已刷新定时任务，共 {len(self.scheduler.get_jobs())} 个")

    async def _cron_callback(self, url: str, user: str):
        """定时任务回调：拉取 RSS 新条目并推送"""
        if url not in self.data or user not in self.data[url].get("subscribers", {}):
            return

        sub = self.data[url]["subscribers"][user]
        last_update = sub.get("last_update", 0)

        rss_items = await self._poll_rss(url, max_items=5, after_ts=last_update)
        if not rss_items:
            logger.debug(f"RSS: {url} 无更新 -> {user}")
            return

        # 逐条推送
        for item in rss_items:
            text = (
                f"[RSS] {item.chan_title}\n"
                f"标题: {item.title}\n"
                f"链接: {item.link}\n"
            )
            if item.pub_date:
                text += f"时间: {item.pub_date}\n"
            text += f"---\n{item.description}"

            chain = MessageChain(chain=[Comp.Plain(text)])
            await self.context.send_message(user, chain)

        # 更新最后拉取时间戳
        max_ts = max(item.pub_date_timestamp for item in rss_items)
        if max_ts > last_update:
            sub["last_update"] = max_ts
        sub["latest_link"] = rss_items[0].link
        self._save_data()
        logger.info(f"RSS: {url} 推送 {len(rss_items)} 条 -> {user}")

    # ==================== 用户指令 ====================

    @filter.command_group("rss", alias={"RSS"})
    def rss(self):
        """RSS 订阅管理

        支持子命令: add-url, list, remove, get

        cron 表达式 (5段): 分 时 日 月 星期
        示例: 0 18 * * * = 每天 18:00
              0/30 * * * * = 每 30 分钟
              0 9-18 * * 1-5 = 工作日 9-18 点整点
        """
        pass

    @rss.command("add-url")
    async def add_url(
        self,
        event: AstrMessageEvent,
        url: str,
        minute: str,
        hour: str,
        day: str,
        month: str,
        day_of_week: str,
    ):
        """通过 URL 直接添加 RSS 订阅

        Args:
            url: RSS Feed 完整地址
            minute: cron 分钟字段
            hour: cron 小时字段
            day: cron 日期字段
            month: cron 月份字段
            day_of_week: cron 星期字段
        """
        cron_expr = f"{minute} {hour} {day} {month} {day_of_week}"
        user = event.unified_msg_origin

        # 验证 cron 表达式
        try:
            self._validate_cron(cron_expr)
        except Exception as e:
            yield event.plain_result(f"cron 表达式无效: {e}")
            return

        # 拉取并验证 RSS 源
        text = await self._fetch_feed_text(url)
        if text is None:
            yield event.plain_result(f"无法访问该 RSS 地址: {url}")
            return

        feed = feedparser.parse(text)
        if feed.bozo and not feed.entries:
            yield event.plain_result("该地址不是有效的 RSS/Atom 源。")
            return

        chan_title = feed.feed.get("title", "未知频道")
        chan_desc = feed.feed.get("description", "") or "无描述"

        # 获取最新条目的时间戳，作为订阅起点（避免推送历史内容）
        latest_ts = int(time.time())
        latest_link = ""
        if feed.entries:
            entry = feed.entries[0]
            parsed_time = entry.get("published_parsed") or entry.get("updated_parsed")
            if parsed_time:
                latest_ts = int(time.mktime(parsed_time))
            latest_link = entry.get("link", "")

        # 保存订阅
        if url not in self.data:
            self.data[url] = {
                "info": {"title": chan_title, "description": chan_desc},
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
            f"订阅成功!\n"
            f"频道: {chan_title}\n"
            f"描述: {chan_desc}\n"
            f"定时: {cron_expr}"
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

        # 如果没有订阅者了，清理该 URL
        if not self.data[url]["subscribers"]:
            del self.data[url]

        self._save_data()
        self._refresh_scheduler()
        yield event.plain_result(f"已取消订阅: {title}")

    @rss.command("get")
    async def get_latest(self, event: AstrMessageEvent, idx: int):
        """立即获取指定订阅的最新内容

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
        # after_ts=0 表示获取所有条目（不过滤时间）
        rss_items = await self._poll_rss(url, max_items=3, after_ts=0)
        if not rss_items:
            yield event.plain_result("暂无内容。")
            return

        for item in rss_items:
            text = (
                f"[RSS] {item.chan_title}\n"
                f"标题: {item.title}\n"
                f"链接: {item.link}\n"
            )
            if item.pub_date:
                text += f"时间: {item.pub_date}\n"
            text += f"---\n{item.description}"
            yield event.plain_result(text)