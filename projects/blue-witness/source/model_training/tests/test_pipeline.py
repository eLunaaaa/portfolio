from __future__ import annotations

import json
import argparse
import os
import hashlib
import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import yaml
import torch
import cv2
import numpy as np

from dataset_contracts import (
    DatasetContractError,
    atomic_write_json,
    atomic_write_jsonl,
    class_maps,
    load_settings,
    next_dataset_version,
    read_json,
    read_jsonl,
)
from export_coco import export_dataset
from prepare_dataset import ProposalDetector, prepare
from review_annotations import (
    ReviewSession,
    center_review_image,
    fullscreen_canvas_width,
    normalize_review_key,
    run_review,
)
from validate_dataset import validate_dataset
from export_onnx import export_model
from compare_outputs import compare
from yolox_runtime import build_yolox_model, load_pretrained_backbone, validate_yolox_installation
from train import create_training_loader
from evaluate import evaluate_checkpoint
from sync_recordings import (
    INITIAL_VEHICLES_EXPECTED_FILENAMES,
    PALISADE_EXPECTED_FILENAMES,
    initial_vehicles_capture_metadata,
    palisade_capture_metadata,
    select_capture_plan_files,
)
from register_recordings import build_manifest, load_capture_plan


PROJECT_DIR = Path(__file__).resolve().parents[1]


class DatasetPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        source_config = yaml.safe_load((PROJECT_DIR / "config.yaml").read_text(encoding="utf-8"))
        source_config["paths"] = {
            "recordings_dir": str(self.root / "recordings"),
            "work_dir": str(self.root / "work"),
            "datasets_dir": str(self.root / "datasets"),
        }
        source_config["frame_extraction"]["frame_interval"] = 10
        source_config["proposal_detector"]["enabled"] = False
        source_config["dataset_export"]["split_search_attempts"] = 1000
        source_config["yolox"]["source_dir"] = str(
            PROJECT_DIR / source_config["yolox"]["source_dir"]
        )
        checkpoint_name = Path(source_config["yolox"]["pretrained_checkpoint"]).name
        source_config["yolox"]["pretrained_checkpoint"] = str(
            PROJECT_DIR / "models" / "pretrained" / checkpoint_name
        )
        # 입력: CI가 생성한 임시 checkpoint 또는 개인 환경의 원본 checkpoint.
        # 출력: 그 fixture의 SHA를 고정한 테스트 설정. 제품 설정/검증 규칙은 변경하지 않습니다.
        fixture=os.environ.get('PORTFOLIO_TEST_PRETRAINED')
        if fixture:
            fixture=Path(fixture).resolve()
            source_config['yolox']['pretrained_checkpoint']=str(fixture)
            source_config['yolox']['pretrained_sha256']=hashlib.sha256(fixture.read_bytes()).hexdigest()
        self.config_path = self.root / "config.yaml"
        self.config_path.write_text(yaml.safe_dump(source_config, sort_keys=False), encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_recording(
        self, number: int, color: str, dataset_usage: str = "development"
    ) -> None:
        recording_id = f"recording_{number}"
        directory = self.root / "recordings" / f"session_{number}" / recording_id
        directory.mkdir(parents=True)
        video_path = directory / "recording.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v", "error",
                "-f", "lavfi",
                "-i", f"color=c={color}:s=640x480:r=30:d=0.4",
                "-c:v", "libx264",
                "-b:v", "2500k",
                "-pix_fmt", "yuv420p",
                "-y", str(video_path),
            ],
            check=True,
        )
        atomic_write_json(
            directory / "recording_manifest.json",
            {
                "schema_version": 2,
                "recording_id": recording_id,
                "session_id": f"session_{number}",
                "source_group_id": f"source_{number}",
                "derived_from": None,
                "encoding_profile": "primary_2_5mbps",
                "dataset_usage": dataset_usage,
                "condition_tags": ["test", f"condition_{number}"],
                "vehicle_instances_present": ["ioniq5_silver"],
                "recorded_at_utc": f"2026-09-02T00:00:0{number}Z",
                "video_file": "recording.mp4",
            },
        )

    def approve_all(self) -> None:
        work_dir = self.root / "work"
        annotations = read_jsonl(work_dir / "annotations.jsonl")
        for annotation in annotations:
            annotation["status"] = "approved"
            annotation["objects"] = [
                {
                    "bbox": [40.0, 50.0, 200.0, 120.0],
                    "proposal_confidence": None,
                    "class_name": "ioniq5_silver",
                    "vehicle_instance": "ioniq5_silver",
                },
                {
                    "bbox": [260.0, 50.0, 160.0, 120.0],
                    "proposal_confidence": None,
                    "class_name": "palisade_black",
                    "vehicle_instance": "palisade_black",
                },
                {
                    "bbox": [440.0, 50.0, 60.0, 100.0],
                    "proposal_confidence": None,
                    "class_name": "other_vehicle",
                    "vehicle_instance": "other_car_1",
                },
                {
                    "bbox": [440.0, 170.0, 60.0, 100.0],
                    "proposal_confidence": None,
                    "class_name": "other_vehicle",
                    "vehicle_instance": "other_car_2",
                },
                {
                    "bbox": [440.0, 290.0, 60.0, 100.0],
                    "proposal_confidence": None,
                    "class_name": "other_vehicle",
                    "vehicle_instance": "other_car_3",
                },
            ]
        atomic_write_jsonl(work_dir / "annotations.jsonl", annotations)

    def test_final_capture_plan_metadata(self) -> None:
        paths = [
            f"/home/craft3/vehicle_recordings/session{session:02d}_video{video:02d}.mp4"
            for session in range(1, 7)
            for video in range(1, 15)
        ]
        selected = select_capture_plan_files(
            paths, INITIAL_VEHICLES_EXPECTED_FILENAMES, "initial_vehicles_14"
        )
        self.assertEqual(selected, paths)
        self.assertEqual(
            initial_vehicles_capture_metadata(paths[0], "2026-09-04T00:00:00Z")[
                "vehicle_instances_present"
            ],
            ["ioniq5_silver"],
        )
        session_two_mixed = initial_vehicles_capture_metadata(
            "/home/craft3/vehicle_recordings/session02_video10.mp4",
            "2026-09-04T00:00:00Z",
        )
        self.assertEqual(
            session_two_mixed["vehicle_instances_present"],
            ["ioniq5_silver", "other_car_2"],
        )
        self.assertEqual(session_two_mixed["condition_tags"], [])
        self.assertEqual(
            initial_vehicles_capture_metadata(
                "/home/craft3/vehicle_recordings/session06_video14.mp4",
                "2026-09-04T00:00:00Z",
            )["vehicle_instances_present"],
            [],
        )
        with self.assertRaisesRegex(DatasetContractError, "파일 구성"):
            select_capture_plan_files(
                paths[:-1], INITIAL_VEHICLES_EXPECTED_FILENAMES, "initial_vehicles_14"
            )

        palisade_paths = [
            f"/home/craft3/vehicle_recordings/session{session:02d}_video{video:02d}.mp4"
            for session in range(7, 11)
            for video in range(1, 9)
        ]
        selected_palisade = select_capture_plan_files(
            paths + palisade_paths, PALISADE_EXPECTED_FILENAMES, "palisade_8"
        )
        self.assertEqual(selected_palisade, palisade_paths)
        self.assertEqual(
            palisade_capture_metadata(palisade_paths[0], "2026-09-07T00:00:00Z")[
                "vehicle_instances_present"
            ],
            ["palisade_black"],
        )
        self.assertEqual(
            palisade_capture_metadata(palisade_paths[-1], "2026-09-07T00:00:00Z")[
                "vehicle_instances_present"
            ],
            ["palisade_black", "other_car_3"],
        )

    def test_augmentation_capture_plan_metadata(self) -> None:
        source = self.root / "session11_video04.mp4"
        source.write_bytes(b"augmentation-video")
        plans_path = Path(__file__).resolve().parents[1] / "capture_plans.yaml"
        plan = load_capture_plan(plans_path, "augmentation_round_01")
        item = next(item for item in plan["recordings"] if item["file"] == source.name)
        manifest = build_manifest(source, plan, item)
        self.assertEqual(manifest["session_id"], "session11")
        self.assertEqual(manifest["dataset_usage"], "development")
        self.assertEqual(
            manifest["vehicle_instances_present"],
            ["ioniq5_silver", "other_car_3"],
        )

    def test_delegated_demo_capture_plan_metadata(self) -> None:
        """인계 촬영 계획의 파일 수와 대표 차량 계약을 확인한다."""
        plans_path = PROJECT_DIR / "capture_plans.yaml"
        plan = load_capture_plan(plans_path, "delegated_demo_round_04")
        self.assertEqual(len(plan["recordings"]), 30)
        self.assertEqual(plan["source_directory"], "data/incoming/session23_25")

        first = next(
            item for item in plan["recordings"]
            if item["file"] == "session23_video01.mp4"
        )
        collision = next(
            item for item in plan["recordings"]
            if item["file"] == "session24_video05.mp4"
        )
        negative = next(
            item for item in plan["recordings"]
            if item["file"] == "session25_video10.mp4"
        )
        self.assertEqual(first["vehicle_instances"], ["ioniq5_silver"])
        self.assertEqual(
            collision["vehicle_instances"], ["ioniq5_silver", "other_car_3"]
        )
        self.assertEqual(negative["vehicle_instances"], [])

    def test_full_prepare_export_validate_pipeline(self) -> None:
        for number, color in enumerate(("red", "green", "blue"), start=1):
            self.make_recording(number, color)
        added, skipped = prepare(self.config_path)
        self.assertEqual(added, 3)
        self.assertEqual(skipped, 0)
        added_again, skipped_again = prepare(self.config_path)
        self.assertEqual(added_again, 0)
        self.assertEqual(skipped_again, 3)

        frames = read_jsonl(self.root / "work" / "frame_manifest.jsonl")
        self.assertEqual(len(frames), 3)
        self.assertTrue(all(item["timestamp_source"] == "ffprobe_best_effort_pts" for item in frames))
        self.assertTrue(all(item["video_bitrate_bps"] >= 0 for item in frames))
        self.assertEqual(len({item["source_video_sha256"] for item in frames}), 3)

        self.approve_all()
        dataset_dir = export_dataset(self.config_path, "dataset_v001")
        report = validate_dataset(self.config_path, dataset_dir)
        self.assertEqual(report["frame_count"], 3)
        self.assertEqual(report["object_count"], 15)
        self.assertEqual(set(report["counts_by_split"]), {"train", "val", "test"})
        train_coco = json.loads(
            (dataset_dir / "annotations" / "instances_train.json").read_text(encoding="utf-8")
        )
        self.assertEqual([item["id"] for item in train_coco["categories"]], [0, 1, 2])
        self.assertEqual(next_dataset_version(self.root / "datasets"), "dataset_v002")
        with self.assertRaises(DatasetContractError):
            export_dataset(self.config_path, "dataset_v001")

        settings = load_settings(self.config_path)
        source_dir, pretrained = validate_yolox_installation(self.config_path, settings)
        model = build_yolox_model(source_dir, 3, settings["yolox"]["model_variant"])
        transfer = load_pretrained_backbone(model, pretrained)
        # Nano와 Tiny는 layer 구성에 따라 tensor 개수가 다르므로 절대 개수보다
        # backbone 대부분이 실제로 전이됐는지를 확인한다.
        self.assertGreater(transfer["loaded_tensor_count"], 400)
        loader = create_training_loader(
            source_dir, dataset_dir, (320, 416), settings["training"], 1, None
        )
        # train에만 6배 가중치를 적용했을 때 실제 COCO 순서의 sampler와
        # 재현 seed가 유지되는지 검사합니다. val/test 파일은 변경하지 않습니다.
        train_images = train_coco["images"]
        weighted_training = dict(settings["training"], session_sampling_weights={
            train_images[0]["session_id"]: 6.0
        })
        weighted_loader = create_training_loader(
            source_dir, dataset_dir, (320, 416), weighted_training, 1, None
        )
        self.assertIsInstance(weighted_loader.sampler, torch.utils.data.WeightedRandomSampler)
        by_id = {item["id"]: item for item in train_images}
        expected = [6.0 if by_id[image_id]["session_id"] == train_images[0]["session_id"] else 1.0
                    for image_id in weighted_loader.dataset.ids]
        self.assertEqual(weighted_loader.sampler.weights.tolist(), expected)
        duplicate_loader = create_training_loader(
            source_dir, dataset_dir, (320, 416), weighted_training, 1, None
        )
        self.assertEqual(list(weighted_loader.sampler), list(duplicate_loader.sampler))
        invalid_training = dict(settings["training"], session_sampling_weights={
            train_images[0]["session_id"]: float("nan")
        })
        with self.assertRaisesRegex(DatasetContractError, "유한한 양수"):
            create_training_loader(source_dir, dataset_dir, (320, 416), invalid_training, 1, None)
        images, targets, _, _ = next(iter(loader))
        model.train()
        losses = model(images.float(), targets.float())
        self.assertTrue(torch.isfinite(losses["total_loss"]))
        losses["total_loss"].backward()
        checkpoint_path = self.root / "best.pth"
        torch.save(
            {
                "model": model.state_dict(),
                "epoch": 1,
                "input_size": [480, 640],
                "dataset_version": "dataset_v001",
                "dataset_sha256": json.loads(
                    (dataset_dir / "dataset_manifest.json").read_text(encoding="utf-8")
                )["dataset_sha256"],
                "classes": ["ioniq5_silver", "palisade_black", "other_vehicle"],
                "model_variant": settings["yolox"]["model_variant"],
            },
            checkpoint_path,
        )
        export_dir = self.root / "export"
        onnx_path = export_model(
            argparse.Namespace(
                config=self.config_path,
                dataset_version="dataset_v001",
                checkpoint=checkpoint_path,
                width=640,
                height=480,
                evaluation=None,
                output_dir=export_dir,
            )
        )
        sample_image = next((dataset_dir / "images" / "train").glob("*.jpg"))
        comparison = compare(
            argparse.Namespace(
                config=self.config_path,
                checkpoint=checkpoint_path,
                onnx=onnx_path,
                image=sample_image,
                width=640,
                height=480,
                confidence_threshold=0.99,
            )
        )
        self.assertEqual(comparison["raw_shape"], [1, 6300, 8])
        self.assertLess(comparison["raw_max_absolute_error"], 1e-3)
        validation = evaluate_checkpoint(
            self.config_path, "dataset_v001", checkpoint_path, "val", (480, 640), "jpeg"
        )
        self.assertEqual(len(validation["confidence_sweep"]), 17)
        with self.assertRaisesRegex(DatasetContractError, "Validation에서 선택한"):
            evaluate_checkpoint(
                self.config_path, "dataset_v001", checkpoint_path, "test", (480, 640), "jpeg"
            )

    def test_final_test_recording_is_excluded_from_export(self) -> None:
        for number, color in enumerate(("red", "green", "blue"), start=1):
            self.make_recording(number, color)
        self.make_recording(4, "white", dataset_usage="final_test")
        prepare(self.config_path)
        self.approve_all()
        annotations_path = self.root / "work" / "annotations.jsonl"
        annotations = read_jsonl(annotations_path)
        for annotation in annotations:
            if annotation["frame_id"].startswith("recording_4__"):
                annotation["status"] = "manual_review_required"
                annotation["objects"] = []
        atomic_write_jsonl(annotations_path, annotations)

        dataset_dir = export_dataset(self.config_path, "dataset_v001")
        manifest = read_json(dataset_dir / "dataset_manifest.json")
        self.assertFalse(
            any(item["session_id"] == "session_4" for item in manifest["frame_trace"])
        )

    def test_proposal_detector_refuses_missing_contract(self) -> None:
        settings = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        settings["proposal_detector"] = {
            "enabled": True,
            "backend": "yolox_pytorch",
            "model_path": None,
            "input_width": None,
            "input_height": None,
            "confidence_threshold": None,
            "nms_iou_threshold": None,
        }
        with self.assertRaisesRegex(DatasetContractError, "실제 모델 계약"):
            ProposalDetector(settings, self.config_path)

    def test_pytorch_proposal_detector_runs_on_cpu(self) -> None:
        proposal_config_path = PROJECT_DIR / "config_2class_nano.yaml"
        settings = yaml.safe_load(proposal_config_path.read_text(encoding="utf-8"))
        settings["proposal_detector"] = {
            "enabled": True,
            "backend": "yolox_pytorch",
            "model_path": "models/pretrained/yolox_nano.pth",
            "input_width": 416,
            "input_height": 416,
            "coco_vehicle_classes": ["car", "bus", "truck"],
            "confidence_threshold": 0.15,
            "nms_iou_threshold": 0.50,
        }
        fixture=os.environ.get('PORTFOLIO_TEST_PRETRAINED_NANO')
        if fixture:
            path=Path(fixture).resolve()
            settings['yolox']['source_dir']=str(PROJECT_DIR/'third_party/YOLOX')
            settings['yolox']['pretrained_checkpoint']=str(path)
            settings['yolox']['pretrained_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            settings['proposal_detector']['model_path']=str(path)
        detector = ProposalDetector(settings, proposal_config_path)
        proposals = detector.detect(np.zeros((480, 640, 3), dtype=np.uint8))
        self.assertIsInstance(proposals, list)
        self.assertEqual(next(detector.model.parameters()).device.type, "cpu")
        self.assertEqual(detector.vehicle_class_ids, {2, 5, 7})

    def test_trained_proposal_resolves_only_unambiguous_vehicle(self) -> None:
        detector = ProposalDetector.__new__(ProposalDetector)
        detector.class_candidate_instances = {
            "ioniq5_silver": ["ioniq5_silver"],
            "other_vehicle": [
                "palisade_black",
                "other_car_1",
                "other_car_2",
                "other_car_3",
            ],
        }
        detector.instance_to_project_class = {
            "ioniq5_silver": "ioniq5_silver",
            "palisade_black": "palisade_black",
            "other_car_1": "other_vehicle",
            "other_car_2": "other_vehicle",
            "other_car_3": "other_vehicle",
        }

        self.assertEqual(
            detector._resolve_project_label("ioniq5_silver", ["ioniq5_silver"]),
            ("ioniq5_silver", "ioniq5_silver"),
        )
        self.assertEqual(
            detector._resolve_project_label("other_vehicle", ["palisade_black"]),
            ("palisade_black", "palisade_black"),
        )
        self.assertEqual(
            detector._resolve_project_label(
                "other_vehicle", ["other_car_2", "other_car_3"]
            ),
            (None, None),
        )

    def test_review_bbox_can_move_and_resize(self) -> None:
        session = ReviewSession.__new__(ReviewSession)
        session.frames = [{"frame_id": "frame", "width": 100, "height": 80}]
        session.index = 0
        session.annotations = {
            "frame": {
                "frame_id": "frame",
                "status": "approved",
                "objects": [{"bbox": [10.0, 10.0, 20.0, 20.0]}],
            }
        }
        session.selected_object = 0
        session.drag_start = None
        session.drag_current = None
        session.drag_mode = None
        session.drag_original_bbox = None
        session.fixed_bbox = None

        session.mouse_callback(cv2.EVENT_LBUTTONDOWN, 20, 20, 0, None)
        session.mouse_callback(cv2.EVENT_LBUTTONUP, 30, 35, 0, None)
        self.assertEqual(session.annotation["objects"][0]["bbox"], [20.0, 25.0, 20.0, 20.0])
        self.assertEqual(session.annotation["status"], "manual_review_required")

        session.mouse_callback(cv2.EVENT_LBUTTONDOWN, 40, 45, 0, None)
        session.mouse_callback(cv2.EVENT_LBUTTONUP, 50, 60, 0, None)
        self.assertEqual(session.annotation["objects"][0]["bbox"], [20.0, 25.0, 30.0, 35.0])

    def test_review_image_is_centered_and_mouse_uses_frame_coordinates(self) -> None:
        image = np.zeros((80, 100, 3), dtype=np.uint8)
        centered, offset, scale = center_review_image(image, 300, 200)
        self.assertEqual(centered.shape, (200, 300, 3))
        self.assertEqual(offset, (100, 60))
        self.assertEqual(scale, 1.0)
        self.assertTrue(np.all(centered[:, :100] == 255))

        session = ReviewSession.__new__(ReviewSession)
        session.frames = [{"frame_id": "frame", "width": 100, "height": 80}]
        session.index = 0
        session.annotations = {"frame": {"status": "draft", "objects": []}}
        session.selected_object = 0
        session.drag_start = None
        session.drag_current = None
        session.drag_mode = None
        session.drag_original_bbox = None
        session.display_offset = offset
        session.display_scale = scale

        session.mouse_callback(cv2.EVENT_LBUTTONDOWN, 50, 50, 0, None)
        self.assertIsNone(session.drag_start)
        session.mouse_callback(cv2.EVENT_LBUTTONDOWN, 120, 80, 0, None)
        session.mouse_callback(cv2.EVENT_LBUTTONUP, 150, 110, 0, None)
        self.assertEqual(session.annotation["objects"][0]["bbox"], [20, 20, 30, 30])

        reduced, small_offset, small_scale = center_review_image(image, 50, 40)
        self.assertEqual(reduced.shape, (40, 50, 3))
        self.assertEqual(small_offset, (0, 0))
        self.assertEqual(small_scale, 0.5)

    def test_review_fullscreen_canvas_uses_screen_aspect_when_window_reports_image_size(self) -> None:
        # Mac 전체화면에서도 OpenCV가 원본 640x480을 반환하는 경우를 재현한다.
        canvas_width = fullscreen_canvas_width(640, 480, 3088 / 2110)
        centered, offset, scale = center_review_image(
            np.zeros((480, 640, 3), dtype=np.uint8), canvas_width, 480
        )
        self.assertGreater(canvas_width, 640)
        self.assertEqual(centered.shape[1], canvas_width)
        self.assertGreater(offset[0], 0)
        self.assertEqual(scale, 1.0)

    def test_review_new_bbox_finishes_at_image_edge_without_mouse_up(self) -> None:
        for edge, expected_bbox in (
            ((99, 45), [20, 20, 80, 25]),
            ((50, 79), [20, 20, 30, 60]),
            ((0, 45), [0, 20, 20, 25]),
            ((50, 0), [20, 0, 30, 20]),
        ):
            with self.subTest(edge=edge):
                session = ReviewSession.__new__(ReviewSession)
                session.frames = [{"frame_id": "frame", "width": 100, "height": 80}]
                session.index = 0
                session.annotations = {"frame": {"status": "draft", "objects": []}}
                session.selected_object = 0
                session.drag_start = None
                session.drag_current = None
                session.drag_mode = None
                session.drag_original_bbox = None
                session.mouse_callback(cv2.EVENT_LBUTTONDOWN, 20, 20, 0, None)
                session.mouse_callback(cv2.EVENT_MOUSEMOVE, *edge, 0, None)
                self.assertEqual(session.annotation["objects"][0]["bbox"], expected_bbox)
                self.assertIsNone(session.drag_start)
                session.mouse_callback(cv2.EVENT_LBUTTONUP, *edge, 0, None)
                self.assertEqual(len(session.annotation["objects"]), 1)

    def test_review_resize_finishes_at_image_edge_without_mouse_up(self) -> None:
        for start, edge, expected_bbox in (
            ((50, 40), (99, 60), [20, 20, 80, 40]),
            ((20, 20), (0, 5), [0, 5, 50, 35]),
            ((50, 40), (70, 79), [20, 20, 50, 60]),
        ):
            with self.subTest(edge=edge):
                session = ReviewSession.__new__(ReviewSession)
                session.frames = [{"frame_id": "frame", "width": 100, "height": 80}]
                session.index = 0
                session.annotations = {"frame": {"status": "approved", "objects": [
                    {"bbox": [20, 20, 30, 20]},
                ]}}
                session.selected_object = 0
                session.drag_start = None
                session.drag_current = None
                session.drag_mode = None
                session.drag_original_bbox = None
                session.fixed_bbox = None

                session.mouse_callback(cv2.EVENT_LBUTTONDOWN, *start, 0, None)
                session.mouse_callback(cv2.EVENT_MOUSEMOVE, *edge, 0, None)
                self.assertEqual(session.annotation["objects"][0]["bbox"], expected_bbox)
                self.assertEqual(session.annotation["status"], "manual_review_required")
                self.assertIsNone(session.drag_start)
                session.mouse_callback(cv2.EVENT_LBUTTONUP, *edge, 0, None)
                self.assertEqual(len(session.annotation["objects"]), 1)

    def test_review_resize_from_existing_edge_does_not_finish_early(self) -> None:
        session = ReviewSession.__new__(ReviewSession)
        session.frames = [{"frame_id": "frame", "width": 100, "height": 80}]
        session.index = 0
        session.annotations = {"frame": {"status": "approved", "objects": [
            {"bbox": [0, 20, 30, 20]},
        ]}}
        session.selected_object = 0
        session.drag_start = None
        session.drag_current = None
        session.drag_mode = None
        session.drag_original_bbox = None
        session.fixed_bbox = None

        session.mouse_callback(cv2.EVENT_LBUTTONDOWN, 0, 40, 0, None)
        session.mouse_callback(cv2.EVENT_MOUSEMOVE, 0, 45, 0, None)
        self.assertEqual(session.drag_start, (0, 40))
        session.mouse_callback(cv2.EVENT_LBUTTONUP, 0, 50, 0, None)
        self.assertEqual(session.annotation["objects"][0]["bbox"], [0, 20, 30, 30])

    def test_review_new_bbox_starts_unassigned(self) -> None:
        session = ReviewSession.__new__(ReviewSession)
        session.frames = [{"frame_id": "frame", "width": 100, "height": 80}]
        session.index = 0
        session.annotations = {
            "frame": {
                "frame_id": "frame",
                "status": "negative",
                "objects": [],
            }
        }
        session.selected_object = 0

        session.add_bbox((10, 15), (50, 45))

        self.assertEqual(len(session.annotation["objects"]), 1)
        item = session.annotation["objects"][0]
        self.assertEqual(item["bbox"], [10, 15, 40, 30])
        self.assertIsNone(item["class_name"])
        self.assertIsNone(item["vehicle_instance"])
        self.assertEqual(session.annotation["status"], "manual_review_required")

    def test_review_new_bbox_uses_single_recording_vehicle(self) -> None:
        session = ReviewSession.__new__(ReviewSession)
        session.frames = [
            {
                "frame_id": "frame",
                "width": 100,
                "height": 80,
                "vehicle_instances_present": ["other_car_3"],
            }
        ]
        session.index = 0
        session.annotations = {
            "frame": {
                "frame_id": "frame",
                "status": "manual_review_required",
                "objects": [],
            }
        }
        session.selected_object = 0
        session.instance_to_class = {"other_car_3": "other_vehicle"}

        session.add_bbox((10, 15), (50, 45))

        item = session.annotation["objects"][0]
        self.assertEqual(item["class_name"], "other_vehicle")
        self.assertEqual(item["vehicle_instance"], "other_car_3")
        self.assertEqual(session.annotation["status"], "manual_review_required")

    def test_review_numeric_shortcuts_override_existing_class_and_advance(self) -> None:
        session = ReviewSession.__new__(ReviewSession)
        session.fixed_bbox = None
        session.frames = [{"frame_id": "frame"}, {"frame_id": "next"}]
        session.index = 0
        session.annotations = {
            "frame": {
                "frame_id": "frame",
                "status": "manual_review_required",
                "objects": [
                    {
                        "bbox": [10.0, 10.0, 20.0, 20.0],
                        "class_name": "ioniq5_silver",
                        "vehicle_instance": "ioniq5_silver",
                    }
                ],
            },
            "next": {"frame_id": "next", "status": "manual_review_required", "objects": []},
        }
        session.selected_object = 0
        session.class_to_id = {"ioniq5_silver": 0, "other_vehicle": 1}
        session.instance_to_class = {
            "ioniq5_silver": "ioniq5_silver",
            "other_car_1": "other_vehicle",
            "other_car_2": "other_vehicle",
            "other_car_3": "other_vehicle",
        }
        moved: list[int] = []
        session.move = moved.append

        session.approve_selected_vehicle_and_move("other_car_1")

        item = session.annotations["frame"]["objects"][0]
        self.assertEqual(item["class_name"], "other_vehicle")
        self.assertEqual(item["vehicle_instance"], "other_car_1")
        self.assertEqual(session.annotations["frame"]["status"], "approved")
        self.assertEqual(moved, [1])

        session.annotations["frame"]["objects"] = [{"bbox": [1.0, 1.0, 2.0, 2.0]}]
        session.set_status_and_move("negative")
        self.assertEqual(session.annotations["frame"]["objects"], [])
        self.assertEqual(session.annotations["frame"]["status"], "negative")
        self.assertEqual(moved, [1, 1])

    def test_review_numeric_shortcut_removes_only_same_vehicle_duplicates(self) -> None:
        session = ReviewSession.__new__(ReviewSession)
        session.fixed_bbox = None
        session.frames = [{"frame_id": "frame"}]
        session.index = 0
        session.annotations = {
            "frame": {
                "frame_id": "frame",
                "status": "draft",
                "objects": [
                    {"bbox": [1, 1, 10, 10], "class_name": "ioniq5_silver", "vehicle_instance": "ioniq5_silver"},
                    {"bbox": [2, 2, 20, 20], "class_name": "other_vehicle", "vehicle_instance": "other_car_1"},
                    {"bbox": [3, 3, 30, 30], "class_name": "ioniq5_silver", "vehicle_instance": "ioniq5_silver"},
                    {"bbox": [4, 4, 40, 40], "class_name": "other_vehicle", "vehicle_instance": "other_car_2"},
                ],
            }
        }
        session.selected_object = 2
        session.class_to_id = {"ioniq5_silver": 0, "other_vehicle": 1}
        session.instance_to_class = {
            "ioniq5_silver": "ioniq5_silver",
            "other_car_1": "other_vehicle",
            "other_car_2": "other_vehicle",
        }
        moved: list[int] = []
        session.move = moved.append

        session.approve_selected_vehicle_and_move("ioniq5_silver")

        self.assertEqual(
            [item["bbox"] for item in session.annotation["objects"]],
            [[2, 2, 20, 20], [3, 3, 30, 30], [4, 4, 40, 40]],
        )
        self.assertEqual(session.selected_object, 1)
        self.assertEqual(session.annotation["status"], "approved")
        self.assertEqual(moved, [1])

    def test_review_numeric_shortcut_stays_when_other_box_unassigned(self) -> None:
        session = ReviewSession.__new__(ReviewSession)
        session.fixed_bbox = None
        session.frames = [{"frame_id": "frame"}]
        session.index = 0
        session.annotations = {
            "frame": {
                "frame_id": "frame", "status": "draft",
                "objects": [
                    {"bbox": [1, 1, 10, 10], "class_name": "ioniq5_silver", "vehicle_instance": "ioniq5_silver"},
                    {"bbox": [2, 2, 20, 20], "class_name": "ioniq5_silver", "vehicle_instance": "ioniq5_silver"},
                    {"bbox": [3, 3, 30, 30], "class_name": None, "vehicle_instance": None},
                ],
            }
        }
        session.selected_object = 1
        session.class_to_id = {"ioniq5_silver": 0}
        session.instance_to_class = {"ioniq5_silver": "ioniq5_silver"}
        moved: list[int] = []
        session.move = moved.append

        with self.assertRaisesRegex(DatasetContractError, "클래스를 먼저 지정"):
            session.approve_selected_vehicle_and_move("ioniq5_silver")

        self.assertEqual(len(session.annotation["objects"]), 2)
        self.assertEqual(session.annotation["status"], "manual_review_required")
        self.assertEqual(moved, [])

    def test_review_rejects_duplicate_bbox_for_same_vehicle(self) -> None:
        session = ReviewSession.__new__(ReviewSession)
        session.frames = [{"frame_id": "frame"}]
        session.index = 0
        session.annotations = {
            "frame": {
                "frame_id": "frame",
                "status": "manual_review_required",
                "objects": [
                    {
                        "bbox": [10.0, 10.0, 20.0, 20.0],
                        "class_name": "ioniq5_silver",
                        "vehicle_instance": "ioniq5_silver",
                    },
                    {
                        "bbox": [40.0, 10.0, 20.0, 20.0],
                        "class_name": "ioniq5_silver",
                        "vehicle_instance": "ioniq5_silver",
                    },
                ],
            }
        }
        session.class_to_id = {"ioniq5_silver": 0}
        session.instance_to_class = {"ioniq5_silver": "ioniq5_silver"}

        with self.assertRaisesRegex(DatasetContractError, "같은 차량에 bbox가 두 개"):
            session.set_status("approved")

        self.assertEqual(
            session.annotations["frame"]["status"], "manual_review_required"
        )

    def test_review_approve_and_move_checks_before_advancing(self) -> None:
        session = ReviewSession.__new__(ReviewSession)
        session.frames = [{"frame_id": "frame"}]
        session.index = 0
        session.annotations = {
            "frame": {"frame_id": "frame", "status": "draft", "objects": []}
        }
        session.class_to_id = {"ioniq5_silver": 0}
        session.instance_to_class = {"ioniq5_silver": "ioniq5_silver"}
        moved: list[int] = []
        session.move = moved.append

        with self.assertRaisesRegex(DatasetContractError, "negative로 확정"):
            session.set_status_and_move("approved")
        self.assertEqual(moved, [])
        self.assertEqual(session.annotation["status"], "draft")

        session.annotation["objects"] = [{
            "bbox": [10.0, 10.0, 20.0, 20.0],
            "class_name": "ioniq5_silver",
            "vehicle_instance": "ioniq5_silver",
        }]
        session.set_status_and_move("approved")
        self.assertEqual(session.annotation["status"], "approved")
        self.assertEqual(moved, [1])

    def test_review_fixed_bbox_propagates_only_within_recording(self) -> None:
        session = ReviewSession.__new__(ReviewSession)
        session.frames = [
            {"frame_id": "first", "recording_id": "video_a", "width": 100, "height": 80},
            {"frame_id": "second", "recording_id": "video_a", "width": 100, "height": 80},
            {"frame_id": "third", "recording_id": "video_a", "width": 100, "height": 80},
            {"frame_id": "fourth", "recording_id": "video_b", "width": 100, "height": 80},
        ]
        session.annotations = {
            "first": {"status": "approved", "objects": [{
                "bbox": [10.0, 15.0, 30.0, 20.0],
                "class_name": "other_vehicle", "vehicle_instance": "other_car_1",
            }]},
            "second": {"status": "draft", "objects": [{
                "bbox": [12.0, 16.0, 28.0, 19.0],
                "class_name": "other_vehicle", "vehicle_instance": "other_car_1",
            }]},
            "third": {"status": "approved", "objects": [{
                "bbox": [50.0, 15.0, 30.0, 20.0],
                "class_name": "other_vehicle", "vehicle_instance": "other_car_1",
            }]},
            "fourth": {"status": "draft", "objects": []},
        }
        session.index = 0
        session.selected_object = 0
        session.instance_to_class = {"other_car_1": "other_vehicle"}
        session.fixed_bbox = None
        session.save = lambda: None
        session.load_image = lambda: None

        session.toggle_fixed_bbox()
        session.move(1)
        self.assertEqual(len(session.annotation["objects"]), 1)
        self.assertEqual(session.annotation["objects"][0]["bbox"], [10.0, 15.0, 30.0, 20.0])
        self.assertEqual(session.annotation["status"], "manual_review_required")

        session.move(1)
        self.assertEqual(session.annotation["objects"][0]["bbox"], [50.0, 15.0, 30.0, 20.0])
        self.assertEqual(session.annotation["status"], "approved")

        session.move(1)
        self.assertIsNone(session.fixed_bbox)
        self.assertEqual(session.annotation["objects"], [])

    def test_review_fixed_bbox_removes_same_instance_proposals(self) -> None:
        session = ReviewSession.__new__(ReviewSession)
        session.frames = [{
            "frame_id": "frame", "recording_id": "video_a",
            "width": 100, "height": 80,
        }]
        session.index = 0
        session.annotations = {
            "frame": {
                "status": "draft",
                "objects": [
                    {"bbox": [1, 1, 10, 10], "class_name": "other_vehicle", "vehicle_instance": "other_car_1"},
                    {"bbox": [2, 2, 10, 10], "class_name": "other_vehicle", "vehicle_instance": "other_car_2"},
                    {"bbox": [3, 3, 10, 10], "class_name": "other_vehicle", "vehicle_instance": "other_car_1"},
                ],
            }
        }
        session.fixed_bbox = {
            "recording_id": "video_a", "bbox": [20, 20, 30, 20],
            "class_name": "other_vehicle", "vehicle_instance": "other_car_1",
        }
        session.selected_object = 0

        session._apply_fixed_bbox()

        self.assertEqual(
            [item["vehicle_instance"] for item in session.annotation["objects"]],
            ["other_car_2", "other_car_1"],
        )
        self.assertEqual(session.annotation["objects"][1]["bbox"], [20, 20, 30, 20])
        self.assertEqual(session.selected_object, 1)
        self.assertEqual(session.annotation["status"], "manual_review_required")

    def test_review_fixed_bbox_removes_only_unassigned_overlapping_proposals(self) -> None:
        session = ReviewSession.__new__(ReviewSession)
        session.frames = [
            {"frame_id": "first", "recording_id": "video_a", "width": 100, "height": 80},
            {"frame_id": "second", "recording_id": "video_a", "width": 100, "height": 80},
        ]
        session.index = 0
        fixed_object = {
            "bbox": [20, 20, 30, 20], "class_name": "ioniq5_silver",
            "vehicle_instance": "ioniq5_silver", "proposal_confidence": None,
        }
        session.annotations = {
            "first": {"status": "draft", "objects": [fixed_object]},
            "second": {"status": "draft", "objects": [
                {"bbox": [21, 21, 30, 20], "class_name": "other_vehicle",
                 "vehicle_instance": "other_car_3", "proposal_confidence": 0.8},
                {"bbox": [19, 19, 30, 20], "class_name": None,
                 "vehicle_instance": None, "proposal_confidence": 0.7},
                {"bbox": [60, 20, 30, 20], "class_name": "other_vehicle",
                 "vehicle_instance": "other_car_1", "proposal_confidence": 0.9},
                {"bbox": [22, 21, 30, 20], "class_name": "other_vehicle",
                 "vehicle_instance": "other_car_2", "proposal_confidence": None},
            ]},
        }
        session.selected_object = 0
        session.instance_to_class = {"ioniq5_silver": "ioniq5_silver"}
        session.fixed_bbox = None
        session.save = lambda: None
        session.load_image = lambda: None

        session.toggle_fixed_bbox()
        session.move(1)

        self.assertEqual(
            [item["vehicle_instance"] for item in session.annotation["objects"]],
            ["other_car_3", "other_car_1", "other_car_2", "ioniq5_silver"],
        )

    def test_review_edited_fixed_bbox_becomes_next_frame_position(self) -> None:
        session = ReviewSession.__new__(ReviewSession)
        session.frames = [
            {"frame_id": frame_id, "recording_id": "video_a", "width": 100, "height": 80}
            for frame_id in ("first", "second", "third")
        ]
        session.annotations = {
            "first": {"status": "draft", "objects": [{
                "bbox": [20, 20, 30, 20], "class_name": "ioniq5_silver",
                "vehicle_instance": "ioniq5_silver",
            }]},
            "second": {"status": "draft", "objects": []},
            "third": {"status": "draft", "objects": []},
        }
        session.index = 0
        session.selected_object = 0
        session.instance_to_class = {"ioniq5_silver": "ioniq5_silver"}
        session.fixed_bbox = None
        session.drag_start = None
        session.drag_current = None
        session.drag_mode = None
        session.drag_original_bbox = None
        session.save = lambda: None
        session.load_image = lambda: None

        session.toggle_fixed_bbox()
        session.move(1)
        session.mouse_callback(cv2.EVENT_LBUTTONDOWN, 35, 30, 0, None)
        session.mouse_callback(cv2.EVENT_LBUTTONUP, 40, 30, 0, None)

        self.assertEqual(session.fixed_bbox["bbox"], [25, 20, 30, 20])
        session.move(1)
        self.assertEqual(session.annotation["objects"][0]["bbox"], [25, 20, 30, 20])

        session.mouse_callback(cv2.EVENT_LBUTTONDOWN, 55, 40, 0, None)
        session.mouse_callback(cv2.EVENT_LBUTTONUP, 60, 45, 0, None)
        self.assertEqual(session.fixed_bbox["bbox"], [25, 20, 35, 25])

    def test_review_fixed_bbox_edits_current_but_preserves_rejected_next_frame(self) -> None:
        session = ReviewSession.__new__(ReviewSession)
        session.frames = [
            {"frame_id": "first", "recording_id": "video_a", "width": 100, "height": 80},
            {"frame_id": "second", "recording_id": "video_a", "width": 100, "height": 80},
        ]
        session.index = 0
        duplicated = [
            {"bbox": [1, 1, 10, 10], "class_name": "ioniq5_silver", "vehicle_instance": "ioniq5_silver"},
            {"bbox": [20, 20, 30, 20], "class_name": "ioniq5_silver", "vehicle_instance": "ioniq5_silver"},
        ]
        session.annotations = {
            "first": {"status": "rejected", "objects": [dict(item) for item in duplicated]},
            "second": {"status": "rejected", "objects": [dict(item) for item in duplicated]},
        }
        session.selected_object = 1
        session.instance_to_class = {"ioniq5_silver": "ioniq5_silver"}
        session.fixed_bbox = None
        session.save = lambda: None
        session.load_image = lambda: None

        session.toggle_fixed_bbox()
        self.assertEqual(len(session.annotation["objects"]), 1)
        self.assertEqual(session.annotation["objects"][0]["bbox"], [20, 20, 30, 20])
        self.assertEqual(session.annotation["status"], "manual_review_required")

        session.move(1)
        self.assertEqual(session.annotation["objects"], duplicated)
        self.assertEqual(session.annotation["status"], "rejected")

    def test_review_fixed_bbox_rejects_unassigned_without_removing_other_boxes(self) -> None:
        session = ReviewSession.__new__(ReviewSession)
        session.frames = [{"frame_id": "frame", "recording_id": "video_a", "width": 640, "height": 480}]
        session.index = 0
        session.annotations = {"frame": {"status": "draft", "objects": [
            {"bbox": [10, 10, 100, 100], "class_name": None, "vehicle_instance": None, "proposal_confidence": 0.9},
            {"bbox": [300, 300, 100, 100], "class_name": None, "vehicle_instance": None, "proposal_confidence": 0.8},
        ]}}
        session.selected_object = 0
        session.fixed_bbox = None
        session.instance_to_class = {"ioniq5_silver": "ioniq5_silver"}
        before = json.loads(json.dumps(session.annotation))

        with self.assertRaisesRegex(DatasetContractError, "먼저 지정"):
            session.toggle_fixed_bbox()

        self.assertIsNone(session.fixed_bbox)
        self.assertEqual(session.annotation, before)

    def test_review_fixed_bbox_label_changes_propagate(self) -> None:
        for action in ("class", "instance", "numeric"):
            with self.subTest(action=action):
                session = ReviewSession.__new__(ReviewSession)
                session.frames = [
                    {"frame_id": name, "recording_id": "video_a", "width": 100, "height": 80}
                    for name in ("first", "second")
                ]
                session.index = 0
                session.selected_object = 0
                session.fixed_bbox = None
                session.class_names = ["ioniq5_silver", "other_vehicle"]
                session.class_to_id = {"ioniq5_silver": 0, "other_vehicle": 1}
                session.instances_by_class = {"ioniq5_silver": ["ioniq5_silver"], "other_vehicle": ["other_car_1", "other_car_2"]}
                session.instance_to_class = {"ioniq5_silver": "ioniq5_silver", "other_car_1": "other_vehicle", "other_car_2": "other_vehicle"}
                initial_instance = "other_car_1" if action == "instance" else "ioniq5_silver"
                session.annotations = {
                    "first": {"status": "draft", "objects": [{
                        "bbox": [20, 20, 30, 20], "class_name": session.instance_to_class[initial_instance],
                        "vehicle_instance": initial_instance,
                    }]},
                    "second": {"status": "draft", "objects": []},
                }
                session.save = lambda: None
                session.load_image = lambda: None
                session.toggle_fixed_bbox()
                if action == "numeric":
                    expected = "other_car_2"
                    session.approve_selected_vehicle_and_move(expected)
                    self.assertEqual(session.annotations["first"]["status"], "approved")
                else:
                    expected = "other_car_1" if action == "class" else "other_car_2"
                    if action == "class":
                        session.cycle_class()
                    else:
                        session.cycle_instance()
                    session.move(1)
                self.assertEqual(session.fixed_bbox["vehicle_instance"], expected)
                self.assertEqual(session.annotation["objects"][0]["vehicle_instance"], expected)
                self.assertEqual(session.annotation["objects"][0]["class_name"], "other_vehicle")

    def test_review_fixed_bbox_preserves_all_completed_statuses(self) -> None:
        for status in ("approved", "negative", "rejected"):
            with self.subTest(status=status):
                session = ReviewSession.__new__(ReviewSession)
                session.frames = [{"frame_id": "frame", "recording_id": "video_a", "width": 100, "height": 80}]
                session.index = 0
                session.annotations = {"frame": {"status": status, "objects": []}}
                session.fixed_bbox = {"recording_id": "video_a", "bbox": [20, 20, 30, 20], "class_name": "ioniq5_silver", "vehicle_instance": "ioniq5_silver"}
                session._apply_fixed_bbox()
                self.assertEqual(session.annotation, {"status": status, "objects": []})

    def test_review_loads_image_from_korean_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = ReviewSession.__new__(ReviewSession)
            session.work_dir = Path(directory) / "한글 이미지"
            session.work_dir.mkdir()
            image = np.full((12, 16, 3), 73, dtype=np.uint8)
            success, encoded = cv2.imencode(".png", image)
            self.assertTrue(success)
            (session.work_dir / "차량.png").write_bytes(encoded.tobytes())
            session.frames = [{"frame_id": "frame", "image_path": "차량.png"}]
            session.index = 0
            session.selected_object = 0
            session.annotations = {"frame": {"status": "draft", "objects": []}}
            session.load_image()
            np.testing.assert_array_equal(session.image, image)

    def test_review_space_moves_without_approving(self) -> None:
        session = Mock()
        session.frame = {"width": 100, "height": 80}
        session.draw.return_value = np.zeros((80, 100, 3), dtype=np.uint8)
        with ExitStack() as stack:
            stack.enter_context(patch("review_annotations.load_settings", return_value={"paths": {"work_dir": "."}}))
            stack.enter_context(patch("review_annotations.ReviewSession", return_value=session))
            stack.enter_context(patch("review_annotations.mac_screen_aspect_ratio", return_value=None))
            stack.enter_context(patch("review_annotations.cv2.getWindowImageRect", return_value=(0, 0, 100, 80)))
            stack.enter_context(patch("review_annotations.cv2.waitKeyEx", side_effect=[ord(" "), ord("q")]))
            for name in ("namedWindow", "resizeWindow", "setMouseCallback", "imshow", "destroyAllWindows"):
                stack.enter_context(patch(f"review_annotations.cv2.{name}"))
            run_review(Path("unused-config.yaml"))
        session.move.assert_called_once_with(1)
        session.set_status.assert_not_called()
        session.set_status_and_move.assert_not_called()
        session.save.assert_called_once_with()

    def test_korean_two_set_keys_match_english_keyboard_positions(self) -> None:
        for korean, english in {
            "ㅁ": "a", "ㅜ": "n", "ㅌ": "x", "ㅊ": "c",
            "ㅍ": "v", "ㅇ": "d", "ㄹ": "f", "ㅂ": "q",
        }.items():
            self.assertEqual(normalize_review_key(ord(korean)), ord(english))
        self.assertEqual(normalize_review_key(ord("9")), ord("9"))
        self.assertEqual(normalize_review_key(ord("ㄹ") & 0xFF), ord("9"))
        self.assertEqual(normalize_review_key(ord("가")), -1)

    def test_class_instance_contract(self) -> None:
        settings = load_settings(self.config_path)
        class_to_id, instance_to_class = class_maps(settings)
        self.assertEqual(class_to_id["other_vehicle"], 2)
        self.assertEqual(instance_to_class["other_car_3"], "other_vehicle")

    def test_export_rejects_unreviewed_frames(self) -> None:
        for number, color in enumerate(("red", "green", "blue"), start=1):
            self.make_recording(number, color)
        prepare(self.config_path)
        with self.assertRaisesRegex(DatasetContractError, "검수가 끝나지 않은"):
            export_dataset(self.config_path, None)


class ManualWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        import register_recordings
        from dataset_contracts import VideoMetadata

        self.temporary = tempfile.TemporaryDirectory(prefix="manual-bbox-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.register = register_recordings
        self.config = yaml.safe_load((PROJECT_DIR / "config.yaml").read_text(encoding="utf-8"))
        self.config["paths"] = {
            "recordings_dir": str(self.root / "recordings"),
            "work_dir": str(self.root / "work"),
            "datasets_dir": str(self.root / "datasets"),
        }
        self.config["frame_extraction"]["frame_interval"] = 1
        self.config["proposal_detector"]["enabled"] = True
        self.config["proposal_detector"]["model_path"] = str(self.root / "absent.onnx")
        self.config_path = self.root / "config.yaml"
        self.config_path.write_text(yaml.safe_dump(self.config), encoding="utf-8")
        self.metadata = VideoMetadata("h264", 640, 480, 30.0, 2500000, 2 / 30, 2)

    @staticmethod
    def labeled_box(x: int = 10) -> list[dict]:
        return [{"bbox": [x, 10, 20, 20], "class_name": "ioniq5_silver",
                 "vehicle_instance": "ioniq5_silver"}]

    def make_work(self, name: str, specs: list[dict]) -> Path:
        import hashlib

        directory = self.root / name
        frames, annotations, recordings = [], [], []
        for spec in specs:
            fid = spec["id"]
            rid = f"recording_{fid}"
            image = np.full((60, 80, 3), spec.get("color", 20), dtype=np.uint8)
            ok, encoded = cv2.imencode(".jpg", image)
            self.assertTrue(ok)
            relative = f"frames/{rid}/{fid}.jpg"
            path = directory / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(encoded.tobytes())
            status = spec.get("status", "approved")
            video_sha = hashlib.sha256(rid.encode()).hexdigest()
            frame = {
                "frame_id": fid, "image_path": relative, "session_id": f"session_{rid}",
                "source_group_id": f"source_{rid}", "condition_tags": [],
                "class_name": None, "vehicle_instance": None,
                "source_video": str(directory / "recording.mp4"),
                "source_video_sha256": video_sha, "recording_id": rid,
                "derived_from": None, "encoding_profile": "primary_2_5mbps",
                "dataset_usage": "development", "frame_index": 0,
                "timestamp_seconds": 0.0, "timestamp_source": "ffprobe_best_effort_pts",
                "video_bitrate_bps": 2500000, "width": 80, "height": 60,
                "image_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "annotation_status": status,
            }
            if spec.get("instances_key", True):
                frame["vehicle_instances_present"] = []
            frames.append(frame)
            annotations.append({"frame_id": fid, "status": status,
                                "objects": spec.get("objects", self.labeled_box())})
            recordings.append({"recording_id": rid, "source_video_sha256": video_sha})
        for filename, records in (("frame_manifest.jsonl", frames), ("annotations.jsonl", annotations),
                                  ("recording_index.jsonl", recordings)):
            atomic_write_jsonl(directory / filename, records)
        return directory

    def snapshot_work(self) -> dict[str, bytes]:
        directory = self.root / "work"
        return {path.relative_to(directory).as_posix(): path.read_bytes()
                for path in directory.rglob("*") if path.is_file()}

    def test_folder_registration_handles_unicode_duplicates_and_new_files(self) -> None:
        source = self.root / "한글 촬영 폴더"
        source.mkdir()
        (source / "첫 촬영.MP4").write_bytes(b"first video")
        (source / "동일 영상.mp4").write_bytes(b"first video")
        with patch.object(self.register, "probe_video", return_value=self.metadata):
            result = self.register.register_recordings(
                self.config_path, self.root / "missing-plans.yaml", None, source)
            self.assertEqual(result, {"found": 2, "validated": 2, "saved": 1, "duplicate": 1})
            repeat = self.register.register_recordings(
                self.config_path, self.root / "missing-plans.yaml", None, source)
            self.assertEqual((repeat["saved"], repeat["duplicate"]), (0, 2))
            (source / "새 촬영.mp4").write_bytes(b"second video")
            added = self.register.register_recordings(
                self.config_path, self.root / "missing-plans.yaml", None, source)
            self.assertEqual((added["saved"], added["duplicate"]), (1, 2))
        manifests = list((self.root / "recordings").rglob("recording_manifest.json"))
        self.assertEqual(len(manifests), 2)
        sessions = set()
        for path in manifests:
            manifest = read_json(path)
            self.assertEqual(manifest["vehicle_instances_present"], [])
            self.assertRegex(manifest["recording_id"], r"^rec_[0-9a-f]{24}$")
            self.assertEqual(manifest["session_id"], "clip_" + manifest["source_video_sha256"][:24])
            sessions.add(manifest["session_id"])
        self.assertEqual(len(sessions), 2)

        other_source = self.root / "other_pc" / "incoming"
        other_source.mkdir(parents=True)
        (other_source / "renamed.mp4").write_bytes(b"first video")
        original_plan = self.register.folder_capture_plan(source)
        original = next(item for item in original_plan["recordings"] if item["file"] == "첫 촬영.MP4")
        copied = self.register.folder_capture_plan(other_source)["recordings"][0]
        self.assertEqual(copied["session_id"], original["session_id"])
        self.assertEqual(copied["recording_id"], original["recording_id"])
        grouped = self.register.folder_capture_plan(source, session_id="shared_capture")
        self.assertEqual({item["session_id"] for item in grouped["recordings"]}, {"shared_capture"})

    def test_manual_prepare_avoids_model_imports_and_preserves_approved(self) -> None:
        import builtins
        import importlib.util

        source = self.root / "incoming"
        source.mkdir()
        (source / "clip.mp4").write_bytes(b"mock video")
        with patch.object(self.register, "probe_video", return_value=self.metadata):
            self.register.register_recordings(self.config_path, self.root / "unused.yaml", None, source)

        class Capture:
            def __init__(self, _path):
                self.index = 0

            def isOpened(self):
                return True

            def read(self):
                if self.index == 2:
                    return False, None
                frame = np.full((480, 640, 3), self.index * 50, dtype=np.uint8)
                self.index += 1
                return True, frame

            def release(self):
                pass

        original_import = builtins.__import__

        def without_models(name, *args, **kwargs):
            if name.split(".")[0] in {"torch", "yolox", "yolox_runtime"}:
                raise AssertionError(f"수동 경로에서 모델 모듈을 불러왔습니다: {name}")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=without_models):
            spec = importlib.util.spec_from_file_location("manual_prepare_under_test", PROJECT_DIR / "prepare_dataset.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with patch.object(module, "probe_video", return_value=self.metadata), \
                    patch.object(module, "probe_frame_pts", return_value=[0.0, 1 / 30]), \
                    patch.object(module.cv2, "VideoCapture", Capture):
                self.assertEqual(module.prepare(self.config_path, manual=True), (2, 0))
                path = self.root / "work" / "annotations.jsonl"
                annotations = read_jsonl(path)
                annotations[0].update(status="approved", objects=self.labeled_box())
                atomic_write_jsonl(path, annotations)
                before = self.snapshot_work()
                self.assertEqual(module.prepare(self.config_path, manual=True), (0, 1))
                self.assertEqual(self.snapshot_work(), before)
        self.assertTrue(yaml.safe_load(self.config_path.read_text(encoding="utf-8"))["proposal_detector"]["enabled"])

    def test_merge_two_workers_deduplicates_images_and_repeated_delivery(self) -> None:
        first = self.make_work("worker_one", [{"id": "first", "color": 20}])
        second = self.make_work("worker_two", [{"id": "second", "color": 90},
                                                 {"id": "same_pixels", "color": 20}])
        result = self.register.merge_work(self.config_path, [first, second])
        self.assertEqual(result, {"added": 2, "updated": 0, "duplicate": 1})
        before = self.snapshot_work()
        repeated = self.register.merge_work(self.config_path, [first, second])
        self.assertEqual(repeated, {"added": 0, "updated": 0, "duplicate": 3})
        self.assertEqual(self.snapshot_work(), before)
        self.assertEqual(len(read_jsonl(self.root / "work" / "recording_index.jsonl")), 3)

    def test_merge_conflicting_completed_boxes_leaves_all_existing_bytes(self) -> None:
        first = self.make_work("first_worker", [{"id": "shared"}])
        self.register.merge_work(self.config_path, [first])
        before = self.snapshot_work()
        second = self.make_work("second_worker", [{"id": "new", "color": 100},
                                                    {"id": "shared", "objects": self.labeled_box(30)}])
        with self.assertRaisesRegex(DatasetContractError, "같은 사진의 박스가 다릅니다"):
            self.register.merge_work(self.config_path, [second])
        self.assertEqual(self.snapshot_work(), before)

    def test_merge_completed_labels_updates_old_metadata_without_instances_key(self) -> None:
        old = self.make_work("old_worker", [{"id": "shared", "status": "manual_review_required",
                                              "objects": [], "instances_key": False}])
        completed = self.make_work("completed_worker", [{"id": "shared"}])
        self.register.merge_work(self.config_path, [old])
        result = self.register.merge_work(self.config_path, [completed])
        self.assertEqual(result, {"added": 0, "updated": 1, "duplicate": 1})
        annotations = read_jsonl(self.root / "work" / "annotations.jsonl")
        self.assertEqual(annotations[0]["status"], "approved")
        self.assertEqual(annotations[0]["objects"], self.labeled_box())
        before = self.snapshot_work()
        self.register.merge_work(self.config_path, [completed])
        self.assertEqual(self.snapshot_work(), before)

    def test_merge_preserves_rejected_unlabeled_boxes(self) -> None:
        objects = [{"bbox": [1, 2, 10, 20], "class_name": None, "vehicle_instance": None}]
        received = self.make_work("rejected_worker", [{"id": "discard", "status": "rejected", "objects": objects}])
        result = self.register.merge_work(self.config_path, [received])
        self.assertEqual(result["added"], 1)
        annotation = read_jsonl(self.root / "work" / "annotations.jsonl")[0]
        self.assertEqual(annotation["status"], "rejected")
        self.assertEqual(annotation["objects"], objects)


if __name__ == "__main__":
    unittest.main()
