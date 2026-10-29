# Blender 任务

在执行电脑的成员能力中加入 `blender`，将需要共享的 `.blend` 文件放进任务工作区的 `arena-exports/`。执行结束后，工作端会使用本机 Blender 后台打开这些文件，记录版本、场景数、对象数和文件 SHA-256，供任务详情与独立验收查看。检查不会保存或覆盖项目文件。

程序会先查找 PATH，再查找常见的 Windows 和 macOS 安装位置。也可以在该电脑的 `worker.json` 顶层指定可执行文件：

```json
"blender_executable": "Blender 可执行文件的绝对路径"
```

使用桌面 MCP 执行 Blender 任务时，成员可以在提交前调用 `inspect_blender_exports(handle)`，提前检查导出的项目；提交后工作端还会记录正式检查结果。

未找到 Blender 时，任务详情显示文件尚未自动检查；没有 `.blend` 导出时显示未找到项目文件。文件无法打开时任务会报告失败，保留工作区供修改。自动打开检查只确认文件可以加载，作品内容仍按任务验收条件由验收成员检查。
