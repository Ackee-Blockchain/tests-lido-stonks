import {AggregatorV2V3Interface} from "./AggregatorV2V3Interface.sol";

contract MockAggregator is AggregatorV2V3Interface {
    uint8 public decimals;
    string public description = "MockAggregator";
    uint256 public immutable version = 1;

    struct Transmission {
        int192 answer;
        uint256 observationsTimestamp;
        uint256 transmissionTimestamp;
    }

    struct HotVars {
        uint256 latestAggregatorRoundId;
    }

    HotVars internal s_hotVars;
    mapping(uint80 roundId => Transmission transmission) internal s_transmissions;

    constructor(uint8 decimals_, uint80 latestRound_, int192 latestAnswer_) {
        s_hotVars.latestAggregatorRoundId = latestRound_;
        decimals = decimals_;

        s_transmissions[latestRound_] = Transmission({
            answer: latestAnswer_,
            observationsTimestamp: block.timestamp,
            transmissionTimestamp: block.timestamp
        });
    }

    function latestRound() external view returns (uint256) {
        return s_hotVars.latestAggregatorRoundId;
    }

    function latestAnswer() external view returns (int256) {
        return s_transmissions[uint32(s_hotVars.latestAggregatorRoundId)].answer;
    }

    function latestTimestamp() external view returns (uint256) {
        return s_transmissions[uint32(s_hotVars.latestAggregatorRoundId)].transmissionTimestamp;
    }

    function getAnswer(uint256 roundId) external view returns (int256) {
        return s_transmissions[uint32(roundId)].answer;
    }

    function getRoundData(
        uint80 roundId_
    )
        public
        view
        returns (
            uint80 roundId,
            int256 answer,
            uint256 startedAt,
            uint256 updatedAt,
            uint80 answeredInRound
        )
    {
        return (
            roundId_,
            s_transmissions[roundId_].answer,
            s_transmissions[roundId_].observationsTimestamp,
            s_transmissions[roundId_].transmissionTimestamp,
            roundId_
        );
    }

    function latestRoundData()
        external
        view
        returns (
            uint80 roundId,
            int256 answer,
            uint256 startedAt,
            uint256 updatedAt,
            uint80 answeredInRound
        )
    {
        return getRoundData(uint80(s_hotVars.latestAggregatorRoundId));
    }

    function getTimestamp(uint256 roundId) external view returns (uint256) {
        return s_transmissions[uint32(roundId)].transmissionTimestamp;
    }
}
