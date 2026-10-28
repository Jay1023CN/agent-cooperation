# 执行电脑工作区清理

工作区预算默认是 5 GB。达到预算时，执行端停止领取新任务；清理不会为了腾空间删除尚未确认的成果。

任务副本和发布副本的 Git 配置会在各自仓库中启用长路径支持，避免 Windows 较深的工作目录无法创建分支；不会改动电脑的全局 Git 配置。Windows 对其他程序和文件系统仍可能有自己的路径限制。

先预览：

```bash
pal-arena workspaces --config worker.json --token-file machine-token.txt
```

确认预览结果后，停止这台电脑的执行端，再执行：

```bash
pal-arena workspaces --config worker.json --token-file machine-token.txt --apply
```

从源码运行时，也可将 `pal-arena workspaces` 替换为 `python -m arena.workspaces`。原生包无需安装 Python；在包内运行 `pal-arena workspaces`，Windows 使用 `pal-arena.exe workspaces`。

`machine-token.txt` 使用这台执行电脑的凭据。macOS/Linux 文件权限必须为 `0600`；Windows 将文件放在只有当前用户可以读取的目录。报告不会包含凭据、任务正文或执行回执正文。`--apply` 才会删除，默认命令只检查并显示计划。

每个项目保留最近三份受管理工作目录，可用 `--keep N` 调整，最少保留一份。其余目录必须同时满足以下条件才会列入可清理项：

- 本地有已确认的执行回执，服务器确认同一次任务已经完成，且需求版本、提交、电脑与目录证据一致。
- 成果已同步到 GitHub，记录的提交仍在记录的远端分支历史中；发布目录检查实际发布的目标分支。
- 工作区 HEAD 与记录的提交相同，未修改、未跟踪和忽略文件都为空。`.arena-inputs` 下载缓存只有在每个文件都与本地回执及服务器记录的产物 ID、SHA-256 一致时才可以移除；缺少校验记录或有修改时保留。
- 没有 Git stash、未包含在确认提交中的本地分支或历史记录，也没有 `assume-unchanged`、`skip-worktree` 等跳过检查的索引设置。
- 目录严格符合 `工作区根目录/项目/任务/次数` 或 `工作区根目录/releases/项目/任务/次数`，且路径与目录树都没有符号链接或 Windows 目录联接。

含 `arena-exports` 的目录始终保留。失败检查点、本地独有成果、活动任务、失联或结果未知的任务、缺少回执的目录也都会保留。GitHub 访问失败、分支删除、凭据过期或状态不一致时保留目录。未识别的目录、执行回执和本地项目基线不会被清理。

实际删除时会重新读取服务器状态、检查本地内容和远端提交。执行端与清理程序使用同一个进程锁，执行端尚在运行时清理会拒绝执行。计划检查不会修改候选工作区；必要的远端历史检查在临时 Git 仓库进行，随后删除临时仓库。

每次清理保留 `_receipts/任务/次数.json`，增加 `workspace_cleanup` 开始时间、完成时间和结果。若删除中途失败，回执保留开始记录，应先检查目录的实际情况。清理只影响这台电脑的旧工作目录，不删除服务器产物或 GitHub 分支。
