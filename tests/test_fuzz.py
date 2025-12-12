import logging
from collections import defaultdict
from dataclasses import dataclass
from wake.testing import *
from wake.testing.fuzzing import *
from typing import NamedTuple

from pytypes.source.contracts.AmountConverter import AmountConverter
from pytypes.source.contracts.Order import Order
from pytypes.source.contracts.Stonks import Stonks
from pytypes.source.contracts.factories.AmountConverterFactory import AmountConverterFactory
from pytypes.source.contracts.factories.StonksFactory import StonksFactory
from pytypes.source.contracts.interfaces.ICoWSwapSettlement import ICoWSwapSettlement
from pytypes.source.contracts.interfaces.IFeedRegistry import IFeedRegistry
from pytypes.source.contracts.routers.OracleRouter import OracleRouter
from pytypes.tests.AggregatorV2V3Interface import AggregatorV2V3Interface
from pytypes.tests.MockAggregator import MockAggregator
from pytypes.openzeppelin.contracts.token.ERC20.extensions.IERC20Metadata import IERC20Metadata
from pytypes.tests.IstETH import IstETH

from .order import Order as CoWOrder


logger = logging.getLogger(__name__)

CHAINLINK_FEED_REGISTRY = IFeedRegistry("0x47Fb2585D2C56Fe188D0E6ec628a38b74fCeeeDf")
COW_SETTLEMENT = ICoWSwapSettlement("0x9008D19f58AAbD9eD0D60971565AA8510560ab41")
COW_VAULT_RELAYER = Account("0xC92E8bdf79f0507f65a392b0ab4667716BFE0110")

USD_DENOMINATION = Account("0x0000000000000000000000000000000000000348")
ETH_DENOMINATION = Account("0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE")

MIN_SELL_AMOUNT = 10
PRICE_DECIMALS = 18

DAI = IERC20Metadata("0x6B175474E89094C44Da98b954EedeAC495271d0F")
USDT = IERC20Metadata("0xdAC17F958D2ee523a2206206994597C13D831ec7")
USDC = IERC20Metadata("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")
STETH = IstETH("0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84")
YFI = IERC20Metadata("0x0bc529c00C6401aEF6D220BE8C6Ea1667F6Ad93e")
AAVE = IERC20Metadata("0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9")

TOKENS: list[IERC20Metadata] = [
    DAI,
    USDT,
    USDC,
    STETH,
    YFI,
    AAVE,
]


# actually mints shares
def mint_steth(to: Account, amount: int) -> None:
    total_supply_slot = 0xe3b4b636e601189b5f4c6742edf2538ac12bb61ed03e6da26949d69838fa447e
    balance_slot = int.from_bytes(keccak256(Abi.encode(["address", "uint256"], [to, 0])), byteorder="big")

    old_total_supply = int.from_bytes(chain.chain_interface.get_storage_at(str(STETH.address), total_supply_slot), byteorder="big")
    chain.chain_interface.set_storage_at(str(STETH.address), total_supply_slot, (old_total_supply + amount).to_bytes(32, "big"))

    old_balance = int.from_bytes(chain.chain_interface.get_storage_at(str(STETH.address), balance_slot), byteorder="big")
    chain.chain_interface.set_storage_at(str(STETH.address), balance_slot, (old_balance + amount).to_bytes(32, "big"))


@dataclass
class StonksInfo:
    manager: Account
    emergency_operator: Account
    token_from: IERC20Metadata
    token_to: IERC20Metadata
    amount_converter: AmountConverter
    order_duration: int
    margin_basis_points: int
    price_tolerance_basis_points: int
    max_improvement_basis_points: int
    allow_partial_fill: bool
    use_eth_anchor: bool


@dataclass
class AmountConverterInfo:
    use_eth_anchor: bool


@dataclass
class OrderInfo:
    manager: Account
    emergency_operator: Account
    stonks: Stonks
    token_from: IERC20Metadata
    token_to: IERC20Metadata
    order: CoWOrder
    hash: bytes32
    buy_amount: int
    cancelled: bool
    token_from_taken: int


@dataclass
class TokenConfig:
    denominator: Account
    max_staleness_seconds: int
    eth_usd_max_staleness_override_seconds: int | None
    is_active: bool


class AggregatorData(NamedTuple):
    round_id: int
    price: int
    timestamp: int


class AggregatorInfo(NamedTuple):
    aggregator: AggregatorV2V3Interface
    decimals: int


