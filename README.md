# 多智能体软件工程工作台

第 11 组大作业雏形。输入一段软件需求，工作台生成任务拆解和讨论材料，组织四路独立提案、聚类、质询与收敛，再推进建设方案、评价、需求规格和人工签收。每一次派发、门禁判定、失败、恢复及版本变更都能从界面回查。

## Windows 双击启动 DeepSeek

解压后双击 `Start-DeepSeek.cmd`，保持启动窗口打开，再访问 [本地工作台](http://127.0.0.1:8765)。需要 Python 3.10 或更高版本。

启动器会读取当前 Windows 账户已有的加密配置。首次在另一台电脑或另一个账户上运行时，启动窗口会提示输入 DeepSeek API 密钥，并为该账户加密保存。更换密钥可在项目目录执行 `Start-DeepSeek.cmd --configure`。

## 运行

需要 Python 3.10 或以上。后端使用标准库和 SQLite，前端无需安装依赖。

```powershell
python app.py
```

打开 `http://127.0.0.1:8765`。默认使用本地演示模型，无需 API 密钥。输入自己的议题，新建运行，再按工作台操作区逐步推进。页面显示的阶段和活动来自后端记录，关闭页面后可以重新打开继续查看。可用 `--port` 和 `--db` 指定端口及数据文件。

## 使用 DeepSeek

在 PowerShell 中安全地读取新密钥，避免将它写进命令历史、源代码或配置文件：

```powershell
$env:DEEPSEEK_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'DeepSeek API Key' -AsSecureString)).Password
try { python app.py --provider deepseek } finally { Remove-Item Env:DEEPSEEK_API_KEY }
```

DeepSeek 模式会把议题与相应节点输入发送给 DeepSeek。`DEEPSEEK_FAST_MODEL` 默认 `deepseek-flash`，用于简报、独立提案及常规整理。`DEEPSEEK_REASONING_MODEL` 默认 `deepseek-v4-pro`，用于批判和综合评价等复杂步骤。模型由后端按节点选择，资格检查和放行由编排规则执行。模型、用量和可估算费用记录在工作台，费用为估算值。模型接口及计费口径见 [DeepSeek 官方文档](https://api-docs.deepseek.com/quick_start/pricing/)。

## 演示顺序

1. **定题和拆解**：整理目标、任务、角色与依赖，负责人确认后冻结简报。任务图可导出为包含运行版本的 JSON。
2. **群体讨论**：产品、用户、技术、质疑四席独立提案，等待所需结果到齐后公开。随后聚类、质询、记录立场及处理异议。查看 G1 未通过时对应的阻塞原因。
3. **方案和需求**：形成建设方案和评价，生成带验收条件的需求规格，经负责人显式签收后保留确认版本。
4. **实现和证据**：推进编码、测试和评审的模拟节点，从任一节点查看产物、用量、失败及后续路径，再选择历史事件查看当时的状态。

首版面向本机单人课堂演示。议题生成的任务图记录角色、依赖与难度，并参与独立提案的模型路由。工作台使用预设阶段、门禁和人工确认规则推进。编码、测试与评审使用模拟产物，接真实代码仓库执行和多账号协作是后续开发范围。

## 开发与验证

```powershell
python -m unittest discover -s tests -v
```

| 路径 | 职责 |
| --- | --- |
| `app.py` | 本机 HTTP 入口与 JSON API |
| `workflow/engine.py` | 任务与阶段、门禁、讨论分流和人工确认 |
| `workflow/store.py` | 状态、事件、幂等与历史查询 |
| `workflow/provider.py` | 演示模型及 DeepSeek JSON 输出适配 |
| `frontend/` | Workflow 总览、群体讨论、事件追溯与确认界面 |
| `tests/` | 模型接口、工作流或 HTTP 约定的验证 |
| `docs/design.md` | 设计范围、交接、数据透明化与联调路线 |
