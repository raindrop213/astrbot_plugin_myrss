import os
import json
import re
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


DATA_FILE = "/data/astrbot_plugin_myrss_data.json"


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
        self.max_items = config.get("max_items_per_poll", 3)
        self.desc_max_len = config.get("description_max_length", 200)
        self.default_cron = config.get("default_cron", "0.18.*.*.*")
        self.default_filter = config.get("default_filter", "")

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
    def _dot_to_cron(dot_expr: str) -> str:
        """点分隔 cron → 空格分隔: 0.18.*.*.* → 0 18 * * *"""
        parts = dot_expr.split(".")
        if len(parts) != 5:
            raise ValueError(f"cron 需要 5 段（分.时.日.月.星期），当前 {len(parts)} 段")
        cron_expr = " ".join(parts)
        # 验证合法性
        CronTrigger(
            minute=parts[0], hour=parts[1], day=parts[2],
            month=parts[3], day_of_week=parts[4],
        )
        return cron_expr

    @staticmethod
    def _validate_cron(cron_expr: str) -> dict:
        """验证空格分隔的 cron 表达式，返回 dict（供调度器使用）"""
        parts = cron_expr.split()
        if len(parts) != 5:
            raise ValueError(f"cron 表达式需要 5 个字段，当前有 {len(parts)} 个")
        fields = {
            "minute": parts[0], "hour": parts[1], "day": parts[2],
            "month": parts[3], "day_of_week": parts[4],
        }
        CronTrigger(**fields)
        return fields

    @staticmethod
    def _filter_items(items: list[RSSItem], pattern: str) -> list[RSSItem]:
        """按正则排除标题匹配的条目（忽略大小写）"""
        if not pattern:
            return items
        try:
            regex = re.compile(pattern, re.IGNORECASE)
            return [item for item in items if not regex.search(item.title)]
        except re.error:
            return items

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

        formatter = get_formatter(url)
        chan_title = formatter.get_chan_title(feed, url)
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
        """定时任务回调：拉取新条目，过滤后一次性推送"""
        sub = self.data.get(url, {}).get("subscribers", {}).get(user)
        if not sub:
            return

        rss_items = await self._poll_rss(url, max_items=self.max_items, after_ts=sub.get("last_update", 0))
        rss_items = self._filter_items(rss_items, sub.get("filter", ""))
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

        /rss add <链接> [定时] [过滤]
        定时格式（点分隔）: 分.时.日.月.星期
        示例: 0.18.*.*.* = 每天18:00
              */30.*.*.*.* = 每30分钟
        过滤: 正则表达式，匹配标题
        """
        pass

    @rss.command("add")
    async def add_sub(self, event: AstrMessageEvent, url: str,
                      cron: str = "", filter_re: str = ""):
        """添加 RSS 订阅（重复添加同一链接则覆盖规则）

        Args:
            url: RSS Feed 完整地址
            cron: 定时规则，点分隔（如 0.18.*.*.*），留空用默认
            filter_re: 过滤正则（匹配标题），留空用默认
        """
        user = event.unified_msg_origin

        # 解析 cron
        try:
            cron_expr = self._dot_to_cron(cron if cron else self.default_cron)
        except Exception as e:
            yield event.plain_result(f"定时规则无效: {e}")
            return

        # 确定过滤规则
        filter_pattern = filter_re if filter_re else self.default_filter

        # 验证过滤正则
        if filter_pattern:
            try:
                re.compile(filter_pattern)
            except re.error as e:
                yield event.plain_result(f"过滤正则无效: {e}")
                return

        # 检查是否已订阅（重复添加 = 覆盖规则）
        existing = self.data.get(url, {}).get("subscribers", {}).get(user)
        if existing:
            existing["cron_expr"] = cron_expr
            existing["filter"] = filter_pattern
            self._save_data()
            self._refresh_scheduler()
            yield event.plain_result(
                f"已更新订阅规则!\n"
                f"频道: {self.data[url]['info']['title']}\n"
                f"定时: {cron_expr}\n"
                f"过滤: {filter_pattern or '无'}"
            )
            return

        # 新订阅：拉取并验证 RSS 源
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
            "cron_expr": cron_expr, "filter": filter_pattern,
            "last_update": latest_ts, "latest_link": latest_link,
        }
        self._save_data()
        self._refresh_scheduler()

        yield event.plain_result(
            f"订阅成功!\n"
            f"频道: {chan_title}\n"
            f"定时: {cron_expr}\n"
            f"过滤: {filter_pattern or '无'}"
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
            f = sub.get("filter", "")
            lines.append(
                f"{i}. {info['info']['title']}\n"
                f"   {url}\n"
                f"   定时: {sub['cron_expr']}  |  过滤: {f or '无'}"
            )
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

        url, info = subs[idx]
        sub = info["subscribers"][user]
        rss_items = await self._poll_rss(url, max_items=self.max_items, after_ts=0)
        rss_items = self._filter_items(rss_items, sub.get("filter", ""))
        if not rss_items:
            yield event.plain_result("暂无内容。")
            return

        yield event.plain_result(self._format_items(rss_items))
