# 让其他电脑和伙伴连上中心

中心那台电脑（比如 Mac mini）没有公网 IP 也没关系，也不用改路由器。我们用 Tailscale 把你们的电脑拉进同一个私有网络：只有加进来的设备能访问，外人打不开。全程大约 10 分钟。

## 1. 每台电脑装 Tailscale 并登录

到 [tailscale.com/download](https://tailscale.com/download) 下载安装（Mac、Windows、手机都有）。打开后点登录，用 GitHub、Google、Microsoft 或 Apple 账号都行。

**你自己的几台电脑用同一个账号登录**，它们就自动在一个网络里了。登录成功后，菜单栏或任务栏的 Tailscale 图标会显示已连接。

## 2. 把伙伴拉进同一个网络

打开 [Tailscale 后台](https://login.tailscale.com/admin/users)，进入 **Users**，点 **Invite users**，把邀请链接发给伙伴。伙伴用自己的账号接受邀请，再在他的电脑上安装并登录 Tailscale。免费版够两三个人、几台电脑用。

## 3. 在中心电脑上开启共享

在中心电脑上启动工作台时加上 `--share`：

```sh
pal-arena serve --share
```

工作台已经在运行的话，另开一个终端运行 `pal-arena share`；或者在中心电脑的浏览器里打开工作台，进入「电脑与成员」，点「在这台电脑上开启共享」。

第一次共享时，Tailscale 会要求为你的网络打开 HTTPS。命令或页面会给你一个 `https://login.tailscale.com/...` 链接，点进去按一次 **Enable**，回来再共享一次就好。之后不用再做。

成功后会显示类似 `https://mac-mini.tail1234.ts.net` 的地址。

## 4. 用这个地址

- **伙伴**：在浏览器打开这个地址，用你邀请时生成的令牌登录。
- **其他执行电脑**：在「电脑与成员 → 连接电脑」生成配置时，中心地址会自动填成这个地址。下载 `worker.json` 后，在那台电脑上运行 `pal-arena doctor --config worker.json`，它会检查这台电脑是否已经加入网络、能不能连上中心。

## 常见问题

**国内能用吗？** 一般可以直接用。两台电脑直连不上时，Tailscale 会自动经中继服务器转发，速度慢一些，但这里只传任务、消息和日志，不影响使用。代码通过 GitHub 交接，不走这条路。

**地址会变吗？** 不会。只要中心电脑在 Tailscale 里的名字不改，地址就一直是这个。

**安全吗？** 中心本身只监听本机 `127.0.0.1`，Tailscale 负责加密并只让你网络里的设备访问。登录工作台仍然需要令牌。不想共享时，在中心电脑运行 `tailscale serve reset` 即可关闭。

**提示"Tailscale 没有响应"或"还没登录"？** 打开 Tailscale 应用，确认已登录并显示已连接，再试一次。
