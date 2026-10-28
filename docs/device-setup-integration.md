# 电脑连接：页面接口

这份文档供工作台和本机安装程序接线使用。设备检测只读取状态，不会安装软件、登录账号或修改 Tailscale 配置。

## 当前电脑

每台电脑需要启动本机助手，助手只监听 `127.0.0.1:8792`。安装程序启动时，将工作台的准确来源传给 `--origin`；允许多个指定来源，不能使用通配符。开发环境可以使用 `http://127.0.0.1:5173`，远程工作台必须使用 HTTPS。来源应由用户信任的安装或配对流程提供，不能由任意网页自行设置。

页面请求 `GET http://127.0.0.1:8792/v1/status`。助手仅接受指定来源和本机 Host，支持对应的浏览器跨来源和 Private Network Access 预检。HTTPS 页面访问本机 HTTP 的支持情况还需要在目标浏览器实测；不能把助手无响应直接解释成没有安装 Tailscale。

主要返回字段：

- `scope: local_device`：结果属于打开本机助手的电脑。
- `device.name`、`device.os`：电脑名称和系统。
- `network.state`：`not_installed`、`login_required`、`approval_required`、`starting`、`disconnected`、`wrong_network`、`network_unverified`、`connected` 或 `unavailable`。
- `network.tailnet`、`network.address`：本机所在网络名称和地址，无法取得时为 `null`。`matches_expected` 为 `true`、`false` 或 `null`（没有足够信息判断）。
- `next_action.kind`、`label`、`url`：根据网络状态给页面的下一步操作。`install` 的 URL 是 Tailscale 官方下载页；登录和批准需由用户在 Tailscale 完成。
- `center.state`：`not_configured`、`not_checked`、`not_found`、`unreachable` 或 `reachable`。`reachable` 只代表 Tailscale 能探测到中心设备，不代表工作台或任务服务已就绪。
- `center.service`：`not_configured`、`not_checked`、`unavailable`、`not_ready` 或 `ready`。设置中心服务地址后，助手通过 Multica 的 `/readyz` 检查数据库和迁移；这个地址必须属于本机 Tailscale 列出的中心设备。`ready` 表示服务和数据库检查通过，不代表代理已经上线。

安装程序可在启动助手时提供 `--expected-tailnet`、`--center` 和 `--center-url`。网络名与 Tailscale 本机状态精确比较；中心地址使用 Tailscale DNS 名或 IP，仅当它出现在本机的设备列表中时才发起探测。`--center-url` 使用中心服务 HTTPS 来源，主机名必须和中心设备地址一致；它可以单独指定。无这些参数时不推断网络一致或中心可用。

## 中心电脑

已有 Arena 工作台登录成员可调用 `GET /api/setup/center`，返回结构与本机检测一致，但 `scope` 为 `workbench_host`：它检查的是运行工作台服务的电脑，不是访问网页的电脑。这个接口不代表 Multica 服务健康或设备已注册。

## 后续接线

当前提供检测接口和启动入口，尚未提供可分发的安装包、页面配对、项目网络配置保存、中心服务部署及 Multica 设备登记。页面需要把“助手未运行”“网络连通”和“中心服务就绪”分别展示。Mac mini 和其他电脑加入网络后的实际连接，还需要在那些设备上验证。
