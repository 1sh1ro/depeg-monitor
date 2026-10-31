# 稳定币脱锚监控

只读、只告警的稳定币价格监控。USDC、USDT、USDe、DAI、USDS、FDUSD、PYUSD 相对 1 美元，sUSDe 相对其金库公允价值。偏离达到阈值时，通过 Telegram（可选 Discord 或通用 webhook）发出中文告警，并附上供应、池子占比、资金费等背景数据，方便区分「短时抛压」和「需要继续核对的异常」。

**本程序不持有私钥，不签名，不下单，不构成投资建议。**

## English

Alert-only monitor for USDC, USDT, USDe, DAI, USDS, FDUSD, PYUSD (against $1) and sUSDe (against its vault fair value). It reads public prices, requires a quorum before paging, and sends Chinese Telegram messages. No keys, no orders, not financial advice. Setup steps are in the Chinese sections below; the secrets you must set are `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

## 它做什么

- 多个独立价格来源，每个来源是一个可插拔模块：Chainlink（Ethereum，另有 Arbitrum / BSC 上存在的喂价）、Curve 池子现货 `get_dy`、Uniswap V3 `slot0`、Binance / Coinbase / Kraken / OKX / Bybit 现货，DeFiLlama 与 CoinGecko 作为后备。
- 先把 USDT、USDC 等报价换成美元（用该稳定币自己的美元来源中位数），再对每个来源取中位数。法定人数默认至少 3 个来源、2 类场所（预言机 / DEX / CEX / 聚合器）。单一坏喂价不会触发告警。
- Chainlink、DEX、CEX 三组中位数差距过大时，在告警里标出，用来看预言机是否滞后。
- 按币配置的 bps 分级：提示 / 警告 / 严重。回到更紧的恢复阈值时发「回归锚定」。同一级别在冷却时间内不重复；升级立即发送。某一轮数据不够时，不把旧告警当成已经恢复。
- 背景数据（失败则跳过，不影响价格告警）：代币供应量变化、Curve 池子占比与深度变化、USDe / sUSDe 的 OKX 永续资金费、sUSDe 的 `convertToAssets` 汇率。告警里的「数据线索」只描述这些数字的形态。

## 它不做什么

- 不读取、不保存私钥或助记词。配置和密钥里只需要机器人 token 和聊天 ID。
- 不发送链上交易，不连接交易所下单接口。
- 不给出买卖建议。告警里的句子是数据描述。

sUSDe 是质押凭证，价格会跟着收益慢慢高于 1 USDe。这里比较的是市场价相对 `convertToAssets` × USDe 共识价，不是相对 1 美元。

## 本地运行

需要 Python 3.11 或更新版本。

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
cp .env.example .env
# 编辑 .env，至少填入 Telegram 两项；只想看输出可以先不填
python -m depeg_monitor --dry-run
python -m depeg_monitor --loop --interval 30
```

`--dry-run` 把告警打到终端，不发送。默认仍会把去重状态写到 `.state/monitor_state.json`（已 gitignore）。不想写状态时加 `--no-save-state`。

公开 RPC 写在 `config.yaml`。自建节点用环境变量插到列表最前面：`RPC_ETHEREUM_URL`、`RPC_ARBITRUM_URL`、`RPC_BSC_URL`（多个地址用逗号分隔）。

## 创建 Telegram 机器人

