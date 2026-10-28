# PAL Arena 本机备份、恢复和开机启动

原生安装包使用包内的 `pal-arena`（Windows 为 `pal-arena.exe`），不需要另外安装 Python。下面以原生包命令为主。源码安装时，将 `./pal-arena maintenance` 换成虚拟环境中的 `python -m arena.maintenance`；也可以使用已安装的 `pal-arena maintenance` 入口。

原生包示例均在解压后的 `pal-arena/` 可执行文件子目录中运行；外层目录中的双击启动脚本不需要移动。

## 备份中心数据

Mac mini 示例：

```sh
./pal-arena maintenance backup \
  --data "$HOME/.pal-arena/server" \
  --output "$HOME/pal-arena-backup-2026-10-28.zip"
```

Windows PowerShell 示例：

```powershell
.\pal-arena.exe maintenance backup `
  --data "$env:USERPROFILE\.pal-arena\server" `
  --output "$env:USERPROFILE\pal-arena-backup-2026-10-28.zip"
```

默认最多包含 5GB 内容，可用 `--max-gb` 调整。已有备份文件不会被覆盖，重复备份请换文件名。

备份包含 SQLite 项目、任务、用户身份记录、当前数据库引用的产物和存在于数据目录中的 `owner-token.txt`。不包含各执行机上的 Git 工作目录、已安装的软件、模型订阅登录缓存或未被数据库记录的临时上传文件。代码分支仍应同步到 GitHub，执行机的工作目录应单独管理。

数据库通过 SQLite 备份 API 生成一致快照，不能用直接复制 `arena.db` 来代替，因为最新数据可能仍在 WAL 文件里。快照期间短暂保留数据库写锁，将不可变产物建立到同一磁盘上的临时硬链接，随后释放写锁再压缩；此时网页删除旧产物也不会损坏备份。数据目录使用 Mac 默认 APFS 或 Windows 默认 NTFS 即可，临时硬链接在备份结束后移除。

**备份包含登录信息，且默认不加密。** Unix 上归档仅允许当前用户读写，临时目录仅允许当前用户访问；Windows 上请使用当前用户受保护的目录。不要将备份提交到 GitHub 或发送给其他人。命令只显示文件路径、数量和大小，不打印登录令牌。清单里的 SHA-256 用于检测损坏，不代表归档经过签名。

## 恢复到新目录

先停止中心服务，再将备份恢复到一个新目录。恢复工具也接受空目录，但不会覆盖已有数据。

```sh
./pal-arena maintenance restore \
  --archive "$HOME/pal-arena-backup-2026-10-28.zip" \
  --target "$HOME/.pal-arena/server-restored"

./pal-arena serve --data "$HOME/.pal-arena/server-restored"
```

源码安装：

```sh
python -m arena.maintenance restore --archive /absolute/path/backup.zip --target /absolute/path/restored
python -m arena serve --data /absolute/path/restored
```

工具会先在临时目录验证格式版本、路径、文件类型、大小、SHA-256、SQLite 完整性及产物引用，再将完整结果移到目标目录。归档里的路径穿越、符号链接、重复文件、清单外文件和超限内容都会被拒绝。失败不会覆盖原数据目录。

恢复后：

- 浏览器会话失效，使用原来的成员令牌重新登录；包含的 `owner-token.txt` 仍可用于 Owner 登录。
- 电脑先显示离线，收到新的心跳后恢复在线。
- 快照中排队、运行和等待子任务的任务变为“执行中断”，旧运行令牌全部失效。
- **先检查 GitHub 提交、桌面会话和部署回执，再决定重试。** 快照中排队的任务可能在备份之后已经执行过，恢复时不会自动重发这些操作。

确认恢复结果后，再将中心服务配置切到新数据目录。同版本恢复需要回滚时，停止新服务并使用原数据目录启动即可。若同时回退软件版本，应将升级前备份恢复到另一个新目录，避免旧程序读取已迁移的新数据库。不要同时运行两个中心实例来处理同一批机器。

