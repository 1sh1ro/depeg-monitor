"""Per-cycle dependencies handed to price sources and context readers."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from depeg_monitor.config import AppConfig
from depeg_monitor.rpc import RpcClient


@dataclass
class FetchContext:
    client: httpx.Client
    rpc: RpcClient
    config: AppConfig
    now: float
