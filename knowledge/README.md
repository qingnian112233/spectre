# knowledge/ —— 知识库

## toolPlugins/ —— 声明式插件

往这里丢一个 `xxx.json` + 同名执行脚本, 重启后自动注册成工具, **不用改核心**。

JSON 字段: `name` / `description` / `schema`(JSON Schema) / `exec`(服务器上的执行脚本绝对路径) ,
可选 `admin_only` / `timeout`。调用时参数 JSON 会作为 `argv[1]` 传给脚本。

## self_learned/ —— 自沉淀

模型执行任务时发现的新知识点写这里, 文件头必须加 `<!-- TRIGGER: 场景关键词 -->`(关键词用 `|` 隔开),
不写就检索不到。

## suggestions/ —— 建议

需要改行为 / 提示词 / 路由时, 模型写建议文档到这里交主人审核, 不自改主程序。