执行机在 `_receipts/<任务 ID>/<尝试编号>.json` 保存本地回执。Git 推送和部署开始前会先记录准确提交、当前阶段和 `execution_uncertain: true`，确认成功后再更新回执；写入检查点失败会停止后续发布操作。断电或进程退出后，`merge_pending`、`deployment_pending` 或结果未知的回执表示需要核对 GitHub 或实际部署服务，不能据此自动重发。备份中心数据不包含这些执行机回执，迁移执行机时应另行保留它们。

## 生成 Mac 登录启动服务

先把原生安装包解压到一个固定位置，之后不要移动它。以下命令只生成服务文件和安装／卸载命令，不会立即注册或启动服务。

```sh
./pal-arena maintenance service \
  --platform macos --role server \
  --output "$HOME/.pal-arena/services-server" \
  --data "$HOME/.pal-arena/server"
```

原生包会自动使用自身可执行文件。源码安装应在虚拟环境中生成，或明确传入解释器：

```sh
python -m arena.maintenance service \
  --platform macos --role server \
  --python /absolute/path/to/venv/bin/python \
  --output "$HOME/.pal-arena/services-server" \
  --data "$HOME/.pal-arena/server"
```

生成目录中包含 `.plist` 和 `.commands.txt`。查看后执行 `.commands.txt` 中的 `launchctl bootstrap` 等命令，即可注册当前用户的登录启动项；同一文件也提供 `bootout` 和删除启动项的命令。服务监听 `127.0.0.1:8765`，远程访问仍通过 Tailscale Serve。

## 工作机登录启动与令牌文件

桌面代理需要当前用户已登录桌面，工作机服务使用用户会话。把网页生成的机器令牌保存到本机专用文件；令牌内容不放进 plist、任务 XML 或命令行参数。

在 Mac 上保存令牌后设置文件权限：

```sh
chmod 600 "$HOME/.pal-arena/machine-token.txt"

./pal-arena maintenance service \
  --platform macos --role worker \
  --output "$HOME/.pal-arena/services-worker" \
  --config "$HOME/.pal-arena/worker.json" \
  --token-file "$HOME/.pal-arena/machine-token.txt"
```

Windows PowerShell：

```powershell
.\pal-arena.exe maintenance service `
  --platform windows --role worker `
  --output "$env:USERPROFILE\.pal-arena\services-worker" `
  --config "$env:USERPROFILE\.pal-arena\worker.json" `
  --token-file "$env:USERPROFILE\.pal-arena\machine-token.txt"
```

Windows 生成当前用户的 Scheduled Task XML，使用 `InteractiveToken` 和普通用户权限。查看同目录的 `.commands.txt`，使用其中的 `Register-ScheduledTask` 与 `Start-ScheduledTask` 注册、启动；停止与卸载命令也在该文件中。工作机登录令牌只在进程启动时从本机文件读取。

服务日志默认在生成目录的 `logs/` 下，采用单文件 5MB、两份轮转备份。日志可用于查看启动失败、连接状态和本机命令错误。原生包自带的普通启动快捷方式适合手动启动；需要登录时自动运行才使用此处生成的服务。

## 发布执行与首次空仓库

项目启用自动合并／部署时，还需要执行机配置明确的 `release_profiles`：验证命令和部署命令都使用参数数组，不解释 shell 字符串。发布器整合已独立验收的提交，执行验证，再普通推送目标分支或运行部署命令。

新的空 GitHub 仓库可以由工作机先推送 `arena/*` 工作分支，发布器使用所有机器一致的空项目基准完成首次 `main` 创建。目标分支不存在但仓库已有其他来源分支或标签时，不会擅自当成新项目处理。已有目标分支在验证期间发生变化，发布器会停止；推送或部署结果不明时会保留状态，必须检查后再重试。
