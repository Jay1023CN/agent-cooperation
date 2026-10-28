# 用 GitHub 登录，并用成员自己的账号推送

配置一次之后：

- 成员和伙伴点「用 GitHub 登录」就能进工作台，不用再粘贴访问令牌。
- 项目设置里选了「提交与推送身份」的成员，任务提交的作者就是他，推送也用他的账号；**任何一台执行电脑都能替他推送**，不需要在每台电脑上再登录 GitHub。

整个过程大约 5 分钟，只需要做一次。

## 1. 新建一个 GitHub App

打开 [GitHub → Settings → Developer settings → GitHub Apps → New GitHub App](https://github.com/settings/apps/new)，填写：

| 项目 | 填什么 |
| --- | --- |
| GitHub App name | 随便起，比如 `our-agent-desk` |
| Homepage URL | 工作台地址，比如 `https://mac-mini.tail1234.ts.net` |
| Callback URL | 工作台地址后面加 `/api/github/callback`。工作台里「伙伴与令牌 → GitHub 登录设置」会把要填的地址列出来；本机地址和 Tailscale 地址可以都加上 |
| Expire user authorization tokens | **勾选**（必须，中心拿到的令牌 8 小时过期，自动续期） |
| Webhook → Active | 取消勾选 |
| Repository permissions → Contents | **Read and write** |
| 其他权限 | 保持默认 |
| Where can this GitHub App be installed? | 仓库都在你自己账号下选 *Only on this account*；伙伴的仓库也要用就选 *Any account* |

点 **Create GitHub App**。

## 2. 安装到你们的仓库

在刚建好的 App 页面左侧点 **Install App**，选择账号，再选要让 AI 推送的仓库。伙伴的仓库在他自己的账号下时，请他也装一次。

## 3. 把 Client ID 和 Client secret 填进工作台

在 App 页面复制 **Client ID**，再点 **Generate a new client secret** 复制密钥。回到工作台，打开「伙伴与令牌 → GitHub 登录设置」，填进去保存。

## 4. 每位成员连接一次

- **你自己**：在「伙伴与令牌」里点自己那一行的「连接 GitHub」，在 GitHub 页面点授权。
- **伙伴**：邀请时填上他的 GitHub 用户名，他打开工作台点「用 GitHub 登录」就进来了，同时完成连接。

之后在项目设置里选好「提交与推送身份」就行。

## 令牌放在哪、谁能用

- Client secret 和成员的 GitHub 令牌只保存在中心电脑数据目录的 `github.sqlite` 里。备份工具不备份这个文件，从备份恢复后成员重新用 GitHub 登录一次即可。
- 访问令牌 8 小时过期，中心在快过期时用续期令牌换新的。续期令牌约 6 个月有效；超过这个时间没用过，需要成员重新登录。
- 派任务时，中心只把短期访问令牌随任务交给执行电脑，由执行端在提交和推送那一步通过环境变量交给 Git，不写入磁盘。AI 运行时的环境里没有这个令牌。
- App 只有你安装过的仓库的「写代码」权限，碰不到其他仓库和账号设置。
- 撤销成员访问时，中心会同时删除他的 GitHub 令牌。成员也可以在「伙伴与令牌」里自己点「断开 GitHub」。想彻底收回，到 GitHub 的 [Settings → Applications → Authorized GitHub Apps](https://github.com/settings/apps/authorizations) 撤销这个 App。

## 和「本机登录」的关系

没有配置 GitHub App 时，仍然可以在每台执行电脑上运行 `pal-arena github login --member <成员 ID>`，见 [调度台说明](arena.md#用成员自己的-github-账号提交和推送)。两种都配置时，执行电脑本机有这位成员的登录就优先用本机的，令牌不经过中心；没有才用中心发的短期令牌。
