import sys
import types
import unittest
from unittest.mock import patch

import numpy as np

from app.ai import reranker, vectorstore
from app.services import projects, repo


class EmployeeEntityTests(unittest.TestCase):
    def test_employee_entities_create_parent_and_filterable_skill_children(self) -> None:
        employee = {
            "id": 9,
            "name": "Sam Rivera",
            "employee_no": "E009",
            "roles": ["Engineer", "Lead"],
            "domain_tags": ["Payments", "Retail"],
            "background": "Builds payment platforms.",
            "skills": [
                {"canonical_skill": "Python", "rating": 4, "category": "Programming"},
            ],
        }

        ids, docs, metas = repo._employee_entities(employee)

        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(metas[0]["entity_type"], "employee_parent")
        self.assertEqual(metas[0]["employee_id"], 9)
        skill_entities = [meta for meta in metas if meta["entity_type"] == "employee_skill"]
        self.assertEqual(len(skill_entities), 4)
        self.assertTrue(all(meta["canonical_skill"] == "Python" for meta in skill_entities))
        self.assertTrue(all(meta["rating"] == 4 for meta in skill_entities))
        self.assertTrue(all(meta["role"] in employee["roles"] for meta in skill_entities))
        self.assertTrue(all(meta["domain_tag"] in employee["domain_tags"] for meta in skill_entities))
        self.assertTrue(all(meta["parent_id"] == ids[0] for meta in metas[1:]))
        self.assertEqual(len(ids), len(docs))


class MetadataFilterTests(unittest.TestCase):
    def test_memory_vector_collection_filters_and_returns_parent_documents(self) -> None:
        col = vectorstore._MemCol()
        col.upsert(
            ids=["employee:1:parent", "employee:1:skill", "employee:2:skill"],
            documents=["Full profile", "Python engineer", "Python analyst"],
            embeddings=[[1.0, 0.0], [0.8, 0.2], [0.2, 0.8]],
            metadatas=[
                {"entity_type": "employee_parent", "employee_id": 1},
                {"entity_type": "employee_skill", "employee_id": 1, "role": "Engineer"},
                {"entity_type": "employee_skill", "employee_id": 2, "role": "Analyst"},
            ],
        )

        result = col.query(
            query_embeddings=[[1.0, 0.0]],
            n_results=3,
            include=["documents", "metadatas", "distances"],
            where={"entity_type": "employee_skill", "role": {"$in": ["Engineer"]}},
        )

        self.assertEqual(result["ids"], [["employee:1:skill"]])
        self.assertEqual(result["documents"], [["Python engineer"]])

    def test_reranked_query_preserves_vector_results_when_cross_encoder_is_unavailable(self) -> None:
        hits = [
            {"id": "skill-a", "similarity": 0.9, "document": "A", "metadata": {}},
            {"id": "skill-b", "similarity": 0.7, "document": "B", "metadata": {}},
        ]
        with (
            patch.object(vectorstore, "query_with_documents", return_value=hits),
            patch.object(vectorstore.reranker, "rerank", side_effect=RuntimeError("model unavailable")),
            self.assertLogs("app.ai.vectorstore", level="WARNING"),
        ):
            result = vectorstore.reranked_query("taxonomy", "query", 2)

        self.assertEqual([item[0] for item in result], ["skill-a", "skill-b"])
        self.assertEqual([item[1] for item in result], [0.9, 0.7])


class RerankerTests(unittest.TestCase):
    def test_rerank_orders_vector_candidates_and_preserves_vector_score(self) -> None:
        fake_flashrank = types.ModuleType("flashrank")
        fake_flashrank.RerankRequest = lambda **kwargs: kwargs
        fake_ranker = types.SimpleNamespace(
            rerank=lambda request: [
                {"id": "second", "score": np.float32(0.91)},
                {"id": "first", "score": np.float32(0.22)},
            ]
        )
        passages = [
            {"id": "first", "document": "Low relevance", "metadata": {}, "similarity": 0.8},
            {"id": "second", "document": "High relevance", "metadata": {}, "similarity": 0.7},
        ]

        with (
            patch.object(reranker, "_get_ranker", return_value=fake_ranker),
            patch.dict(sys.modules, {"flashrank": fake_flashrank}),
        ):
            ranked = reranker.rerank("query", passages)

        self.assertEqual([item["id"] for item in ranked], ["second", "first"])
        self.assertEqual(ranked[0]["similarity"], 0.7)
        self.assertAlmostEqual(ranked[0]["rerank_score"], 0.91)

    def test_employee_retrieval_uses_entity_filters_then_reranks_parent_profiles(self) -> None:
        parents = [
            {"id": "employee:1:parent", "similarity": 0.6, "document": "Profile one",
             "metadata": {"employee_id": 1, "entity_type": "employee_parent"}},
            {"id": "employee:2:parent", "similarity": 0.5, "document": "Profile two",
             "metadata": {"employee_id": 2, "entity_type": "employee_parent"}},
        ]
        children = [
            {"id": "employee:1:skill", "similarity": 0.8, "document": "Python skill",
             "metadata": {"employee_id": 1, "entity_type": "employee_skill"}},
        ]
        call_results = iter([parents, children])

        with (
            patch.object(projects.vectorstore, "query_with_documents", side_effect=lambda *a, **k: next(call_results)) as search,
            patch.object(projects.vectorstore, "count", return_value=20),
            patch.object(projects.reranker, "rerank", side_effect=lambda _query, passages: [
                {**passage, "rerank_score": 0.9 if passage["metadata"]["employee_id"] == 1 else 0.3}
                for passage in passages
            ]) as rerank,
        ):
            scores, did_rerank = projects._employee_relevance("Python", 2)

        self.assertTrue(did_rerank)
        self.assertAlmostEqual(scores[1], 0.85)
        self.assertAlmostEqual(scores[2], 0.4)
        self.assertEqual(search.call_args_list[0].kwargs["where"], {"entity_type": "employee_parent"})
        self.assertEqual(search.call_args_list[1].kwargs["where"]["entity_type"]["$in"][0], "employee_skill")
        rerank.assert_called_once()


if __name__ == "__main__":
    unittest.main()
