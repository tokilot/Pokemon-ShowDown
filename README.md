# Pokemon ShowDown Livestream Bot

这是一个基于 `FastAPI`、`poke-env` 和 `OpenAI` 开发的 Pokemon Showdown 对战直播项目。

项目会启动一个 Web 服务，并驱动一个 LLM Agent 登录 Pokemon Showdown 账号自动进行对战；前端页面会展示待机状态，并在发现对局后引导打开 Play Showdown 观战页面。对局结束后会回到本地待机画面，同时将历史对局记录保存到 `battle_history.json`。

## 项目说明

开发这个小项目的兴趣来源主要来自两方面：一是我对 LLM Agent 如何在规则明确、状态复杂、需要连续决策的环境中行动能模仿人类行动很感兴趣；二是 Pokemon Showdown 这类对战场景本身就非常适合作为观察智能体策略、失误、风格和推理过程的实验平台。相比纯文本任务，对战过程更直观，也更容易展示 Agent 在真实交互环境中的表现。

把「LLM 智能体」与「可实时观看的 Pokemon Showdown 对战界面」结合起来，做成一个既能自动对战、又方便展示和观察决策过程的小型直播应用。它不只是一个简单的对战脚本，更像是一个面向演示、实验和课程实践的 Agent 项目：后端负责调度对战与智能体决策，前端负责把当前状态和战斗画面实时呈现出来。


## 功能特性

- 使用 `poke-env` 连接 Pokemon Showdown 并控制对战流程
- 使用 OpenAI 兼容接口驱动智能体决策
- 基于 `FastAPI + WebSocket` 实时推送对战页面状态
- 提供首页直播视图与最近动作日志页
- 支持 `ladder`、`accept`、`challenge` 三种匹配模式

## 项目结构

```text
.
├── main.py                 # FastAPI 应用入口与生命周期管理
├── agents.py               # LLM 对战代理逻辑
├── pages.py                # HTML 页面与静态资源路由
├── utils.py                # 配置加载与日志辅助函数
├── requirements.txt        # Python 依赖
├── Dockerfile              # 容器构建文件
├── battle_history.json     # 历史对局记录
└── yaml/
    ├── config.example.yaml # 配置模板
    ├── config.yaml         # 本地实际配置（需自行创建）
    ├── schema.yaml         # Agent 输出结构约束
    └── pokemon.png         # 页面资源图片
```

## 环境要求

- Python `3.9+`
- 一个可登录的 Pokemon Showdown 账号
- 一个可用的 OpenAI 兼容 API Key

## 安装依赖

```bash
pip install -r requirements.txt
```

## 配置说明

复制示例配置：

```bash
cp yaml/config.example.yaml yaml/config.yaml
```

然后修改 `yaml/config.yaml`，至少填写以下内容：

```yaml
showdown:
  username: your_showdown_username
  password: your_showdown_password

battle:
  format: gen9randombattle

matchmaking:
  mode: ladder
  challenge_target_username:
  matches_per_activation: 1

openai:
  api_key: your_openai_api_key
  model: gpt-5.2
  base_url: https://right.codes/codex/v1
```

### 配置项说明

- `showdown.username`：Pokemon Showdown 登录用户名
- `showdown.password`：Pokemon Showdown 登录密码
- `battle.format`：对战模式，如 `gen9randombattle`
- `matchmaking.mode`：匹配方式，可选 `ladder`、`accept`、`challenge`
- `matchmaking.challenge_target_username`：当模式为 `challenge` 时的目标用户名
- `matchmaking.matches_per_activation`：每轮激活时发起的对局数量，默认建议保持 `1`
- `openai.api_key`：LLM 服务的 API Key
- `openai.model`：使用的模型名称
- `openai.base_url`：OpenAI 兼容接口地址

## 启动方式

### 本地启动

```bash
python main.py
```

默认启动后服务地址为：

- 首页：`http://127.0.0.1:6007/`

### 使用 Uvicorn 启动

```bash
uvicorn main:app --host 0.0.0.0 --port 6007
```

## 页面说明

- `/`：直播主页面，会通过 WebSocket 动态更新当前对战视图
- `/ws`：前端使用的 WebSocket 接口

## 运行流程

1. 应用启动后读取 `yaml/config.yaml`
2. 校验 Pokemon Showdown 账号与 OpenAI 配置
3. 创建后台生命周期任务
4. 根据匹配模式进入 ladder、等待挑战或主动挑战指定用户
5. 前端页面通过 WebSocket 接收当前状态与对战页面

## Docker

项目包含 `Dockerfile`，可以按需构建镜像：

```bash
docker build -t pokemon-showdown-bot .
```

说明：当前 `Dockerfile` 中包含若干构建期 secret 挂载示例，使用前需要根据你的部署环境进一步调整。

## 注意事项

- 实际运行前请确认 `yaml/config.yaml` 已创建并填写正确
- `battle_history.json` 在首次启动时会自动创建
- `matches_per_activation` 建议保持为 `1`，当前实现更适合单场并发
- 若前端页面无法显示对战内容，请先检查后端日志、Showdown 账号登录状态和 API 配置

## 依赖说明

`requirements.txt` 当前包含以下核心依赖：

- `fastapi`：提供 Web 服务和接口
- `uvicorn[standard]`：运行 ASGI 服务
- `poke-env`：对接 Pokemon Showdown
- `httpx` / `aiohttp`：网络请求与异步通信
- `openai`：调用 OpenAI 兼容模型接口

## 致谢

本项目的整体思路受到 Hugging Face Agents Course Bonus Unit 3 的启发：

- 课程页面：`https://huggingface.co/learn/agents-course/bonus-unit3/`

在实现过程中，也参考了 Hugging Face Space `PShowdown/pokemon_agents` 的项目结构与相关源码思路：

- 参考源码：`https://huggingface.co/spaces/PShowdown/pokemon_agents`

感谢以上课程内容与开源项目提供的思路启发，让这个项目能够在课程实践、Agent 设计和对战可视化之间建立起一个比较完整的实现。
