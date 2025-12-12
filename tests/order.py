from dataclasses import dataclass
from wake.testing import *

from pytypes.openzeppelin.contracts.token.ERC20.extensions.IERC20Metadata import IERC20Metadata


@dataclass
class Order(Struct):
    sellToken: IERC20Metadata
    buyToken: IERC20Metadata
    receiver: Address
    sellAmount: uint256
    buyAmount: uint256
    validTo: uint32
    appData: bytes32
    feeAmount: uint256
    kind: str
    partiallyFillable: bool
    sellTokenBalance: str
    buyTokenBalance: str
