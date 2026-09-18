import pytest

from prompt_os.store import DocumentStore


def test_put_get_and_exact_query_are_schema_neutral() -> None:
    store = DocumentStore()
    first = store.put("anything", {"name": "alpha", "nested": {"state": "open"}})
    store.put("anything", {"name": "beta", "nested": {"state": "closed"}})

    assert store.get(first.id).value["name"] == "alpha"
    assert [doc.id for doc in store.query(where={"nested.state": "open"})] == [first.id]
    store.close()


def test_put_with_same_id_updates_without_changing_creation_time() -> None:
    store = DocumentStore()
    before = store.put("records", {"value": 1}, document_id="fixed")
    after = store.put("records", {"value": 2}, document_id="fixed")

    assert after.value == {"value": 2}
    assert after.created_at == before.created_at
    assert len(store.query(collection="records")) == 1
    assert [revision.value for revision in store.history("fixed")] == [
        {"value": 1},
        {"value": 2},
    ]
    assert [revision.event for revision in store.history("fixed")] == [
        "created",
        "updated",
    ]
    store.close()


def test_archive_is_soft_delete() -> None:
    store = DocumentStore()
    document = store.put("records", {"value": 1})
    archived = store.archive(document.id, "no longer needed")

    assert archived.archived_at is not None
    assert archived.archive_reason == "no longer needed"
    assert store.query(collection="records") == []
    assert [revision.event for revision in store.history(document.id)] == [
        "created",
        "archived",
    ]
    with pytest.raises(KeyError):
        store.get(document.id)
    store.close()


def test_aggregate_uses_every_matching_document_and_exact_decimal_strings() -> None:
    store = DocumentStore()
    for index in range(501):
        store.put(
            "expenses",
            {"amount": "0.10", "currency": "EUR"},
            document_id=str(index),
        )

    result = store.aggregate("sum", field="amount", group_by="currency")

    assert result["groups"] == [{"key": "EUR", "value": "50.10"}]
    assert store.aggregate("count")["groups"][0]["value"] == 501
    store.close()


def test_search_is_deterministic_and_application_scoped(tmp_path) -> None:
    first = DocumentStore(tmp_path / "data.sqlite", app_id="first")
    second = DocumentStore(tmp_path / "data.sqlite", app_id="second")
    first.put("notes", {"text": "SQLite keeps deployment simple"}, "one")
    first.put("notes", {"text": "A different decision"}, "two")
    second.put("notes", {"text": "SQLite private value"}, "other")

    assert [item.id for item in first.search("sqlite deployment")] == ["one"]
    assert second.search("deployment") == []
    first.close()
    second.close()


def test_collection_and_limit_are_validated() -> None:
    store = DocumentStore()
    with pytest.raises(ValueError):
        store.put("Not Valid", {})
    with pytest.raises(ValueError):
        store.query(limit=0)
    store.close()