1. 在 Telegram 里打开 [@BotFather](https://t.me/BotFather)，发送 `/newbot`，按提示取一个名字。
2. BotFather 返回的 HTTP API token 就是 `TELEGRAM_BOT_TOKEN`。不要发到聊天记录或提交到 git。
3. 给你的机器人发一条任意消息（如果要发到频道，先把机器人加为频道管理员）。
4. 浏览器打开 `https://api.telegram.org/bot<token>/getUpdates`，在结果里找 `chat.id`。私聊是正整数，超级群或频道通常是 `-100` 开头。这就是 `TELEGRAM_CHAT_ID`。
5. 写入 `.env`，或在 GitHub 仓库的 Settings → Secrets and variables → Actions 里添加同名 secret。

可选：

| 变量 | 作用 |
| --- | --- |
| `DISCORD_WEBHOOK_URL` | Discord incoming webhook |
| `ALERT_WEBHOOK_URL` | 任意接收 JSON `{"event","text"}` 的地址 |
| `COINGECKO_API_KEY` | CoinGecko demo key，不填也能跑 |
| `LOG_LEVEL` | 默认 `INFO` |

没有 Telegram、也没有 webhook 时，正式运行会直接退出。`--dry-run` 不需要这些密钥。

## GitHub Actions

仓库里有两个 workflow：

- `.github/workflows/ci.yml`：测试和 lint。
- `.github/workflows/monitor.yml`：每 5 分钟跑一轮（`*/5 * * * *`），也可手动触发。手动触发时可以勾选 dry-run。

GitHub 定时任务最短就是 5 分钟，而且负载高时会晚十几分钟甚至更久。仓库 60 天没有任何提交时，GitHub 会暂停 schedule，需要有人手动跑一次才会恢复。要更密、更准时的告警，用下面的 Docker 循环，不要指望 Actions 的 cron。

第一次使用：

1. 把本仓库的 Actions 打开（Actions 页如果提示启用 workflow，点启用）。
2. 添加 secret：`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`。其余见上表，都是可选的。
3. 在 Actions 里打开 Depeg monitor，用 workflow_dispatch 先 dry-run 一次，确认日志里各币都有价格。
4. 去掉 dry-run 再跑，或等下一次 schedule。

去重状态放在 Actions cache（`.state/monitor_state.json`），key 带 `run_id`，下一轮用 `restore-keys` 找回。缓存偶尔会丢，丢了之后下一轮可能把仍在脱锚的币再报一次，不会把「没读到数据」报成已经恢复。workflow 只有 `contents: read`，不能往仓库推代码；`actions: write` 只用于这块缓存。

## Docker

```bash
docker compose up -d --build
```

容器入口是 `depeg-monitor --loop --interval 30`。状态在 volume `/data`。环境变量来自 `.env`。

## 配置

可运行的示例就是仓库根目录的 [`config.yaml`](config.yaml)（[`config.example.yaml`](config.example.yaml) 是同一份副本）。阈值、池子地址、交易对都在这里，不放密钥。

每个币可以覆盖：

- `thresholds_bps`：`info` / `warning` / `critical`，单位是基点（100 bps = 1%）。必须越来越严。
- `recovery_bps`：必须小于 `info`，避免在阈值边缘来回翻。
- `cooldown_seconds`：同一级别的重复告警间隔。升级、降级、恢复不受冷却限制。
- `min_sources` / `min_categories`：法定人数。
- `sanity_min` / `sanity_max`：明显不可能的价格（例如 0 或 10）在进入中位数之前丢掉。

默认大致是：提示 20 bps、警告 50 bps、严重 100 bps。USDC 更紧。FDUSD 的 Chainlink 报价经常比 1 美元低几十个 bps，所以它的提示阈值是 40 bps，避免把常态折价当成新事故。sUSDe 的偏离是相对金库公允价值。

分组分歧默认 40 bps。共识价还在锚定内、但 Chainlink 与 DEX/CEX 拉开时，会另发一条「来源分歧」提示。

## 背景数据怎么读

这些句子出现在告警正文里，不是操作建议：

- **预言机滞后**：Chainlink 更靠近锚定，DEX 和 CEX 同向偏离。
- **单边流入**：该币在监控池中的占比既高于均衡，又比上一份快照跳了一截。3pool 长期 USDT 偏多，所以只看水位、不看跳变，不会被标成「新的抛压」。水位本身仍会写在告警里。
- **供应变化**：窗口默认 24 小时，绝对值达到 3% 才标出。增加通常对应净铸造，减少通常对应净赎回。
- **深度下降**：池子代币存量比上次快照少 20% 以上。
- **资金费**：只对 USDe / sUSDe。OKX 的 ETH 与 BTC 永续资金费都低于 -2 bps（当次结算）才标出。负数表示空头在付钱。

任何一项读失败，告警里会写「本轮没有取到」或直接省略，价格告警照常。

## 已知限制

- `api.binance.com` 在很多云区域（包括 GitHub 托管 runner）返回 HTTP 451。默认改用公开行情镜像 `data-api.binance.vision`。Binance 现货是 USDT 计价，会再用 USDT 的美元中位数换算。
- Bybit 经常对云厂商 IP 返回 403。这一路失败不影响其他来源。
- Binance 合约资金费接口同样有地域限制，所以资金费用 OKX 公共接口。
- 稳定币对稳定币的池子价格，在两边一起脱锚时会互相引用。换算步骤和 CEX / Chainlink 的美元价格用来减轻这个问题，不能从数学上消除。
- 公开 RPC 会限流或偶发失败。程序会换下一个 URL；整条链失败就丢掉这条链的喂价。
- CoinGecko 免费接口会被限流。失败就跳过。
- Curve 的 HTTP 价格若超过 12 小时没更新（FDUSD 出现过），会丢弃。链上 `get_dy` 仍可用。
- USDS 的 Uniswap v3 池较浅，只是 DEX 来源之一。

## 开发

```bash
pip install -r requirements-dev.txt
pip install -e .
ruff check src tests
ruff format --check src tests
pytest
```

测试不访问网络。解析器、中位数与法定人数、分级、冷却、恢复、背景线索都在 `tests/`。

## 先前项目

设计时参考了这些项目的问题定义，没有复制它们的代码：

- yearn 的 stables 监控：用聚合价格加 Telegram。
- 多来源脱锚监控：DEX 与 CEX 分开看，并设阈值。
- 以 bps 分档，而不是只报一个百分比。
- 把 Curve 池子失衡当作领先数据，而不是事后解释。

本仓库是独立的 MIT 实现。

## License

MIT。见 [LICENSE](LICENSE)。
