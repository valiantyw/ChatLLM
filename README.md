# ChatLLM

MiniMax、QuickRouter 与 NVIDIA NIM 的 Tkinter 桌面客户端，支持流式对话、附件、
图像与音乐生成，以及本地会话历史。

## 环境准备

需要 Python 3.11+、Tkinter 和图形桌面。主要平台为 Windows；Linux 还需安装
`python3-tk`。

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 配置

以 `.env.example` 为模板，创建项目根目录下的 `.env`，只配置你实际使用的厂商：
API key 与对应的端点变量都必须提供。代码中没有任何硬编码的端点默认值；
已存在的进程环境变量优先级高于 `.env`。

| 厂商 | API key | 必需的端点变量 |
| --- | --- | --- |
| MiniMax | `MINIMAX_API_KEY` | `MINIMAX_BASE_URL` |
| QuickRouter | `QUICKROUTER_API_KEY` | `QUICKROUTER_BASE_URL` |
| NVIDIA NIM | `NVIDIA_API_KEY` | `NVIDIA_NIM_BASE_URL` |

MiniMax 的音乐生成接口自 2026 年 8 月 20 日起不再向新用户开放：这类账号请求时会
返回 HTTP 410、`base_resp.status_code` 为 2153，应用会把厂商的原文、错误码和
Trace-Id 一并显示出来。现有付费用户不受影响；新用户可用 MiniMax 的网页版
Audio，或开源的 [MiniMax-Music3](https://huggingface.co/MiniMaxAI/MiniMax-Music3)
模型。图像生成与聊天不受此调整影响。

`.env` 只放 key 和地址。超时与自动标题是 `tk-ChatLLM.py` 顶部的常量。
端点必须使用 HTTPS；出现证书错误时应配置受信任的 CA，而不是关闭校验。

`MINIMAX_STREAM_MODE` 同样是文件顶部的常量，而不是环境变量。它的 `auto` 取值
并非自动探测：其行为与 `cumulative` 完全一致，适合每个分片都重发「截至目前完整
文本」的服务端；若服务端只发送新增文本，请改用 `delta`。

## 运行

```powershell
.\.venv\Scripts\python.exe -B tk-ChatLLM.py
```

- 回车发送，Shift+回车换行；支持取消与重试。
- 模型名称与能力在每个 `providers/*_api.py` 的 `MODELS` 表中定义一次，
  `PROVIDERS`、界面里的模型列表，以及每项能力使用的端点都由它派生。
  因此新增厂商只需添加一个 `providers/*_api.py` 模块，暴露 `MODELS`、
  `PROVIDERS`、可选的 `ALIASES` 与 `provider_settings(kind=...)`，
  无需改动主程序。端点按能力选择，而不是按厂商名判断。
- 旧版本写下的会话可能带有已不可选的厂商名（`MiniMax (Native)`、
  `MiniMax (Anthropic)`）。这些名字仍可解析，以便历史记录正常加载；
  它们仅用于读取。
- 附件支持 UTF-8 文本和模型支持的图片/视频；不支持 PDF 与音频输入。
  上限：文本 1 MiB、图片 10 MiB、视频与合计 20 MiB。
- API 访问与计费取决于你的厂商账户。自动标题会额外发起一次请求；
  本地取消不一定能取消服务端的处理或计费。

## 上下文预算

每个模型在其 `MODELS` 表中声明 `context_budget`，单位是**近似 token 数**，
不是字节或字符数。估算方式：非拉丁字符大致每字 1 个 token，Latin-1 文本大致
每 4 个字符 1 个 token；再为回复预留 2048 个 token，每张图片或视频另计
5120/8192 个 token。想用满大模型的上下文窗口，可在对应厂商的 `MODELS` 表中调高
`context_budget`。超出预算时按「从最旧开始」丢弃历史轮次，界面上会提示省略了几轮。

## 数据

历史记录是 `conversations/` 下每个会话一个 JSON 文件，旁边是 `attachments/`
附件快照和 `cache/` 媒体缓存。内容变化时采用紧凑的原子写入；没有变化则跳过保存。
旧文件保持兼容。

**新建的会话只有在模型给出有效回复之后才会写盘。** 回复失败、被取消或中断都不算
有效回复，只输入了草稿而没有发送也不算，因此这些会话不会留下 JSON 文件。会话一旦
有了有效回复就会正常保存，之后即使某次请求失败也不会消失。

启动时会读取并校验每个会话文件来生成历史列表，因此启动开销随历史总量增长
（600 条消息约 15 毫秒）。随后只有当前会话和最近使用的 8 个会话保留消息体；
尚未获得有效回复的会话在切换离开时会被丢弃。

`cache/` 中的媒体按内容寻址，不会自动过期，应用也不会自动清理它。以 Base64
返回的生成图没有可重新下载的 URL，文件一旦删除就无法恢复；有 URL 的图片和音频
在需要时会重新下载。删除会话不会连带删除共享的附件与媒体，因此这些文件会留在
`cache/` 里。需要回收磁盘空间时，直接清理 `cache/` 目录中不再需要的文件即可——
注意确认对应的会话已经不再使用它们。

数据未加密：请整体备份该目录，不要提交它或 `.env`。
同一个数据目录请只运行一个应用实例。

## 测试

```powershell
py -3 -m unittest discover -s tests -v
```

测试套件只依赖标准库，不需要额外安装任何东西。GUI 测试会创建真实但隐藏的 Tk
窗口，在没有图形界面时会自动跳过。

主程序文件名含连字符，无法用 `import` 语句导入，因此 `tests/app_under_test.py`
用 `importlib` 按路径加载它，并注册成名为 `chatllm` 的模块。测试里统一写
`from app_under_test import chatllm`。
