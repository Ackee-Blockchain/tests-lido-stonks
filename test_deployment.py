import wake.deployment
from wake.testing import *

from pytypes.contracts.factories.AmountConverterFactory import AmountConverterFactory
from pytypes.contracts.factories.StonksFactory import StonksFactory
from pytypes.contracts.routers.OracleRouter import OracleRouter
from pytypes.contracts.AmountConverter import AmountConverter
from pytypes.contracts.Order import Order
from pytypes.contracts.Stonks import Stonks


pre_chain = Chain()
post_chain = wake.deployment.Chain()

LDO = Account("0x5a98fcbea516cf06857215779fd812ca3bef1b32", chain=pre_chain)
STETH = Account("0xae7ab96520de3a18e5e111b5eaab095312d7fe84", chain=pre_chain)


@pre_chain.connect(fork="http://localhost:8545@24340845")
@post_chain.connect("http://localhost:8545")
def test_deployment():
    creator = Account("0x97AF512812789bd392fF099D89c44FF6Aa1Fa7F5", chain=pre_chain)
    admin = Account("0x2e59a20f205bb85a89c53f1936454680651e618e", chain=pre_chain)
    agent = Account("0x3e40d73eb977dc6a537af587d48316fee66e9c8c", chain=pre_chain)
    manager = Account("a02fc823cce0d016bd7e17ac684c9abab2d6d647", chain=pre_chain)
    cow_settlement = Account("0x9008D19f58AAbD9eD0D60971565AA8510560ab41", chain=pre_chain)
    cow_relayer = Account("0xC92E8bdf79f0507f65a392b0ab4667716BFE0110", chain=pre_chain)
    chainlink_feed_registry = Account("0x47Fb2585D2C56Fe188D0E6ec628a38b74fCeeeDf", chain=pre_chain)

    pre_chain.default_tx_account = creator

    stonks_factory = StonksFactory.deploy(admin, agent, cow_settlement, cow_relayer, chain=pre_chain)
    stonks_factory2 = StonksFactory("0x78470F9e0A563b5B5b343D42B6CD1392A88de0E3", chain=post_chain)
    assert stonks_factory.code == stonks_factory2.code
    assert stonks_factory.address == stonks_factory2.address

    order_sample = Order(stonks_factory.ORDER_SAMPLE(), chain=pre_chain)
    order_sample2 = Order("0x5eA1EA922090c29943DFf33f05b6D48eD6941AEd", chain=post_chain)
    assert order_sample.code == order_sample2.code
    assert order_sample.address == order_sample2.address

    creator.nonce += 1

    oracle_router = OracleRouter.deploy(admin, chainlink_feed_registry, chain=pre_chain)
    oracle_router2 = OracleRouter("0x79ef3a538200Fe4981D67E7e886bfb36D4Cb5a31", chain=post_chain)
    assert oracle_router.code == oracle_router2.code
    assert oracle_router.address == oracle_router2.address

    creator.nonce += 1

    amount_converter_factory = AmountConverterFactory.deploy(oracle_router, chain=pre_chain)
    amount_converter_factory2 = AmountConverterFactory("0xD96223670BF73cB191a9F0b526653B7eC99dcf45", chain=post_chain)
    assert amount_converter_factory.code == amount_converter_factory2.code
    assert amount_converter_factory.address == amount_converter_factory2.address

    amount_converter_factory.deployAmountConverter([LDO], [STETH], False)

    amount_converter_usd = AmountConverter(amount_converter_factory.deployAmountConverter([STETH], [LDO], False).return_value, chain=pre_chain)
    amount_converter_usd2 = AmountConverter("0x611E9364Fa0201E03dC78451CF62f7247fe55125", chain=post_chain)
    assert amount_converter_usd.code == amount_converter_usd2.code
    assert amount_converter_usd.address == amount_converter_usd2.address

    amount_converter_eth = AmountConverter(amount_converter_factory.deployAmountConverter([STETH], [LDO], True).return_value, chain=pre_chain)
    amount_converter_eth2 = AmountConverter("0x70dA04C5D0f325F5AF1426dE6672BF2424B4593d", chain=post_chain)
    assert amount_converter_eth.code == amount_converter_eth2.code
    assert amount_converter_eth.address == amount_converter_eth2.address

    stonks = Stonks(stonks_factory.deployStonks(manager, STETH, LDO, amount_converter_eth, 1800, 110, 550, 1000, True).return_value, chain=pre_chain)
    stonks2 = Stonks("0xDC8fA2A1D8e3Dc8162E9de08AB2967c220FA3DED", chain=post_chain)
    assert stonks.code == stonks2.code
    assert stonks.address == stonks2.address