class StonksFuzzTest(FuzzTest):
    agent: Account
    admin: Account
    oracle_router_manager: Account
    amount_converter_factory: AmountConverterFactory
    stonks_factory: StonksFactory
    oracle_router: OracleRouter

    eth_usd_max_staleness_seconds: int

    amount_converters: dict[AmountConverter, AmountConverterInfo]
    stonks: dict[Stonks, StonksInfo]
    orders: dict[Order, OrderInfo]
    token_configs: dict[IERC20Metadata, TokenConfig]
    contract_aggregators: dict[tuple[Account, Account], AggregatorInfo]  # what oracle router sees
    actual_aggregators: dict[tuple[Account, Account], AggregatorInfo]  # what is actually set in registry
    aggregator_data: dict[Account, AggregatorData]
    balances: dict[IERC20Metadata, dict[Account, int]]

    def pre_sequence(self) -> None:
        self.agent = Account.new()
        self.admin = Account.new()
        self.oracle_router_manager = Account(0)

        self.oracle_router = OracleRouter.deploy(
            self.admin, CHAINLINK_FEED_REGISTRY
        )
        self.amount_converter_factory = AmountConverterFactory.deploy(
            self.oracle_router
        )
        self.stonks_factory = StonksFactory.deploy(
            self.admin, self.agent, COW_SETTLEMENT, COW_VAULT_RELAYER
        )

        self.amount_converters = {}
        self.stonks = {}
        self.orders = {}
        self.token_configs = {}
        self.contract_aggregators = {}
        self.actual_aggregators = {}
        self.aggregator_data = {}
        # actually holds shares for STETH
        self.balances = defaultdict(lambda: defaultdict(int))

        self.eth_usd_aggregator = AggregatorV2V3Interface(
            CHAINLINK_FEED_REGISTRY.getFeed(ETH_DENOMINATION, USD_DENOMINATION)
        )

        for token in TOKENS:
            token.label = f"{token.symbol()}({token.address})"

            for acc in chain.accounts:
                if token == STETH:
                    self.balances[token][acc] = STETH.sharesOf(acc)
                else:
                    self.balances[token][acc] = token.balanceOf(acc)

            denomination = random.choice(
                [OracleRouter.QuoteDenomination.USD, OracleRouter.QuoteDenomination.ETH]
            )
            max_staleness_seconds = random_int(60, 60 * 60 * 24)
            self.oracle_router.setTokenFeed(
                token, denomination, max_staleness_seconds, True, from_=self.admin
            )

            if denomination == OracleRouter.QuoteDenomination.USD:
                denominator = USD_DENOMINATION
            else:
                denominator = ETH_DENOMINATION

            self.token_configs[token] = TokenConfig(denominator, max_staleness_seconds, None, True)

            logger.info(f"Setting up aggregators for {token.symbol()}")
            for denom in [USD_DENOMINATION, ETH_DENOMINATION]:
                self._setup_aggregator(token, denom)
                aggregator, _ = self.contract_aggregators[(token, denom)]
                self._update_aggregator_price(
                    aggregator, self.aggregator_data[aggregator].price + 1
                )

        self.eth_usd_max_staleness_seconds = random_int(60, 60 * 60 * 24)
        self.oracle_router.setEthUsdBridge(
            self.eth_usd_max_staleness_seconds, from_=self.admin
        )

        self._setup_aggregator(ETH_DENOMINATION, USD_DENOMINATION)
        aggregator, _ = self.contract_aggregators[(ETH_DENOMINATION, USD_DENOMINATION)]
        self._update_aggregator_price(
            aggregator, self.aggregator_data[aggregator].price + 1
        )

    def _setup_aggregator(self, base: Account, quote: Account) -> None:
        aggregator = AggregatorV2V3Interface(
            CHAINLINK_FEED_REGISTRY.getFeed(base, quote)
        )
        decimals = aggregator.decimals()
        self.contract_aggregators[(base, quote)] = AggregatorInfo(aggregator, decimals)
        self.actual_aggregators[(base, quote)] = AggregatorInfo(aggregator, decimals)

        round_id: int = read_storage_variable(
            aggregator, "s_hotVars", keys=["latestAggregatorRoundId"]
        )
        _, price, _, updated_at, _ = aggregator.latestRoundData()
        self.aggregator_data[aggregator] = AggregatorData(
            round_id,
            price,
            updated_at,
        )

        _, price, _, _, _ = CHAINLINK_FEED_REGISTRY.latestRoundData(base, quote)
        assert price == self.aggregator_data[aggregator].price

    def _update_aggregator_price(self, aggregator: Account, new_price: int):
        round_id = self.aggregator_data[aggregator].round_id + 1
        timestamp = chain.blocks["latest"].timestamp
        self.aggregator_data[aggregator] = AggregatorData(
            round_id,
            new_price,
            timestamp,
        )

        write_storage_variable(
            aggregator, "s_hotVars", round_id, keys=["latestAggregatorRoundId"]
        )
        # write_storage_variable(aggregator, "s_transmissions", {"answer": new_price, "timestamp": timestamp}, keys=[round_id])
        write_storage_variable(
            aggregator,
            "s_transmissions",
            {
                "answer": new_price,
                "observationsTimestamp": timestamp,
                "transmissionTimestamp": timestamp,
            },
            keys=[round_id],
        )

        _, price, _, updated_at, _ = AggregatorV2V3Interface(
            aggregator
        ).latestRoundData()
        assert price == new_price
        assert updated_at == timestamp

    @flow(weight=3)
    def flow_change_feed(self) -> str | None:
        token = random.choice(TOKENS)
        if random.random() < 0.8:
            denomination = self.token_configs[token].denominator
        else:
            denomination = random.choice([USD_DENOMINATION, ETH_DENOMINATION])

        self._change_feed(token, denomination)

        if self.token_configs[token].denominator == denomination:
            assert not self.oracle_router.isFeedInSync(token)

        logger.info(f"Changed feed for {token.symbol()} to {self.actual_aggregators[(token, denomination)].aggregator}")

    @flow(weight=3)
    def flow_change_eth_usd_feed(self) -> str | None:
        pair = (ETH_DENOMINATION, USD_DENOMINATION)
        self._change_feed(*pair)

        if self.contract_aggregators[pair] != self.actual_aggregators[pair]:
            assert not self.oracle_router.isBridgeInSync()

        logger.info(f"Changed ETH/USD feed to {self.actual_aggregators[pair].aggregator}")

    def _change_feed(self, base: Account, quote: Account) -> None:
        old_aggregator, old_decimals = self.actual_aggregators[(base, quote)]
        aggregator_data = self.aggregator_data[old_aggregator]

        new_decimals = max(1, min(38, old_decimals + random_int(-2, 2)))
        if new_decimals >= old_decimals:
            new_price = aggregator_data.price * 10 ** (new_decimals - old_decimals)
        else:
            new_price = aggregator_data.price // 10 ** (old_decimals - new_decimals)

        tx = MockAggregator.deploy(new_decimals, aggregator_data.round_id + 1, new_price, return_tx=True)
        new_aggregator = tx.return_value

        phase_id = abi.decode(
            CHAINLINK_FEED_REGISTRY.call(abi.encode_with_signature("getCurrentPhaseId(address,address)", base, quote)),
            [uint16],
        )
        write_storage_variable(CHAINLINK_FEED_REGISTRY, "s_phaseAggregators", new_aggregator, keys=[base, quote, phase_id])

        self.actual_aggregators[(base, quote)] = AggregatorInfo(new_aggregator, new_decimals)
        self.aggregator_data[new_aggregator] = AggregatorData(
            aggregator_data.round_id + 1,
            new_price,
            tx.block.timestamp,
        )

    @flow(weight=3)
    def flow_change_feed_decimals(self) -> str | None:
        token = random.choice(TOKENS)
        if random.random() < 0.8:
            denomination = self.token_configs[token].denominator
        else:
            denomination = random.choice([USD_DENOMINATION, ETH_DENOMINATION])

        if error := self._change_feed_decimals(token, denomination):
            return error

        if (
            self.token_configs[token].denominator == denomination
            and self.contract_aggregators[(token, denomination)] != self.actual_aggregators[(token, denomination)]
        ):
            assert not self.oracle_router.isFeedInSync(token)

        logger.info(f"Changed feed decimals for {token.symbol()} to {self.actual_aggregators[(token, denomination)].decimals}")

    @flow(weight=3)
    def flow_change_eth_usd_feed_decimals(self) -> str | None:
        pair = (ETH_DENOMINATION, USD_DENOMINATION)
        if error := self._change_feed_decimals(*pair):
            return error

        if self.contract_aggregators[pair] != self.actual_aggregators[pair]:
            assert not self.oracle_router.isBridgeInSync()

        logger.info(f"Changed ETH/USD feed decimals to {self.actual_aggregators[pair].decimals}")

    def _change_feed_decimals(self, base: Account, quote: Account) -> str | None:
        aggregator, old_decimals = self.actual_aggregators[(base, quote)]
        new_decimals = max(1, min(38, old_decimals + random_int(-2, 2)))

        aggregator_data = self.aggregator_data[aggregator]

        if new_decimals >= old_decimals:
            new_price = aggregator_data.price * 10 ** (new_decimals - old_decimals)
        else:
            new_price = aggregator_data.price // 10 ** (old_decimals - new_decimals)

        try:
            write_storage_variable(aggregator, "decimals", new_decimals)
        except ValueError:
            # cannot change decimals for original forked feeds
            return "Cannot change decimals for original forked feeds"

        self._update_aggregator_price(aggregator, new_price)

        self.actual_aggregators[(base, quote)] = AggregatorInfo(aggregator, new_decimals)

    @flow(max_times=10)
    def flow_deploy_amount_converter(self) -> None:
        use_eth_anchor = random_bool()
        tx = self.amount_converter_factory.deployAmountConverter(
            [token.address for token in TOKENS],
            [token.address for token in TOKENS],
            use_eth_anchor,
        )

        self.amount_converters[AmountConverter(tx.return_value)] = AmountConverterInfo(use_eth_anchor)

        logger.info(f"Deployed amount converter: {tx.return_value}")

    @flow(max_times=10)
    def flow_deploy_stonks(self) -> str | None:
        if not self.amount_converters:
            return "No amount converters deployed"

        sender = random_account()
        manager = random_account()
        token_from = random.choice(TOKENS)
        token_to = random.choice(TOKENS)
        amount_converter = random.choice(list(self.amount_converters.keys()))
        order_duration = random_int(60, 60 * 60 * 24)
        margin_basis_points = random_int(0, 1_000, edge_values_prob=0.33)  # 0% - 10%
        price_tolerance_basis_points = random_int(
            0, 1_000, edge_values_prob=0.33
        )  # 0% - 10%
        max_improvement_basis_points = random_int(
            0, 1_000, edge_values_prob=0.33
        )  # 0% - 10%
        allow_partial_fill = random_bool()

        with may_revert() as ex:
            tx = self.stonks_factory.deployStonks(
                manager,
                token_from,
                token_to,
                amount_converter,
                order_duration,
                margin_basis_points,
                price_tolerance_basis_points,
                max_improvement_basis_points,
                allow_partial_fill,
                from_=sender,
            )

        if token_from == token_to:
            assert ex.value == Stonks.TokensCannotBeSame()
            return "Tokens cannot be the same"
        else:
            assert ex.value is None

        self.stonks[Stonks(tx.return_value)] = StonksInfo(
            manager=manager,
            emergency_operator=Account(0),
            token_from=token_from,
            token_to=token_to,
            amount_converter=amount_converter,
            order_duration=order_duration,
            margin_basis_points=margin_basis_points,
            price_tolerance_basis_points=price_tolerance_basis_points,
            max_improvement_basis_points=max_improvement_basis_points,
            allow_partial_fill=allow_partial_fill,
            use_eth_anchor=self.amount_converters[amount_converter].use_eth_anchor,
        )

        logger.info(f"Deployed stonks: {tx.return_value}")

    @flow()
    def flow_set_token_feed(self) -> str | None:
        token = random.choice(TOKENS)
        quote_denomination = random.choice(
            [OracleRouter.QuoteDenomination.USD, OracleRouter.QuoteDenomination.ETH]
        )
        max_staleness_seconds = random_int(60, 60 * 60 * 24)
        sender = random.choice([self.admin, self.oracle_router_manager]) if self.oracle_router_manager != Account(0) else self.admin

        tx = self.oracle_router.setTokenFeed(
            token, quote_denomination, max_staleness_seconds, True, from_=sender
        )

        if quote_denomination == OracleRouter.QuoteDenomination.USD:
            self.token_configs[token].denominator = USD_DENOMINATION
        else:
            self.token_configs[token].denominator = ETH_DENOMINATION
        self.token_configs[token].max_staleness_seconds = max_staleness_seconds
        self.token_configs[token].is_active = True
        self.contract_aggregators[(token, self.token_configs[token].denominator)] = self.actual_aggregators[(token, self.token_configs[token].denominator)]

        logger.info(f"Set token {token.symbol()} feed to {quote_denomination}")

    @flow()
    def flow_set_token_eth_usd_staleness_override(self) -> str | None:
        token = random.choice(TOKENS)
        if random.random() < 0.2:
            # disable the override
            override_seconds = 0
        else:
            override_seconds = random_int(60, 60 * 60 * 24)

        sender = random.choice([self.admin, self.oracle_router_manager]) if self.oracle_router_manager != Account(0) else self.admin

        tx = self.oracle_router.setTokenEthUsdStalenessOverride(token, override_seconds, from_=sender)

        self.token_configs[token].eth_usd_max_staleness_override_seconds = override_seconds if override_seconds > 0 else None

        logger.info(f"Set token {token.symbol()} ETH/USD staleness override to {override_seconds} seconds")

    @flow(weight=1)
    def flow_set_token_active(self) -> str | None:
        token = random.choice(TOKENS)
        is_active = random_bool()

        sender = random.choice([self.admin, self.oracle_router_manager]) if self.oracle_router_manager != Account(0) else self.admin

        with may_revert() as ex:
            tx = self.oracle_router.setTokenActive(token, is_active, from_=sender)

        self.token_configs[token].is_active = is_active

        logger.info(f"Set token {token.symbol()} active to {is_active}")

    @flow()
    def flow_transfer_tokens(self) -> str | None:
        if not self.stonks and not self.orders:
            return "No stonks or orders deployed"

        if random_bool() and self.stonks or not self.orders:
            recipient = random.choice(list(self.stonks.keys()))
            info = self.stonks[recipient]
        else:
            recipient = random.choice(list(self.orders.keys()))
            info = self.orders[recipient]

        token = random.choice([info.token_from, info.token_to])
        sender = random_account()
        amount = random_int(0, 1_000_000)

        if token == STETH:
            mint_steth(sender, amount)
            STETH.transferShares(recipient, amount, from_=sender)
        else:
            mint_erc20(token, sender, amount)
            token.transfer(recipient, amount, from_=sender)

        self.balances[token][recipient] += amount

        logger.info(f"Transferred {amount} {token.symbol()} from {sender} to {recipient}")

    @flow()
    def flow_place_order(self) -> str | None:
        if not self.stonks:
            return "No stonks deployed"

        stonks = random.choice(list(self.stonks.keys()))
        info = self.stonks[stonks]
        sender = random.choice([self.admin, info.manager])
        sell_amount = self.balances[info.token_from][stonks]

        if info.token_from == STETH:
            sell_amount = STETH.getPooledEthByShares(sell_amount)

        try:
            if random_bool():
                # set min buy below estimated output
                min_buy_amount = self._compute_out_amount_with_margin(stonks, sell_amount) * random_int(8800, 10000) // 10000
            else:
                # set min buy above estimated output
                min_buy_amount = self._compute_out_amount_with_margin(stonks, sell_amount) * random_int(10000, 11200) // 10000
        except RevertError:
            min_buy_amount = 1

        with may_revert() as ex:
            tx = stonks.placeOrder(min_buy_amount, from_=sender)

        if ex.value is not None:
            tx = ex.value.tx

        if error := self._post_place_order(tx, info, stonks, sell_amount, min_buy_amount):
            return error

        logger.info(f"Placed order: {tx.return_value}")

    @flow()
    def flow_place_order_with_amount(self) -> str | None:
        if not self.stonks:
            return "No stonks deployed"

        stonks = random.choice(list(self.stonks.keys()))
        info = self.stonks[stonks]
        sender = random.choice([self.admin, info.manager])
        sell_amount = random_int(0, 1_000_000)

        try:
            if random_bool():
                # set min buy below estimated output
                min_buy_amount = self._compute_out_amount_with_margin(stonks, sell_amount) * random_int(8800, 10000) // 10000
            else:
                # set min buy above estimated output
                min_buy_amount = self._compute_out_amount_with_margin(stonks, sell_amount) * random_int(10000, 11200) // 10000
        except RevertError:
            min_buy_amount = 1

        with may_revert() as ex:
            tx = stonks.placeOrderWithAmount(sell_amount, min_buy_amount, from_=sender)

        if ex.value is not None:
            tx = ex.value.tx

        if error := self._post_place_order(tx, info, stonks, sell_amount, min_buy_amount):
            return error

        logger.info(f"Placed order with amount: {tx.return_value}")

    @flow()
    def flow_fill_order(self) -> str | None:
        if not self.orders:
            return "No orders deployed"

        order = random.choice(list(self.orders.keys()))
        info = self.orders[order]

        with may_revert() as ex:
            assert order.isValidSignature(info.hash, b"") == order.isValidSignature.selector

        max_improvement = self.stonks[info.stonks].max_improvement_basis_points
        tolerance = self.stonks[info.stonks].price_tolerance_basis_points
        use_eth_anchor = self.stonks[info.stonks].use_eth_anchor

        if info.order.sellAmount - info.token_from_taken == 0:
            return "Sell amount is zero"

        if info.token_from == STETH:
            order_balance = STETH.getPooledEthByShares(self.balances[info.token_from][order])
        else:
            order_balance = self.balances[info.token_from][order]

        if chain.blocks["latest"].timestamp > info.order.validTo:
            assert ex.value == Order.OrderExpired(info.order.validTo)
            return "Order is expired"
        elif info.cancelled:
            assert ex.value == Order.OrderIsCancelled()
            return "Order is cancelled"
        elif not self.stonks[info.stonks].allow_partial_fill and order_balance < info.order.sellAmount:
            assert ex.value == Order.InsufficientSellBalance(info.order.sellAmount, order_balance)
            return "Insufficient sell balance"
        elif error := self._check_oracles(
            ex.value, self.stonks[info.stonks], chain.blocks["latest"].timestamp, ETH_DENOMINATION if use_eth_anchor else USD_DENOMINATION
        ):
            return error

        if info.token_from == STETH:
            sell_amount = min(order_balance, info.order.sellAmount)
        else:
            sell_amount = min(order_balance, info.order.sellAmount)

        estimated_out = self._compute_out_amount_with_margin(info.stonks, sell_amount)
        baseline_buy_amount = info.buy_amount * sell_amount // info.order.sellAmount

        original_price = info.order.buyAmount * 10 ** 18 // info.order.sellAmount
        current_price = estimated_out * 10 ** 18 // sell_amount

        if estimated_out == 0:
            assert ex.value == Order.ZeroQuotableAmount(sell_amount)
            return "Zero quotable amount"
        elif abs(estimated_out - baseline_buy_amount) <= 2:
            assert ex.value is None
        elif estimated_out == baseline_buy_amount:
            assert ex.value is None
        elif current_price > original_price and max_improvement == 0:
            assert ex.value == Order.PriceImprovementRejectedInStrictMode(baseline_buy_amount, estimated_out)
            return "Price improvement rejected in strict mode"
        elif current_price > original_price and max_improvement != uint256.max and (current_price - original_price) * 10000 // original_price > max_improvement:
            max_buy = baseline_buy_amount * (10000 + max_improvement) // 10000
            assert ex.value == Order.PriceImprovementExceedsLimit(max_buy, estimated_out)
            return "Price improvement exceeds limit"
        elif current_price < original_price and tolerance == 0:
            assert ex.value == Order.PriceShortfallExceedsTolerance(baseline_buy_amount, estimated_out)
            return "Price shortfall rejected in strict mode"
        elif current_price < original_price and (original_price - current_price) * 10000 // original_price > tolerance:
            min_acceptable = baseline_buy_amount - baseline_buy_amount * tolerance // 10000
            if abs(min_acceptable - estimated_out) <= 2:
                assert ex.value is None
            else:
                assert ex.value == Order.PriceShortfallExceedsTolerance(min_acceptable, estimated_out)
                return "Price shortfall exceeds tolerance"
        else:
            assert ex.value is None

        with chain.snapshot_and_revert():
            if info.token_from == STETH:
                mint_steth(order, random_int(1, 10))
            else:
                mint_erc20(info.token_from, order, random_int(1, 10))

            with may_revert((OracleRouter.OracleStale, Order.OrderExpired, Order.ZeroQuotableAmount)) as ex:
                assert order.isValidSignature(info.hash, b"") == order.isValidSignature.selector

        if self.stonks[info.stonks].allow_partial_fill:
            filled_amount = random_int(0, sell_amount)
        else:
            filled_amount = sell_amount

        info.token_from.transferFrom(order, COW_VAULT_RELAYER, filled_amount, from_=COW_VAULT_RELAYER)

        if info.token_from == STETH:
            shares_taken = STETH.getSharesByPooledEth(filled_amount)
            info.token_from_taken += STETH.getPooledEthByShares(shares_taken)
            self.balances[info.token_from][order] -= shares_taken
        else:
            info.token_from_taken += filled_amount
            self.balances[info.token_from][order] -= filled_amount

        if info.token_from_taken < info.order.sellAmount and self.stonks[info.stonks].allow_partial_fill:
            with may_revert((OracleRouter.OracleStale, Order.OrderExpired, Order.ZeroQuotableAmount)) as ex:
                assert order.isValidSignature(info.hash, b"") == order.isValidSignature.selector

        logger.info(f"Filled {filled_amount} {info.token_from.symbol()} from order {order}")

    @flow()
    def flow_recover_token_from(self) -> str | None:
        if not self.orders:
            return "No orders deployed"

        order = random.choice(list(self.orders.keys()))
        info = self.orders[order]

        with may_revert() as ex:
            tx = order.recoverTokenFrom(from_=random_account())

        if chain.blocks["latest"].timestamp <= info.order.validTo:
            assert ex.value == Order.OrderNotExpired(info.order.validTo, chain.blocks["latest"].timestamp)
            return "Order is not expired"

        if info.token_from == STETH:
            recover_amount = STETH.getSharesByPooledEth(STETH.getPooledEthByShares(self.balances[info.token_from][order]))
        else:
            recover_amount = self.balances[info.token_from][order]

        self.balances[info.token_from][info.stonks] += recover_amount
        self.balances[info.token_from][order] -= recover_amount

        logger.info(f"Recovered {recover_amount} {info.token_from.symbol()} from order {order}")

    @flow()
    def flow_emergency_cancel_and_return(self) -> str | None:
        if not self.orders:
            return "No orders deployed"

        order = random.choice(list(self.orders.keys()))
        info = self.orders[order]
        if info.emergency_operator == Account(0):
            sender = random.choice([self.admin, info.manager])
        else:
            sender = random.choice([self.admin, info.manager, info.emergency_operator])

        tx = order.emergencyCancelAndReturn(from_=sender)

        info.cancelled = True

        if info.token_from == STETH:
            recover_amount = STETH.getSharesByPooledEth(STETH.getPooledEthByShares(self.balances[info.token_from][order]))
        else:
            recover_amount = self.balances[info.token_from][order]

        self.balances[info.token_from][info.stonks] += recover_amount
        self.balances[info.token_from][order] -= recover_amount

        logger.info(f"Emergency canceled and returned {recover_amount} {info.token_from.symbol()} from order {order}")

    @flow()
    def flow_set_emergency_operator(self) -> str | None:
        if not self.stonks and not self.orders:
            return "No stonks or orders deployed"

        if random.random() < 0.5 and self.stonks or not self.orders:
            stonks = random.choice(list(self.stonks.keys()))
            info = self.stonks[stonks]
            operator = random.choice(list(chain.accounts) + [self.admin, self.agent, info.manager])

            tx = stonks.setEmergencyOperator(operator, from_=self.admin)

            info.emergency_operator = operator
            logger.info(f"Set emergency operator {operator} for stonks {stonks}")
        else:
            order = random.choice(list(self.orders.keys()))
            info = self.orders[order]
            operator = random.choice(list(chain.accounts) + [self.admin, self.agent, info.manager])

            tx = order.setEmergencyOperator(operator, from_=self.admin)

            info.emergency_operator = operator
            logger.info(f"Set emergency operator {operator} for order {order}")

    @flow()
    def flow_set_manager(self) -> str | None:
        if random.random() < 0.45 and self.stonks:
            stonks = random.choice(list(self.stonks.keys()))
            info = self.stonks[stonks]
            manager = random.choice(list(chain.accounts) + [self.admin, self.agent, info.manager])

            tx = stonks.setManager(manager, from_=self.admin)

            info.manager = manager
            logger.info(f"Set manager {manager} for stonks {stonks}")
        elif random.random() < 0.9 and self.orders:
            order = random.choice(list(self.orders.keys()))
            info = self.orders[order]
            manager = random.choice(list(chain.accounts) + [self.admin, self.agent, info.manager])

            tx = order.setManager(manager, from_=self.admin)

            info.manager = manager
            logger.info(f"Set manager {manager} for order {order}")
        else:
            manager = random.choice(list(chain.accounts) + [self.admin, self.agent])
            tx = self.oracle_router.setManager(manager, from_=self.admin)

            self.oracle_router_manager = manager

            logger.info(f"Set manager {manager} for oracle router")

    def _sync_token_feed(self, token: IERC20Metadata) -> None:
        sender = random.choice([self.admin, self.oracle_router_manager]) if self.oracle_router_manager != Account(0) else self.admin

        tx = self.oracle_router.syncTokenFeed(token, from_=sender)

        self.contract_aggregators[(token, self.token_configs[token].denominator)] = self.actual_aggregators[(token, self.token_configs[token].denominator)]

    def _sync_eth_usd_bridge(self) -> None:
        sender = random.choice([self.admin, self.oracle_router_manager]) if self.oracle_router_manager != Account(0) else self.admin

        tx = self.oracle_router.syncEthUsdBridge(from_=sender)

        self.contract_aggregators[(ETH_DENOMINATION, USD_DENOMINATION)] = self.actual_aggregators[(ETH_DENOMINATION, USD_DENOMINATION)]

    def _post_place_order(self, tx: TransactionAbc[Address], info: StonksInfo, stonks: Stonks, sell_amount: int, min_buy_amount: int) -> str | None:
        if info.token_from == STETH:
            stonks_balance = STETH.getPooledEthByShares(self.balances[info.token_from][stonks])
        else:
            stonks_balance = self.balances[info.token_from][stonks]

        if min_buy_amount == 0:
            assert tx.error == Stonks.InvalidAmount(min_buy_amount)
            return "Min buy amount is zero"
        elif sell_amount < MIN_SELL_AMOUNT:
            assert tx.error == Stonks.MinimumPossibleBalanceNotMet(
                MIN_SELL_AMOUNT, sell_amount
            )
            return "Sell amount is below minimum possible balance"
        elif sell_amount > stonks_balance:
            assert tx.error == Stonks.SellAmountExceedsBalance(
                stonks_balance, sell_amount
            )
            return "Sell amount exceeds balance"

        if error := self._check_oracles(tx.error, info, tx.block.timestamp, ETH_DENOMINATION if info.use_eth_anchor else USD_DENOMINATION):
            return error

        assert tx.error is None

        order = Order(tx.return_value)

        if info.token_from == STETH:
            transfer_amount = STETH.getSharesByPooledEth(sell_amount)
            sell_amount = STETH.getPooledEthByShares(transfer_amount)
        else:
            transfer_amount = sell_amount

        self.balances[info.token_from][stonks] -= transfer_amount
        self.balances[info.token_from][order] += transfer_amount

        valid_to = tx.block.timestamp + info.order_duration
        buy_amount = max(min_buy_amount, self._compute_out_amount_with_margin(stonks, sell_amount))

        cow_order = CoWOrder(
            sellToken=info.token_from,
            buyToken=info.token_to,
            receiver=self.agent.address,
            sellAmount=sell_amount,
            buyAmount=buy_amount,
            validTo=valid_to,
            appData=keccak256(b"{}"),
            feeAmount=0,
            kind="sell",
            partiallyFillable=info.allow_partial_fill,
            sellTokenBalance="erc20",
            buyTokenBalance="erc20",
        )
        order_hash = cow_order.get_eip712_signing_hash(
            Eip712Domain(name="Gnosis Protocol", version="v2", chainId=chain.chain_id, verifyingContract=COW_SETTLEMENT)
        )
        assert order.getOrderDetails() == (
            order_hash,
            info.token_from.address,
            info.token_to.address,
            sell_amount,
            buy_amount,
            valid_to,
        )

        self.orders[order] = OrderInfo(
            manager=info.manager,
            emergency_operator=Account(0),
            stonks=stonks,
            token_from=info.token_from,
            token_to=info.token_to,
            order=cow_order,
            hash=order_hash,
            buy_amount=max(min_buy_amount, self._compute_out_amount_with_margin(stonks, sell_amount)),
            cancelled=False,
            token_from_taken=0,
        )

    def _check_oracles(self, error: RevertError | Halt | None, info: StonksInfo, timestamp: int, requested_denominator: Account) -> str | None:
        for token in [info.token_from, info.token_to]:
            if not self.token_configs[token].is_active:
                assert error == OracleRouter.TokenNotConfigured(token.address)
                return "Token is not active"

        cached_eth_usd = False
        if (
            self.token_configs[info.token_from].denominator != requested_denominator
            and self.token_configs[info.token_to].denominator != requested_denominator
            and (common_staleness := self._get_max_eth_usd_staleness(info.token_from)) == self._get_max_eth_usd_staleness(info.token_to)
        ):
            cached_eth_usd = True
            if err := self._check_eth_usd_bridge(error, common_staleness, timestamp):
                return err

        for token in [info.token_from, info.token_to]:
            aggregator, decimals = self.contract_aggregators[(token, self.token_configs[token].denominator)]
            price = self.aggregator_data[aggregator].price

            if decimals >= PRICE_DECIMALS:
                normalized_price = price // 10 ** (decimals - PRICE_DECIMALS)
            else:
                normalized_price = price * 10 ** (PRICE_DECIMALS - decimals)

            if (aggregator, decimals) != (actual_info := self.actual_aggregators[(token, self.token_configs[token].denominator)]):
                assert error == OracleRouter.FeedConfigOutOfSync(
                    aggregator.address,
                    actual_info.aggregator.address,
                    decimals,
                    actual_info.decimals,
                )

                # sync so it doesn't fail next time
                self._sync_token_feed(token)
                return "Feed config is out of sync"
            elif price <= 0:
                assert error == OracleRouter.OracleBadAnswer(aggregator.address, price)
                return "Token feed has bad answer"
            elif self.aggregator_data[aggregator].timestamp + self.token_configs[token].max_staleness_seconds < timestamp:
                assert error == OracleRouter.OracleStale(aggregator.address, self.aggregator_data[aggregator].timestamp)

                # update the aggregator price so it doesn't fail next time
                self._update_aggregator_price(aggregator, self.aggregator_data[aggregator].price + 1)
                return "Token feed is stale"
            elif normalized_price == 0:
                assert error == OracleRouter.OracleQuantizedToZero(aggregator.address, decimals, PRICE_DECIMALS)
                return "Token feed is quantized to zero"
            elif not cached_eth_usd and self.token_configs[token].denominator != requested_denominator:
                max_staleness = self._get_max_eth_usd_staleness(token)

                if err := self._check_eth_usd_bridge(error, max_staleness, timestamp):
                    return err

    def _check_eth_usd_bridge(self, error: RevertError | Halt | None, max_staleness: int, timestamp: int) -> str | None:
        eth_usd_aggregator, eth_usd_decimals = self.contract_aggregators[(ETH_DENOMINATION, USD_DENOMINATION)]
        price = self.aggregator_data[eth_usd_aggregator].price

        if eth_usd_decimals >= PRICE_DECIMALS:
            normalized_price = price // 10 ** (eth_usd_decimals - PRICE_DECIMALS)
        else:
            normalized_price = price * 10 ** (PRICE_DECIMALS - eth_usd_decimals)

        if (eth_usd_aggregator, eth_usd_decimals) != (actual_info := self.actual_aggregators[(ETH_DENOMINATION, USD_DENOMINATION)]):
            assert error == OracleRouter.FeedConfigOutOfSync(
                eth_usd_aggregator.address,
                actual_info.aggregator.address,
                eth_usd_decimals,
                actual_info.decimals,
            )
            return "ETH/USD feed config is out of sync"
        elif price <= 0:
            assert error == OracleRouter.OracleBadAnswer(eth_usd_aggregator.address, price)
            return "ETH/USD feed has bad answer"
        elif self.aggregator_data[eth_usd_aggregator].timestamp + max_staleness < timestamp:
            assert error == OracleRouter.OracleStale(eth_usd_aggregator.address, self.aggregator_data[eth_usd_aggregator].timestamp)
            return "ETH/USD feed is stale"
        elif normalized_price == 0:
            assert error == OracleRouter.OracleQuantizedToZero(eth_usd_aggregator.address, eth_usd_decimals, PRICE_DECIMALS)
            return "ETH/USD feed is quantized to zero"

    def _get_max_eth_usd_staleness(self, token: IERC20Metadata) -> int:
        if (token_staleness := self.token_configs[token].eth_usd_max_staleness_override_seconds or uint.max) < self.eth_usd_max_staleness_seconds:
            return token_staleness
        return self.eth_usd_max_staleness_seconds

    def _get_token_price_in_denomination(self, token: IERC20Metadata, denomination: Account) -> int:
        aggregator, price_decimals = self.contract_aggregators[(token, self.token_configs[token].denominator)]
        price = self.aggregator_data[aggregator].price
        if price <= 0:
            raise OracleRouter.OracleBadAnswer(aggregator.address, price)

        if price_decimals >= PRICE_DECIMALS:
            price //= 10 ** (price_decimals - PRICE_DECIMALS)
        else:
            price *= 10 ** (PRICE_DECIMALS - price_decimals)
        if price == 0:
            raise OracleRouter.OracleQuantizedToZero(aggregator.address, price_decimals, PRICE_DECIMALS)

        if denomination == self.token_configs[token].denominator:
            return price
        else:
            eth_usd_aggregator, eth_usd_decimals = self.contract_aggregators[(ETH_DENOMINATION, USD_DENOMINATION)]
            eth_usd_price = self.aggregator_data[eth_usd_aggregator].price
            if eth_usd_price <= 0:
                raise OracleRouter.OracleBadAnswer(eth_usd_aggregator.address, eth_usd_price)

            if eth_usd_decimals >= PRICE_DECIMALS:
                eth_usd_price //= 10 ** (eth_usd_decimals - PRICE_DECIMALS)
            else:
                eth_usd_price *= 10 ** (PRICE_DECIMALS - eth_usd_decimals)
            if eth_usd_price == 0:
                raise OracleRouter.OracleQuantizedToZero(eth_usd_aggregator.address, eth_usd_decimals, PRICE_DECIMALS)

            if denomination == ETH_DENOMINATION:
                return (price * 10 ** PRICE_DECIMALS) // eth_usd_price
            else:
                return (price * eth_usd_price) // 10 ** PRICE_DECIMALS

    def _compute_out_amount(self, stonks: Stonks, sell_amount: int) -> int:
        if self.stonks[stonks].use_eth_anchor:
            denominator = ETH_DENOMINATION
        else:
            denominator = USD_DENOMINATION

        price_from = self._get_token_price_in_denomination(self.stonks[stonks].token_from, denominator)
        price_to = self._get_token_price_in_denomination(self.stonks[stonks].token_to, denominator)

        if price_from == 0:
            if self.stonks[stonks].use_eth_anchor:
                raise AmountConverter.PriceFromEthZero()
            else:
                raise AmountConverter.PriceFromUsdZero()
        if price_to == 0:
            if self.stonks[stonks].use_eth_anchor:
                raise AmountConverter.PriceToEthZero()
            else:
                raise AmountConverter.PriceToUsdZero()

        from_token_decimals = self.stonks[stonks].token_from.decimals()
        to_token_decimals = self.stonks[stonks].token_to.decimals()
        if from_token_decimals > to_token_decimals:
            price_to *= 10 ** (from_token_decimals - to_token_decimals)
        else:
            price_from *= 10 ** (to_token_decimals - from_token_decimals)

        return (sell_amount * price_from) // price_to

    def _compute_out_amount_with_margin(self, stonks: Stonks, sell_amount: int) -> int:
        out_amount = self._compute_out_amount(stonks, sell_amount)
        return out_amount * (10000 - self.stonks[stonks].margin_basis_points) // 10000

    @invariant()
    def invariant_balances(self) -> None:
        for token in TOKENS:
            for acc in list(chain.accounts) + list(self.stonks.keys()):
                if token == STETH:
                    assert self.balances[token][acc] == STETH.sharesOf(acc)
                else:
                    assert self.balances[token][acc] == token.balanceOf(acc)

    @invariant()
    def invariant_trade_output(self) -> None:
        for stonks in self.stonks.keys():
            if (balance := self.balances[self.stonks[stonks].token_from][stonks]) == 0:
                continue

            if self.stonks[stonks].token_from == STETH:
                balance = STETH.getPooledEthByShares(balance)

            with may_revert((
                OracleRouter.OracleStale, OracleRouter.TokenNotConfigured, OracleRouter.FeedConfigOutOfSync, OracleRouter.OracleQuantizedToZero,
                OracleRouter.OracleBadAnswer, AmountConverter.PriceFromEthZero, AmountConverter.PriceFromUsdZero,
                AmountConverter.PriceToEthZero, AmountConverter.PriceToUsdZero,
            )) as ex:
                assert stonks.estimateTradeOutputFromCurrentBalance() == self._compute_out_amount_with_margin(stonks, balance)

            if isinstance(ex.value, (OracleRouter.OracleQuantizedToZero, OracleRouter.OracleBadAnswer)):
                with must_revert(ex.value):
                    self._compute_out_amount_with_margin(stonks, balance)


@chain.connect(fork="http://localhost:8545@23819940")
def test_stonks_fuzz() -> None:
    StonksFuzzTest().run(100_000, 3_000)
