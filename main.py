import os
import json
import time
import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import feedparser

from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
import astrbot.api.message_components as Comp

from .formatters import RSSItem, get_formatter


DATA_FILE = "/data/plugin_data/astrbot_plugin_myrss_data/astrbot_plugin_myrss_data.json"


def _load_metadata() -> dict:
    """读取 metadata.yaml"""
    meta = {}
    path = os.path.join(os.path.dirname(__file__), "metadata.yaml")
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if ":" in line and not line.startswith("#"):
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip().strip('"')
    return meta

_meta = _load_metadata()

@register(_meta["name"], _meta["author"], _meta["desc"], _meta["version"].lstrip("v"))
class MyRSSPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config
        self.data: dict = self._load_data()
        self.scheduler = AsyncIOScheduler()

        # 从插件设置读取配置
        self.max_items = config.get("max_items_per_poll", 5)
        self.desc_max_len = config.get("description_max_length", 200)

    async def initialize(self):
        self.scheduler.start()
        self._refresh_scheduler()
        logger.info("MyRSS 插件初始化完成")

    async def terminate(self):
        self.scheduler.shutdown(wait=False)
        logger.info("MyRSS 插件已停止")

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

    # ==================== 工具方法 ====================

    @staticmethod
    def _validate_cron(cron_expr: str) -> dict:
        """验证并解析 5 段 cron 表达式，返回 dict；不合法则抛异常"""
        parts = cron_expr.split()
        if len(parts) != 5:
            raise ValueError(f"cron 表达式需要 5 个字段，当前有 {len(parts)} 个")
        fields = {
            "minute": parts[0], "hour": parts[1], "day": parts[2],
            "month": parts[3], "day_of_week": parts[4],
        }
        CronTrigger(**fields)
        return fields

    def _get_user_subs(self, user: str) -> list[tuple[str, dict]]:
        """获取指定用户的所有订阅 [(url, info), ...]"""
        return [
            (url, info) for url, info in self.data.items()
            if user in info.get("subscribers", {})
        ]

    # ==================== RSS 拉取 ====================

    async def _fetch_feed(self, url: str):
        """异步请求并解析 RSS 源，返回 feedparser 结果或 None"""
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            async with aiohttp.ClientSession(
                trust_env=True, connector=connector, timeout=timeout, headers=headers
            ) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.error(f"RSS: {url} 返回状态码 {resp.status}")
                        return None
                    return feedparser.parse(await resp.text())
        except Exception as e:
            logger.error(f"RSS: 请求 {url} 失败: {e}")
            return None

    async def _poll_rss(self, url: str, max_items: int = 5, after_ts: int = 0) -> list[RSSItem]:
        """拉取 RSS，返回比 after_ts 更新的条目列表"""
        feed = await self._fetch_feed(url)
        if not feed:
            return []

        chan_title = feed.feed.get("title", "未知频道")
        formatter = get_formatter(url)
        items: list[RSSItem] = []

        for entry in feed.entries:
            item = formatter.parse_entry(entry, chan_title, self.desc_max_len)
            if item.pub_date_timestamp <= after_ts:
                continue
            items.append(item)
            if len(items) >= max_items:
                break

        return items

    @staticmethod
    def _format_items(items: list[RSSItem]) -> str:
        """将多条 RSS 条目格式化为一条消息（统一格式）"""
        parts = []
        for item in items:
            text = f"[RSS] {item.chan_title}\n标题: {item.title}\n链接: {item.link}"
            if item.pub_date:
                text += f"\n时间: {item.pub_date}"
            if item.description:
                text += f"\n---\n{item.description}"
            if item.extra:
                text += f"\n额外: {item.extra}"
            parts.append(text)
        return "\n\n".join(parts)

    # ==================== 定时任务 ====================

    def _refresh_scheduler(self):
        self.scheduler.remove_all_jobs()
        for url, info in self.data.items():
            for user, sub in info.get("subscribers", {}).items():
                try:
                    self.scheduler.add_job(
                        self._cron_callback, "cron",
                        **self._validate_cron(sub["cron_expr"]),
                        args=[url, user],
                    )
                except Exception as e:
                    logger.error(f"RSS: 添加定时任务失败 {url} -> {user}: {e}")
        logger.info(f"RSS: 已刷新定时任务，共 {len(self.scheduler.get_jobs())} 个")

    async def _cron_callback(self, url: str, user: str):
        """定时任务回调：拉取新条目并一次性推送"""
        sub = self.data.get(url, {}).get("subscribers", {}).get(user)
        if not sub:
            return

        rss_items = await self._poll_rss(url, max_items=self.max_items, after_ts=sub.get("last_update", 0))
        if not rss_items:
            return

        chain = MessageChain(chain=[Comp.Plain(self._format_items(rss_items))])
        await self.context.send_message(user, chain)

        max_ts = max(item.pub_date_timestamp for item in rss_items)
        if max_ts > sub.get("last_update", 0):
            sub["last_update"] = max_ts
        sub["latest_link"] = rss_items[0].link
        self._save_data()
        logger.info(f"RSS: {url} 推送 {len(rss_items)} 条 -> {user}")

    # ==================== 用户指令 ====================

    @filter.command_group("rss", alias={"RSS"})
    def rss(self):
        """RSS 订阅管理

        支持子命令: add, list, remove, get

        cron 表达式 (5段): 分 时 日 月 星期
        示例: 0 18 * * * = 每天 18:00
              0/30 * * * * = 每 30 分钟
              0 9-18 * * 1-5 = 工作日 9-18 点整点
        """
        pass

    @rss.command("add")
    async def add_sub(self, event: AstrMessageEvent, url: str,
                      minute: str, hour: str, day: str, month: str, day_of_week: str):
        """添加 RSS 订阅

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

        try:
            self._validate_cron(cron_expr)
        except Exception as e:
            yield event.plain_result(f"cron 表达式无效: {e}")
            return

        feed = await self._fetch_feed(url)
        if not feed:
            yield event.plain_result(f"无法访问该 RSS 地址: {url}")
            return
        if feed.bozo and not feed.entries:
            yield event.plain_result("该地址不是有效的 RSS/Atom 源。")
            return

        chan_title = feed.feed.get("title", "未知频道")
        chan_desc = feed.feed.get("description", "") or "无描述"

        # 以最新条目时间戳为起点，避免推送历史内容
        latest_ts, latest_link = int(time.time()), ""
        if feed.entries:
            entry = feed.entries[0]
            parsed_time = entry.get("published_parsed") or entry.get("updated_parsed")
            if parsed_time:
                latest_ts = int(time.mktime(parsed_time))
            latest_link = entry.get("link", "")

        if url not in self.data:
            self.data[url] = {"info": {"title": chan_title, "description": chan_desc}, "subscribers": {}}
        self.data[url]["subscribers"][user] = {
            "cron_expr": cron_expr, "last_update": latest_ts, "latest_link": latest_link,
        }
        self._save_data()
        self._refresh_scheduler()

        yield event.plain_result(
            f"订阅成功!\n频道: {chan_title}\n描述: {chan_desc}\n定时: {cron_expr}"
        )

    @rss.command("list")
    async def list_subs(self, event: AstrMessageEvent):
        """列出当前会话的所有 RSS 订阅"""
        user = event.unified_msg_origin
        subs = self._get_user_subs(user)
        if not subs:
            yield event.plain_result("当前没有任何 RSS 订阅。")
            return

        lines = ["当前订阅列表:"]
        for i, (url, info) in enumerate(subs):
            sub = info["subscribers"][user]
            lines.append(f"{i}. {info['info']['title']}\n   {url}\n   定时: {sub['cron_expr']}")
        yield event.plain_result("\n".join(lines))

    @rss.command("remove")
    async def remove_sub(self, event: AstrMessageEvent, idx: int):
        """删除一个 RSS 订阅

        Args:
            idx: 订阅索引，可通过 /rss list 查看
        """
        user = event.unified_msg_origin
        subs = self._get_user_subs(user)
        if idx < 0 or idx >= len(subs):
            yield event.plain_result("索引无效，请使用 /rss list 查看。")
            return

        url, info = subs[idx]
        title = info["info"]["title"]
        del self.data[url]["subscribers"][user]
        if not self.data[url]["subscribers"]:
            del self.data[url]

        self._save_data()
        self._refresh_scheduler()
        yield event.plain_result(f"已取消订阅: {title}")

    @rss.command("get")
    async def get_latest(self, event: AstrMessageEvent, idx: int):
        """立即获取指定订阅的最新内容（一次性发送）

        Args:
            idx: 订阅索引，可通过 /rss list 查看
        """
        user = event.unified_msg_origin
        subs = self._get_user_subs(user)
        if idx < 0 or idx >= len(subs):
            yield event.plain_result("索引无效，请使用 /rss list 查看。")
            return

        url, _ = subs[idx]
        rss_items = await self._poll_rss(url, max_items=self.max_items, after_ts=0)
        if not rss_items:
            yield event.plain_result("暂无内容。")
            return

        yield event.plain_result(self._format_items(rss_items))
