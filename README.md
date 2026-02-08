# astrbot_plugin_myrss

简单的 AstrBot RSS 订阅插件，支持 cron 定时推送、正则过滤、多站点自定义解析。

## 指令

```
/rss add <链接> [定时] [过滤]   添加/更新订阅
/rss list                       查看订阅列表
/rss remove <索引>              删除订阅
/rss get <索引>                 手动获取最新内容
```

- **定时**：点分隔 cron（`分.时.日.月.星期`），留空用设置中的默认值
- **过滤**：正则表达式，排除标题匹配的条目，留空不过滤
- 重复添加同一链接会覆盖规则，不会重推旧内容

示例：

```
/rss add https://nyaa.si/?page=rss&q=test
/rss add https://nyaa.si/?page=rss&q=test 0.18.*.*.*
/rss add https://nyaa.si/?page=rss&q=test 0.18.*.*.* 720
```

## 定时规则

| 表达式 | 含义 |
|--------|------|
| `0.18.*.*.*` | 每天 18:00 |
| `*/30.*.*.*.*` | 每 30 分钟 |
| `0.9-18.*.*.1-5` | 工作日 9-18 点整点 |

## 插件设置

| 设置项 | 默认值 | 说明 |
|--------|--------|------|
| 默认定时规则 | `0.18.*.*.*` | add 不指定定时时使用 |
| 默认过滤正则 | 空 | add 不指定过滤时使用 |
| 最大条目数 | 3 | 每次推送/获取的上限 |
| 描述最大长度 | 200 | 超过则截取 |

## 自定义网站解析

编辑 `formatters.py`，继承 `DefaultFormatter` 重写 `_extract_fields`，注册域名即可。

已内置：nyaa.si、share.dmhy.org、mikan.tangbai.cc
