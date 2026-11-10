import unittest

from app.services.learning import describe_review

SKILL = "LLMs & Prompt Engineering"
LONG = {"title": "Introduction to generative AI for trainers", "url": "https://learn.example/long", "kind": "course"}
SHORT = {"title": "Introduction to prompt engineering with GitHub Copilot", "url": "https://learn.example/short", "kind": "course"}
VIDEO = {"title": "Prompt Engineering Tutorial", "url": "https://video.example/v", "kind": "video"}
BY_ID = {"c1": {"skill": SKILL, "course": LONG}, "c2": {"skill": SKILL, "course": SHORT}, "c3": {"skill": SKILL, "course": VIDEO}}


def draft(study_id="c1", watch_id="c3", practice="Write prompts for a small task."):
    return {"skills": [{"skill": SKILL, "study_id": study_id, "watch_id": watch_id, "why": "It fits.", "practice": practice}]}


class DescribeReviewTests(unittest.TestCase):
    def test_course_swap_uses_titles_and_a_clean_reason(self) -> None:
        picks = {SKILL: {"study": SHORT, "watch": VIDEO, "practice": "Write prompts for a small task."}}
        review = [{"skill": SKILL, "reason": "It is shorter and closer to beginner level, so it fits the planned time"}]
        notes = describe_review(draft(), picks, BY_ID, review)
        self.assertEqual(notes, [
            f"For {SKILL}, I swapped the course “{LONG['title']}” for “{SHORT['title']}”: "
            "it is shorter and closer to beginner level, so it fits the planned time."
        ])

    def test_reason_with_internal_ids_or_field_names_is_dropped(self) -> None:
        picks = {SKILL: {"study": SHORT, "watch": VIDEO, "practice": "Write prompts for a small task."}}
        for reason in ("Selected c2 for study_id as it is shorter", "better planned_hours fit", "see the JSON"):
            notes = describe_review(draft(), picks, BY_ID, [{"skill": SKILL, "reason": reason}])
            self.assertEqual(notes, [f"For {SKILL}, I swapped the course “{LONG['title']}” for “{SHORT['title']}”."], reason)
            self.assertNotIn("study_id", notes[0])

    def test_no_note_when_nothing_changed(self) -> None:
        picks = {SKILL: {"study": LONG, "watch": VIDEO, "why": "It fits.", "practice": "Write prompts for a small task."}}
        self.assertEqual(describe_review(draft(), picks, BY_ID, [{"skill": SKILL, "reason": "looks fine"}]), [])

    def test_change_overridden_by_the_rules_is_not_claimed(self) -> None:
        # the reviewer's course was rejected by verify_picks, so the final picks carry no study course
        picks = {SKILL: {"watch": VIDEO, "practice": "Write prompts for a small task."}}
        self.assertEqual(describe_review(draft(), picks, BY_ID, []), [])

    def test_reworded_practice_task(self) -> None:
        picks = {SKILL: {"study": LONG, "watch": VIDEO, "why": "It fits.", "practice": "Build a prompt library for code reviews."}}
        self.assertEqual(describe_review(draft(), picks, BY_ID, []), [f"For {SKILL}, I reworded the practice task."])

    def test_old_string_review_format_or_missing_draft_is_safe(self) -> None:
        picks = {SKILL: {"study": SHORT}}
        self.assertEqual(describe_review(None, picks, BY_ID, ["Selected c2 as study_id"]), [])
        self.assertEqual(len(describe_review(draft(), picks, BY_ID, ["Selected c2 as study_id"])), 1)


if __name__ == "__main__":
    unittest.main()
