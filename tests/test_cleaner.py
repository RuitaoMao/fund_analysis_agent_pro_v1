from src.data.cleaner import standardize_fund_code, standardize_date


def test_standardize_fund_code():
    assert standardize_fund_code("001623.OF") == "001623"
    assert standardize_fund_code("162411.SZ") == "162411"
    assert standardize_fund_code(44) == "000044"


def test_standardize_date():
    assert standardize_date("2026-03-31") == "2026-03-31"
