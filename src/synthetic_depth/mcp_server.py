import asyncio
from mcp.server.fastmcp import FastMCP
from synthetic_depth.microstructure.synthetic_book import SyntheticDepthEstimator, SlippageEstimator

# Create an MCP server instance
mcp = FastMCP("Synthetic Depth Engine")

@mcp.tool()
def get_synthetic_order_book(symbol: str, num_levels: int = 10, tick_size: float = 0.01) -> dict:
    """Fetch the latest synthetic order book for a given stock symbol.
    
    Args:
        symbol: The stock symbol (e.g. AAPL)
        num_levels: The number of price levels to generate for bids and asks
        tick_size: The price increment per level
    """
    estimator = SyntheticDepthEstimator()
    book = estimator.get_latest_synthetic_book(symbol, num_levels, tick_size)
    if not book:
        return {"error": f"Could not generate synthetic book for {symbol}"}
    return book.to_dict()

@mcp.tool()
def estimate_slippage(symbol: str, order_size: int, side: str = "BUY") -> dict:
    """Estimate the slippage cost and average execution price for a hypothetical order.
    
    Args:
        symbol: The stock symbol (e.g. AAPL)
        order_size: The number of shares to execute
        side: The order side, either 'BUY' or 'SELL'
    """
    depth_estimator = SyntheticDepthEstimator()
    book = depth_estimator.get_latest_synthetic_book(symbol, 10, 0.01)
    if not book:
        return {"error": f"Could not fetch order book for {symbol}"}
        
    slippage_estimator = SlippageEstimator()
    try:
        estimate = slippage_estimator.estimate_slippage(book, order_size, side)
        return estimate.to_dict()
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    mcp.run()
