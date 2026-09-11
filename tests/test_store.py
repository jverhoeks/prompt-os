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
    store.close()


def test_archive_is_soft_delete() -> None:
    store = DocumentStore()
    document = store.put("records", {"value": 1})
    archived = store.archive(document.id, "no longer needed")

    assert archived.archived_at is not None
    assert archived.archive_reason == "no longer needed"
    assert store.query(collection="records") == []
    with pytest.raises(KeyError):
        store.get(document.id)
    store.close()


def test_collection_and_limit_are_validated() -> None:
    store = DocumentStore()
    with pytest.raises(ValueError):
        store.put("Not Valid", {})
    with pytest.raises(ValueError):
        store.query(limit=0)
    store.close()

