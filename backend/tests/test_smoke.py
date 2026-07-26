"""Smoke test — verify the trading_lab package is importable."""


def test_import_trading_lab():
    import trading_lab

    assert hasattr(trading_lab, "__version__")
    assert trading_lab.__version__ == "0.1.0"
