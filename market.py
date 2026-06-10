"""
LMSR (Logarithmic Market Scoring Rule) mechanics.

The LMSR cost function is:  C(q_yes, q_no) = b * ln(exp(q_yes/b) + exp(q_no/b))

- q_yes / q_no  : total shares outstanding for each side
- b             : liquidity parameter (higher = less price impact per dollar)

Starting at q_yes = q_no = 0, the YES probability is exactly 0.5.
Buying YES shares increases q_yes, which raises the YES probability.
Each YES/NO share pays out $1.00 if that side wins on resolution.
"""

import math

DEFAULT_B = 100.0  # liquidity parameter; adjust per market


def lmsr_cost(q_yes: float, q_no: float, b: float = DEFAULT_B) -> float:
    """LMSR cost function C(q_yes, q_no) with log-sum-exp numerical stability."""
    a, c = q_yes / b, q_no / b
    m = max(a, c)
    return b * (m + math.log(math.exp(a - m) + math.exp(c - m)))


def lmsr_prob_yes(q_yes: float, q_no: float, b: float = DEFAULT_B) -> float:
    """Current YES probability (= marginal cost of a YES share)."""
    a, c = q_yes / b, q_no / b
    m = max(a, c)
    ea, ec = math.exp(a - m), math.exp(c - m)
    return ea / (ea + ec)


def shares_for_dollars_yes(dollars: float, q_yes: float, q_no: float, b: float = DEFAULT_B) -> float:
    """
    Number of YES shares received when spending `dollars`.

    Derived by solving:
        C(q_yes + delta, q_no) - C(q_yes, q_no) = dollars
    """
    # Numerically stable form: factor exp(q_no/b) out of the inner expression.
    # delta = b * ln(exp(q_no/b) * [exp((q_yes-q_no+dollars)/b) + exp(dollars/b) - 1]) - q_yes
    x = (q_yes - q_no + dollars) / b
    y = dollars / b
    inner = math.exp(x) + math.exp(y) - 1.0
    if inner <= 0:
        raise ValueError("Dollar amount is too small to purchase any shares.")
    log_val = q_no / b + math.log(inner)
    return b * log_val - q_yes


def shares_for_dollars_no(dollars: float, q_yes: float, q_no: float, b: float = DEFAULT_B) -> float:
    """Number of NO shares received when spending `dollars`."""
    x = (q_no - q_yes + dollars) / b
    y = dollars / b
    inner = math.exp(x) + math.exp(y) - 1.0
    if inner <= 0:
        raise ValueError("Dollar amount is too small to purchase any shares.")
    log_val = q_yes / b + math.log(inner)
    return b * log_val - q_no


def dollars_for_selling_yes(sell_shares: float, q_yes: float, q_no: float, b: float = DEFAULT_B) -> float:
    """
    Dollars returned to the user for selling `sell_shares` YES shares back to
    the market.  The market's q_yes decreases by `sell_shares`.
    """
    if sell_shares > q_yes + 1e-9:
        raise ValueError("Not enough YES shares outstanding in the market.")
    return lmsr_cost(q_yes, q_no, b) - lmsr_cost(q_yes - sell_shares, q_no, b)


def dollars_for_selling_no(sell_shares: float, q_yes: float, q_no: float, b: float = DEFAULT_B) -> float:
    """Dollars returned for selling `sell_shares` NO shares back to the market."""
    if sell_shares > q_no + 1e-9:
        raise ValueError("Not enough NO shares outstanding in the market.")
    return lmsr_cost(q_yes, q_no, b) - lmsr_cost(q_yes, q_no - sell_shares, b)
