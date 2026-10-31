from pathlib import Path

import pytest

from depeg_monitor.config import ConfigError, load_config
from depeg_monitor.notify import build_notifiers

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def test_shipped_config_loads():
    config = load_config(ROOT / "config.yaml")
    symbols = {coin.symbol for coin in config.enabled_coins()}
    assert symbols == {"USDC", "USDT", "DAI", "USDe", "USDS", "PYUSD", "FDUSD", "sUSDe"}
    susde = config.coin("sUSDe")
    assert susde is not None
    assert susde.peg_mode == "vault"
    assert susde.vault is not None
    assert susde.vault.numeraire == "USDe"
    assert config.chainlink_feeds["DAI"][1].decimals == 18
    assert config.cex["binance"].base_url.startswith("https://data-api.binance.vision")


def test_recovery_must_sit_below_the_info_tier(tmp_path: Path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        """
defaults:
  recovery_bps: 30
  thresholds_bps: {info: 20, warning: 50, critical: 100}
  cooldown_seconds: {info: 10, warning: 10, critical: 10}
chains:
  ethereum: ["https://example.invalid"]
coins:
  USDC:
    token: {chain: ethereum, address: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", decimals: 6}
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="recovery_bps"):
        load_config(path)


def test_notifiers_come_only_from_the_environment(monkeypatch: pytest.MonkeyPatch):
    for name in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "DISCORD_WEBHOOK_URL",
        "ALERT_WEBHOOK_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    assert build_notifiers() == []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://example.invalid/hook")
    names = [item.name for item in build_notifiers()]
    assert names == ["telegram", "webhook"]


def test_source_tree_has_no_trading_surface():
    banned = ("eth_sendRawTransaction", "private_key", "eth_account", "sign_transaction")
    text = "\n".join(path.read_text(encoding="utf-8") for path in SRC.rglob("*.py"))
    lowered = text.lower()
    for word in banned:
        assert word.lower() not in lowered
