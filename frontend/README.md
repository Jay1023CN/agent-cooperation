# PAL Arena 独立前端

这是一套独立的 Vite + 原生 JavaScript 前端。页面、样式、状态管理和视图都在本目录；修改 UI 不需要修改 Python 调度、数据库或执行端。正式运行使用提交在 `arena/static/` 的构建产物，不要求用户安装 Node，也不连接外部字体或 CDN。

## 本地开发

需要 Node.js 22（最低 20.19）。在仓库根目录启动 API：

```bash
python -m arena serve --data /tmp/pal-arena-dev --port 8765
```

另一个终端运行：

```bash
cd frontend
npm ci
npm run dev
```

打开 `http://127.0.0.1:5173/static/`。使用指定数据目录中的 `owner-token.txt` 登录。`/api` 和 `/health` 代理到 `127.0.0.1:8765`，并保留浏览器的 Host；请不要设置 `changeOrigin: true`，否则会破坏 API 对 Cookie 请求的 Origin / Host 校验。开发服务器默认仅监听本机。

只看界面可打开 `http://127.0.0.1:5173/static/?demo=1`。示例模式在界面上明确标注，数据仅来自 `src/data/demo.js`；所有实际写入均被禁用。

## 构建与校验

```bash
npm run check       # 格式及每个 JavaScript 模块的语法
npm test            # 不确定网络结果重试、频道隔离与退出清理
npm run build       # 生成 ../arena/static/index.html 和 assets/*
npm audit           # 检查锁定的开发依赖
```

构建输出使用 `/static/` 作为资源前缀。FastAPI 的 `/` 返回构建后的 `index.html`，`/static` 提供本地资源；不需要 SPA 回退路由或后端模板改动。`arena/static/` 是生成文件，修改源代码后重新构建并一同提交。锁文件固定依赖；不要手改哈希资源。`npm run format` 统一格式。

导出可直接打开的单个离线只读文件：

```bash
npm run build
node scripts/export-demo.mjs /path/to/pal-arena-preview.html
```

导出文件内嵌 CSS、JavaScript 和示例数据，不需要服务器，不调用 API。它不是可以连接真实账号的安装包。

## 设计规范

默认主题跟随操作系统，并实时响应系统浅色 / 深色切换。用户可在右上角覆盖为浅色或深色，选择保存在本机 `localStorage` 的 `pal-arena-theme`。主题色只在 `src/styles.css` 的 `:root` 定义，通过 `light-dark()` 使用同一套语义令牌。使用支持 `light-dark()` 的现代浏览器（Chrome / Edge 123+、Safari 17.5+、Firefox 120+）。

- **方向**：清爽明亮的系统风格。蓝色用于操作与焦点；任务状态通过小圆点和浅色背景区分。
- **字体**：优先使用设备的系统字体，中文回落到苹方、鸿蒙黑体、MiSans 或微软雅黑；命令和提交号使用等宽字体，不额外下载字体。
- **字号**：`--text-xs` 12、`--text-sm` 14、`--text-base` 15、`--text-md` 19、`--text-lg` 26、`--text-xl` 34。
- **间距**：`--space-1/2/3/4/5/6/8/12` 对应 4/8/12/16/20/28/36/56px。
- **控件**：共用 `.button`、`.icon-button`、`.field`、`.pill`、`.notice`、`.segmented`。使用柔和阴影和圆角区分层次，状态颜色用于提示而非装饰。
- **信息结构**：侧栏平铺全部页面，顶部是命令面板入口；桌面端没有顶栏，项目头部一行，下面是任务管线条和「需处理」条（授权可就地允许 / 拒绝）。对话页是固定高度的工作台：对话占满剩余高度，右栏「机位」每台电脑一条轨道，显示每个执行位上的任务，下面是排队任务及其等待原因。地址用 `#/<项目>/<页面>` 或 `#/<项目>/task/<任务>`，最近项目记在本机。
- **快捷操作**：`Ctrl/⌘ K` 命令面板（`src/components/palette.js`），`N` 新建任务，`/` 聚焦输入框，`G` + `C/T/R/F/M/Y/L` 切换页面；弹窗里的 `pre` 自动带复制按钮；页面在后台时每 20 秒同步一次，开启提醒后用系统通知提示授权和失败。`public/` 里的 `manifest.webmanifest` 和图标让工作台可以安装为应用。
- **无障碍**：原生按钮 / 表单 / `dialog`、清楚的焦点样式、图标按钮名称；菜单支持 Escape，收起的手机导航使用 `inert`。`/` 聚焦对话输入框。尊重减少动态效果设置。

## 源码地图

| 文件 | 修改内容 |
| --- | --- |
| `index.html` | 主布局、导航、登录、对话容器 |
| `src/styles.css` | 统一设计令牌、控件与响应式布局 |
| `src/app.js` | 登录、刷新、页面切换、动作绑定及状态联动 |
| `src/lib/api.js` | Cookie 请求、错误反馈、请求 ID |
| `src/lib/state.js` | 空快照、可重试提交的幂等队列 |
| `src/components/ui.js` | 安全转义、图标、状态 / 角色标签、空态 |
| `src/components/theme.js` | 系统主题与手动覆盖 |
| `src/components/navigation.js` | 手机导航和键盘行为 |
| `src/views/dashboard.js` | 项目概览、电脑与成员状态 |
| `src/views/projects.js` | 创建项目、需求版本、项目与发布设置 |
| `src/views/tasks.js` | 建任务、分配原因、证据、记忆快照和授权 |
| `src/views/workspace.js` | 看板、验收、成果列表与日志 |
| `src/views/memory.js` | 共享 / 个人记忆列表 |
| `src/views/connection.js` | 连接电脑、客户端配置与启动命令 |
| `src/data/demo.js` | 明确标注的只读示例 |

所有来自 API 的文字必须经过 `e()` 或写入 `textContent`，不能作为任意 HTML 执行。HTML 成果预览使用无权限沙箱和 CSP，图片使用受限大小的本地 Blob；关闭弹窗时回收 Object URL。令牌只在生成后短暂显示，不写入配置下载、URL 或 `localStorage`。机器在线、客户端检测到、订阅账号可用要分别展示，不能把其中之一当作其他状态。

## 界面验收

本次在真实 FastAPI 的隔离数据目录和 Chromium 上验证过：

1. Vite 代理实际 Cookie 登录、创建项目及版本冲突保护设置保存。
2. 个人对话转到共享输入框、任务创建和失败后重试。
3. 共享记忆提议 / 确认 / 停用，个人记忆隔离。
4. 电脑配置下载不含令牌，客户端能力和调度偏好被保留。
5. PNG 成果预览、GitHub 固定提交链接、运行中任务授权。
6. 系统主题实时变化、手动覆盖后刷新、恢复跟随系统。
7. 1440px 桌面、390px 手机，无页面横向溢出或 JavaScript 错误。

真实订阅登录态、四台实机执行效果仍需在对应电脑连接后验证。界面不会把未连接机器或示例任务显示成已经实测的结果。
