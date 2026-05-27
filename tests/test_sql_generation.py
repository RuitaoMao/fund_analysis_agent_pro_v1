from src.agent.sql_generation import GeneratedSQLAgent


def test_generated_sql_mock_two_stage_query(temp_store):
    agent = GeneratedSQLAgent(temp_store)
    state = agent.run("在规模top100基金中，业绩最好的10个", mode="mock")
    result = state["tool_result"]
    assert result.tables["generated_sql_result"]
    assert "JOIN fund_performance" in result.metadata["sql"]
    assert len(result.tables["generated_sql_result"]) == 10


def test_generated_sql_validator_rejects_write_sql(temp_store):
    agent = GeneratedSQLAgent(temp_store)
    errors = agent.validate_sql("DROP TABLE fund_size")
    assert errors
