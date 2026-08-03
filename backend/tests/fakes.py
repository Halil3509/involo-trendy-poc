"""Minimal in-memory async Mongo doubles for service-level tests.

Only the operators used by the services under test are implemented.
"""

from __future__ import annotations

import itertools
from typing import Any


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, condition in query.items():
        if key == "$or":
            if not any(_matches(document, branch) for branch in condition):
                return False
            continue
        if key == "$and":
            if not all(_matches(document, branch) for branch in condition):
                return False
            continue
        value = document.get(key)
        if isinstance(condition, dict) and "$in" in condition:
            if value not in condition["$in"]:
                return False
        elif isinstance(condition, dict) and "$nin" in condition:
            if value in condition["$nin"]:
                return False
        elif isinstance(condition, dict) and "$ne" in condition:
            if value == condition["$ne"]:
                return False
        elif isinstance(condition, dict) and "$lt" in condition:
            if value is None or value >= condition["$lt"]:
                return False
        elif isinstance(condition, dict) and "$lte" in condition:
            if value is None or value > condition["$lte"]:
                return False
        elif isinstance(condition, dict) and "$gt" in condition:
            if value is None or value <= condition["$gt"]:
                return False
        elif isinstance(condition, dict) and "$gte" in condition:
            if value is None or value < condition["$gte"]:
                return False
        elif isinstance(condition, dict) and "$exists" in condition:
            if (key in document) != bool(condition["$exists"]):
                return False
        else:
            if value != condition:
                return False
    return True


class UpdateResult:
    def __init__(self, matched: int, upserted_id: Any | None) -> None:
        self.matched_count = matched
        self.upserted_id = upserted_id


class InsertResult:
    def __init__(self, inserted_id: Any) -> None:
        self.inserted_id = inserted_id


class FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs
        self._limit: int | None = None
        self._sort: tuple[str, int] | None = None

    def limit(self, value: int) -> FakeCursor:
        self._limit = value
        return self

    def sort(self, key: str, direction: int = 1) -> FakeCursor:
        self._sort = (key, direction)
        return self

    def _resolved(self) -> list[dict[str, Any]]:
        docs = list(self._docs)
        if self._sort is not None:
            key, direction = self._sort
            docs.sort(key=lambda d: d.get(key), reverse=direction < 0)
        if self._limit is not None:
            docs = docs[: self._limit]
        return docs

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        docs = self._resolved()
        if length is not None:
            docs = docs[:length]
        return docs

    def __aiter__(self) -> Any:
        async def gen() -> Any:
            for doc in self._resolved():
                yield doc

        return gen()


class FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []
        self._ids = itertools.count(1)

    def find(self, query: dict[str, Any] | None = None) -> FakeCursor:
        query = query or {}
        return FakeCursor([d for d in self.docs if _matches(d, query)])

    async def find_one(
        self, query: dict[str, Any] | None = None, sort: list[tuple[str, int]] | None = None
    ) -> dict[str, Any] | None:
        matches = [d for d in self.docs if _matches(d, query or {})]
        if sort:
            key, direction = sort[0]
            matches.sort(key=lambda d: d.get(key), reverse=direction < 0)
        return matches[0] if matches else None

    async def count_documents(self, query: dict[str, Any]) -> int:
        return sum(1 for d in self.docs if _matches(d, query))

    async def insert_one(self, document: dict[str, Any]) -> InsertResult:
        stored = dict(document)
        stored.setdefault("_id", next(self._ids))
        self.docs.append(stored)
        document["_id"] = stored["_id"]
        return InsertResult(stored["_id"])

    async def delete_one(self, query: dict[str, Any]) -> None:
        target = next((d for d in self.docs if _matches(d, query)), None)
        if target is not None:
            self.docs.remove(target)

    async def delete_many(self, query: dict[str, Any]) -> None:
        self.docs[:] = [document for document in self.docs if not _matches(document, query)]

    async def update_one(
        self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False
    ) -> UpdateResult:
        target = next((d for d in self.docs if _matches(d, query)), None)
        if target is None:
            if not upsert:
                return UpdateResult(0, None)
            target = {"_id": next(self._ids)}
            for key, value in query.items():
                if not isinstance(value, dict):
                    target[key] = value
            self.docs.append(target)
            self._apply(target, update)
            return UpdateResult(0, target["_id"])
        self._apply(target, update)
        return UpdateResult(1, None)

    async def update_many(self, query: dict[str, Any], update: dict[str, Any]) -> UpdateResult:
        matched = 0
        for document in self.docs:
            if _matches(document, query):
                self._apply(document, update)
                matched += 1
        return UpdateResult(matched, None)

    def _apply(self, document: dict[str, Any], update: dict[str, Any]) -> None:
        for key, value in update.get("$set", {}).items():
            document[key] = value
        for key, value in update.get("$setOnInsert", {}).items():
            document.setdefault(key, value)
        for key, value in update.get("$addToSet", {}).items():
            existing = document.setdefault(key, [])
            if value not in existing:
                existing.append(value)
        for key, value in update.get("$inc", {}).items():
            document[key] = document.get(key, 0) + value

    async def distinct(self, field: str, query: dict[str, Any] | None = None) -> list[Any]:
        values: list[Any] = []
        for doc in self.docs:
            if _matches(doc, query or {}):
                candidate = doc.get(field)
                if candidate is not None and candidate not in values:
                    values.append(candidate)
        return values


class FakeDatabase:
    def __init__(self) -> None:
        self._collections: dict[str, FakeCollection] = {}

    def __getattr__(self, name: str) -> FakeCollection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._collections.setdefault(name, FakeCollection())
