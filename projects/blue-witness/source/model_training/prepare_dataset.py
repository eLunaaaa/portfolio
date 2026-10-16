#!/usr/bin/env python3
"""녹화 MP4를 추적 가능한 JPEG 프레임과 검수 초안으로 변환한다."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch

from dataset_contracts import (
    DatasetContractError,
    atomic_write_jsonl,
    discover_recordings,
    load_settings,
    normalize_bbox,
    probe_frame_pts,
    probe_video,
    read_jsonl,
    resolve_project_path,
    sha256_file,
    validate_video_profile,
    write_jpeg,
)
from yolox_runtime import (
    build_yolox_nano,
    decode_raw_predictions,
    filter_and_nms,
    import_yolox,
    load_official_checkpoint,
    preprocess_frame,
    validate_yolox_installation,
)


class ProposalDetector:
    """공식 COCO YOLOX 또는 프로젝트 ONNX 모델로 검수 초안을 생성한다.

    프로젝트 모델은 bbox와 class를 먼저 채울 수 있지만 자동 승인하지 않는다.
    하나의 class에 여러 실제 차량이 묶인 경우에는 vehicle instance를 비워 두어
    검수자가 실제 차량을 선택하도록 한다.
    """

    SUPPORTED_BACKENDS = {"yolox_pytorch", "onnx_decoded", "onnx_yolox_raw"}
    SUPPORTED_OUTPUT = "yolox_decoded_cxcywh_objectness_class_scores"
    RAW_YOLOX_OUTPUT = "yolox_raw_grid_offsets_objectness_class_scores"
    SUPPORTED_NORMALIZATIONS = {"zero_to_one", "none"}

    def __init__(self, settings: dict[str, Any], config_path: Path) -> None:
        config = settings["proposal_detector"]
        self.enabled = bool(config.get("enabled", False))
        self.net: Any | None = None
        self.model: torch.nn.Module | None = None
        if not self.enabled:
            return
        self.backend = str(config.get("backend", ""))
        if self.backend not in self.SUPPORTED_BACKENDS:
            raise DatasetContractError(
                f"지원하지 않는 proposal_detector.backend={self.backend}. "
                f"지원값: {', '.join(sorted(self.SUPPORTED_BACKENDS))}"
            )
        required = ("model_path", "input_width", "input_height", "confidence_threshold", "nms_iou_threshold")
        missing = [key for key in required if config.get(key) is None]
        if missing:
            raise DatasetContractError(
                "proposal_detector.enabled=true이지만 실제 모델 계약이 빠졌습니다: "
                + ", ".join(missing)
            )
        model_path = Path(str(config["model_path"])).expanduser()
        if not model_path.is_absolute():
            model_path = (config_path.parent / model_path).resolve()
        if not model_path.is_file():
            raise DatasetContractError(f"제안 모델 파일이 없습니다: {model_path}")
        expected_model_sha = config.get("model_sha256")
        if expected_model_sha is not None:
            if (
                not isinstance(expected_model_sha, str)
                or sha256_file(model_path) != expected_model_sha
            ):
                raise DatasetContractError(
                    f"제안 모델 SHA-256이 설정과 다릅니다: {model_path}"
                )
        self.input_width = int(config["input_width"])
        self.input_height = int(config["input_height"])
        self.confidence_threshold = float(config["confidence_threshold"])
        self.nms_iou_threshold = float(config["nms_iou_threshold"])
        if self.input_width <= 0 or self.input_height <= 0:
            raise DatasetContractError("제안 모델 입력 크기는 양수여야 합니다.")
        if not 0.0 <= self.confidence_threshold <= 1.0 or not 0.0 <= self.nms_iou_threshold <= 1.0:
            raise DatasetContractError("제안 모델 confidence/NMS threshold는 0~1이어야 합니다.")

        if self.backend == "yolox_pytorch":
            source_dir, verified_checkpoint = validate_yolox_installation(config_path, settings)
            if model_path != verified_checkpoint:
                raise DatasetContractError(
                    "PyTorch 제안 모델은 yolox.pretrained_checkpoint와 같은 검증 파일이어야 합니다."
                )
            import_yolox(source_dir)
            from yolox.data.datasets import COCO_CLASSES

            requested_classes = config.get("coco_vehicle_classes")
            if not isinstance(requested_classes, list) or not requested_classes:
                raise DatasetContractError("proposal_detector.coco_vehicle_classes가 필요합니다.")
            unknown = [name for name in requested_classes if name not in COCO_CLASSES]
            if unknown:
                raise DatasetContractError(f"알 수 없는 COCO 제안 클래스: {unknown}")
            self.coco_class_names = tuple(COCO_CLASSES)
            self.vehicle_class_ids = {self.coco_class_names.index(name) for name in requested_classes}
            self.model = build_yolox_nano(source_dir, len(self.coco_class_names))
            load_official_checkpoint(self.model, model_path)
            self.model.eval().cpu()
        else:
            for key in ("output_format", "normalization"):
                if config.get(key) is None:
                    raise DatasetContractError(f"ONNX 제안 모델에는 {key} 설정이 필요합니다.")
            expected_output = (
                self.RAW_YOLOX_OUTPUT
                if self.backend == "onnx_yolox_raw"
                else self.SUPPORTED_OUTPUT
            )
            if config["output_format"] != expected_output:
                raise DatasetContractError(
                    f"지원하지 않는 proposal_detector.output_format={config['output_format']}. "
                    f"현재 backend의 지원값: {expected_output}"
                )
            if config["normalization"] not in self.SUPPORTED_NORMALIZATIONS:
                raise DatasetContractError(f"지원하지 않는 normalization={config['normalization']}")
            self.normalization = str(config["normalization"])
            self.net = cv2.dnn.readNetFromONNX(str(model_path))
            self.proposal_class_names: list[str] = []
            self.class_candidate_instances: dict[str, list[str]] = {}
            self.instance_to_project_class: dict[str, str] = {}
            if self.backend == "onnx_yolox_raw":
                class_names = config.get("class_names")
                if not isinstance(class_names, list) or not class_names or any(
                    not isinstance(name, str) or not name for name in class_names
                ):
                    raise DatasetContractError(
                        "onnx_yolox_raw 제안 모델에는 class_names 문자열 배열이 필요합니다."
                    )
                project_classes = {item["name"] for item in settings["classes"]}
                unknown_classes = sorted(set(class_names) - project_classes)
                if unknown_classes:
                    raise DatasetContractError(
                        f"제안 모델 class_names에 프로젝트에 없는 class가 있습니다: {unknown_classes}"
                    )
                candidates = config.get("class_candidate_instances")
                if not isinstance(candidates, dict) or set(candidates) != set(class_names):
                    raise DatasetContractError(
                        "class_candidate_instances는 제안 모델의 모든 class를 정확히 포함해야 합니다."
                    )
                instance_to_class = {
                    instance: item["name"]
                    for item in settings["classes"]
                    for instance in item["vehicle_instances"]
                }
                for class_name, instances in candidates.items():
                    if not isinstance(instances, list) or not instances or any(
                        instance not in instance_to_class for instance in instances
                    ):
                        raise DatasetContractError(
                            f"{class_name}의 candidate instance 목록이 올바르지 않습니다."
                        )
                self.proposal_class_names = list(class_names)
                self.class_candidate_instances = {
                    name: list(instances) for name, instances in candidates.items()
                }
                self.instance_to_project_class = instance_to_class

    def detect(
        self,
        frame: Any,
        vehicle_instances_present: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """선택한 backend로 차량 후보를 찾고 프로젝트 bbox 초안으로 변환한다."""
        if not self.enabled:
            return []
        if self.backend == "yolox_pytorch":
            return self._detect_yolox_pytorch(frame)
        if self.backend == "onnx_yolox_raw":
            return self._detect_trained_onnx(frame, vehicle_instances_present or [])
        return self._detect_onnx(frame)

    def _detect_trained_onnx(
        self,
        frame: Any,
        vehicle_instances_present: list[str],
    ) -> list[dict[str, Any]]:
        """학습된 YOLOX raw 출력을 해석해 bbox와 project class 초안을 만든다.

        아이오닉처럼 class와 실제 차량이 일대일이면 instance도 채운다. 여러 차량을
        묶은 other_vehicle은 잘못된 정답 전파를 막기 위해 instance만 비워 둔다.
        """
        assert self.net is not None
        frame_height, frame_width = frame.shape[:2]
        prepared, scale = preprocess_frame(frame, (self.input_height, self.input_width))
        self.net.setInput(prepared[np.newaxis, ...])
        raw_output = np.asarray(self.net.forward())
        expected_fields = 5 + len(self.proposal_class_names)
        if raw_output.ndim != 3 or raw_output.shape[0] != 1 or raw_output.shape[2] != expected_fields:
            raise DatasetContractError(
                "학습 제안 모델 출력은 [1, N, 5+C]여야 합니다. "
                f"실제 shape={raw_output.shape}, class 수={len(self.proposal_class_names)}"
            )
        decoded = decode_raw_predictions(
            raw_output, (self.input_height, self.input_width)
        )
        detections = filter_and_nms(
            decoded,
            scale,
            self.confidence_threshold,
            self.nms_iou_threshold,
        )
        proposals: list[dict[str, Any]] = []
        for detection in detections:
            class_id = int(detection["class_id"])
            if not 0 <= class_id < len(self.proposal_class_names):
                raise DatasetContractError(f"제안 모델 class ID 범위 오류: {class_id}")
            bbox = normalize_bbox(
                detection["bbox"], frame_width, frame_height
            )
            if bbox is None:
                continue
            proposal_class = self.proposal_class_names[class_id]
            class_name, vehicle_instance = self._resolve_project_label(
                proposal_class, vehicle_instances_present
            )
            proposals.append(
                {
                    "bbox": bbox,
                    "proposal_confidence": round(float(detection["score"]), 6),
                    "proposal_source_class": proposal_class,
                    "class_name": class_name,
                    "vehicle_instance": vehicle_instance,
                }
            )
        return proposals

    def _resolve_project_label(
        self,
        proposal_class: str,
        vehicle_instances_present: list[str],
    ) -> tuple[str | None, str | None]:
        """2-class 제안과 recording manifest를 조합해 안전한 정답만 미리 채운다.

        2-class 모델의 other_vehicle에는 팰리세이드와 기타 차량 세 대가 함께 포함된다.
        현재 녹화에 해당 후보가 정확히 한 대일 때만 3-class/instance로 변환하며,
        여러 대가 등장하면 잘못된 자동 선별을 피하기 위해 둘 다 비워 둔다.
        """
        matching_instances = [
            instance
            for instance in vehicle_instances_present
            if instance in self.class_candidate_instances[proposal_class]
        ]
        if len(matching_instances) != 1:
            return None, None
        vehicle_instance = matching_instances[0]
        return self.instance_to_project_class[vehicle_instance], vehicle_instance

    def _detect_yolox_pytorch(self, frame: Any) -> list[dict[str, Any]]:
        """공식 COCO 모델에서 car·bus·truck 후보 위치만 class-agnostic NMS로 남긴다."""
        assert self.model is not None
        frame_height, frame_width = frame.shape[:2]
        prepared, scale = preprocess_frame(frame, (self.input_height, self.input_width))
        with torch.inference_mode():
            output = self.model(torch.from_numpy(prepared).unsqueeze(0)).cpu().numpy()[0]
        class_ids = np.argmax(output[:, 5:], axis=1)
        class_scores = output[np.arange(len(output)), class_ids + 5]
        scores = output[:, 4] * class_scores
        candidates = np.where(
            (scores >= self.confidence_threshold) & np.isin(class_ids, list(self.vehicle_class_ids))
        )[0]
        boxes: list[list[float]] = []
        kept_scores: list[float] = []
        source_classes: list[str] = []
        for index in candidates:
            center_x, center_y, width, height = map(float, output[index, :4])
            bbox = normalize_bbox(
                [
                    (center_x - width / 2.0) / scale,
                    (center_y - height / 2.0) / scale,
                    width / scale,
                    height / scale,
                ],
                frame_width,
                frame_height,
            )
            if bbox is not None:
                boxes.append(bbox)
                kept_scores.append(float(scores[index]))
                source_classes.append(self.coco_class_names[int(class_ids[index])])
        indexes = cv2.dnn.NMSBoxes(
            boxes, kept_scores, self.confidence_threshold, self.nms_iou_threshold
        )
        kept = np.asarray(indexes).reshape(-1).tolist() if len(indexes) else []
        return [
            {
                "bbox": boxes[index],
                "proposal_confidence": round(kept_scores[index], 6),
                "proposal_source_class": source_classes[index],
                "class_name": None,
                "vehicle_instance": None,
            }
            for index in kept
        ]

    def _detect_onnx(self, frame: Any) -> list[dict[str, Any]]:
        """기존 decoded ONNX 계약을 사용하는 제안 경로를 호환용으로 유지한다."""
        assert self.net is not None
        frame_height, frame_width = frame.shape[:2]
        scale = min(self.input_width / frame_width, self.input_height / frame_height)
        resized_width = max(1, round(frame_width * scale))
        resized_height = max(1, round(frame_height * scale))
        resized = cv2.resize(frame, (resized_width, resized_height))
        canvas = np.full((self.input_height, self.input_width, 3), 114, dtype=np.uint8)
        pad_x = (self.input_width - resized_width) // 2
        pad_y = (self.input_height - resized_height) // 2
        canvas[pad_y : pad_y + resized_height, pad_x : pad_x + resized_width] = resized
        factor = 1.0 / 255.0 if self.normalization == "zero_to_one" else 1.0
        blob = cv2.dnn.blobFromImage(canvas, scalefactor=factor, swapRB=True)
        self.net.setInput(blob)
        output = np.asarray(self.net.forward()).squeeze()
        if output.ndim != 2 or output.shape[1] < 6:
            raise DatasetContractError(
                f"제안 모델 출력은 [N, 5+C]여야 합니다. 실제 shape={output.shape}"
            )
        boxes: list[list[float]] = []
        scores: list[float] = []
        for row in output:
            score = float(row[4]) * float(np.max(row[5:]))
            if score < self.confidence_threshold:
                continue
            center_x, center_y, width, height = map(float, row[:4])
            bbox = normalize_bbox(
                [
                    (center_x - width / 2.0 - pad_x) / scale,
                    (center_y - height / 2.0 - pad_y) / scale,
                    width / scale,
                    height / scale,
                ],
                frame_width,
                frame_height,
            )
            if bbox is not None:
                boxes.append(bbox)
                scores.append(score)
        indexes = cv2.dnn.NMSBoxes(
            boxes, scores, self.confidence_threshold, self.nms_iou_threshold
        )
        kept = np.asarray(indexes).reshape(-1).tolist() if len(indexes) else []
        return [
            {
                "bbox": boxes[index],
                "proposal_confidence": round(scores[index], 6),
                "class_name": None,
                "vehicle_instance": None,
            }
            for index in kept
        ]


def _timestamp_for_frame(
    frame_index: int,
    timestamps: list[float | None],
    fps: float,
    allow_fallback: bool,
    video_path: Path,
) -> tuple[float, str]:
    if frame_index < len(timestamps) and timestamps[frame_index] is not None:
        return float(timestamps[frame_index]), "ffprobe_best_effort_pts"
    if allow_fallback and fps > 0:
        return frame_index / fps, "frame_index_divided_by_fps"
    raise DatasetContractError(
        f"프레임 {frame_index}의 PTS가 없습니다: {video_path}. "
        "필요하면 frame_extraction.allow_fps_timestamp_fallback을 true로 명시하세요."
    )


def prepare(config_path: Path) -> tuple[int, int]:
    """새 녹화만 추출하고 기존 프레임·검수 결과는 보존한다."""
    settings = load_settings(config_path)
    recordings_dir = resolve_project_path(config_path, settings["paths"]["recordings_dir"])
    work_dir = resolve_project_path(config_path, settings["paths"]["work_dir"])
    frames_dir = work_dir / "frames"
    frame_manifest_path = work_dir / "frame_manifest.jsonl"
    annotations_path = work_dir / "annotations.jsonl"
    recording_index_path = work_dir / "recording_index.jsonl"
    existing_frames = read_jsonl(frame_manifest_path)
    existing_annotations = read_jsonl(annotations_path)
    frames_by_id = {record["frame_id"]: record for record in existing_frames}
    annotations_by_id = {record["frame_id"]: record for record in existing_annotations}
    image_sha_to_frame = {
        record["image_sha256"]: record["frame_id"] for record in existing_frames
    }
    recording_index = read_jsonl(recording_index_path)
    recording_sha = {record["recording_id"]: record["source_video_sha256"] for record in recording_index}
    for record in existing_frames:
        recording_sha.setdefault(record["recording_id"], record["source_video_sha256"])
    sha_to_recording = {digest: recording_id for recording_id, digest in recording_sha.items()}

    extraction = settings["frame_extraction"]
    interval = int(extraction["frame_interval"])
    quality = int(extraction["jpeg_quality"])
    if interval <= 0 or not 1 <= quality <= 100:
        raise DatasetContractError("frame_interval은 양수, jpeg_quality는 1~100이어야 합니다.")
    detector = ProposalDetector(settings, config_path)
    added_frames = 0
    skipped_recordings = 0

    for video_path, recording in discover_recordings(recordings_dir, settings):
        video_sha = sha256_file(video_path)
        recording_id = recording["recording_id"]
        declared_sha = recording.get("source_video_sha256")
        if declared_sha is not None and declared_sha != video_sha:
            raise DatasetContractError(
                f"manifest와 실제 MP4 SHA가 다릅니다: {recording_id}: {declared_sha} != {video_sha}"
            )
        if video_sha in sha_to_recording and sha_to_recording[video_sha] != recording_id:
            raise DatasetContractError(
                f"동일 MP4 SHA를 다른 recording_id로 등록할 수 없습니다: "
                f"{sha_to_recording[video_sha]}, {recording_id}"
            )
        if recording_id in recording_sha:
            if recording_sha[recording_id] != video_sha:
                raise DatasetContractError(
                    f"기존 recording_id의 MP4가 바뀌었습니다: {recording_id}"
                )
            skipped_recordings += 1
            continue

        metadata = probe_video(video_path)
        validate_video_profile(metadata, recording["encoding_profile"], settings, video_path)
        timestamps = probe_frame_pts(video_path)
        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            raise DatasetContractError(f"OpenCV가 영상을 열지 못했습니다: {video_path}")
        frame_index = 0
        try:
            while True:
                success, frame = capture.read()
                if not success:
                    break
                if frame_index % interval != 0:
                    frame_index += 1
                    continue
                timestamp, timestamp_source = _timestamp_for_frame(
                    frame_index,
                    timestamps,
                    metadata.fps,
                    bool(extraction.get("allow_fps_timestamp_fallback", False)),
                    video_path,
                )
                frame_id = f"{recording_id}__f{frame_index:09d}"
                relative_image_path = Path("frames") / recording_id / f"{frame_id}.jpg"
                image_path = work_dir / relative_image_path
                write_jpeg(image_path, frame, quality)
                image_sha = sha256_file(image_path)
                if image_sha in image_sha_to_frame:
                    image_path.unlink(missing_ok=True)
                    frame_index += 1
                    continue
                objects = detector.detect(
                    frame, recording.get("vehicle_instances_present", [])
                )
                status = "draft" if objects else "manual_review_required"
                frames_by_id[frame_id] = {
                    "frame_id": frame_id,
                    "image_path": relative_image_path.as_posix(),
                    "session_id": recording["session_id"],
                    "source_group_id": recording["source_group_id"],
                    "condition_tags": recording["condition_tags"],
                    # 검수자가 새 bbox를 그렸을 때 이 영상에 실제 차량이 하나뿐이면
                    # class와 instance를 다시 입력하지 않아도 되도록 촬영 계약을 전달한다.
                    "vehicle_instances_present": recording.get(
                        "vehicle_instances_present", []
                    ),
                    "class_name": None,
                    "vehicle_instance": None,
                    "source_video": str(video_path.resolve()),
                    "source_video_sha256": video_sha,
                    "recording_id": recording_id,
                    "derived_from": recording.get("derived_from"),
                    "encoding_profile": recording["encoding_profile"],
                    # 이전 manifest는 development로 간주하여 기존 데이터와 호환한다.
                    "dataset_usage": recording.get("dataset_usage", "development"),
                    "frame_index": frame_index,
                    "timestamp_seconds": round(timestamp, 9),
                    "timestamp_source": timestamp_source,
                    "video_bitrate_bps": metadata.bitrate_bps,
                    "width": int(frame.shape[1]),
                    "height": int(frame.shape[0]),
                    "image_sha256": image_sha,
                    "annotation_status": status,
                }
                annotations_by_id[frame_id] = {
                    "frame_id": frame_id,
                    "status": status,
                    "objects": objects,
                }
                image_sha_to_frame[image_sha] = frame_id
                added_frames += 1
                frame_index += 1
        finally:
            capture.release()
        if frame_index == 0:
            raise DatasetContractError(f"디코딩된 프레임이 없습니다: {video_path}")
        recording_sha[recording_id] = video_sha
        sha_to_recording[video_sha] = recording_id

    ordered_frames = [frames_by_id[key] for key in sorted(frames_by_id)]
    ordered_annotations = [annotations_by_id[key] for key in sorted(annotations_by_id)]
    atomic_write_jsonl(frame_manifest_path, ordered_frames)
    atomic_write_jsonl(annotations_path, ordered_annotations)
    atomic_write_jsonl(
        recording_index_path,
        [
            {"recording_id": recording_id, "source_video_sha256": recording_sha[recording_id]}
            for recording_id in sorted(recording_sha)
        ],
    )
    return added_frames, skipped_recordings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        added, skipped = prepare(args.config.resolve())
    except (DatasetContractError, OSError, RuntimeError, cv2.error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"완료: 새 프레임 {added}개, 이미 처리된 녹화 {skipped}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
