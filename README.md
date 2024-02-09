# Tests for Lido Stonks
This repository serves as an example of tests written in a development and testing framework called [Wake](https://github.com/Ackee-Blockchain/wake).

![horizontal splitter](https://github.com/Ackee-Blockchain/wake-detect-action/assets/56036748/ec488c85-2f7f-4433-ae58-3d50698a47de)

## Setup

1. Clone this repository
2. `git submodule update --init --recursive` if not cloned with `--recursive`
3. `cd source && npm install && cd ..` to install dependencies
4. `wake up pytypes` to generate pytypes
5. `wake test` to run tests

Tested with `wake` version `4.4.1` and `anvil` version `0.2.0 (0688b5a 2024-02-06T00:16:34.878992291Z)`. The fuzz test expects a local archive node running at http://localhost:8545 with the Ethereum mainnet with blocks from 17034871 to 19049237 available.