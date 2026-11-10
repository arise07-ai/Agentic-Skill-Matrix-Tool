import unittest

from app.services import groups


class SkillGroupTests(unittest.TestCase):
    def test_mobile_frontend_alias_resolves_to_canonical_mobile_group(self) -> None:
        groups.reload()

        self.assertEqual(groups.match("Mobile Development"), "Mobile Development")
        self.assertEqual(groups.match(" mobile frontend development "), "Mobile Development")
        self.assertTrue(groups.is_group("Mobile Frontend Development"))
        self.assertNotIn("Mobile Frontend Development", groups.names())

    def test_hyphenated_full_stack_alias_resolves_to_canonical_group(self) -> None:
        groups.reload()

        self.assertEqual(groups.match("Full-Stack Development"), "Frontend & Backend")

    def test_alias_uses_canonical_group_members_and_own_rating(self) -> None:
        self.assertEqual(
            groups.effective({"React Native": 4}, "Mobile Frontend Development"),
            (4, "React Native"),
        )
        self.assertEqual(
            groups.effective({"Mobile Frontend Development": 5}, "Mobile Development"),
            (5, None),
        )
        self.assertEqual(
            groups.effective({"Full-Stack Development": 4}, "Full Stack Development"),
            (4, None),
        )

    def test_specific_leadership_rating_is_not_inferred_from_communication(self) -> None:
        groups.reload()
        ratings = {"Leadership": 3, "Communication": 5}

        self.assertIsNone(groups.match("Leadership"))
        self.assertEqual(groups.effective(ratings, "Leadership"), (3, None))
        self.assertEqual(groups.effective(ratings, "Soft Skills & Leadership"), (5, "Communication"))


if __name__ == "__main__":
    unittest.main()
