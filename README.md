# Tests for Lido Stonks 2.0
This repository serves as an example of tests written in a development and testing framework called [Wake](https://github.com/Ackee-Blockchain/wake).

![horizontal splitter](https://github.com/Ackee-Blockchain/wake-detect-action/assets/56036748/ec488c85-2f7f-4433-ae58-3d50698a47de)

## Fuzzing

1. Clone this repository
2. `git submodule update --init --recursive` if not cloned with `--recursive`
3. `cd source && npm install && cd ..` to install dependencies
4. `wake up pytypes` to generate pytypes
5. `wake test` to run tests

Tested with `wake` version `5.0.0rc2`. The fuzz test expects a local node running at http://localhost:8545 with the Ethereum mainnet synchronized to the block 23819940.

## Deployment verification

1. Clone this repository
2. `git submodule update --init --recursive` if not cloned with `--recursive`
3. `cp test_deployment.py source/`
3. `cd source && npm install`
4. `wake up pytypes --evm-version paris --target-version 0.8.23 --optimizer-enabled` to generate pytypes inside the `source` directory
5. `wake test test_deployment.py` to run the deployment verification test

Tested with `wake` version `5.0.0rc2`. The deployment verification test expects a local node running at http://localhost:8545 with the Ethereum mainnet synchronized to the block 24340845.

