import "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";


abstract contract IstETH is IERC20Metadata {
    function sharesOf(address account) external virtual view returns (uint256);

    function getSharesByPooledEth(uint256 _ethAmount) external virtual view returns (uint256);

    function getPooledEthByShares(uint256 _sharesAmount) external virtual view returns (uint256);

    function transferShares(address to, uint256 amount) external virtual;
}