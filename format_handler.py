"""
RSS 输出格式自定义模块

本文件用于自定义不同 RSS 源的输出格式。
你可以直接编辑此文件来调整推送消息的样式。

每个网站对应一个格式化函数，接收以下参数:
    chan_title   - 频道标题
    title        - 条目标题
    link         - 条目链接
    description  - 条目描述（已去除 HTML 标签）
    pub_date     - 发布时间

使用方法:
    1. 修改 format_default() 来更改默认输出格式
    2. 在 DOMAIN_HANDLERS 中添加域名关键字和对应的函数
    3. 新增网站只需写一个新函数，然后在 DOMAIN_HANDLERS 注册即可
"""


# ==================== nyaa.si ====================
# description 格式: "#ID | 标题 | 大小 | 分类 | infoHash"
# link 是 .torrent 下载链接

def format_nyaa(chan_title: str, title: str, link: str,
                description: str, pub_date: str) -> str:
    # 从 description 中提取大小信息
    size = ""
    if description and "|" in description:
        parts = [p.strip() for p in description.split("|")]
        if len(parts) >= 3:
            size = parts[2]  # 第3段是文件大小

    lines = [f"[Nyaa] {title}"]
    if size:
        lines.append(f"大小: {size}")
    lines.append(link)
    if pub_date:
        lines.append(f"时间: {pub_date}")
    return "\n".join(lines)


# ==================== share.dmhy.org ====================
# title 已包含字幕组、番名、分辨率、集数等全部信息
# description 是大段 HTML 噪音（网盘链接、表情包等），不需要展示

def format_dmhy(chan_title: str, title: str, link: str,
                description: str, pub_date: str) -> str:
    lines = [f"[动漫花园] {title}"]
    lines.append(link)
    if pub_date:
        lines.append(f"时间: {pub_date}")
    return "\n".join(lines)


# ==================== 默认格式 ====================
# 适用于未在 DOMAIN_HANDLERS 中注册的其他网站

def format_default(chan_title: str, title: str, link: str,
                   description: str, pub_date: str) -> str:
    lines = [f"[RSS] {chan_title}"]
    lines.append(f"标题: {title}")
    lines.append(f"链接: {link}")
    if pub_date:
        lines.append(f"时间: {pub_date}")
    if description:
        lines.append(f"---\n{description}")
    return "\n".join(lines)


# ==================== 域名 → 函数 映射 ====================
# 键: RSS URL 中包含的关键字（只要 URL 中包含该字符串即匹配）
# 值: 对应的格式化函数
#
# 新增网站示例:
#   1. 写一个 format_xxx(...) 函数
#   2. 在下面的字典中添加 "xxx.com": format_xxx

DOMAIN_HANDLERS = {
    "nyaa.si": format_nyaa,
    "share.dmhy.org": format_dmhy,
}


def format_item(url: str, chan_title: str, title: str, link: str,
                description: str, pub_date: str) -> str:
    """根据 URL 匹配对应的格式化函数，返回格式化后的文本

    Args:
        url: RSS 源地址，用于匹配域名
        chan_title: 频道标题
        title: 条目标题
        link: 条目链接
        description: 条目描述
        pub_date: 发布时间

    Returns:
        格式化后的消息文本
    """
    handler = format_default
    for domain, fn in DOMAIN_HANDLERS.items():
        if domain in url:
            handler = fn
            break

    return handler(chan_title, title, link, description, pub_date)
