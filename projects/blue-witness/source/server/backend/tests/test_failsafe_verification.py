import unittest

from app.services.failsafe_verification import (
    pipeline_state_problem,
)


class FailSafeVerificationTest(
    unittest.TestCase
):
    """
    사고와 병합 작업 상태의 일관성 규칙을 검사함.
    """

    def test_accepts_normal_pipeline_states(
        self,
    ) -> None:
        normal_pairs = (
            ("COLLECTING", "WAITING"),
            ("READY", "PENDING"),
            ("MERGING", "RUNNING"),
            ("COMPLETED", "COMPLETED"),
            ("FAILED", "FAILED"),
        )

        for event_status, job_status in normal_pairs:
            with self.subTest(
                event_status=event_status,
                job_status=job_status,
            ):
                self.assertIsNone(
                    pipeline_state_problem(
                        event_status,
                        job_status,
                    )
                )

    def test_rejects_inconsistent_state(
        self,
    ) -> None:
        problem = pipeline_state_problem(
            "COMPLETED",
            "PENDING",
        )

        self.assertIsNotNone(problem)
        self.assertIn(
            "일치하지 않음",
            problem or "",
        )

    def test_rejects_unknown_event_state(
        self,
    ) -> None:
        problem = pipeline_state_problem(
            "UNKNOWN_STATE",
            None,
        )

        self.assertIsNotNone(problem)
        self.assertIn(
            "알 수 없는",
            problem or "",
        )
