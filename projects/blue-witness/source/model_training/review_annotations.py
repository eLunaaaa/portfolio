#!/usr/bin/env python3
"""@file review_annotations.py
@brief 자동 bbox 초안을 사람이 class·차량 instance와 함께 최종 확정하는 OpenCV 도구다.

전체 흐름
1. prepare_dataset.py가 만든 frame manifest와 annotation 초안을 읽는다.
2. 완료되지 않은 첫 frame부터 이미지와 bbox를 화면 중앙에 표시한다.
3. 마우스로 bbox를 추가·이동·resize하고 키보드로 실제 차량과 상태를 지정한다.
4. frame을 이동하거나 종료할 때 annotation과 manifest를 원자적으로 저장한다.

고정 bbox는 같은 recording 안에서만 다음 frame으로 전달되며, 미지정 proposal과 크게
겹치거나 같은 차량으로 지정된 중복 bbox를 제거한다. proposal은 검수 편의를 위한
초안일 뿐 자동 승인하지 않는다. 모든 객체의 class-instance 계약이 맞아야 approved가
가능하며, 최종 결과는 export_coco.py의 데이터셋 입력으로 사용된다.
"""

from __future__ import annotations

import argparse
import ctypes
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from dataset_contracts import (
    DatasetContractError,
    atomic_write_jsonl,
    class_maps,
    load_settings,
    normalize_bbox,
    read_jsonl,
    resolve_project_path,
)


WINDOW_NAME = "Vehicle dataset review"
COLORS = [(70, 220, 70), (255, 170, 40), (70, 100, 255), (220, 80, 220)]
HANDLE_RADIUS = 6
LARGE_FRAME_JUMP = 100
FIXED_PROPOSAL_IOU_THRESHOLD = 0.6
KOREAN_TWO_SET_KEYS = {
    "ㅂ": "q", "ㅈ": "w", "ㄷ": "e", "ㄱ": "r", "ㅅ": "t",
    "ㅛ": "y", "ㅕ": "u", "ㅑ": "i", "ㅐ": "o", "ㅔ": "p",
    "ㅁ": "a", "ㄴ": "s", "ㅇ": "d", "ㄹ": "f", "ㅎ": "g",
    "ㅗ": "h", "ㅓ": "j", "ㅏ": "k", "ㅣ": "l", "ㅋ": "z",
    "ㅌ": "x", "ㅊ": "c", "ㅍ": "v", "ㅠ": "b", "ㅜ": "n", "ㅡ": "m",
    "ㅃ": "q", "ㅉ": "w", "ㄸ": "e", "ㄲ": "r", "ㅆ": "t",
    "ㅒ": "o", "ㅖ": "p",
}


def normalize_review_key(key_code: int) -> int:
    """영문·두벌식 한글 입력을 같은 물리 자판의 단축키로 통일한다."""
    if 0 <= key_code < 128:
        return key_code
    if 0 <= key_code <= 0x10FFFF:
        mapped = KOREAN_TWO_SET_KEYS.get(chr(key_code))
        if mapped is not None:
            return ord(mapped)
    return -1


def bbox_iou(left: list[float], right: list[float]) -> float:
    """xywh bbox 두 개의 교집합 면적을 합집합 면적으로 나눈다."""
    left_x, left_y, left_width, left_height = left
    right_x, right_y, right_width, right_height = right
    overlap_width = max(0.0, min(left_x + left_width, right_x + right_width) - max(left_x, right_x))
    overlap_height = max(0.0, min(left_y + left_height, right_y + right_height) - max(left_y, right_y))
    intersection = overlap_width * overlap_height
    union = left_width * left_height + right_width * right_height - intersection
    return intersection / union if union > 0 else 0.0


