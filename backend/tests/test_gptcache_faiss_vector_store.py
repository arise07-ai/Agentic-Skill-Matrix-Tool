import tempfile
import unittest
from pathlib import Path

import numpy as np

from gptcache.manager import manager_factory


class GPTCacheFaissVectorStoreTests(unittest.TestCase):
    def test_vector_and_cache_data_survive_manager_recreation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            manager = manager_factory(
                manager="sqlite,faiss",
                data_dir=str(data_dir),
                vector_params={"dimension": 3},
            )
            self.assertEqual(type(manager.v).__name__, "Faiss")
            vector = np.array([1.0, 0.0, 0.0], dtype="float32")
            manager.save("known question", "known answer", vector)
            manager.close()

            reopened_manager = manager_factory(
                manager="sqlite,faiss",
                data_dir=str(data_dir),
                vector_params={"dimension": 3},
            )
            self.assertEqual(type(reopened_manager.v).__name__, "Faiss")
            nearest = reopened_manager.search(vector, top_k=1)
            self.assertEqual(len(nearest), 1)
            self.assertEqual(nearest[0][0], 0.0)

            record = reopened_manager.get_scalar_data(nearest[0])
            self.assertIsNotNone(record)
            self.assertEqual(record.question, "known question")
            answer = record.answers[0]
            self.assertEqual(getattr(answer, "answer", answer), "known answer")
            reopened_manager.close()


if __name__ == "__main__":
    unittest.main()
