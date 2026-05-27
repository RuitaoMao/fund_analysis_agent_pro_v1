def test_sqlite_store_rebuild_and_metadata(temp_store):
    assert temp_store.table_row_count("fund_size") > 0
    assert temp_store.table_row_count("fund_holding") > 0
    assert temp_store.table_row_count("fund_performance") > 0
    assert temp_store.max_date("fund_size") == "2026-03-31"
    assert temp_store.date_exists("fund_size", "2026-03-31")
    assert "主动权益" in temp_store.distinct_values("fund_size", "asset_type")
