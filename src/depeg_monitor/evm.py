"""Tiny ABI helpers for the few view calls this monitor makes.

The monitor never signs a transaction. These helpers only decode ``eth_call``
results from public price feeds, pool spot quotes, and ERC-20 supplies.
"""

from __future__ import annotations

SEL_LATEST_ROUND = "0xfeaf968c"  # latestRoundData()
SEL_TOTAL_SUPPLY = "0x18160ddd"  # totalSupply()
SEL_CONVERT_TO_ASSETS = "0x07a2d13a"  # convertToAssets(uint256)
SEL_GET_DY = "0x5e0d443f"  # get_dy(int128,int128,uint256)
SEL_BALANCES = "0x4903b0d1"  # balances(uint256)
SEL_SLOT0 = "0x3850c7bd"  # slot0()

WORD_BITS = 256
WORD_BYTES = 32
INT256_SIGN = 2**255


class AbiError(ValueError):
    """A response was not the ABI shape the caller expected."""


def encode_uint(value: int) -> str:
    if value < 0 or value >= 2**WORD_BITS:
        raise AbiError(f"uint256 out of range: {value}")
    return f"{value:064x}"


def encode_get_dy(i: int, j: int, dx: int) -> str:
    """ABI data for Curve ``get_dy(int128,int128,uint256)``.

    Non-negative indexes encode the same way as uint256. This matches the
    plain pools and stable-ng pools checked against public Ethereum RPCs.
    """
    return SEL_GET_DY + encode_uint(i) + encode_uint(j) + encode_uint(dx)


def encode_word_call(selector: str, value: int) -> str:
    return selector + encode_uint(value)


def hex_to_words(data: str) -> list[int]:
    raw = data[2:] if data.startswith("0x") else data
    if not raw:
        raise AbiError("empty eth_call result")
    if len(raw) % 64 != 0:
        raise AbiError("eth_call result is not 32-byte aligned")
    try:
        return [int(raw[i : i + 64], 16) for i in range(0, len(raw), 64)]
    except ValueError as exc:
        raise AbiError("eth_call result is not hex") from exc


def as_int256(word: int) -> int:
    if word >= INT256_SIGN:
        return word - 2**WORD_BITS
    return word


def parse_latest_round_data(data: str) -> tuple[int, int]:
    """Return ``(answer, updated_at)`` from Chainlink ``latestRoundData``.

    Layout is five ABI words: roundId, answer, startedAt, updatedAt,
    answeredInRound. ``answer`` is int256.
    """
    words = hex_to_words(data)
    if len(words) < 5:
        raise AbiError(f"latestRoundData returned {len(words)} words, want 5")
    return as_int256(words[1]), words[3]


def price_from_answer(answer: int, decimals: int) -> float:
    if decimals < 0 or decimals > 36:
        raise AbiError(f"implausible feed decimals: {decimals}")
    return answer / 10**decimals


def parse_uint256(data: str) -> int:
    words = hex_to_words(data)
    if len(words) < 1:
        raise AbiError("missing uint256 word")
    return words[0]


def is_fresh(
    updated_at: int, now: float, max_age_seconds: int, future_skew_seconds: int = 600
) -> bool:
    """True when ``updated_at`` is recent enough to use as a price.

    A timestamp slightly ahead of the local clock is treated as fresh so a
    small skew does not drop an otherwise healthy feed.
    """
    if updated_at <= 0:
        return False
    age = now - updated_at
    if age < -future_skew_seconds:
        return False
    return age <= max_age_seconds


def sqrt_price_x96(data: str) -> int:
    """First word of Uniswap V3 ``slot0()``."""
    words = hex_to_words(data)
    if not words:
        raise AbiError("empty slot0")
    return words[0]


def price_from_sqrt(
    sqrt_price_x96_value: int,
    *,
    base_is_token0: bool,
    decimals0: int,
    decimals1: int,
) -> float:
    """Human price of the configured base token, in units of the other token.

    Uniswap stores ``sqrt(token1_raw / token0_raw) * 2**96``. Multiplying by
    ``10 ** (decimals0 - decimals1)`` converts that ratio into whole tokens.
    """
    if sqrt_price_x96_value <= 0:
        raise AbiError("sqrtPriceX96 is zero")
    raw = (sqrt_price_x96_value / 2**96) ** 2
    token1_per_token0 = raw * 10 ** (decimals0 - decimals1)
    if token1_per_token0 <= 0:
        raise AbiError("pool price is zero")
    if base_is_token0:
        return token1_per_token0
    return 1.0 / token1_per_token0
