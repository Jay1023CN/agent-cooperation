# DeepSeek Harness 接入

DSH 在 Agent Cooperation 中是执行任务的 harness，接入标识为 `dsh`。平台通过 ACP 创建独立任务会话，传入当前 Git 工作目录和任务专属 MCP，再接收执行结果、权限请求和取消状态。任务仍进入原有的提交与独立验收流程。

模型及供应商由 DSH 配置。平台不注入 DeepSeek 官方模型、API 地址或 API Key；模型 ID 留空时也不会主动切换模型。

## 先配置 DSH

本机需要可启动的 `dsh` 命令，以及一个包含 ACP bridge 的 profile。默认启动命令是 `dsh --profile acp`；也可以指定自己的 profile。

注意：DSH 自带 `acp` profile 的初始配置指向 DeepSeek 官方模型。要用其他服务商，需要先在 DSH 的 profile 或 home patch 中配置实际路由；桌面聊天里的模型设置不等于 ACP profile 的设置。Agent Cooperation 不会复制桌面登录信息，也不会替你更改 DSH 配置。

profile 的配置层依次包括 bundle、`$DSH_HOME/profiles/<名称>/cordis.patch.yml` 和 `$DSH_HOME/cordis.patch.yml`。自定义 profile 必须保留 ACP bundle。具体字段以 [DSH CLI 文档](https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/cli/README.md)和 [ACP 配置文档](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/acp/acp/README.md)为准。

## 添加执行成员

在工作台的电脑配置中选择「DeepSeek Harness · ACP」，高级设置里填已配置的 ACP profile 名称，下载 `worker.json`。等价的成员配置如下：

```json
{
  "key": "dsh-worker",
  "name": "DSH 执行成员",
  "provider": "dsh",
  "roles": ["worker"],
  "capabilities": ["code", "docs"],
  "adapter": {"profile": "cooperation"}
}
```

`adapter.profile` 留空时使用 `acp`。`command` 可填写启动命令的参数数组，例如 `["node", "/absolute/path/to/dsh/bin.mjs"]`；只填可执行入口，平台负责追加 `--profile <名称>`。Windows 自动发现也支持标准 npm 安装的 DSH。

模型 ID 留空使用该 profile 的配置。需要覆盖时，填写 DSH 通过 ACP `configOptions` 返回的模型选项值；该值是 DSH 的路由标识，不保证等于模型的显示名称。平台使用 `session/set_config_option` 选择模型，不使用 Qoder 的旧模型接口。

## 当前验证范围

协议夹具已验证启动参数、默认模型保留、显式模型选择、任务专属 MCP、权限转交、结果流与取消清理。电脑预检只检查可执行入口，不提交模型请求，也不把找到程序当成账号可用。

本次没有使用真实 DSH 账号执行模型任务；真实 profile、凭证和模型路由仍需要在安装 DSH 的电脑上联调。
