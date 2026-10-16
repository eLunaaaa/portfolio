from pathlib import Path
import tempfile
import unittest

from runtime_contract import load_runtime_contract, resolve_export_confidence


class RuntimeContractTest(unittest.TestCase):
    def test_release_uses_runtime_settings(self):
        contract = load_runtime_contract()
        self.assertEqual(resolve_export_confidence(None, contract), 0.60)
        self.assertEqual(resolve_export_confidence(0.60, contract), 0.60)
        self.assertEqual(contract["inference_frame_step"], 6)
        self.assertEqual(contract["nms_iou_threshold"], 0.65)

    def test_mismatched_and_non_finite_thresholds_are_rejected(self):
        contract = load_runtime_contract()
        for value in [0.55, 0.65, 0.70, 0.80, float("nan"), float("inf")]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                resolve_export_confidence(value, contract)

    def test_header_change_reaches_export_without_another_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inference_config.h"
            path.write_text("#define INFERENCE_CONFIDENCE_THRESHOLD 0.62f\n"
                            "#define INFERENCE_NMS_IOU_THRESHOLD 0.65f\n"
                            "#define INFERENCE_FRAME_STEP 6U\n", encoding="utf-8")
            self.assertEqual(resolve_export_confidence(None, load_runtime_contract(path)), 0.62)

    def test_missing_setting_fails_instead_of_falling_back(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inference_config.h"
            path.write_text("", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_runtime_contract(path)


if __name__ == "__main__":
    unittest.main()
