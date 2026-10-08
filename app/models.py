from dataclasses import dataclass, field, asdict
from typing import List, Optional
from time import time

@dataclass
class RankedDigit:
    rank: int
    digit: int
    score: float

@dataclass
class Leg:
    rank: int
    digit: int
    stake: float
    proposal_id: Optional[str] = None
    contract_id: Optional[str] = None
    payout: float = 0.0
    profit: float = 0.0
    result: str = "PENDING"

@dataclass
class Basket:
    basket_no: int
    symbol: str
    account_mode: str
    source_epoch: int
    source_digit: int
    total_stake: float
    stake_per_leg: float
    ranked: List[RankedDigit]
    legs: List[Leg]
    status: str = "ARMED"
    outcome_epoch: Optional[int] = None
    outcome_digit: Optional[int] = None
    gross_return: float = 0.0
    net_profit: float = 0.0
    created_at: float = field(default_factory=time)
    settled_at: Optional[float] = None

    def as_dict(self):
        return asdict(self)
