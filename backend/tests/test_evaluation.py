import asyncio
import sys
import types
import unittest
from unittest.mock import Mock, patch

from app.ai import evaluation, eval_checks


class EvaluationMetricTests(unittest.TestCase):
    def test_collections_judge_receives_and_records_max_output_tokens(self) -> None:
        class FakeMetric:
            def __init__(self, **_kwargs):
                pass

        openai = types.ModuleType("openai")
        openai.AsyncOpenAI = lambda **_kwargs: object()
        ragas = types.ModuleType("ragas")
        ragas.__path__ = []
        ragas_embeddings = types.ModuleType("ragas.embeddings")
        ragas_embeddings.OpenAIEmbeddings = lambda **_kwargs: object()
        ragas_llms = types.ModuleType("ragas.llms")
        ragas_llms.llm_factory = Mock(return_value=object())
        ragas_metrics = types.ModuleType("ragas.metrics")
        collections = types.ModuleType("ragas.metrics.collections")
        collections.Faithfulness = FakeMetric
        collections.AnswerRelevancy = FakeMetric
        collections.ContextRelevance = FakeMetric
        ragas_metrics.collections = collections
        settings = types.SimpleNamespace(
            openai_api_key="test-key",
            openai_base_url="",
            llm_model="test-model",
            embedding_model="test-embedding",
        )

        with patch.dict(sys.modules, {
            "openai": openai,
            "ragas": ragas,
            "ragas.embeddings": ragas_embeddings,
            "ragas.llms": ragas_llms,
            "ragas.metrics": ragas_metrics,
            "ragas.metrics.collections": collections,
        }):
            setup = evaluation._Collections(settings)

        self.assertEqual(
            ragas_llms.llm_factory.call_args.kwargs["max_tokens"],
            evaluation.JUDGE_MAX_TOKENS,
        )
        self.assertEqual(setup.judge_max_tokens, evaluation.JUDGE_MAX_TOKENS)

    def test_staffing_is_not_a_ragas_feature(self) -> None:
        self.assertEqual(evaluation.METRICS, ["faithfulness", "answer_relevancy", "context_relevance"])
        self.assertNotIn("staffing", evaluation.RAGAS_FEATURES)
        self.assertIn("staffing", evaluation.DETERMINISTIC_FEATURES)

    def test_scorable_rows_do_not_need_a_reference_answer(self) -> None:
        row = {"question": "Who knows Python?", "answer": "Alex does.", "contexts": ["Alex: Python 4/5."]}

        eligible, skipped = evaluation.scorable([row])

        self.assertEqual(eligible, [row])
        self.assertEqual(skipped, [])

    def test_dataset_adapts_missing_reference_for_legacy_schema(self) -> None:
        class FakeDataset:
            @staticmethod
            def from_list(rows):
                return rows

        ragas = types.ModuleType("ragas")
        ragas.EvaluationDataset = FakeDataset
        row = {"question": "Who knows Python?", "answer": "Alex does.", "contexts": ["Alex: Python 4/5."]}

        with patch.dict(sys.modules, {"ragas": ragas}):
            dataset = evaluation._dataset([row])

        self.assertEqual(dataset[0]["reference"], "")

    def test_collection_metrics_do_not_read_reference_answer(self) -> None:
        class FakeMetric:
            async def ascore(self, **kwargs):
                return kwargs

        setup = evaluation._Collections.__new__(evaluation._Collections)
        setup.metrics = {"context_relevance": FakeMetric()}
        row = {"question": "Who knows Python?", "answer": "Alex does.", "contexts": ["Alex: Python 4/5."]}

        result = asyncio.run(setup.call("context_relevance", row))

        self.assertEqual(result["user_input"], row["question"])
        self.assertEqual(result["retrieved_contexts"], row["contexts"])


class DeterministicStaffingEvaluationTests(unittest.TestCase):
    def test_false_candidate_skill_evidence_fails_database_checks(self) -> None:
        row = {
            "answer": "Sarah Lee is the best fit.",
            "meta": {
                "requirements": [{"skill": "Leadership", "level": 4}],
                "team_details": [{
                    "id": 1, "name": "Sarah Lee", "role": "contributor",
                    "meets": ["Leadership"], "below_target": [], "adds": ["Leadership"],
                }],
                "team_size": 1,
                "uncovered": [],
                "nobody": [],
                "team_limited": [],
                "learning": [],
                "not_chosen": [],
            },
        }

        checks = eval_checks.check_staffing(row, {1: {"Leadership": 3}}, {1: "Sarah Lee"})
        results = {check["check"]: check["passed"] for check in checks}

        self.assertFalse(results["candidate_skill_evidence_matches_database"])
        self.assertFalse(results["team_contributions_match_coverage"])
        self.assertFalse(results["company_skill_coverage_matches_database"])


if __name__ == "__main__":
    unittest.main()