def center_review_image(
    image: np.ndarray, viewport_width: int, viewport_height: int
) -> tuple[np.ndarray, tuple[int, int], float]:
    """창의 표시 영역 중앙에 검수 이미지를 배치하고 좌표 변환 정보를 반환한다."""
    image_height, image_width = image.shape[:2]
    viewport_width = max(viewport_width, 1)
    viewport_height = max(viewport_height, 1)
    scale = min(1.0, viewport_width / image_width, viewport_height / image_height)
    display_width = max(1, round(image_width * scale))
    display_height = max(1, round(image_height * scale))
    displayed = (
        image if scale == 1.0 else cv2.resize(image, (display_width, display_height))
    )
    offset_x = (viewport_width - display_width) // 2
    offset_y = (viewport_height - display_height) // 2
    canvas = np.full((viewport_height, viewport_width, 3), 255, dtype=np.uint8)
    canvas[offset_y:offset_y + display_height, offset_x:offset_x + display_width] = displayed
    return canvas, (offset_x, offset_y), scale


def mac_screen_aspect_ratio() -> float | None:
    """Mac 주 화면의 실제 가로·세로 비율을 읽어 전체화면 캔버스에 사용한다."""
    if sys.platform != "darwin":
        return None
    try:
        core_graphics = ctypes.CDLL(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        core_graphics.CGMainDisplayID.restype = ctypes.c_uint32
        core_graphics.CGDisplayPixelsWide.argtypes = [ctypes.c_uint32]
        core_graphics.CGDisplayPixelsWide.restype = ctypes.c_size_t
        core_graphics.CGDisplayPixelsHigh.argtypes = [ctypes.c_uint32]
        core_graphics.CGDisplayPixelsHigh.restype = ctypes.c_size_t
        display_id = core_graphics.CGMainDisplayID()
        width = core_graphics.CGDisplayPixelsWide(display_id)
        height = core_graphics.CGDisplayPixelsHigh(display_id)
    except (OSError, AttributeError):
        return None
    return width / height if width > 0 and height > 0 else None


def fullscreen_canvas_width(viewport_width: int, viewport_height: int, screen_aspect: float) -> int:
    """Mac 전체화면이 원본 크기를 보고해도 화면 비율만큼 캔버스 폭을 확보한다."""
    return max(viewport_width, round(viewport_height * screen_aspect))


class ReviewSession:
    """검수 frame, bbox 편집 상태와 저장 lifecycle을 함께 관리한다.

    annotation과 frame manifest를 frame_id로 연결하고, 현재 선택 bbox·drag·고정 전파
    상태를 한 객체에 유지한다. 화면 좌표는 중앙 정렬과 확대·축소 비율을 역변환하여
    원본 640×480 좌표로 저장하므로 창 크기가 달라져도 COCO bbox 계약은 변하지 않는다.
    """

    def __init__(self, settings: dict[str, Any], work_dir: Path) -> None:
        self.settings = settings
        self.work_dir = work_dir
        self.frame_manifest_path = work_dir / "frame_manifest.jsonl"
        self.annotations_path = work_dir / "annotations.jsonl"
        self.frames = read_jsonl(self.frame_manifest_path)
        annotations = read_jsonl(self.annotations_path)
        if not self.frames:
            raise DatasetContractError("검수할 프레임이 없습니다. prepare_dataset.py를 먼저 실행하세요.")
        self.annotations = {item["frame_id"]: item for item in annotations}
        if set(self.annotations) != {item["frame_id"] for item in self.frames}:
            raise DatasetContractError("frame_manifest와 annotations의 frame_id 집합이 다릅니다.")
        self.frame_by_id = {item["frame_id"]: item for item in self.frames}
        self.class_to_id, self.instance_to_class = class_maps(settings)
        self.class_names = [item["name"] for item in settings["classes"]]
        self.instances_by_class = {
            item["name"]: list(item["vehicle_instances"]) for item in settings["classes"]
        }
        self.index = self._resume_index()
        self.selected_object = 0
        self.drag_start: tuple[int, int] | None = None
        self.drag_current: tuple[int, int] | None = None
        self.drag_mode: str | None = None
        self.drag_original_bbox: list[float] | None = None
        self.image: Any | None = None
        self.fixed_bbox: dict[str, Any] | None = None
        self.display_offset = (0, 0)
        self.display_scale = 1.0

    def _resume_index(self) -> int:
        for index, frame in enumerate(self.frames):
            status = self.annotations[frame["frame_id"]]["status"]
            if status not in {"approved", "negative", "rejected"}:
                return index
        return 0

    @property
    def frame(self) -> dict[str, Any]:
        return self.frames[self.index]

    @property
    def annotation(self) -> dict[str, Any]:
        return self.annotations[self.frame["frame_id"]]

    def load_image(self) -> None:
        path = self.work_dir / self.frame["image_path"]
        self.image = cv2.imdecode(
            np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR
        )
        if self.image is None:
            raise DatasetContractError(f"이미지를 읽을 수 없습니다: {path}")
        objects = self.annotation["objects"]
        self.selected_object = min(self.selected_object, max(0, len(objects) - 1))

    def save(self) -> None:
        """annotation 상태를 frame manifest에 동기화한 뒤 원자적으로 저장한다."""
        for frame in self.frames:
            frame["annotation_status"] = self.annotations[frame["frame_id"]]["status"]
        atomic_write_jsonl(
            self.annotations_path,
            [self.annotations[frame["frame_id"]] for frame in self.frames],
        )
        atomic_write_jsonl(self.frame_manifest_path, self.frames)

    def set_status(self, status: str) -> None:
        if status == "approved":
            if not self.annotation["objects"]:
                raise DatasetContractError("객체가 없는 프레임은 approved가 아니라 negative로 확정하세요.")
            assigned_instances: list[str] = []
            for item in self.annotation["objects"]:
                class_name = item.get("class_name")
                vehicle_instance = item.get("vehicle_instance")
                if class_name not in self.class_to_id:
                    raise DatasetContractError("모든 객체의 클래스를 먼저 지정하세요.")
                if self.instance_to_class.get(vehicle_instance) != class_name:
                    raise DatasetContractError("모든 객체의 vehicle_instance를 클래스와 맞게 지정하세요.")
                assigned_instances.append(vehicle_instance)
            duplicated_instances = sorted(
                instance
                for instance, count in Counter(assigned_instances).items()
                if count > 1
            )
            if duplicated_instances:
                raise DatasetContractError(
                    "같은 차량에 bbox가 두 개 이상 지정됐습니다. 한 차량은 하나의 bbox로 "
                    f"합쳐야 합니다: {', '.join(duplicated_instances)}"
                )
        if status == "negative":
            self.annotation["objects"] = []
            self.selected_object = 0
        self.annotation["status"] = status

    def cycle_class(self) -> None:
        objects = self.annotation["objects"]
        if not objects:
            return
        item = objects[self.selected_object]
        current = item.get("class_name")
        next_index = 0 if current not in self.class_names else (self.class_names.index(current) + 1) % len(self.class_names)
        class_name = self.class_names[next_index]
        item["class_name"] = class_name
        item["vehicle_instance"] = self.instances_by_class[class_name][0]
        self._sync_fixed_label(item)
        self.annotation["status"] = "manual_review_required"

    def cycle_instance(self) -> None:
        objects = self.annotation["objects"]
        if not objects:
            return
        item = objects[self.selected_object]
        class_name = item.get("class_name")
        if class_name not in self.instances_by_class:
            class_name = self.class_names[0]
            item["class_name"] = class_name
        instances = self.instances_by_class[class_name]
        current = item.get("vehicle_instance")
        next_index = 0 if current not in instances else (instances.index(current) + 1) % len(instances)
        item["vehicle_instance"] = instances[next_index]
        self._sync_fixed_label(item)
        self.annotation["status"] = "manual_review_required"

    def _sync_fixed_label(self, item: dict[str, Any]) -> None:
        """고정 중인 객체의 라벨 수정도 다음 프레임에 반영한다."""
        fixed = self.fixed_bbox
        if fixed is not None and item is fixed.get("object"):
            fixed["class_name"] = item["class_name"]
            fixed["vehicle_instance"] = item["vehicle_instance"]

    def approve_selected_vehicle_and_move(self, vehicle_instance: str) -> None:
        """선택 bbox를 지정 차량의 유일한 박스로 확정하고 다음 frame으로 이동한다.

        자동 제안 bbox의 UNASSIGNED 상태와 사람이 추가한 기본 class 상태를 구분하지
        않고 instance 계약으로 class까지 직접 결정한다. 동일 차량에 이미 지정된
        다른 bbox는 중복 제안으로 보고 삭제한다. 다른 차량 bbox는 보존하며, 그중
        미지정 또는 중복 객체가 남으면 approved 검증에서 현재 frame에 머무른다.
        """
        objects = self.annotation["objects"]
        if not objects:
            raise DatasetContractError("차량 단축키를 사용하려면 bbox를 먼저 추가하세요.")
        class_name = self.instance_to_class.get(vehicle_instance)
        if class_name is None:
            raise DatasetContractError(
                f"현재 config에 없는 차량 단축키입니다: {vehicle_instance}"
            )
        item = objects[self.selected_object]
        item["class_name"] = class_name
        item["vehicle_instance"] = vehicle_instance
        self._sync_fixed_label(item)
        self.annotation["objects"] = [
            candidate for candidate in objects
            if candidate is item or candidate.get("vehicle_instance") != vehicle_instance
        ]
        self.selected_object = self.annotation["objects"].index(item)
        self.annotation["status"] = "manual_review_required"
        self.set_status("approved")
        self.move(1)

    def set_status_and_move(self, status: str) -> None:
        """상태별 승인 검사를 거친 뒤 저장하고 다음 frame으로 이동한다."""
        self.set_status(status)
        self.move(1)

    def delete_selected(self) -> None:
        objects = self.annotation["objects"]
        if objects:
            objects.pop(self.selected_object)
            self.selected_object = min(self.selected_object, max(0, len(objects) - 1))
            self.annotation["status"] = "manual_review_required"

    # -------------------------------------------------------------------------
    # 기능: 선택한 정지 차량의 bbox를 같은 녹화의 다음 프레임들에 고정한다.
    # 동작 방법: F 키로 현재 bbox·class·instance를 기억하고, 한 프레임씩 앞으로
    #   이동할 때 동일 instance의 미검수 제안을 대체하거나 새 bbox를 추가한다.
    # 사용 이유: 움직이지 않는 차량을 매 프레임 다시 그리지 않으면서도 각 프레임을
    #   사람이 직접 확인하도록 승인 상태는 변경하지 않기 때문이다.
    # 입력/반환: 현재 선택 객체와 frame의 recording_id를 사용하며 반환값은 없다.
    # -------------------------------------------------------------------------
    def toggle_fixed_bbox(self) -> None:
        if self.fixed_bbox is not None:
            self.fixed_bbox = None
            return
        objects = self.annotation["objects"]
        if not objects:
            raise DatasetContractError("고정할 bbox를 먼저 선택하세요.")
        selected = objects[self.selected_object]
        instance = selected.get("vehicle_instance")
        class_name = selected.get("class_name")
        if instance not in self.instance_to_class or self.instance_to_class[instance] != class_name:
            raise DatasetContractError("고정할 bbox의 class와 실제 차량을 먼저 지정하세요.")
        self.fixed_bbox = {
            "recording_id": self.frame["recording_id"],
            "bbox": list(selected["bbox"]),
            "class_name": class_name,
            "vehicle_instance": instance,
        }
        # F 키는 현재 프레임을 고정의 시작점으로 삼는다. 중복 제안이 이미 있거나
        # rejected였던 프레임도 명시적인 편집이므로 즉시 박스를 정리한다.
        self._apply_fixed_bbox(include_reviewed=True)

    def _apply_fixed_bbox(self, include_reviewed: bool = False) -> None:
        """같은 녹화의 bbox 중복을 제거하고 고정 bbox 하나를 적용한다.

        F 키로 선택한 현재 프레임은 상태와 무관하게 명시적으로 수정한다. 이후
        자동 전파에서는 approved·negative·rejected 상태를 그대로 유지한다.
        """
        fixed = self.fixed_bbox
        if fixed is None:
            return
        if self.frame["recording_id"] != fixed["recording_id"]:
            self.fixed_bbox = None
            return
        annotation = self.annotation
        if not include_reviewed and annotation["status"] in {"approved", "negative", "rejected"}:
            return
        bbox = normalize_bbox(
            fixed["bbox"], self.frame["width"], self.frame["height"]
        )
        if bbox is None:
            raise DatasetContractError("고정 bbox가 현재 프레임 크기와 맞지 않습니다.")
        fixed_object = {
            "bbox": bbox,
            "proposal_confidence": None,
            "class_name": fixed["class_name"],
            "vehicle_instance": fixed["vehicle_instance"],
        }
        # 같은 차량의 중복 박스와 고정 박스에 겹치는 미지정 자동 제안을 정리한다.
        # 다른 차량으로 지정된 bbox는 겹쳐 있어도 보존한다.
        remaining_objects = [
            item for item in annotation["objects"]
            if item.get("vehicle_instance") != fixed["vehicle_instance"]
            and not (
                item.get("proposal_confidence") is not None
                and item.get("vehicle_instance") is None
                and bbox_iou(item["bbox"], bbox) >= FIXED_PROPOSAL_IOU_THRESHOLD
            )
        ]
        remaining_objects.append(fixed_object)
        annotation["objects"] = remaining_objects
        fixed["object"] = fixed_object
        self.selected_object = len(remaining_objects) - 1
        annotation["status"] = "manual_review_required"

    def add_bbox(self, start: tuple[int, int], end: tuple[int, int]) -> None:
        """새 bbox를 추가하고 촬영 계약이 단일 차량이면 label 초안을 함께 채운다.

        모델이 차량을 놓쳐 사람이 직접 bbox를 그린 경우에는 해당 bbox에 사용할 모델
        예측이 없다. 대신 frame에 기록된 실제 등장 차량이 정확히 하나라면 recording
        manifest의 확정 정보를 재사용한다. 여러 차량이 등장한 frame은 잘못된 차량을
        자동 지정하지 않도록 class와 instance를 비워 사람이 선택하게 한다.
        """
        x1, x2 = sorted((start[0], end[0]))
        y1, y2 = sorted((start[1], end[1]))
        bbox = normalize_bbox(
            [x1, y1, x2 - x1, y2 - y1], self.frame["width"], self.frame["height"]
        )
        if bbox is None or bbox[2] < 3 or bbox[3] < 3:
            return
        vehicle_instances = self.frame.get("vehicle_instances_present", [])
        vehicle_instance = vehicle_instances[0] if len(vehicle_instances) == 1 else None
        class_name = (
            self.instance_to_class.get(vehicle_instance)
            if vehicle_instance is not None
            else None
        )
        self.annotation["objects"].append(
            {
                "bbox": bbox,
                "proposal_confidence": None,
                "class_name": class_name,
                "vehicle_instance": vehicle_instance,
            }
        )
        self.selected_object = len(self.annotation["objects"]) - 1
        self.annotation["status"] = "manual_review_required"

    @staticmethod
    def _corners(bbox: list[float]) -> dict[str, tuple[float, float]]:
        """xywh bbox의 네 resize handle 좌표를 반환한다."""
        x, y, width, height = bbox
        return {
            "resize_nw": (x, y),
            "resize_ne": (x + width, y),
            "resize_sw": (x, y + height),
            "resize_se": (x + width, y + height),
        }

    def _find_drag_target(self, x: int, y: int) -> tuple[int, str] | None:
        """위에 그려진 bbox부터 handle 또는 내부 hit를 찾아 편집 대상을 정한다."""
        objects = self.annotation["objects"]
        for index, item in reversed(list(enumerate(objects))):
            for mode, (corner_x, corner_y) in self._corners(item["bbox"]).items():
                if abs(x - corner_x) <= HANDLE_RADIUS and abs(y - corner_y) <= HANDLE_RADIUS:
                    return index, mode
        for index, item in reversed(list(enumerate(objects))):
            box_x, box_y, box_width, box_height = item["bbox"]
            if box_x <= x <= box_x + box_width and box_y <= y <= box_y + box_height:
                return index, "move"
        return None

    def _edited_bbox(self, end: tuple[int, int]) -> list[float] | None:
        """현재 drag mode에 맞춰 이동 또는 resize된 bbox를 이미지 경계 안에서 계산한다."""
        if self.drag_start is None or self.drag_original_bbox is None or self.drag_mode is None:
            return None
        start_x, start_y = self.drag_start
        end_x, end_y = end
        x, y, width, height = self.drag_original_bbox
        if self.drag_mode == "move":
            moved_x = min(max(x + end_x - start_x, 0.0), max(0.0, self.frame["width"] - width))
            moved_y = min(max(y + end_y - start_y, 0.0), max(0.0, self.frame["height"] - height))
            return [round(moved_x, 3), round(moved_y, 3), width, height]

        left, top, right, bottom = x, y, x + width, y + height
        if self.drag_mode in {"resize_nw", "resize_sw"}:
            left = end_x
        else:
            right = end_x
        if self.drag_mode in {"resize_nw", "resize_ne"}:
            top = end_y
        else:
            bottom = end_y
        resized = normalize_bbox(
            [min(left, right), min(top, bottom), abs(right - left), abs(bottom - top)],
            self.frame["width"],
            self.frame["height"],
        )
        if resized is None or resized[2] < 3 or resized[3] < 3:
            return None
        return resized

    def move(self, offset: int) -> None:
        wraps = self.index + offset < 0 or self.index + offset >= len(self.frames)
        self.save()
        self.index = (self.index + offset) % len(self.frames)
        self.selected_object = 0
        if offset == 1 and not wraps and self.fixed_bbox is not None:
            self._apply_fixed_bbox()
            self.save()
        elif self.fixed_bbox is not None:
            self.fixed_bbox = None
        self.load_image()

    def counts(self) -> tuple[int, Counter[str]]:
        remaining = 0
        class_counts: Counter[str] = Counter()
        for annotation in self.annotations.values():
            if annotation["status"] not in {"approved", "negative", "rejected"}:
                remaining += 1
            if annotation["status"] == "approved":
                class_counts.update(
                    item["class_name"] for item in annotation["objects"] if item.get("class_name")
                )
        return remaining, class_counts

    def draw(self) -> Any:
        canvas = self.image.copy()
        for index, item in enumerate(self.annotation["objects"]):
            x, y, width, height = [int(round(value)) for value in item["bbox"]]
            class_name = item.get("class_name") or "UNASSIGNED"
            instance = item.get("vehicle_instance") or "UNASSIGNED"
            color_index = self.class_to_id.get(class_name, len(COLORS) - 1)
            color = COLORS[color_index % len(COLORS)]
            thickness = 3 if index == self.selected_object else 1
            cv2.rectangle(canvas, (x, y), (x + width, y + height), color, thickness)
            if index == self.selected_object:
                for corner_x, corner_y in self._corners(item["bbox"]).values():
                    cv2.rectangle(
                        canvas,
                        (int(round(corner_x)) - HANDLE_RADIUS, int(round(corner_y)) - HANDLE_RADIUS),
                        (int(round(corner_x)) + HANDLE_RADIUS, int(round(corner_y)) + HANDLE_RADIUS),
                        color,
                        -1,
                    )
            confidence = item.get("proposal_confidence")
            confidence_text = "" if confidence is None else f" {confidence:.2f}"
            source_class = item.get("proposal_source_class")
            source_text = "" if source_class is None else f" proposal={source_class}"
            cv2.putText(
                canvas,
                f"{index}: {class_name}/{instance}{source_text}{confidence_text}",
                (x, max(18, y - 5)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                color,
                1,
                cv2.LINE_AA,
            )
        if self.drag_start and self.drag_current:
            if self.drag_mode == "add":
                preview_start, preview_end = self.drag_start, self.drag_current
            else:
                preview = self._edited_bbox(self.drag_current)
                if preview is None:
                    preview_start = preview_end = self.drag_current
                else:
                    px, py, pw, ph = [int(round(value)) for value in preview]
                    preview_start, preview_end = (px, py), (px + pw, py + ph)
            cv2.rectangle(canvas, preview_start, preview_end, (255, 255, 255), 1)
        remaining, class_counts = self.counts()
        lines = [
            f"{self.index + 1}/{len(self.frames)}  {self.frame['frame_id']}",
            f"status={self.annotation['status']}  remaining={remaining}",
            "classes=" + ", ".join(f"{name}:{class_counts[name]}" for name in self.class_names),
            "empty drag:add  box drag:move  corner drag:resize  C:class V:instance D:delete F:fix/unfix",
            "A:approve N:negative X:reject  [ ]:prev/next { }:100 prev/next SPACE:save+next Q:quit",
            "1:Ioniq+next 2:Palisade+next 3:Other1+next 4:Other2+next 5:Other3+next 0:negative+next 9:reject+next",
        ]
        if self.fixed_bbox is not None:
            lines.append(f"FIXED: {self.fixed_bbox['vehicle_instance']} (same recording only)")
        for line_index, line in enumerate(lines):
            y = 22 + line_index * 20
            cv2.putText(canvas, line, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(canvas, line, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1, cv2.LINE_AA)
        return canvas

    def _finish_drag(self, end: tuple[int, int]) -> None:
        """드래그 마지막 좌표로 박스를 추가·수정하고 임시 드래그 상태를 비운다."""
        start = self.drag_start
        if start is None:
            return
        mode = self.drag_mode
        x, y = end
        moved = abs(x - start[0]) >= 1 or abs(y - start[1]) >= 1
        if mode == "add" and abs(x - start[0]) >= 3 and abs(y - start[1]) >= 3:
            self.add_bbox(start, end)
        elif mode != "add" and moved:
            edited = self._edited_bbox(end)
            if edited is not None:
                selected = self.annotation["objects"][self.selected_object]
                selected["bbox"] = edited
                # 고정 박스를 이동·크기 조절했다면 이 좌표를 다음 프레임의
                # 전파 기준으로 삼는다. 다른 차량 bbox 편집은 고정값에 영향이 없다.
                if self.fixed_bbox is not None and selected is self.fixed_bbox.get("object"):
                    self.fixed_bbox["bbox"] = list(edited)
                self.annotation["status"] = "manual_review_required"
        self.drag_start = None
        self.drag_current = None
        self.drag_mode = None
        self.drag_original_bbox = None

    def mouse_callback(self, event: int, x: int, y: int, flags: int, parameter: Any) -> None:
        """새 bbox 생성·크기 조절 중 이미지 경계에 닿으면 드래그를 확정한다."""
        del flags, parameter
        offset_x, offset_y = getattr(self, "display_offset", (0, 0))
        scale = getattr(self, "display_scale", 1.0)
        x = round((x - offset_x) / scale)
        y = round((y - offset_y) / scale)
        if event == cv2.EVENT_LBUTTONDOWN:
            if not (0 <= x < self.frame["width"] and 0 <= y < self.frame["height"]):
                return
            self.drag_start = (x, y)
            self.drag_current = (x, y)
            target = self._find_drag_target(x, y)
            if target is None:
                self.drag_mode = "add"
                self.drag_original_bbox = None
            else:
                self.selected_object, self.drag_mode = target
                self.drag_original_bbox = list(self.annotation["objects"][self.selected_object]["bbox"])
        elif event == cv2.EVENT_MOUSEMOVE and self.drag_start is not None:
            self.drag_current = (x, y)
            width, height = self.frame["width"], self.frame["height"]
            start_x, start_y = self.drag_start
            reached_new_edge = (
                (x <= 0 and start_x > 0)
                or (x >= width - 1 and start_x < width - 1)
                or (y <= 0 and start_y > 0)
                or (y >= height - 1 and start_y < height - 1)
            )
            is_new_bbox = self.drag_mode == "add"
            is_resizing = self.drag_mode is not None and self.drag_mode.startswith("resize_")
            if (
                reached_new_edge
                and (
                    (is_new_bbox and abs(x - start_x) >= 3 and abs(y - start_y) >= 3)
                    or (is_resizing and (x, y) != self.drag_start)
                )
            ):
                # 창 밖에서 버튼을 놓으면 mouse-up callback이 오지 않을 수 있다.
                # 시작부터 닿아 있던 변은 무시하고 새로 도달한 경계에서 확정한다.
                edge_x = 0 if x <= 0 else width if x >= width - 1 else x
                edge_y = 0 if y <= 0 else height if y >= height - 1 else y
                self._finish_drag((edge_x, edge_y))
        elif event == cv2.EVENT_LBUTTONUP and self.drag_start is not None:
            self._finish_drag((x, y))


# -----------------------------------------------------------------------------
# 기능:
#   OpenCV 검수 창을 열고 마우스·키보드 입력을 annotation 상태 변경으로 연결한다.
#
# 동작 방법:
#   1. config와 작업 manifest를 읽어 ReviewSession을 만든다.
#   2. 창의 실제 viewport 크기에 맞춰 이미지를 중앙에 배치한다.
#   3. 영문 또는 두벌식 한글 키 입력을 같은 물리 자판 단축키로 정규화한다.
#   4. frame 이동과 종료 시 변경 내용을 저장하고 OpenCV resource를 정리한다.
#
# 사용 이유:
#   자동 proposal을 정답으로 사용하지 않고 사람이 bbox·class·instance를 확정한 뒤
#   COCO 내보내기 단계로 전달하기 위한 pipeline의 수동 검수 경계다.
#
# 입력:
#   config_path - class 계약과 work directory가 기록된 YAML config 경로.
#
# 반환:
#   반환값은 없으며 검수 결과를 annotations.jsonl과 frame_manifest.jsonl에 저장한다.
# -----------------------------------------------------------------------------
def run_review(config_path: Path) -> None:
    settings = load_settings(config_path)
    work_dir = resolve_project_path(config_path, settings["paths"]["work_dir"])
    session = ReviewSession(settings, work_dir)
    session.load_image()
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW_NAME, session.frame["width"], session.frame["height"])
    cv2.setMouseCallback(WINDOW_NAME, session.mouse_callback)
    screen_aspect = mac_screen_aspect_ratio()
    try:
        while True:
            review_image = session.draw()
            try:
                _, _, viewport_width, viewport_height = cv2.getWindowImageRect(WINDOW_NAME)
            except cv2.error:
                viewport_height, viewport_width = review_image.shape[:2]
            if viewport_width <= 0 or viewport_height <= 0:
                viewport_height, viewport_width = review_image.shape[:2]
            if screen_aspect is not None:
                viewport_width = fullscreen_canvas_width(
                    viewport_width, viewport_height, screen_aspect
                )
            centered, session.display_offset, session.display_scale = center_review_image(
                review_image, viewport_width, viewport_height
            )
            cv2.imshow(WINDOW_NAME, centered)
            key = normalize_review_key(cv2.waitKeyEx(30))
            try:
                if key in (ord("q"), 27):
                    break
                vehicle_shortcuts = {
                    ord("1"): "ioniq5_silver",
                    ord("2"): "palisade_black",
                    ord("3"): "other_car_1",
                    ord("4"): "other_car_2",
                    ord("5"): "other_car_3",
                }
                if key in vehicle_shortcuts:
                    session.approve_selected_vehicle_and_move(vehicle_shortcuts[key])
                elif key == ord("0"):
                    session.set_status_and_move("negative")
                elif key == ord("9"):
                    session.set_status_and_move("rejected")
                elif key == ord("c"):
                    session.cycle_class()
                elif key == ord("v"):
                    session.cycle_instance()
                elif key == ord("d"):
                    session.delete_selected()
                elif key == ord("f"):
                    session.toggle_fixed_bbox()
                elif key == ord("a"):
                    session.set_status("approved")
                elif key == ord("n"):
                    session.set_status("negative")
                elif key == ord("x"):
                    session.set_status("rejected")
                elif key == ord("["):
                    session.move(-1)
                elif key == ord("]"):
                    session.move(1)
                elif key == ord(" "):
                    session.move(1)
                elif key == ord("{"):
                    session.move(-LARGE_FRAME_JUMP)
                elif key == ord("}"):
                    session.move(LARGE_FRAME_JUMP)
            except DatasetContractError as error:
                print(f"검수 입력 거부: {error}", file=sys.stderr)
    finally:
        session.save()
        cv2.destroyAllWindows()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        run_review(args.config.resolve())
    except (DatasetContractError, OSError, cv2.error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
