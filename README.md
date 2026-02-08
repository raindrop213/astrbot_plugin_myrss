# astrbot_plugin_myrss

一个简单的 AstrBot RSS 订阅插件，支持通过直接 URL 订阅 RSS 源，并使用 cron 表达式设置定时拉取与推送。

## 功能

- 通过完整 URL 直接订阅任意 RSS/Atom 源
- 自定义 cron 定时规则，灵活控制拉取频率
- 自动去重，仅推送订阅后的新条目
- 支持查看、删除订阅及手动获取最新内容
- 数据本地持久化，重启后自动恢复
- **自定义输出格式**：可针对不同站点设置不同的消息模板
- **可配置推送数量**：通过插件设置调整获取和推送的条目数

## 安装

在 AstrBot 中安装本插件后，会自动安装依赖 `feedparser`。

## 指令说明

所有指令以 `/rss` 开头。

### 添加订阅

```
/rss add <RSS地址> <分> <时> <日> <月> <星期>
```

示例：每天 18:00 检查 nyaa.si 的 RSS 源：

```
/rss add https://nyaa.si/?page=rss&q=TMW&c=0_0&f=0 0 18 * * *
```

### 查看订阅列表

```
/rss list
```

### 删除订阅

```
/rss remove <索引>
```

索引可通过 `/rss list` 查看。

### 手动获取最新内容

```
/rss get <索引>
```

返回条目数量可在插件设置中调整（默认 5 条）。

## Cron 表达式

采用 5 段格式：`分 时 日 月 星期`

| 字段 | 取值范围 | 说明 |
|------|---------|------|
| 分钟 | 0-59 | `*` 表示每分钟 |
| 小时 | 0-23 | `*` 表示每小时 |
| 日期 | 1-31 | `*` 表示每天 |
| 月份 | 1-12 | `*` 表示每月 |
| 星期 | 0-6 | 0 = 周日，`*` 表示不限 |

常用示例：

| 表达式 | 含义 |
|--------|------|
| `0 18 * * *` | 每天 18:00 |
| `0/30 * * * *` | 每 30 分钟 |
| `0 9-18 * * 1-5` | 工作日 9:00-18:00 整点 |
| `0 0 1,15 * *` | 每月 1 号和 15 号 0:00 |

## 插件设置

在 AstrBot 管理面板中可配置以下参数：

| 设置项 | 默认值 | 说明 |
|--------|--------|------|
| 手动获取条数 | 5 | `/rss get` 返回的最大条目数 |
| 定时推送条数 | 5 | 定时推送时的最大条目数 |
| 描述截断长度 | 200 | 条目描述最大字符数，0 表示不截断 |

## 自定义输出格式

编辑插件目录下的 `format_handler.py` 文件即可自定义消息输出格式。

### 可用占位符

| 占位符 | 说明 |
|--------|------|
| `{chan_title}` | 频道标题 |
| `{title}` | 条目标题 |
| `{link}` | 条目链接 |
| `{description}` | 条目描述 |
| `{pub_date}` | 发布时间 |

### 针对不同站点设置格式

在 `format_handler.py` 的 `DOMAIN_FORMATS` 字典中添加条目：

```python
DOMAIN_FORMATS = {
    "nyaa.si": "[Nyaa] {title}\n{link}",
    "share.dmhy.org": "[动漫花园] {title}\n{link}",
}
```

键为 URL 中包含的关键字，只要 RSS 地址包含该关键字就会匹配对应格式。

## 依赖

- [feedparser](https://pypi.org/project/feedparser/) -- RSS/Atom 解析
- [apscheduler](https://pypi.org/project/APScheduler/) -- 定时任务调度
- [aiohttp](https://pypi.org/project/aiohttp/) -- 异步 HTTP 请求

## 参考

- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
